"""
Motor de estoque (seções 8 e 9).

Uso:
    with operacao(repos, usuario_id) as op:
        op.entrada(material, 10, "LOC-000001", "compra", valor_unitario=150, fornecedor_id=...)
        op.saida(material, 2, "venda", venda_id=...)
Tudo é validado em memória e gravado de uma vez no commit (saldos, lotes,
movimentações e custo médio dos materiais) sob o lock global de transação.
Se uma exceção ocorrer dentro do bloco, nada é gravado.
"""
from contextlib import contextmanager
from datetime import date, timedelta

from ..utils import agora_iso, arred, hoje_iso, formatar_qtd
from .precos import custo_base

TIPOS_MOV = {
    "compra": "Compra",
    "venda": "Venda",
    "devolucao": "Devolução",
    "troca": "Troca",
    "sucata": "Sucata / Descarte",
    "doacao": "Doação",
    "ajuste": "Ajuste de inventário",
    "transferencia": "Transferência",
    "estorno": "Estorno (cancelamento)",
}
MOTIVOS_SUCATA = ["Avaria", "Vencido", "Defeito", "Outro"]


class ErroEstoque(Exception):
    pass


def chave_saldo(material_id, localizacao_id, lote_id):
    return f"{material_id}|{localizacao_id}|{lote_id or ''}"


def lote_vencido(lote, hoje=None):
    return bool(lote and lote.get("validade") and lote["validade"] < (hoje or hoje_iso()))


