import glob
import os
from copy import deepcopy

from .json_store import JsonStore, Repositorio


class UsuarioRepo(Repositorio):
    nome_arquivo, prefixo_id = "usuarios", "USR"


class GrupoRepo(Repositorio):
    nome_arquivo, prefixo_id = "grupos", "GRP"


class AuditoriaRepo(Repositorio):
    nome_arquivo, prefixo_id = "auditoria", "AUD"


class TipoRepo(Repositorio):
    nome_arquivo, prefixo_id = "tipos_material", "TIP"


class MaterialRepo(Repositorio):
    """Produtos (MAT-) e serviços (SRV-) no mesmo arquivo."""
    nome_arquivo, prefixo_id = "materiais", "MAT"


class FornecedorRepo(Repositorio):
    nome_arquivo, prefixo_id = "fornecedores", "FOR"


class ClienteRepo(Repositorio):
    nome_arquivo, prefixo_id = "clientes", "CLI"


class LocalizacaoRepo(Repositorio):
    nome_arquivo, prefixo_id = "localizacoes", "LOC"


class LoteRepo(Repositorio):
    nome_arquivo, prefixo_id = "lotes", "LOT"


class SaldoRepo(Repositorio):
    """Saldo por material × localização × lote. id = 'MAT|LOC|LOT'."""
    nome_arquivo, prefixo_id = "saldos", "SAL"

    def por_material(self):
        tot = {}
        for s in self.store.ler(self.nome_arquivo):
            tot[s["material_id"]] = round(tot.get(s["material_id"], 0) + s["quantidade"], 3)
        return tot

    def do_material(self, material_id):
        return [s for s in self.listar() if s["material_id"] == material_id and s["quantidade"]]


class CaixaRepo(Repositorio):
    nome_arquivo, prefixo_id = "caixas", "CX"

    def aberto(self):
        return next((c for c in self.listar() if c.get("status") == "aberto"), None)


class VendaRepo(Repositorio):
    nome_arquivo, prefixo_id = "vendas", "VEN"


class DevolucaoRepo(Repositorio):
    nome_arquivo, prefixo_id = "devolucoes", "DEV"


class PromocaoRepo(Repositorio):
    nome_arquivo, prefixo_id = "promocoes", "PRM"


class HistoricoPrecoRepo(Repositorio):
    nome_arquivo, prefixo_id = "historico_precos", "HPR"


class InventarioRepo(Repositorio):
    nome_arquivo, prefixo_id = "inventarios", "INV"


class CreditoClienteRepo(Repositorio):
    nome_arquivo, prefixo_id = "creditos_cliente", "CRD"

    def saldo(self, cliente_id):
        """Saldo em centavos = entradas − usos."""
        total = 0
        for c in self.store.ler(self.nome_arquivo):
            if c.get("cliente_id") == cliente_id:
                total += int(c.get("valor", 0)) * (-1 if c.get("tipo") == "uso" else 1)
        return total

    def saldos(self):
        res = {}
        for c in self.store.ler(self.nome_arquivo):
            v = int(c.get("valor", 0)) * (-1 if c.get("tipo") == "uso" else 1)
            res[c.get("cliente_id")] = res.get(c.get("cliente_id"), 0) + v
        return res

    def lancar(self, cliente_id, tipo, valor, usuario_id, data_hora, **origem):
        """tipo: entrada | uso | ajuste (ajuste pode ser negativo)."""
        with self.store.transacao():
            saldo = self.saldo(cliente_id)
            delta = -valor if tipo == "uso" else valor
            if saldo + delta < 0:
                raise ValueError("Saldo de crédito insuficiente.")
            return self.adicionar({"cliente_id": cliente_id, "tipo": tipo, "valor": valor,
                                   "saldo_apos": saldo + delta, "usuario_id": usuario_id,
                                   "data_hora": data_hora, **origem})


class MovimentacaoRepo:
    """Movimentações particionadas por mês: movimentacoes/AAAA-MM.json."""

    def __init__(self, store):
        self.store = store

    def _arquivo(self, data_hora):
        return f"movimentacoes/{data_hora[:7]}"

    def meses(self):
        pasta = os.path.join(self.store.data_dir, "movimentacoes")
        return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(pasta, "*.json")))

    def adicionar_varios(self, movimentos):
        """Grava vários movimentos (já com data_hora). Chamar dentro de transacao()."""
        por_mes = {}
        for m in movimentos:
            if not m.get("id"):
                m["id"] = self.store.proximo_id("MOV")
            por_mes.setdefault(self._arquivo(m["data_hora"]), []).append(m)
        for arq, novos in por_mes.items():
            regs = self.store.ler(arq)
            regs.extend(novos)
            self.store.gravar(arq, regs)
        return movimentos

    def listar(self, inicio=None, fim=None, filtro=None):
        """inicio/fim: 'AAAA-MM-DD' (inclusive)."""
        res = []
        for mes in self.meses():
            if inicio and mes < inicio[:7]:
                continue
            if fim and mes > fim[:7]:
                continue
            for m in self.store.ler(f"movimentacoes/{mes}"):
                d = m["data_hora"][:10]
                if (inicio and d < inicio) or (fim and d > fim):
                    continue
                if filtro and not filtro(m):
                    continue
                res.append(m)
        return deepcopy(res)


class ConfiguracaoRepo:
    """configuracao.json guarda um único objeto."""
    def __init__(self, store):
        self.store = store

    def obter(self):
        dados = self.store.ler("configuracao")
        return dados if isinstance(dados, dict) else {}

    def salvar(self, config):
        self.store.gravar("configuracao", config)


class Repos:
    """Ponto único de acesso (current_app.repos)."""
    def __init__(self, data_dir):
        self.store = JsonStore(data_dir)
        self.usuarios = UsuarioRepo(self.store)
        self.grupos = GrupoRepo(self.store)
        self.auditoria = AuditoriaRepo(self.store)
        self.configuracao = ConfiguracaoRepo(self.store)
        self.tipos = TipoRepo(self.store)
        self.materiais = MaterialRepo(self.store)
        self.fornecedores = FornecedorRepo(self.store)
        self.clientes = ClienteRepo(self.store)
        self.creditos = CreditoClienteRepo(self.store)
        self.localizacoes = LocalizacaoRepo(self.store)
        self.lotes = LoteRepo(self.store)
        self.saldos = SaldoRepo(self.store)
        self.movimentacoes = MovimentacaoRepo(self.store)
        self.caixas = CaixaRepo(self.store)
        self.vendas = VendaRepo(self.store)
        self.devolucoes = DevolucaoRepo(self.store)
        self.promocoes = PromocaoRepo(self.store)
        self.historico_precos = HistoricoPrecoRepo(self.store)
        self.inventarios = InventarioRepo(self.store)
