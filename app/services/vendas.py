"""
Regras do PDV (seção 3): venda, pagamentos, cancelamento, devolução/troca e fechamento de caixa.
Todos os valores em centavos. O servidor recalcula tudo — o navegador só envia a intenção.
"""
from datetime import date, timedelta

from ..utils import agora_iso, arred, dias_entre, formatar_cpf, formatar_data, hoje_iso, mascarar_cpf
from . import pdf
from .estoque import ErroEstoque, operacao
from .precos import preco_caixa, preco_efetivo, promocoes_vigentes

FORMAS = pdf.FORMAS
MOTIVOS_DEVOLUCAO = ["Arrependimento", "Defeito", "Tamanho/modelo", "Outro"]


class ErroVenda(Exception):
    pass


def _int(v, padrao=0):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return padrao


def _float(v):
    try:
        return round(float(str(v).replace(",", ".")), 3)
    except (TypeError, ValueError):
        raise ErroVenda("Quantidade inválida.")


# ================================================================ itens
def montar_itens(repos, itens_req, permitir_desconto=True):
    """Valida e precifica os itens. Retorna lista de itens prontos (sem baixa de estoque)."""
    if not itens_req:
        raise ErroVenda("Adicione ao menos um item.")
    promos = promocoes_vigentes(repos)
    itens = []
    for req in itens_req:
        m = repos.materiais.obter(req.get("material_id", ""))
        if not m:
            raise ErroVenda(f"Item {req.get('material_id')} não encontrado.")
        if not m.get("ativo", True):
            raise ErroVenda(f"{m['nome']} está inativo.")
        qtd = _float(req.get("quantidade"))
        if qtd <= 0:
            raise ErroVenda(f"{m['nome']}: quantidade deve ser maior que zero.")
        item = {"material_id": m["id"], "codigo": m["id"], "nome": m["nome"], "natureza": m["natureza"],
                "observacao": (req.get("observacao") or "").strip()[:200], "devolvido": 0, "baixas": []}
        if m["natureza"] == "servico":
            preco = _int(req.get("preco_informado"))
            if preco <= 0:
                raise ErroVenda(f"{m['nome']}: informe o preço do serviço (maior que zero).")
            if qtd != int(qtd):
                raise ErroVenda(f"{m['nome']}: quantidade deve ser inteira.")
            item.update(unidade_venda="item", quantidade=qtd, fator=1, quantidade_base=qtd,
                        preco_unitario=preco, preco_original=preco, promo_id=None)
            desconto_permitido = False
        else:
            fator = m.get("fator_caixa") or 1
            unidade = req.get("unidade_venda") or ("metro" if m.get("unidade") == "metro" else "item")
            if m.get("unidade") == "metro":
                unidade, fator_uso = "metro", 1
            elif unidade == "caixa" and fator > 1:
                fator_uso = fator
            else:
                unidade, fator_uso = "item", 1
            if unidade != "metro" and qtd != int(qtd):
                raise ErroVenda(f"{m['nome']}: quantidade deve ser inteira.")
            unit, original, promo_id = preco_efetivo(m, promos)
            if not unit:
                raise ErroVenda(f"{m['nome']}: sem preço de venda cadastrado.")
            if unidade == "caixa":
                unit = preco_caixa(m, promos)
                original = m.get("preco_venda_caixa") or (m["preco_venda"] * fator)
            item.update(unidade_venda=unidade, quantidade=qtd, fator=fator_uso,
                        quantidade_base=round(qtd * fator_uso, 3), preco_unitario=unit,
                        preco_original=original, promo_id=promo_id)
            desconto_permitido = permitir_desconto
        item["valor_bruto"] = arred(item["preco_unitario"] * qtd)
        desconto = 0
        if desconto_permitido and req.get("desconto_valor"):
            if req.get("desconto_tipo") == "pct":
                desconto = arred(item["valor_bruto"] * float(req["desconto_valor"]) / 100)
            else:
                desconto = _int(req["desconto_valor"])
        item["desconto"] = max(0, min(desconto, item["valor_bruto"]))
        itens.append(item)
    return itens