class OperacaoEstoque:
    def __init__(self, repos, usuario_id):
        self.repos = repos
        self.usuario_id = usuario_id
        self.agora = agora_iso()
        self.hoje = self.agora[:10]
        self.saldos = {s["id"]: s for s in repos.saldos.listar()}
        self.lotes = {l["id"]: l for l in repos.lotes.listar()}
        self.lotes_novos = []
        self.locs = {l["id"]: l for l in repos.localizacoes.listar()}
        self.materiais = {}
        self.alteracoes_material = {}
        self.movimentos = []

    # ------------------------------------------------------------ consultas
    def material(self, mat):
        """Aceita id ou dict; retorna o dict atualizado em memória."""
        mid = mat if isinstance(mat, str) else mat["id"]
        if mid not in self.materiais:
            m = self.repos.materiais.obter(mid)
            if not m:
                raise ErroEstoque(f"Material {mid} não encontrado.")
            self.materiais[mid] = m
        return self.materiais[mid]

    def saldo_total(self, material_id):
        return round(sum(s["quantidade"] for s in self.saldos.values() if s["material_id"] == material_id), 3)

    def saldo_local(self, material_id, localizacao_id):
        return round(sum(s["quantidade"] for s in self.saldos.values()
                         if s["material_id"] == material_id and s["localizacao_id"] == localizacao_id), 3)

    def disponivel(self, material_id, localizacao_id=None, permitir_vencido=False):
        total = 0
        for s in self.saldos.values():
            if s["material_id"] != material_id or (localizacao_id and s["localizacao_id"] != localizacao_id):
                continue
            if not permitir_vencido and lote_vencido(self.lotes.get(s.get("lote_id")), self.hoje):
                continue
            total += s["quantidade"]
        return round(total, 3)

    # ------------------------------------------------------------ validação
    def _validar_qtd(self, m, qtd):
        if m.get("natureza") != "produto":
            raise ErroEstoque(f"{m['nome']}: serviços não movimentam estoque.")
        try:
            qtd = round(float(qtd), 3)
        except (TypeError, ValueError):
            raise ErroEstoque(f"{m['nome']}: quantidade inválida.")
        if qtd <= 0:
            raise ErroEstoque(f"{m['nome']}: quantidade deve ser maior que zero.")
        if m.get("unidade") != "metro" and qtd != int(qtd):
            raise ErroEstoque(f"{m['nome']}: quantidade deve ser inteira (item).")
        return qtd

    def _validar_loc(self, localizacao_id):
        loc = self.locs.get(localizacao_id)
        if not loc:
            raise ErroEstoque("Localização não encontrada.")
        if not loc.get("ativo", True):
            raise ErroEstoque(f"Localização {loc['codigo']} está inativa.")
        return loc

    def _custo(self, m):
        cm = self.alteracoes_material.get(m["id"], {}).get("custo_medio", m.get("custo_medio"))
        return cm if cm is not None else custo_base(m)

    # ---------------------------------------------------------------- lotes
    def lote_para(self, material_id, validade):
        """Lote existente com a mesma validade ou cria um novo."""
        for l in list(self.lotes.values()):
            if l["material_id"] == material_id and l.get("validade") == validade:
                return l["id"]
        lote = {"id": self.repos.store.proximo_id("LOT"), "material_id": material_id,
                "validade": validade, "criado_em": self.agora}
        self.lotes[lote["id"]] = lote
        self.lotes_novos.append(lote)
        return lote["id"]

    # ------------------------------------------------------------- operações
    def _mov(self, tipo, sentido, m, qtd, loc_id, lote_id, valor_unitario, extra):
        mov = {"tipo": tipo, "sentido": sentido, "material_id": m["id"], "localizacao_id": loc_id,
               "lote_id": lote_id, "quantidade": qtd, "valor_unitario": valor_unitario,
               "custo_unitario": self._custo(m), "saldo_apos": self.saldo_total(m["id"]),
               "usuario_id": self.usuario_id, "data_hora": self.agora}
        mov.update({k: v for k, v in extra.items() if v not in (None, "")})
        self.movimentos.append(mov)
        return mov

    def entrada(self, mat, qtd, localizacao_id, tipo, lote_id=None, validade=None,
                valor_unitario=None, atualizar_custo=False, **extra):
        m = self.material(mat)
        qtd = self._validar_qtd(m, qtd)
        self._validar_loc(localizacao_id)
        if m.get("controla_validade") and not lote_id:
            if not validade:
                raise ErroEstoque(f"{m['nome']}: informe a data de validade.")
            lote_id = self.lote_para(m["id"], validade)
        elif not m.get("controla_validade"):
            lote_id = None
        if atualizar_custo and valor_unitario is not None:
            saldo_ant = max(self.saldo_total(m["id"]), 0)
            cm = self._custo(m)
            novo = valor_unitario if not cm or saldo_ant <= 0 else arred(
                (saldo_ant * cm + qtd * valor_unitario) / (saldo_ant + qtd))
            self.alteracoes_material.setdefault(m["id"], {})["custo_medio"] = novo
        k = chave_saldo(m["id"], localizacao_id, lote_id)
        s = self.saldos.setdefault(k, {"id": k, "material_id": m["id"], "localizacao_id": localizacao_id,
                                       "lote_id": lote_id, "quantidade": 0})
        s["quantidade"] = round(s["quantidade"] + qtd, 3)
        return self._mov(tipo, "entrada", m, qtd, localizacao_id, lote_id, valor_unitario, extra)

    def _ordem_saida(self, m, localizacao_id, lote_id, permitir_vencido, preferir_loja):
        candidatos = []
        for s in self.saldos.values():
            if s["material_id"] != m["id"] or s["quantidade"] <= 0:
                continue
            if localizacao_id and s["localizacao_id"] != localizacao_id:
                continue
            if lote_id and s.get("lote_id") != lote_id:
                continue
            lote = self.lotes.get(s.get("lote_id"))
            if not permitir_vencido and lote_vencido(lote, self.hoje):
                continue
            loc = self.locs.get(s["localizacao_id"], {})
            prioridade = 0 if (preferir_loja and loc.get("area") == "LOJA") or \
                (not preferir_loja and s["localizacao_id"] == m.get("localizacao_padrao_id")) else 1
            candidatos.append(((prioridade, (lote or {}).get("validade") or "9999-12-31", loc.get("codigo", "")), s))
        return [s for _, s in sorted(candidatos, key=lambda x: x[0])]

    def saida(self, mat, qtd, tipo, localizacao_id=None, lote_id=None, permitir_vencido=False,
              preferir_loja=False, valor_unitario=None, **extra):
        """Baixa por FEFO. Retorna a lista de baixas [{localizacao_id, lote_id, quantidade}]."""
        m = self.material(mat)
        qtd = self._validar_qtd(m, qtd)
        if localizacao_id:
            self._validar_loc(localizacao_id)
        candidatos = self._ordem_saida(m, localizacao_id, lote_id, permitir_vencido, preferir_loja)
        disponivel = round(sum(s["quantidade"] for s in candidatos), 3)
        if disponivel + 1e-9 < qtd:
            onde = f" em {self.locs[localizacao_id]['codigo']}" if localizacao_id else ""
            obs = "" if permitir_vencido else " (lotes vencidos não contam)"
            raise ErroEstoque(f"{m['nome']}: saldo insuficiente{onde}. Disponível: "
                              f"{formatar_qtd(disponivel, m)}; solicitado: {formatar_qtd(qtd, m)}{obs}.")
        restante, baixas = qtd, []
        for s in candidatos:
            if restante <= 1e-9:
                break
            usar = round(min(s["quantidade"], restante), 3)
            s["quantidade"] = round(s["quantidade"] - usar, 3)
            restante = round(restante - usar, 3)
            baixas.append({"localizacao_id": s["localizacao_id"], "lote_id": s.get("lote_id"), "quantidade": usar})
            self._mov(tipo, "saida", m, usar, s["localizacao_id"], s.get("lote_id"), valor_unitario, extra)
        return baixas

    def transferir(self, mat, qtd, origem_id, destino_id, observacao=""):
        if origem_id == destino_id:
            raise ErroEstoque("Origem e destino devem ser diferentes.")
        self._validar_loc(destino_id)
        tid = self.repos.store.proximo_id("TRF")
        baixas = self.saida(mat, qtd, "transferencia", localizacao_id=origem_id, permitir_vencido=True,
                            transferencia_id=tid, destino_id=destino_id, observacao=observacao)
        for b in baixas:
            self.entrada(mat, b["quantidade"], destino_id, "transferencia", lote_id=b["lote_id"],
                         transferencia_id=tid, origem_id=origem_id, observacao=observacao)
        return tid

    def ajustar(self, mat, localizacao_id, contado, motivo, inventario_id=None):
        """Ajusta o saldo de uma localização para a quantidade contada."""
        m = self.material(mat)
        atual = self.saldo_local(m["id"], localizacao_id)
        diferenca = round(float(contado) - atual, 3)
        if abs(diferenca) < 1e-9:
            return 0
        extra = {"motivo": motivo, "inventario_id": inventario_id, "contado": contado, "anterior": atual}
        if diferenca > 0:
            lote_id = None
            if m.get("controla_validade"):
                lotes = [self.lotes[s["lote_id"]] for s in self.saldos.values()
                         if s["material_id"] == m["id"] and s.get("lote_id") in self.lotes]
                if lotes:
                    lote_id = max(lotes, key=lambda l: l.get("validade") or "")["id"]
                else:
                    lote_id = self.lote_para(m["id"], None)
            self.entrada(m, diferenca, localizacao_id, "ajuste", lote_id=lote_id, **extra)
        else:
            self.saida(m, -diferenca, "ajuste", localizacao_id=localizacao_id, permitir_vencido=True, **extra)
        return diferenca

    # ----------------------------------------------------------------- commit
    def commit(self):
        saldos = [s for s in self.saldos.values() if abs(s["quantidade"]) > 1e-9]
        self.repos.saldos.substituir_todos(saldos)
        if self.lotes_novos:
            self.repos.lotes.substituir_todos(self.repos.lotes.listar() + self.lotes_novos)
        if self.alteracoes_material:
            regs = self.repos.materiais.listar()
            for r in regs:
                if r["id"] in self.alteracoes_material:
                    r.update(self.alteracoes_material[r["id"]])
            self.repos.materiais.substituir_todos(regs)
        if self.movimentos:
            self.repos.movimentacoes.adicionar_varios(self.movimentos)


