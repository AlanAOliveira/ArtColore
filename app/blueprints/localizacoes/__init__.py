"""Localização física / endereçamento (seção 8)."""
import io
import re

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import requer_permissao
from ...services import pdf as svc_pdf
from ...utils import agora_iso, normalizar

bp = Blueprint("localizacoes", __name__, url_prefix="/localizacoes")
AREAS = {"LOJA": "Loja", "DEP": "Depósito"}


def montar_codigo(area, corredor, prateleira, nivel):
    partes = [re.sub(r"[^A-Z0-9]", "", normalizar(area).upper()) or "LOJA"]
    for prefixo, valor, largura in (("C", corredor, 2), ("P", prateleira, 2), ("N", nivel, 1)):
        valor = str(valor or "").strip().upper()
        if valor:
            partes.append(f"{prefixo}{valor.zfill(largura) if valor.isdigit() else valor}")
    return "-".join(partes)


@bp.route("/")
@login_required
@requer_permissao("localizacoes.gerenciar", "estoque.visualizar")
def lista():
    area = request.args.get("area", "")
    repos = current_app.repos
    itens = sorted(repos.localizacoes.listar(lambda l: not area or l.get("area") == area), key=lambda l: l["codigo"])
    padrao, itens_loc = {}, {}
    for m in repos.materiais.listar():
        if m.get("localizacao_padrao_id"):
            padrao[m["localizacao_padrao_id"]] = padrao.get(m["localizacao_padrao_id"], 0) + 1
    for s in repos.saldos.listar():
        itens_loc.setdefault(s["localizacao_id"], set()).add(s["material_id"])
    areas = sorted({l.get("area") for l in repos.localizacoes.listar()} | set(AREAS))
    return render_template("localizacoes/lista.html", itens=itens, padrao=padrao, itens_loc=itens_loc, areas=areas,
                           area=area, pode_editar=current_user.tem("localizacoes.gerenciar"))


@bp.route("/<id_>/conteudo")
@login_required
@requer_permissao("localizacoes.gerenciar", "estoque.visualizar")
def conteudo(id_):
    repos = current_app.repos
    loc = repos.localizacoes.obter(id_) or abort(404)
    materiais = {m["id"]: m for m in repos.materiais.listar()}
    lotes = {l["id"]: l for l in repos.lotes.listar()}
    saldos = [{**s, "material": materiais.get(s["material_id"], {}), "lote": lotes.get(s.get("lote_id"))}
              for s in repos.saldos.listar(lambda s: s["localizacao_id"] == id_)]
    saldos.sort(key=lambda s: normalizar(s["material"].get("nome", "")))
    return render_template("localizacoes/conteudo.html", loc=loc, saldos=saldos)


@bp.route("/novo", methods=["GET", "POST"])
@bp.route("/<id_>", methods=["GET", "POST"])
@login_required
@requer_permissao("localizacoes.gerenciar")
def form(id_=None):
    repo = current_app.repos.localizacoes
    loc = repo.obter(id_) if id_ else None
    if id_ and not loc:
        abort(404)
    if request.method == "POST":
        f = request.form
        dados = {"area": re.sub(r"[^A-Z0-9]", "", normalizar(f.get("area", "")).upper()) or "LOJA",
                 "corredor": f.get("corredor", "").strip(), "prateleira": f.get("prateleira", "").strip(),
                 "nivel": f.get("nivel", "").strip(), "descricao": f.get("descricao", "").strip(),
                 "ativo": f.get("ativo") == "on"}
        codigo = f.get("codigo", "").strip().upper() or montar_codigo(dados["area"], dados["corredor"], dados["prateleira"], dados["nivel"])
        dados["codigo"] = re.sub(r"\s+", "-", codigo)
        outro = repo.obter_por("codigo", dados["codigo"])
        if outro and outro["id"] != id_:
            flash(f"Código {dados['codigo']} já existe.", "danger")
            return render_template("localizacoes/form.html", loc=loc, l={**(loc or {}), **dados}, areas=AREAS)
        if loc and not dados["ativo"] and current_app.repos.saldos.listar(lambda s: s["localizacao_id"] == id_):
            flash("Localização com saldo em estoque não pode ser inativada. Transfira os itens antes.", "danger")
            return render_template("localizacoes/form.html", loc=loc, l={**loc, **dados}, areas=AREAS)
        if loc:
            novo = repo.atualizar(id_, dados)
            registrar("localizacao_editada", "localizacao", id_, antes=loc, depois=novo)
            flash("Localização atualizada.", "success")
        else:
            dados["criado_em"] = agora_iso()
            novo = repo.adicionar(dados)
            registrar("localizacao_criada", "localizacao", novo["id"], depois=novo)
            flash(f"Localização {novo['codigo']} criada.", "success")
        if f.get("acao") == "salvar_novo":
            return redirect(url_for("localizacoes.form", area=dados["area"], corredor=dados["corredor"]))
        return redirect(url_for("localizacoes.lista"))
    l = loc or {"area": request.args.get("area", "LOJA"), "corredor": request.args.get("corredor", ""), "ativo": True}
    return render_template("localizacoes/form.html", loc=loc, l=l, areas=AREAS)


@bp.route("/etiquetas", methods=["POST"])
@login_required
@requer_permissao("localizacoes.gerenciar", "estoque.visualizar")
def etiquetas():
    itens = []
    for lid in request.form.getlist("ids"):
        l = current_app.repos.localizacoes.obter(lid)
        if l:
            itens.append({"titulo": l["codigo"], "subtitulo": l.get("descricao", ""), "qr": l["codigo"], "codigo": None})
    if not itens:
        flash("Selecione ao menos uma localização.", "warning")
        return redirect(url_for("localizacoes.lista"))
    buf = io.BytesIO()
    svc_pdf.etiquetas(buf, itens)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", download_name="etiquetas_localizacoes.pdf")