def ratear_desconto(itens, desconto_total):
    """Distribui o desconto geral proporcionalmente; define valor_liquido de cada item."""
    base = [it["valor_bruto"] - it["desconto"] for it in itens]
    soma = sum(base)
    restante = desconto_total
    for i, it in enumerate(itens):
        if i == len(itens) - 1:
            parte = restante
        else:
            parte = arred(desconto_total * base[i] / soma) if soma else 0
            restante -= parte
        it["desconto_rateado"] = parte
        it["valor_liquido"] = base[i] - parte


# ============================================================ pagamentos
def validar_pagamentos(repos, pagamentos_req, total, cliente_id):
    pagamentos = []
    for p in pagamentos_req or []:
        forma = p.get("forma")
        valor = _int(p.get("valor"))
        if forma not in FORMAS:
            raise ErroVenda(f"Forma de pagamento inválida: {forma}.")
        if valor <= 0:
            continue
        reg = {"forma": forma, "valor": valor}
        if forma == "credito":
            reg["parcelas"] = max(1, min(_int(p.get("parcelas"), 1), 24))
        if forma in ("pix", "debito", "credito", "outros") and p.get("nsu"):
            reg["nsu"] = str(p["nsu"]).strip()[:40]
        pagamentos.append(reg)
    if total <= 0:
        if pagamentos:
            raise ErroVenda("Não há valor a pagar.")
        return pagamentos, 0, 0
    pago = sum(p["valor"] for p in pagamentos)
    if pago < total:
        raise ErroVenda(f"Pagamento insuficiente: faltam {pdf.formatar_moeda(total - pago)}.")
    dinheiro = sum(p["valor"] for p in pagamentos if p["forma"] == "dinheiro")
    troco = pago - total
    if troco > dinheiro:
        raise ErroVenda("Troco só pode ser dado em dinheiro: o valor em cartão/Pix/crédito excede o total.")
    credito = sum(p["valor"] for p in pagamentos if p["forma"] == "credito_cliente")
    if credito:
        if not cliente_id:
            raise ErroVenda("Crédito do cliente exige cliente identificado (CPF).")
        saldo = repos.creditos.saldo(cliente_id)
        if credito > saldo:
            raise ErroVenda(f"Crédito do cliente insuficiente (saldo {pdf.formatar_moeda(saldo)}).")
    return pagamentos, troco, credito


# ================================================================ contexto
def _ctx(repos, usuario_id, cliente_id):
    cfg = repos.configuracao.obter()
    u = repos.usuarios.obter(usuario_id) or {}
    ctx = {"empresa": cfg.get("empresa", {}), "operador": u.get("nome", usuario_id), "cliente": None}
    if cliente_id:
        c = repos.clientes.obter(cliente_id)
        if c:
            ctx["cliente"] = {"nome": c["nome"], "cpf": mascarar_cpf(c["cpf"])}
            ctx["saldo_credito"] = repos.creditos.saldo(cliente_id)
    return ctx


def prazo_dias(repos):
    return int(repos.configuracao.obter().get("prazo_devolucao_dias", 7))


def caixa_do_operador(repos, usuario_id):
    caixa = repos.caixas.aberto()
    if not caixa:
        raise ErroVenda("Nenhum caixa aberto. Abra o caixa para continuar.")
    if caixa["usuario_id"] != usuario_id:
        u = repos.usuarios.obter(caixa["usuario_id"]) or {}
        raise ErroVenda(f"O caixa está aberto por {u.get('nome', caixa['usuario_id'])}. "
                        "Troca de operador exige fechar e reabrir o caixa.")
    return caixa