@contextmanager
def operacao(repos, usuario_id):
    with repos.store.transacao():
        op = OperacaoEstoque(repos, usuario_id)
        yield op
        op.commit()


# ================================================================= consultas
def status_estoque(material, saldo):
    if material.get("natureza") != "produto":
        return None
    if saldo <= 0:
        return "zerado"
    if saldo <= (material.get("estoque_minimo") or 0):
        return "baixo"
    return "ok"


def posicao(repos, apenas_ativos=True):
    """Lista de produtos com saldo, status e valores (custo médio e venda)."""
    saldos = repos.saldos.por_material()
    res = []
    for m in repos.materiais.listar(lambda m: m.get("natureza") == "produto"):
        if apenas_ativos and not m.get("ativo", True):
            continue
        q = saldos.get(m["id"], 0)
        custo = m.get("custo_medio") if m.get("custo_medio") is not None else custo_base(m)
        res.append({"material": m, "saldo": q, "status": status_estoque(m, q),
                    "custo_unitario": custo, "valor_custo": arred(q * (custo or 0)),
                    "valor_venda": arred(q * (m.get("preco_venda") or 0))})
    return res


def alertas_estoque(repos):
    pos = posicao(repos)
    return {"baixo": [p for p in pos if p["status"] == "baixo"],
            "zerado": [p for p in pos if p["status"] == "zerado"]}


