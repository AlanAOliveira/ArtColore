"""Preços: promoções, reajuste em massa e histórico (seção 5)."""
from decimal import Decimal

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import requer_permissao
from ...services.precos import (calcular_preco_venda, custo_base, preco_promocional, promo_vigente,
                                registrar_historico)
from ...utils import agora_iso, arred, dias_atras_iso, hoje_iso, normalizar, parse_data, parse_decimal, parse_moeda

bp = Blueprint("precos", __name__, url_prefix="/precos")
TIPOS_PROMO = {"valor": "Preço final (R$)", "percentual": "Desconto (%)", "absoluto": "Desconto (R$)"}


# ================================================================ promoções
@bp.route("/promocoes", methods=["GET", "POST"])
@login_required
@requer_permissao("precos.gerenciar", "materiais.visualizar")
def promocoes():
    repos = current_app.repos
    if request.method == "POST":
        if not current_user.tem("precos.gerenciar"):
            abort(403)
        f = request.form
        erros = []
        m = repos.materiais.obter(f.get("item_id", ""))
        tipo = f.get("tipo")
        if not m or m["natureza"] != "produto":
            erros.append("Selecione um produto (serviços não têm promoção).")
        if tipo not in TIPOS_PROMO:
            erros.append("Tipo inválido.")
        try:
            valor = parse_decimal(f.get("valor"), 2) if tipo == "percentual" else parse_moeda(f.get("valor"))
            inicio, fim = parse_data(f.get("inicio")), parse_data(f.get("fim"))
        except ValueError as e:
            erros.append(str(e))
            valor = inicio = fim = None
        if not valor or valor <= 0:
            erros.append("Informe o valor da promoção.")
        if tipo == "percentual" and valor and valor >= 100:
            erros.append("Percentual deve ser menor que 100%.")
        if not inicio or not fim or fim < inicio:
            erros.append("Informe o período (fim ≥ início).")
        if not erros and m:
            preco = preco_promocional({"tipo": tipo, "valor": valor}, m["preco_venda"])
            if preco <= 0:
                erros.append("Preço promocional resultante deve ser maior que zero.")
            elif preco >= m["preco_venda"]:
                erros.append("Preço promocional deve ser menor que o preço de venda atual.")
        if erros:
            for e in erros:
                flash(e, "danger")
        else:
            p = repos.promocoes.adicionar({"material_id": m["id"], "tipo": tipo, "valor": valor, "inicio": inicio, "fim": fim,
                                           "descricao": f.get("descricao", "").strip(), "ativo": True,
                                           "criado_em": agora_iso(), "criado_por": current_user.id})
            registrar("promocao_criada", "promocao", p["id"], depois=p)
            custo = custo_base(m)
            if custo and preco < custo:
                flash(f"Atenção: preço promocional ({preco / 100:.2f}) abaixo do custo ({custo / 100:.2f}).".replace(".", ","), "warning")
            flash(f"Promoção {p['id']} criada.", "success")
        return redirect(url_for("precos.promocoes"))

    hoje = hoje_iso()
    materiais = {m["id"]: m for m in repos.materiais.listar()}
    lista = []
    for p in repos.promocoes.listar():
        m = materiais.get(p["material_id"], {})
        p["material"] = m
        p["preco"] = preco_promocional(p, m.get("preco_venda") or 0)
        custo = custo_base(m) if m else None
        p["abaixo_custo"] = bool(custo and p["preco"] < custo)
        p["situacao"] = ("encerrada" if not p.get("ativo", True) or p["fim"] < hoje
                         else "vigente" if promo_vigente(p, hoje) else "futura")
        lista.append(p)
    ordem = {"vigente": 0, "futura": 1, "encerrada": 2}
    lista.sort(key=lambda p: (ordem[p["situacao"]], p["inicio"]))
    return render_template("precos/promocoes.html", promocoes=lista, tipos=TIPOS_PROMO, hoje=hoje)


@bp.route("/promocoes/<id_>/encerrar", methods=["POST"])
@login_required
@requer_permissao("precos.gerenciar")
def promocao_encerrar(id_):
    p = current_app.repos.promocoes.obter(id_) or abort(404)
    current_app.repos.promocoes.atualizar(id_, {"ativo": False, "encerrada_em": agora_iso(), "encerrada_por": current_user.id})
    registrar("promocao_encerrada", "promocao", id_, antes=p)
    flash("Promoção encerrada.", "info")
    return redirect(url_for("precos.promocoes"))


# ================================================================== reajuste
def _alvos(repos, f):
    tipo, fornecedor = f.get("tipo_id", ""), f.get("fornecedor_id", "")
    codigos = {c.strip().upper() for c in f.get("codigos", "").replace(",", " ").split() if c.strip()}
    res = []
    for m in repos.materiais.listar(lambda m: m["natureza"] == "produto" and m.get("ativo", True)):
        if tipo and m.get("tipo_id") != tipo:
            continue
        if fornecedor and not any(l["fornecedor_id"] == fornecedor for l in m.get("fornecedores", [])):
            continue
        if codigos and m["id"] not in codigos and (m.get("codigo_barras") or "") not in codigos:
            continue
        res.append(m)
    return sorted(res, key=lambda m: normalizar(m["nome"]))