# ================================================================== venda
def finalizar_venda(repos, data_dir, usuario, req, autorizar):
    """usuario: current_user; autorizar: função (permissao, supervisor) -> id|None."""
    caixa = caixa_do_operador(repos, usuario.id)
    cliente_id = req.get("cliente_id") or None
    if cliente_id and not repos.clientes.obter(cliente_id):
        raise ErroVenda("Cliente não encontrado.")
    itens = montar_itens(repos, req.get("itens"))
    subtotal = sum(it["valor_bruto"] - it["desconto"] for it in itens)
    dt = req.get("desconto_total") or {}
    desconto_total = 0
    if dt.get("valor"):
        desconto_total = arred(subtotal * float(dt["valor"]) / 100) if dt.get("tipo") == "pct" else _int(dt["valor"])
    desconto_total = max(0, min(desconto_total, subtotal))
    total = subtotal - desconto_total
    if total <= 0:
        raise ErroVenda("O total da venda deve ser maior que zero.")
    bruto = sum(it["valor_bruto"] for it in itens)
    desconto_concedido = sum(it["desconto"] for it in itens) + desconto_total
    desconto_pct = round(desconto_concedido * 100 / bruto, 2) if bruto else 0
    autorizado_por = None
    if desconto_pct > usuario.limite_desconto + 1e-9:
        autorizado_por = autorizar("pdv.desconto_acima_limite", req.get("supervisor"))
        if not autorizado_por:
            raise ErroVenda(f"Desconto de {desconto_pct:.2f}%".replace(".", ",") +
                            f" acima do seu limite ({usuario.limite_desconto}%). Informe login e senha de um supervisor.")
    ratear_desconto(itens, desconto_total)
    pagamentos, troco, credito = validar_pagamentos(repos, req.get("pagamentos"), total, cliente_id)

    with repos.store.transacao():
        venda_id = repos.store.proximo_id("VEN")
        agora = agora_iso()
        with operacao(repos, usuario.id) as op:
            for it in itens:
                if it["natureza"] == "produto":
                    unit_liq = arred(it["valor_liquido"] / it["quantidade_base"]) if it["quantidade_base"] else 0
                    it["baixas"] = op.saida(it["material_id"], it["quantidade_base"], "venda",
                                            preferir_loja=True, valor_unitario=unit_liq, venda_id=venda_id)
        venda = {"id": venda_id, "caixa_id": caixa["id"], "cliente_id": cliente_id, "usuario_id": usuario.id,
                 "data_hora": agora, "status": "finalizada", "itens": itens, "subtotal": subtotal,
                 "desconto_total": desconto_total, "desconto_pct": desconto_pct, "autorizado_por": autorizado_por,
                 "total": total, "pagamentos": pagamentos, "troco": troco, "credito_utilizado": credito}
        if credito:
            repos.creditos.lancar(cliente_id, "uso", credito, usuario.id, agora, venda_id=venda_id)
        rel, caminho = pdf.caminho_comprovante(data_dir, venda_id, agora)
        venda["comprovante_pdf"] = rel
        repos.vendas.adicionar(venda)
    ctx = _ctx(repos, usuario.id, cliente_id)
    ctx["prazo_troca"] = formatar_data((date.fromisoformat(agora[:10]) + timedelta(days=prazo_dias(repos))).isoformat())
    pdf.comprovante_venda(caminho, venda, ctx)
    return venda


