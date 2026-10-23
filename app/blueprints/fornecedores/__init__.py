"""Cadastro de fornecedores (seção 6)."""
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import requer_permissao
from ...utils import agora_iso, normalizar, so_digitos, validar_cnpj, validar_cpf

bp = Blueprint("fornecedores", __name__, url_prefix="/fornecedores")
CAMPOS = ("razao_social", "fantasia", "documento", "contato_nome", "telefone", "whatsapp", "email", "endereco", "observacoes")


@bp.route("/")
@login_required
@requer_permissao("fornecedores.visualizar")
def lista():
    q = request.args.get("q", "").strip()
    situacao = request.args.get("situacao", "ativos")
    qn, qd = normalizar(q), so_digitos(q)

    def filtro(f):
        if situacao == "ativos" and not f.get("ativo", True):
            return False
        if situacao == "inativos" and f.get("ativo", True):
            return False
        if q:
            alvo = normalizar(" ".join(str(f.get(k, "")) for k in ("id", "razao_social", "fantasia", "contato_nome", "email")))
            return qn in alvo or bool(qd and qd in f.get("documento", ""))
        return True

    itens = sorted(current_app.repos.fornecedores.listar(filtro), key=lambda f: normalizar(f.get("fantasia") or f["razao_social"]))
    contagem = {}
    for m in current_app.repos.materiais.listar():
        for l in m.get("fornecedores", []):
            contagem[l["fornecedor_id"]] = contagem.get(l["fornecedor_id"], 0) + 1
    return render_template("fornecedores/lista.html", itens=itens, contagem=contagem, filtros={"q": q, "situacao": situacao})


@bp.route("/novo", methods=["GET", "POST"])
@bp.route("/<id_>", methods=["GET", "POST"])
@login_required
@requer_permissao("fornecedores.visualizar")
def form(id_=None):
    repo = current_app.repos.fornecedores
    fornecedor = repo.obter(id_) if id_ else None
    if id_ and not fornecedor:
        flash("Fornecedor não encontrado.", "danger")
        return redirect(url_for("fornecedores.lista"))
    pode_editar = current_user.tem("fornecedores.gerenciar")
    if not id_ and not pode_editar:
        abort(403)
    if request.method == "POST":
        if not pode_editar:
            abort(403)
        dados = {k: request.form.get(k, "").strip() for k in CAMPOS}
        for k in ("documento", "telefone", "whatsapp"):
            dados[k] = so_digitos(dados[k])
        dados["ativo"] = request.form.get("ativo") == "on"
        erros = []
        if not dados["razao_social"]:
            erros.append("Informe a razão social / nome.")
        doc = dados["documento"]
        if doc:
            if len(doc) == 11 and not validar_cpf(doc):
                erros.append("CPF inválido.")
            elif len(doc) == 14 and not validar_cnpj(doc):
                erros.append("CNPJ inválido.")
            elif len(doc) not in (11, 14):
                erros.append("Documento deve ser CPF (11 dígitos) ou CNPJ (14 dígitos).")
            outro = repo.obter_por("documento", doc)
            if outro and outro["id"] != id_:
                erros.append(f"Documento já cadastrado em {outro['id']} - {outro['razao_social']}.")
        try:
            dados["prazo_entrega"] = int(request.form.get("prazo_entrega") or 0) or None
        except ValueError:
            erros.append("Prazo de entrega deve ser um número de dias.")
        if erros:
            for e in erros:
                flash(e, "danger")
            return render_template("fornecedores/form.html", fornecedor=fornecedor, f={**(fornecedor or {}), **dados},
                                   pode_editar=True, materiais=[])
        if fornecedor:
            novo = repo.atualizar(id_, dados)
            registrar("fornecedor_editado", "fornecedor", id_, antes=fornecedor, depois=novo)
            flash("Fornecedor atualizado.", "success")
        else:
            dados["criado_em"] = agora_iso()
            novo = repo.adicionar(dados)
            registrar("fornecedor_criado", "fornecedor", novo["id"], depois=novo)
            flash(f"Fornecedor {novo['id']} criado.", "success")
        return redirect(url_for("fornecedores.form", id_=novo["id"]))
    materiais = []
    if fornecedor:
        for m in current_app.repos.materiais.listar():
            for l in m.get("fornecedores", []):
                if l["fornecedor_id"] == id_:
                    materiais.append({"material": m, "linha": l})
        materiais.sort(key=lambda x: normalizar(x["material"]["nome"]))
    return render_template("fornecedores/form.html", fornecedor=fornecedor, f=fornecedor or {"ativo": True},
                           pode_editar=pode_editar, materiais=materiais)
