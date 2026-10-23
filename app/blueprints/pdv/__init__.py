"""Ponto de venda: caixa, venda, cancelamento, devolução/troca e comprovantes PDF (seção 3)."""
import os

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import autorizar, requer_permissao
from ...services import vendas as svc
from ...services.estoque import ErroEstoque
from ...utils import agora_iso, dias_atras_iso, dias_entre, hoje_iso, parse_moeda, so_digitos

bp = Blueprint("pdv", __name__, url_prefix="/pdv")


def _data_dir():
    return current_app.config["DATA_DIR"]


def _locs():
    return sorted([l for l in current_app.repos.localizacoes.listar() if l.get("ativo", True)], key=lambda l: l["codigo"])


def _nomes_usuarios():
    return {u["id"]: u["nome"] for u in current_app.repos.usuarios.listar()}


# ===================================================================== tela
@bp.route("/")
@login_required
@requer_permissao("pdv.vender")
def index():
    caixa = current_app.repos.caixas.aberto()
    if caixa and caixa["usuario_id"] == current_user.id:
        cliente = None
        cid = request.args.get("cliente")
        if cid:
            c = current_app.repos.clientes.obter(cid)
            if c:
                cliente = svc.cliente_json(current_app.repos, c, current_user.tem("clientes.ver_dados_completos"))
        servicos = sorted([m for m in current_app.repos.materiais.listar()
                           if m["natureza"] == "servico" and m.get("ativo", True)], key=lambda m: m["nome"])
        return render_template("pdv/venda.html", caixa=caixa, servicos=servicos, cliente_inicial=cliente)
    operador = (current_app.repos.usuarios.obter(caixa["usuario_id"]) or {}) if caixa else None
    return render_template("pdv/abrir_caixa.html", caixa=caixa, operador=operador)


# ==================================================================== caixa
@bp.route("/caixa/abrir", methods=["POST"])
@login_required
@requer_permissao("pdv.vender")
def caixa_abrir():
    repos = current_app.repos
    with repos.store.transacao():
        if repos.caixas.aberto():
            flash("Já existe um caixa aberto (apenas um por vez).", "danger")
            return redirect(url_for("pdv.index"))
        try:
            valor = parse_moeda(request.form.get("valor_inicial")) or 0
        except ValueError:
            flash("Valor inicial inválido.", "danger")
            return redirect(url_for("pdv.index"))
        if valor < 0:
            flash("Valor inicial inválido.", "danger")
            return redirect(url_for("pdv.index"))
        caixa = repos.caixas.adicionar({"usuario_id": current_user.id, "status": "aberto", "abertura": agora_iso(),
                                        "valor_inicial": valor, "movimentos": []})
    registrar("caixa_aberto", "caixa", caixa["id"], detalhe={"valor_inicial": valor})
    flash(f"Caixa {caixa['id']} aberto.", "success")
    return redirect(url_for("pdv.index"))


@bp.route("/caixa/movimento", methods=["POST"])
@login_required
@requer_permissao("pdv.vender")
def caixa_movimento():
    repos = current_app.repos
    try:
        caixa = svc.caixa_do_operador(repos, current_user.id)
        tipo = request.form.get("tipo")
        valor = parse_moeda(request.form.get("valor")) or 0
        motivo = request.form.get("motivo", "").strip()
        if tipo not in ("sangria", "suprimento") or valor <= 0 or not motivo:
            raise svc.ErroVenda("Informe tipo, valor e motivo.")
        if tipo == "sangria":
            disponivel = svc.resumo_caixa(repos, caixa)["esperado"]
            if valor > disponivel:
                raise svc.ErroVenda(f"Sangria maior que o dinheiro esperado em caixa ({disponivel / 100:.2f}).".replace(".", ","))
        movs = caixa.get("movimentos", []) + [{"tipo": tipo, "valor": valor, "motivo": motivo,
                                               "data_hora": agora_iso(), "usuario_id": current_user.id}]
        repos.caixas.atualizar(caixa["id"], {"movimentos": movs})
        registrar(f"caixa_{tipo}", "caixa", caixa["id"], detalhe={"valor": valor, "motivo": motivo})
        flash(f"{tipo.capitalize()} registrada.", "success")
    except (svc.ErroVenda, ValueError) as e:
        flash(str(e), "danger")
    return redirect(request.referrer or url_for("pdv.index"))


@bp.route("/caixa/fechar", methods=["GET", "POST"])
@login_required
@requer_permissao("pdv.vender")
def caixa_fechar():
    repos = current_app.repos
    try:
        caixa = svc.caixa_do_operador(repos, current_user.id)
    except svc.ErroVenda as e:
        flash(str(e), "danger")
        return redirect(url_for("pdv.index"))
    if request.method == "POST":
        try:
            contado = parse_moeda(request.form.get("valor_contado"))
        except ValueError:
            contado = None
        if contado is None or contado < 0:
            flash("Informe o valor contado em dinheiro.", "danger")
        else:
            caixa, resumo = svc.fechar_caixa(repos, _data_dir(), current_user, contado, request.form.get("observacao", ""))
            registrar("caixa_fechado", "caixa", caixa["id"], detalhe={"contado": contado, "diferenca": resumo["diferenca"]})
            flash(f"Caixa {caixa['id']} fechado.", "success")
            return redirect(url_for("pdv.caixa_detalhe", id_=caixa["id"], abrir_pdf=1))
    return render_template("pdv/fechar_caixa.html", caixa=caixa, resumo=svc.resumo_caixa(repos, caixa))