def cancelar_venda(repos, data_dir, usuario, venda_id, motivo, autorizado_por):
    venda = repos.vendas.obter(venda_id)
    if not venda:
        raise ErroVenda("Venda não encontrada.")
    if venda["status"] != "finalizada":
        raise ErroVenda("Venda já cancelada.")
    if venda["data_hora"][:10] != hoje_iso():
        raise ErroVenda("Somente vendas do dia podem ser canceladas.")
    caixa = caixa_do_operador(repos, usuario.id)
    if venda["caixa_id"] != caixa["id"]:
        raise ErroVenda("A venda pertence a outra sessão de caixa (já fechada).")
    if any(it.get("devolvido") for it in venda["itens"]):
        raise ErroVenda("Venda com devolução/troca registrada não pode ser cancelada.")
    if not (motivo or "").strip():
        raise ErroVenda("Informe o motivo do cancelamento.")
    with repos.store.transacao():
        can_id = repos.store.proximo_id("CAN")
        agora = agora_iso()
        with operacao(repos, usuario.id) as op:
            for it in venda["itens"]:
                for b in it.get("baixas", []):
                    op.entrada(it["material_id"], b["quantidade"], b["localizacao_id"], "estorno",
                               lote_id=b.get("lote_id"), venda_id=venda_id, observacao=f"Cancelamento {can_id}")
        if venda.get("credito_utilizado"):
            repos.creditos.lancar(venda["cliente_id"], "entrada", venda["credito_utilizado"], usuario.id, agora,
                                  venda_id=venda_id, observacao=f"Estorno cancelamento {can_id}")
        rel, caminho = pdf.caminho_comprovante(data_dir, can_id, agora)
        venda = repos.vendas.atualizar(venda_id, {
            "status": "cancelada", "cancelada_em": agora, "cancelado_por": usuario.id, "cancelamento_id": can_id,
            "autorizado_cancelamento_por": autorizado_por, "motivo_cancelamento": motivo.strip(),
            "comprovante_cancelamento_pdf": rel})
    ctx = _ctx(repos, usuario.id, venda.get("cliente_id"))
    aut = repos.usuarios.obter(autorizado_por) or {}
    ctx.update(cancelamento_id=can_id, autorizador=aut.get("nome", autorizado_por))
    pdf.comprovante_cancelamento(caminho, venda, ctx)
    return venda


# ========================================================= devolução/troca
def disponivel_devolucao(item):
    return round(item["quantidade_base"] - item.get("devolvido", 0), 3)