def alertas_validade(repos, dias=None):
    if dias is None:
        dias = repos.configuracao.obter().get("alertas", {}).get("dias_vencimento", 30)
    hoje = hoje_iso()
    limite = (date.fromisoformat(hoje) + timedelta(days=int(dias))).isoformat()
    lotes = {l["id"]: l for l in repos.lotes.listar()}
    materiais = {m["id"]: m for m in repos.materiais.listar()}
    locs = {l["id"]: l for l in repos.localizacoes.listar()}
    vencidos, a_vencer = [], []
    for s in repos.saldos.listar():
        lote = lotes.get(s.get("lote_id"))
        if not lote or not lote.get("validade") or s["quantidade"] <= 0:
            continue
        item = {"saldo": s, "lote": lote, "material": materiais.get(s["material_id"], {}),
                "localizacao": locs.get(s["localizacao_id"], {})}
        if lote["validade"] < hoje:
            vencidos.append(item)
        elif lote["validade"] <= limite:
            a_vencer.append(item)
    chave = lambda i: i["lote"]["validade"]  # noqa: E731
    return {"vencidos": sorted(vencidos, key=chave), "a_vencer": sorted(a_vencer, key=chave), "dias": dias}


def sugestao_compra(repos):
    """Itens em alerta agrupados por fornecedor principal: mínimo × fator − saldo."""
    fator = repos.configuracao.obter().get("alertas", {}).get("fator_sugestao_compra", 2)
    fornecedores = {f["id"]: f for f in repos.fornecedores.listar()}
    grupos = {}
    for p in [p for p in posicao(repos) if p["status"] in ("baixo", "zerado")]:
        m = p["material"]
        sugerido = round(max((m.get("estoque_minimo") or 0) * fator - p["saldo"], 0), 3)
        if m.get("unidade") != "metro":
            sugerido = int(-(-sugerido // 1))
        linha = next((l for l in m.get("fornecedores", []) if l.get("principal")), (m.get("fornecedores") or [{}])[0])
        f = fornecedores.get(linha.get("fornecedor_id"), {"id": "", "razao_social": "Sem fornecedor"})
        caixas = None
        if (m.get("fator_caixa") or 1) > 1 and linha.get("unidade_compra") == "caixa":
            caixas = int(-(-sugerido // m["fator_caixa"]))
        grupos.setdefault(f["id"], {"fornecedor": f, "itens": []})["itens"].append(
            {**p, "sugerido": sugerido, "caixas": caixas, "linha": linha})
    return sorted(grupos.values(), key=lambda g: g["fornecedor"].get("fantasia") or g["fornecedor"]["razao_social"])