@bp.route("/caixas")
@login_required
@requer_permissao("pdv.vender", "relatorios.visualizar")
def caixas():
    lista = sorted(current_app.repos.caixas.listar(), key=lambda c: c["abertura"], reverse=True)[:200]
    return render_template("pdv/caixas.html", caixas=lista, usuarios=_nomes_usuarios())


@bp.route("/caixas/<id_>")
@login_required
@requer_permissao("pdv.vender", "relatorios.visualizar")
def caixa_detalhe(id_):
    caixa = current_app.repos.caixas.obter(id_) or abort(404)
    return render_template("pdv/caixa_detalhe.html", caixa=caixa, resumo=svc.resumo_caixa(current_app.repos, caixa),
                           usuarios=_nomes_usuarios())


# ==================================================================== venda
@bp.route("/api/venda", methods=["POST"])
@login_required
@requer_permissao("pdv.vender")
def api_venda():
    req = request.get_json(silent=True) or {}
    try:
        venda = svc.finalizar_venda(current_app.repos, _data_dir(), current_user, req, autorizar)
    except (svc.ErroVenda, ErroEstoque) as e:
        precisa_sup = "supervisor" in str(e)
        return jsonify({"ok": False, "erro": str(e), "supervisor": precisa_sup}), 400
    registrar("venda", "venda", venda["id"], detalhe={"total": venda["total"], "itens": len(venda["itens"]),
                                                      "autorizado_por": venda.get("autorizado_por")})
    return jsonify({"ok": True, "venda_id": venda["id"], "total": venda["total"], "troco": venda["troco"],
                    "pdf": url_for("pdv.comprovante", rel=venda["comprovante_pdf"])})


@bp.route("/vendas")
@login_required
@requer_permissao("pdv.vender", "relatorios.visualizar")
def vendas():
    repos = current_app.repos
    inicio = request.args.get("inicio") or hoje_iso()
    fim = request.args.get("fim") or hoje_iso()
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip().upper()
    lista = repos.vendas.listar(lambda v: inicio <= v["data_hora"][:10] <= fim and (not status or v["status"] == status)
                                and (not q or q in v["id"]))
    lista.sort(key=lambda v: v["data_hora"], reverse=True)
    clientes = {c["id"]: c for c in repos.clientes.listar()}
    return render_template("pdv/vendas.html", vendas=lista, clientes=clientes, usuarios=_nomes_usuarios(),
                           filtros={"inicio": inicio, "fim": fim, "status": status, "q": q},
                           total=sum(v["total"] for v in lista if v["status"] == "finalizada"))


@bp.route("/vendas/<id_>")
@login_required
@requer_permissao("pdv.vender", "relatorios.visualizar")
def venda_detalhe(id_):
    repos = current_app.repos
    venda = repos.vendas.obter(id_.upper()) or abort(404)
    cliente = repos.clientes.obter(venda["cliente_id"]) if venda.get("cliente_id") else None
    devs = [repos.devolucoes.obter(d) for d in venda.get("devolucoes", [])]
    caixa = repos.caixas.aberto()
    pode_cancelar = (venda["status"] == "finalizada" and venda["data_hora"][:10] == hoje_iso() and caixa
                     and caixa["id"] == venda["caixa_id"] and caixa["usuario_id"] == current_user.id
                     and not any(it.get("devolvido") for it in venda["itens"]))
    return render_template("pdv/venda_detalhe.html", venda=venda, cliente=cliente, devs=[d for d in devs if d],
                           usuarios=_nomes_usuarios(), pode_cancelar=pode_cancelar,
                           dias=dias_entre(venda["data_hora"], hoje_iso()), prazo=svc.prazo_dias(repos))


@bp.route("/vendas/<id_>/cancelar", methods=["POST"])
@login_required
@requer_permissao("pdv.vender")
def venda_cancelar(id_):
    sup = {"login": request.form.get("sup_login"), "senha": request.form.get("sup_senha")}
    aut = autorizar("pdv.cancelar_venda", sup)
    if not aut:
        flash("Cancelamento exige permissão: informe login e senha de um supervisor.", "danger")
        return redirect(url_for("pdv.venda_detalhe", id_=id_))
    try:
        venda = svc.cancelar_venda(current_app.repos, _data_dir(), current_user, id_, request.form.get("motivo", ""), aut)
        registrar("venda_cancelada", "venda", id_, detalhe={"autorizado_por": aut, "motivo": venda["motivo_cancelamento"]})
        flash(f"Venda {id_} cancelada ({venda['cancelamento_id']}).", "success")
    except (svc.ErroVenda, ErroEstoque) as e:
        flash(str(e), "danger")
    return redirect(url_for("pdv.venda_detalhe", id_=id_))