def processar_devolucao(repos, data_dir, usuario, req, autorizar):
    tipo = req.get("tipo")
    if tipo not in ("devolucao", "troca"):
        raise ErroVenda("Tipo inválido.")
    perm = "pdv.troca" if tipo == "troca" else "pdv.devolucao"
    if not usuario.tem(perm):
        raise ErroVenda("Sem permissão para esta operação.")
    caixa = caixa_do_operador(repos, usuario.id)
    venda = repos.vendas.obter(req.get("venda_id", ""))
    if not venda or venda["status"] != "finalizada":
        raise ErroVenda("Venda não encontrada ou cancelada.")
    motivo = (req.get("motivo") or "").strip()
    if not motivo:
        raise ErroVenda("Informe o motivo.")

    dias = dias_entre(venda["data_hora"], hoje_iso())
    prazo = prazo_dias(repos)
    autorizado_por = None
    if dias > prazo:
        autorizado_por = autorizar("pdv.devolucao_fora_prazo", req.get("supervisor"))
        if not autorizado_por:
            raise ErroVenda(f"Venda realizada há {dias} dias (prazo: {prazo}). Necessária autorização de supervisor.")

    cliente_id = venda.get("cliente_id") or req.get("cliente_id")
    if not cliente_id or not repos.clientes.obter(cliente_id):
        raise ErroVenda("Informe o cliente (CPF) para vincular o crédito.")

    locs = {l["id"]: l for l in repos.localizacoes.listar() if l.get("ativo", True)}
    devolvidos = []
    for req_it in req.get("itens") or []:
        idx = _int(req_it.get("indice"), -1)
        if not 0 <= idx < len(venda["itens"]):
            raise ErroVenda("Item da venda inválido.")
        it = venda["itens"][idx]
        q = _float(req_it.get("quantidade"))
        if q <= 0:
            continue
        if it["natureza"] == "produto" and it["unidade_venda"] != "metro" and q != int(q):
            raise ErroVenda(f"{it['nome']}: quantidade deve ser inteira.")
        if q > disponivel_devolucao(it) + 1e-9:
            raise ErroVenda(f"{it['nome']}: máximo para devolução {disponivel_devolucao(it):g}.")
        antes = it.get("devolvido", 0)
        valor = arred(it["valor_liquido"] * (antes + q) / it["quantidade_base"]) - \
            arred(it["valor_liquido"] * antes / it["quantidade_base"])
        d = {"indice": idx, "material_id": it["material_id"], "codigo": it["codigo"], "nome": it["nome"],
             "natureza": it["natureza"], "quantidade": q, "valor": valor}
        if it["natureza"] == "produto":
            cond = req_it.get("condicao") if req_it.get("condicao") in ("revenda", "avariado") else "revenda"
            loc_id = req_it.get("localizacao_id") or (it["baixas"][0]["localizacao_id"] if it["baixas"] else None)
            if loc_id not in locs:
                raise ErroVenda(f"{it['nome']}: escolha a localização de destino.")
            d.update(condicao=cond, localizacao_id=loc_id,
                     lote_id=it["baixas"][0].get("lote_id") if it["baixas"] else None)
        devolvidos.append(d)
    if not devolvidos:
        raise ErroVenda("Selecione ao menos um item para devolver.")
    valor_devolvido = sum(d["valor"] for d in devolvidos)

    novos = []
    if tipo == "troca":
        novos = montar_itens(repos, req.get("novos"), permitir_desconto=False)
        ratear_desconto(novos, 0)
    elif req.get("novos"):
        raise ErroVenda("Devolução não leva itens novos — use Troca.")
    valor_novos = sum(n["valor_liquido"] for n in novos)
    diferenca = valor_novos - valor_devolvido
    pagamentos, troco, credito_usado = [], 0, 0
    if diferenca > 0:
        pagamentos, troco, credito_usado = validar_pagamentos(repos, req.get("pagamentos"), diferenca, cliente_id)
    credito_gerado = valor_devolvido if tipo == "devolucao" else max(0, -diferenca)

    with repos.store.transacao():
        dev_id = repos.store.proximo_id("DEV")
        agora = agora_iso()
        with operacao(repos, usuario.id) as op:
            for d in devolvidos:
                if d["natureza"] != "produto":
                    continue
                unit = arred(d["valor"] / d["quantidade"])
                op.entrada(d["material_id"], d["quantidade"], d["localizacao_id"], tipo, lote_id=d.get("lote_id"),
                           valor_unitario=unit, venda_id=venda["id"], devolucao_id=dev_id, motivo=motivo,
                           condicao=d["condicao"])
                if d["condicao"] == "avariado":
                    op.saida(d["material_id"], d["quantidade"], "sucata", localizacao_id=d["localizacao_id"],
                             lote_id=d.get("lote_id"), permitir_vencido=True, devolucao_id=dev_id,
                             motivo="Avaria (devolução)")
            for n in novos:
                if n["natureza"] == "produto":
                    n["baixas"] = op.saida(n["material_id"], n["quantidade_base"], "troca", preferir_loja=True,
                                           valor_unitario=arred(n["valor_liquido"] / n["quantidade_base"]),
                                           venda_id=venda["id"], devolucao_id=dev_id)
        itens_venda = venda["itens"]
        for d in devolvidos:
            itens_venda[d["indice"]]["devolvido"] = round(itens_venda[d["indice"]].get("devolvido", 0) + d["quantidade"], 3)
        repos.vendas.atualizar(venda["id"], {"itens": itens_venda,
                                             "devolucoes": venda.get("devolucoes", []) + [dev_id]})
        if credito_usado:
            repos.creditos.lancar(cliente_id, "uso", credito_usado, usuario.id, agora, devolucao_id=dev_id)
        if credito_gerado:
            repos.creditos.lancar(cliente_id, "entrada", credito_gerado, usuario.id, agora,
                                  devolucao_id=dev_id, venda_id=venda["id"])
        rel, caminho = pdf.caminho_comprovante(data_dir, dev_id, agora)
        dev = {"id": dev_id, "tipo": tipo, "venda_id": venda["id"], "caixa_id": caixa["id"], "cliente_id": cliente_id,
               "usuario_id": usuario.id, "autorizado_por": autorizado_por, "motivo": motivo, "data_hora": agora,
               "itens_devolvidos": devolvidos, "itens_novos": novos, "valor_devolvido": valor_devolvido,
               "valor_novos": valor_novos, "diferenca": diferenca, "pagamentos": pagamentos, "troco": troco,
               "credito_utilizado": credito_usado, "credito_gerado": credito_gerado, "comprovante_pdf": rel}
        repos.devolucoes.adicionar(dev)
    ctx = _ctx(repos, usuario.id, cliente_id)
    pdf.comprovante_devolucao(caminho, dev, ctx)
    return dev


