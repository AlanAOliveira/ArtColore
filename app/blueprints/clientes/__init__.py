"""Cadastro básico de clientes: CPF, nome, telefone + crédito (seção 7)."""
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import requer_permissao
from ...services.vendas import cliente_json
from ...utils import agora_iso, normalizar, parse_moeda, so_digitos, validar_cpf

bp = Blueprint("clientes", __name__, url_prefix="/clientes")


def _validar(cpf, nome, telefone, id_atual=None):
    erros = []
    if not validar_cpf(cpf):
        erros.append("CPF inválido.")
    else:
        outro = current_app.repos.clientes.obter_por("cpf", cpf)
        if outro and outro["id"] != id_atual:
            erros.append(f"CPF já cadastrado ({outro['id']} - {outro['nome']}).")
    if not nome:
        erros.append("Informe o nome.")
    if len(telefone) not in (10, 11):
        erros.append("Telefone deve ter DDD + número (10 ou 11 dígitos).")
    return erros


def _pode_ver_clientes():
    if not current_user.tem("clientes.visualizar", "pdv.vender"):
        abort(403)


@bp.route("/")
@login_required
@requer_permissao("clientes.visualizar")
def lista():
    q = request.args.get("q", "").strip()
    qn, qd = normalizar(q), so_digitos(q)

    def filtro(c):
        if not q:
            return True
        if qd and (qd in c.get("cpf", "") or qd in c.get("telefone", "")):
            return True
        return qn in normalizar(f"{c['id']} {c['nome']}")

    itens = sorted(current_app.repos.clientes.listar(filtro), key=lambda c: normalizar(c["nome"]))
    return render_template("clientes/lista.html", itens=itens[:500], total=len(itens),
                           saldos=current_app.repos.creditos.saldos(), q=q,
                           completo=current_user.tem("clientes.ver_dados_completos"))


@bp.route("/novo", methods=["GET", "POST"])
@bp.route("/<id_>", methods=["GET", "POST"])
@login_required
@requer_permissao("clientes.visualizar")
def form(id_=None):
    repos = current_app.repos
    repo = repos.clientes
    cliente = repo.obter(id_) if id_ else None
    if id_ and not cliente:
        flash("Cliente não encontrado.", "danger")
        return redirect(url_for("clientes.lista"))
    pode_editar = current_user.tem("clientes.gerenciar")
    completo = current_user.tem("clientes.ver_dados_completos") or not cliente
    if not id_ and not pode_editar:
        abort(403)
    if request.method == "POST":
        if not pode_editar:
            abort(403)
        nome = request.form.get("nome", "").strip()
        telefone = so_digitos(request.form.get("telefone", "")) or (cliente or {}).get("telefone", "")
        cpf = so_digitos(request.form.get("cpf", "")) if completo else cliente["cpf"]
        erros = _validar(cpf, nome, telefone, id_)
        if erros:
            for e in erros:
                flash(e, "danger")
            c = {**(cliente or {}), "cpf": cpf, "nome": nome, "telefone": telefone}
            return render_template("clientes/form.html", cliente=cliente, c=c, pode_editar=True, completo=completo,
                                   saldo=0, extrato=[], vendas=[], devolucoes=[])
        if cliente:
            novo = repo.atualizar(id_, {"cpf": cpf, "nome": nome, "telefone": telefone})
            registrar("cliente_editado", "cliente", id_, antes=cliente, depois=novo)
            flash("Cliente atualizado.", "success")
        else:
            novo = repo.adicionar({"cpf": cpf, "nome": nome, "telefone": telefone,
                                   "criado_em": agora_iso(), "criado_por": current_user.id})
            registrar("cliente_criado", "cliente", novo["id"], depois=novo)
            flash(f"Cliente {novo['id']} criado.", "success")
        return redirect(url_for("clientes.form", id_=novo["id"]))
    extrato, saldo, vendas, devolucoes = [], 0, [], []
    if cliente:
        extrato = sorted(repos.creditos.listar(lambda c: c.get("cliente_id") == id_), key=lambda c: c.get("data_hora", ""), reverse=True)
        saldo = repos.creditos.saldo(id_)
        vendas = sorted(repos.vendas.listar(lambda v: v.get("cliente_id") == id_), key=lambda v: v["data_hora"], reverse=True)
        devolucoes = sorted(repos.devolucoes.listar(lambda d: d.get("cliente_id") == id_), key=lambda d: d["data_hora"], reverse=True)
    return render_template("clientes/form.html", cliente=cliente, c=cliente or {}, pode_editar=pode_editar,
                           completo=completo, saldo=saldo, extrato=extrato, vendas=vendas, devolucoes=devolucoes)


@bp.route("/<id_>/ajustar-credito", methods=["POST"])
@login_required
@requer_permissao("clientes.ajustar_credito")
def ajustar_credito(id_):
    cliente = current_app.repos.clientes.obter(id_) or abort(404)
    try:
        valor = parse_moeda(request.form.get("valor")) or 0
    except ValueError:
        valor = 0
    if request.form.get("operacao") == "debitar":
        valor = -valor
    motivo = request.form.get("motivo", "").strip()
    if not valor or not motivo:
        flash("Informe valor e motivo do ajuste.", "danger")
    else:
        try:
            current_app.repos.creditos.lancar(id_, "ajuste", valor, current_user.id, agora_iso(), observacao=motivo)
            registrar("credito_ajustado", "cliente", id_, detalhe={"valor": valor, "motivo": motivo})
            flash("Crédito ajustado.", "success")
        except ValueError as e:
            flash(str(e), "danger")
    return redirect(url_for("clientes.form", id_=cliente["id"]))


# ---------------------------------------------- API (PDV: busca e cadastro rápido)
@bp.route("/api/buscar")
@login_required
def api_buscar():
    _pode_ver_clientes()
    q = request.args.get("q", "").strip()
    qn, qd = normalizar(q), so_digitos(q)
    if len(q) < 3:
        return jsonify([])
    res = [c for c in current_app.repos.clientes.listar()
           if (qd and (c["cpf"] == qd or qd in c.get("telefone", "") or (len(qd) >= 6 and qd in c["cpf"])))
           or (not qd and qn in normalizar(c["nome"]))]
    completo = current_user.tem("clientes.ver_dados_completos")
    return jsonify([cliente_json(current_app.repos, c, completo) for c in res[:20]])


@bp.route("/api/rapido", methods=["POST"])
@login_required
def api_rapido():
    if not current_user.tem("clientes.gerenciar", "pdv.vender"):
        abort(403)
    dados = request.get_json(silent=True) or request.form
    cpf, nome, tel = so_digitos(dados.get("cpf", "")), (dados.get("nome") or "").strip(), so_digitos(dados.get("telefone", ""))
    erros = _validar(cpf, nome, tel)
    if erros:
        return jsonify({"ok": False, "erros": erros}), 400
    novo = current_app.repos.clientes.adicionar({"cpf": cpf, "nome": nome, "telefone": tel,
                                                 "criado_em": agora_iso(), "criado_por": current_user.id})
    registrar("cliente_criado", "cliente", novo["id"], depois=novo)
    return jsonify({"ok": True, "cliente": cliente_json(current_app.repos, novo, current_user.tem("clientes.ver_dados_completos"))})