@bp.route("/comprovante/<path:rel>")
@login_required
def comprovante(rel):
    if not current_user.tem("pdv.vender", "relatorios.visualizar", "clientes.visualizar"):
        abort(403)
    if not rel.startswith("comprovantes/") or ".." in rel:
        abort(404)
    caminho = os.path.join(_data_dir(), *rel.split("/"))
    if not os.path.exists(caminho):
        abort(404)
    return send_from_directory(os.path.dirname(caminho), os.path.basename(caminho), mimetype="application/pdf")


# ========================================================= devolução/troca
def _pode_devolver():
    if not current_user.tem("pdv.devolucao", "pdv.troca"):
        abort(403)


@bp.route("/devolucao")
@login_required
def devolucao_busca():
    _pode_devolver()
    repos = current_app.repos
    q = request.args.get("q", "").strip()
    resultados = []
    if q:
        venda = repos.vendas.obter(q.upper())
        if venda:
            return redirect(url_for("pdv.devolucao", venda_id=venda["id"]))
        qd = so_digitos(q)
        cliente = repos.clientes.obter_por("cpf", qd) if len(qd) == 11 else None
        if cliente:
            resultados = repos.vendas.listar(lambda v: v.get("cliente_id") == cliente["id"] and v["status"] == "finalizada")
        elif qd:
            resultados = repos.vendas.listar(lambda v: qd in v["id"] and v["status"] == "finalizada")
        if not resultados:
            flash("Nenhuma venda encontrada.", "warning")
    resultados.sort(key=lambda v: v["data_hora"], reverse=True)
    return render_template("pdv/devolucao_busca.html", q=q, resultados=resultados[:50], prazo=svc.prazo_dias(repos),
                           hoje=hoje_iso())


@bp.route("/devolucao/<venda_id>")
@login_required
def devolucao(venda_id):
    _pode_devolver()
    repos = current_app.repos
    venda = repos.vendas.obter(venda_id) or abort(404)
    if venda["status"] != "finalizada":
        flash("Venda cancelada não aceita devolução.", "danger")
        return redirect(url_for("pdv.devolucao_busca"))
    caixa = repos.caixas.aberto()
    if not caixa or caixa["usuario_id"] != current_user.id:
        flash("Abra o caixa para registrar devoluções/trocas.", "warning")
        return redirect(url_for("pdv.index"))
    cliente = repos.clientes.obter(venda["cliente_id"]) if venda.get("cliente_id") else None
    itens = [{**it, "indice": i, "disponivel": svc.disponivel_devolucao(it),
              "unit_liquido": round(it["valor_liquido"] / it["quantidade_base"]) if it["quantidade_base"] else 0}
             for i, it in enumerate(venda["itens"])]
    dias = dias_entre(venda["data_hora"], hoje_iso())
    return render_template("pdv/devolucao.html", venda=venda, itens=itens, localizacoes=_locs(), dias=dias,
                           prazo=svc.prazo_dias(repos), motivos=svc.MOTIVOS_DEVOLUCAO,
                           cliente=svc.cliente_json(repos, cliente, current_user.tem("clientes.ver_dados_completos")) if cliente else None)


@bp.route("/api/devolucao", methods=["POST"])
@login_required
def api_devolucao():
    _pode_devolver()
    req = request.get_json(silent=True) or {}
    try:
        dev = svc.processar_devolucao(current_app.repos, _data_dir(), current_user, req, autorizar)
    except (svc.ErroVenda, ErroEstoque) as e:
        return jsonify({"ok": False, "erro": str(e), "supervisor": "supervisor" in str(e)}), 400
    registrar(dev["tipo"], "devolucao", dev["id"], detalhe={"venda": dev["venda_id"], "credito": dev["credito_gerado"]})
    return jsonify({"ok": True, "id": dev["id"], "credito_gerado": dev["credito_gerado"],
                    "pdf": url_for("pdv.comprovante", rel=dev["comprovante_pdf"])})


@bp.route("/devolucoes")
@login_required
@requer_permissao("pdv.vender", "relatorios.visualizar")
def devolucoes():
    repos = current_app.repos
    inicio = request.args.get("inicio") or dias_atras_iso(30)
    fim = request.args.get("fim") or hoje_iso()
    lista = sorted(repos.devolucoes.listar(lambda d: inicio <= d["data_hora"][:10] <= fim),
                   key=lambda d: d["data_hora"], reverse=True)
    return render_template("pdv/devolucoes.html", devs=lista, clientes={c["id"]: c for c in repos.clientes.listar()},
                           usuarios=_nomes_usuarios(), filtros={"inicio": inicio, "fim": fim})


@bp.route("/devolucoes/<id_>")
@login_required
@requer_permissao("pdv.vender", "relatorios.visualizar")
def devolucao_detalhe(id_):
    repos = current_app.repos
    dev = repos.devolucoes.obter(id_) or abort(404)
    cliente = repos.clientes.obter(dev["cliente_id"])
    return render_template("pdv/devolucao_detalhe.html", dev=dev, cliente=cliente, usuarios=_nomes_usuarios(),
                           locs={l["id"]: l for l in repos.localizacoes.listar()})