# ================================================================== caixa
def resumo_caixa(repos, caixa):
    vendas = repos.vendas.listar(lambda v: v.get("caixa_id") == caixa["id"])
    devs = repos.devolucoes.listar(lambda d: d.get("caixa_id") == caixa["id"])
    finalizadas = [v for v in vendas if v["status"] == "finalizada"]
    por_forma, dinheiro, troco = {}, 0, 0
    creditos_utilizados = 0
    for doc in finalizadas + devs:
        for p in doc.get("pagamentos", []):
            por_forma[p["forma"]] = por_forma.get(p["forma"], 0) + p["valor"]
            if p["forma"] == "dinheiro":
                dinheiro += p["valor"]
        troco += doc.get("troco", 0)
        creditos_utilizados += doc.get("credito_utilizado", 0)
    if "dinheiro" in por_forma:
        por_forma["dinheiro"] -= troco
    sup = sum(m["valor"] for m in caixa.get("movimentos", []) if m["tipo"] == "suprimento")
    san = sum(m["valor"] for m in caixa.get("movimentos", []) if m["tipo"] == "sangria")
    esperado = caixa["valor_inicial"] + (dinheiro - troco) + sup - san
    contado = caixa.get("valor_contado")
    return {"qtd_vendas": len(finalizadas), "qtd_canceladas": len(vendas) - len(finalizadas),
            "total_vendas": sum(v["total"] for v in finalizadas), "por_forma": por_forma,
            "dinheiro_liquido": dinheiro - troco, "suprimentos": sup, "sangrias": san, "esperado": esperado,
            "diferenca": (contado - esperado) if contado is not None else None,
            "qtd_devolucoes": len(devs), "creditos_gerados": sum(d.get("credito_gerado", 0) for d in devs),
            "creditos_utilizados": creditos_utilizados, "vendas": vendas, "devolucoes": devs}


def fechar_caixa(repos, data_dir, usuario, valor_contado, observacao=""):
    caixa = caixa_do_operador(repos, usuario.id)
    agora = agora_iso()
    rel, caminho = pdf.caminho_comprovante(data_dir, caixa["id"], agora)
    caixa = repos.caixas.atualizar(caixa["id"], {"status": "fechado", "fechamento": agora, "valor_contado": valor_contado,
                                                  "observacao_fechamento": observacao, "comprovante_pdf": rel})
    resumo = resumo_caixa(repos, caixa)
    repos.caixas.atualizar(caixa["id"], {"valor_esperado": resumo["esperado"], "diferenca": resumo["diferenca"]})
    ctx = _ctx(repos, usuario.id, None)
    pdf.fechamento_caixa(caminho, caixa, resumo, ctx)
    return caixa, resumo


def cliente_json(repos, c, completo):
    return {"id": c["id"], "nome": c["nome"],
            "cpf": formatar_cpf(c["cpf"]) if completo else mascarar_cpf(c["cpf"]),
            "saldo_credito": repos.creditos.saldo(c["id"])}


__all__ = ["ErroVenda", "ErroEstoque", "finalizar_venda", "cancelar_venda", "processar_devolucao",
           "resumo_caixa", "fechar_caixa", "MOTIVOS_DEVOLUCAO"]