def _aplicar_valor(preco, modo, valor):
    if preco is None:
        return None
    if modo == "pct":
        novo = arred(Decimal(preco) * (1 + Decimal(str(valor)) / 100))
    else:
        novo = preco + int(valor)
    return max(1, novo)


def _simular(repos, f):
    alvo, modo = f.get("alvo", "venda"), f.get("modo", "pct")
    valor = parse_decimal(f.get("valor"), 2) if modo == "pct" else parse_moeda(f.get("valor"))
    if not valor:
        raise ValueError("Informe o valor do reajuste.")
    fornecedor = f.get("fornecedor_id", "")
    linhas = []
    for m in _alvos(repos, f):
        novo = dict(m)
        novo["fornecedores"] = [dict(l) for l in m.get("fornecedores", [])]
        aviso = ""
        if alvo in ("compra", "ambos"):
            for l in novo["fornecedores"]:
                if not fornecedor or l["fornecedor_id"] == fornecedor:
                    l["preco_compra"] = _aplicar_valor(l["preco_compra"], modo, valor)
                    l["atualizado_em"] = agora_iso()
            novo["preco_venda"] = calcular_preco_venda(novo)
        if alvo in ("venda", "ambos"):
            if alvo == "venda" or m.get("regra_preco") == "manual":
                if m.get("regra_preco") != "manual":
                    aviso = "regra automática → manual"
                    novo["regra_preco"] = "manual"
                novo["preco_venda"] = _aplicar_valor(m["preco_venda"], modo, valor)
                if m.get("preco_venda_caixa"):
                    novo["preco_venda_caixa"] = _aplicar_valor(m["preco_venda_caixa"], modo, valor)
        custo_ant, custo_novo = custo_base(m), custo_base(novo)
        linhas.append({"material": m, "novo": novo, "aviso": aviso,
                       "custo_ant": custo_ant, "custo_novo": custo_novo,
                       "venda_ant": m.get("preco_venda"), "venda_nova": novo.get("preco_venda"),
                       "var_abs": (novo.get("preco_venda") or 0) - (m.get("preco_venda") or 0),
                       "var_pct": round(((novo.get("preco_venda") or 0) - (m.get("preco_venda") or 0)) * 100 / m["preco_venda"], 2) if m.get("preco_venda") else None,
                       "margem_pct": round(((novo.get("preco_venda") or 0) - custo_novo) * 100 / custo_novo, 1) if custo_novo else None,
                       "abaixo_custo": bool(custo_novo and (novo.get("preco_venda") or 0) < custo_novo)})
    return linhas


@bp.route("/reajuste", methods=["GET", "POST"])
@login_required
@requer_permissao("precos.reajuste_massa")
def reajuste():
    repos = current_app.repos
    ctx = {"tipos": repos.tipos.listar(lambda t: t["natureza"] == "produto"),
           "fornecedores": sorted(repos.fornecedores.listar(), key=lambda f: normalizar(f.get("fantasia") or f["razao_social"]))}
    if request.method == "POST":
        f = request.form
        try:
            linhas = _simular(repos, f)
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("precos/reajuste.html", form=f, linhas=None, **ctx)
        if not linhas:
            flash("Nenhum produto corresponde aos filtros.", "warning")
            return render_template("precos/reajuste.html", form=f, linhas=None, **ctx)
        if f.get("acao") == "aplicar":
            with repos.store.transacao():
                for l in linhas:
                    n = l["novo"]
                    novo = repos.materiais.atualizar(n["id"], {k: n.get(k) for k in ("fornecedores", "preco_venda",
                                                                                        "preco_venda_caixa", "regra_preco")})
                    registrar_historico(repos, l["material"], novo, current_user.id, "reajuste")
            registrar("reajuste_massa", "material", "", detalhe={k: f.get(k) for k in ("alvo", "modo", "valor", "tipo_id",
                                                                                     "fornecedor_id", "codigos")}
                      | {"itens": len(linhas)})
            flash(f"Reajuste aplicado a {len(linhas)} produto(s).", "success")
            return redirect(url_for("precos.historico", origem="reajuste"))
        return render_template("precos/reajuste.html", form=f, linhas=linhas, **ctx)
    return render_template("precos/reajuste.html", form={"alvo": "venda", "modo": "pct"}, linhas=None, **ctx)


# ================================================================ histórico
@bp.route("/historico")
@login_required
@requer_permissao("precos.gerenciar", "materiais.visualizar")
def historico():
    repos = current_app.repos
    inicio = request.args.get("inicio") or dias_atras_iso(90)
    fim = request.args.get("fim") or hoje_iso()
    origem = request.args.get("origem", "")
    material = request.args.get("material", "").strip().upper()
    regs = repos.historico_precos.listar(lambda h: inicio <= h["data_hora"][:10] <= fim
                                         and (not origem or h.get("origem") == origem)
                                         and (not material or h["material_id"] == material))
    regs.sort(key=lambda h: h["data_hora"], reverse=True)
    return render_template("precos/historico.html", regs=regs[:1000], filtros={"inicio": inicio, "fim": fim, "origem": origem,
                                                                               "material": material},
                           materiais={m["id"]: m for m in repos.materiais.listar()},
                           fornecedores={f["id"]: f.get("fantasia") or f["razao_social"] for f in repos.fornecedores.listar()},
                           usuarios={u["id"]: u["nome"] for u in repos.usuarios.listar()})
