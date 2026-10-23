"""Cadastro de materiais (produtos) e serviços personalizados + tipos (seção 4)."""
import io
import os

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, url_for)
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import requer_permissao
from ...services import imagens as svc_img
from ...services import pdf as svc_pdf
from ...services.precos import (calcular_preco_venda, custo_base, margem, preco_caixa, preco_efetivo,
                                promocoes_vigentes, registrar_historico)
from ...utils import (agora_iso, formatar_moeda, gerar_ean13_interno, normalizar, parse_decimal, parse_moeda,
                      so_digitos, validar_ean13)

bp = Blueprint("materiais", __name__, url_prefix="/materiais")

UNIDADES = {"unidade": "Unitário (item)", "metro": "Metro (m)"}
REGRAS = {"manual": "Manual", "markup": "Markup (%)", "fixo": "Custo + valor fixo (R$)"}
BASES = {"principal": "Fornecedor principal", "menor": "Menor preço entre fornecedores", "ultima": "Último preço atualizado"}
ORIGENS = {"0": "0 - Nacional", "1": "1 - Estrangeira (importação direta)", "2": "2 - Estrangeira (mercado interno)"}


def _tipos(apenas_ativos=True):
    tipos = current_app.repos.tipos.listar()
    if apenas_ativos:
        tipos = [t for t in tipos if t.get("ativo", True)]
    return sorted(tipos, key=lambda t: t["nome"])


def _contexto_form():
    return {
        "tipos": _tipos(),
        "fornecedores": sorted([f for f in current_app.repos.fornecedores.listar() if f.get("ativo", True)],
                               key=lambda f: normalizar(f.get("fantasia") or f["razao_social"])),
        "localizacoes": sorted([l for l in current_app.repos.localizacoes.listar() if l.get("ativo", True)],
                               key=lambda l: l["codigo"]),
        "unidades": UNIDADES, "regras": REGRAS, "bases": BASES, "origens": ORIGENS,
    }


# ------------------------------------------------------------------- lista
@bp.route("/")
@login_required
@requer_permissao("materiais.visualizar")
def lista():
    q = request.args.get("q", "").strip()
    tipo_id = request.args.get("tipo", "")
    natureza = request.args.get("natureza", "")
    situacao = request.args.get("situacao", "ativos")
    alerta = request.args.get("alerta", "")
    tipos = {t["id"]: t for t in current_app.repos.tipos.listar()}
    qn, qd = normalizar(q), so_digitos(q)
    saldos = current_app.repos.saldos.por_material()

    def filtro(m):
        if situacao == "ativos" and not m.get("ativo", True):
            return False
        if situacao == "inativos" and m.get("ativo", True):
            return False
        if tipo_id and m.get("tipo_id") != tipo_id:
            return False
        if natureza and m.get("natureza") != natureza:
            return False
        if alerta and m.get("natureza") == "produto":
            s = saldos.get(m["id"], 0)
            if alerta == "zerado" and s > 0:
                return False
            if alerta == "baixo" and not (0 < s <= (m.get("estoque_minimo") or 0)):
                return False
        elif alerta:
            return False
        if q:
            if qd and qd == m.get("codigo_barras"):
                return True
            alvo = normalizar(f"{m['id']} {m['nome']} {m.get('descricao', '')} {m.get('codigo_barras', '')}")
            return qn in alvo
        return True

    itens = sorted(current_app.repos.materiais.listar(filtro), key=lambda m: normalizar(m["nome"]))
    if q and qd and len(itens) == 1 and itens[0].get("codigo_barras") == qd:
        return redirect(url_for("materiais.form", id_=itens[0]["id"]))
    return render_template("materiais/lista.html", itens=itens[:500], total=len(itens), tipos=tipos, saldos=saldos,
                           promos=promocoes_vigentes(current_app.repos),
                           filtros={"q": q, "tipo": tipo_id, "natureza": natureza, "situacao": situacao, "alerta": alerta})


# ---------------------------------------------------------------- formulário
def _ler_form(existente):
    f = request.form
    erros = []
    repo = current_app.repos.materiais
    tipo = current_app.repos.tipos.obter(f.get("tipo_id", ""))
    if not tipo:
        erros.append("Selecione o tipo.")
    natureza = tipo["natureza"] if tipo else "produto"
    if existente and tipo and existente["natureza"] != natureza:
        erros.append("Não é possível trocar um produto por serviço (ou vice-versa). Escolha um tipo da mesma natureza.")

    dados = {
        "nome": f.get("nome", "").strip(),
        "descricao": f.get("descricao", "").strip(),
        "tipo_id": f.get("tipo_id", ""),
        "natureza": natureza,
        "codigo_barras": f.get("codigo_barras", "").strip().replace(" ", ""),
        "ativo": f.get("ativo") == "on",
        "fiscal": {k: f.get(f"fiscal_{k}", "").strip() for k in ("ncm", "cest", "cfop", "origem", "cst_csosn", "unidade_comercial")},
    }
    if not dados["nome"]:
        erros.append("Informe o nome.")
    elif len(dados["nome"]) > 120:
        erros.append("Nome deve ter no máximo 120 caracteres.")
    cb = dados["codigo_barras"]
    if cb:
        if cb.isdigit() and len(cb) == 13 and not validar_ean13(cb):
            erros.append("Código de barras EAN-13 com dígito verificador inválido.")
        outro = repo.obter_por("codigo_barras", cb)
        if outro and (not existente or outro["id"] != existente["id"]):
            erros.append(f"Código de barras já usado por {outro['id']} - {outro['nome']}.")

    ids, precos, unids = f.getlist("forn_id"), f.getlist("forn_preco"), f.getlist("forn_unidade")
    principal_idx = f.get("forn_principal", "0")
    linhas, vistos = [], set()
    anteriores = {l["fornecedor_id"]: l for l in (existente or {}).get("fornecedores", [])}
    for i, fid in enumerate(ids):
        if not fid:
            continue
        if fid in vistos:
            erros.append("Fornecedor repetido na lista.")
            continue
        vistos.add(fid)
        try:
            preco = parse_moeda(precos[i] if i < len(precos) else "")
        except ValueError as e:
            erros.append(str(e))
            preco = None
        if natureza == "produto" and not preco:
            erros.append("Informe o preço de compra de cada fornecedor.")
        unidade = unids[i] if i < len(unids) else "item"
        ant = anteriores.get(fid, {})
        atualizado = ant.get("atualizado_em") if ant.get("preco_compra") == preco and ant.get("unidade_compra") == unidade else agora_iso()
        linhas.append({"fornecedor_id": fid, "preco_compra": preco, "unidade_compra": unidade,
                       "principal": str(i) == principal_idx, "atualizado_em": atualizado})
    if linhas and not any(l["principal"] for l in linhas):
        linhas[0]["principal"] = True
    dados["fornecedores"] = linhas

    if natureza == "produto":
        if not linhas:
            erros.append("Produto precisa de ao menos um fornecedor.")
        unidade = f.get("unidade", "unidade")
        dados["unidade"] = unidade if unidade in UNIDADES else "unidade"
        try:
            fator = int(f.get("fator_caixa") or 1)
        except ValueError:
            fator = 0
        if dados["unidade"] == "metro":
            fator = 1
        if fator < 1:
            erros.append("Itens por caixa deve ser um número inteiro ≥ 1.")
        dados["fator_caixa"] = fator
        try:
            dados["estoque_minimo"] = parse_decimal(f.get("estoque_minimo")) or 0
            if dados["unidade"] == "unidade" and dados["estoque_minimo"] != int(dados["estoque_minimo"]):
                erros.append("Estoque mínimo de item unitário deve ser inteiro.")
        except ValueError as e:
            erros.append(str(e))
        dados["controla_validade"] = f.get("controla_validade") == "on"
        if existente and existente.get("controla_validade") != dados["controla_validade"] and \
                current_app.repos.saldos.do_material(existente["id"]):
            erros.append("Não é possível alterar o controle de validade de um item com saldo em estoque.")
        dados["localizacao_padrao_id"] = f.get("localizacao_padrao_id") or None
        dados["regra_preco"] = f.get("regra_preco") if f.get("regra_preco") in REGRAS else "manual"
        dados["base_preco"] = f.get("base_preco") if f.get("base_preco") in BASES else "principal"
        try:
            if dados["regra_preco"] == "markup":
                dados["valor_regra"] = parse_decimal(f.get("valor_regra"), 2) or 0
            elif dados["regra_preco"] == "fixo":
                dados["valor_regra"] = parse_moeda(f.get("valor_regra")) or 0
            else:
                dados["valor_regra"] = None
            dados["preco_venda"] = parse_moeda(f.get("preco_venda"))
            dados["preco_venda_caixa"] = parse_moeda(f.get("preco_venda_caixa")) if fator > 1 else None
        except ValueError as e:
            erros.append(str(e))
        dados["preco_venda"] = calcular_preco_venda({**(existente or {}), **dados})
        if not dados.get("preco_venda"):
            erros.append("Preço de venda deve ser maior que zero.")
        dados["preco_referencia"] = None
    else:
        dados.update({"unidade": "unidade", "fator_caixa": 1, "estoque_minimo": None,
                      "controla_validade": False, "localizacao_padrao_id": None,
                      "regra_preco": "manual", "base_preco": "principal", "valor_regra": None,
                      "preco_venda": None, "preco_venda_caixa": None})
        try:
            dados["preco_referencia"] = parse_moeda(f.get("preco_referencia"))
        except ValueError as e:
            erros.append(str(e))
    return dados, erros


def gerar_codigo_interno(repo):
    with current_app.repos.store.transacao():
        while True:
            codigo = gerar_ean13_interno(int(current_app.repos.store.proximo_id("EAN").split("-")[1]))
            if not repo.obter_por("codigo_barras", codigo):
                return codigo


@bp.route("/novo", methods=["GET", "POST"])
@bp.route("/<id_>", methods=["GET", "POST"])
@login_required
@requer_permissao("materiais.visualizar")
def form(id_=None):
    repos = current_app.repos
    repo = repos.materiais
    material = repo.obter(id_) if id_ else None
    if id_ and not material:
        flash("Material não encontrado.", "danger")
        return redirect(url_for("materiais.lista"))
    pode_editar = current_user.tem("materiais.gerenciar")
    if not id_ and not pode_editar:
        abort(403)

    if request.method == "POST":
        if not pode_editar:
            abort(403)
        dados, erros = _ler_form(material)
        if erros:
            for e in dict.fromkeys(erros):
                flash(e, "danger")
            return render_template("materiais/form.html", material=material, m={**(material or {}), **dados},
                                   pode_editar=True, extras={}, **_contexto_form())
        gerar = request.form.get("gerar_codigo") == "on" and not dados["codigo_barras"]
        if gerar:
            dados["codigo_barras"] = gerar_codigo_interno(repo)
        if material:
            dados["alterado_em"], dados["alterado_por"] = agora_iso(), current_user.id
            novo = repo.atualizar(id_, dados)
            registrar_historico(repos, material, novo, current_user.id, "cadastro")
            registrar("material_editado", "material", id_, antes=material, depois=novo)
            flash("Cadastro atualizado.", "success")
        else:
            dados.update({"imagens": [], "custo_medio": None, "criado_em": agora_iso(), "criado_por": current_user.id})
            prefixo = "SRV" if dados["natureza"] == "servico" else "MAT"
            novo = repo.adicionar(dados, prefixo=prefixo)
            registrar("material_criado", "material", novo["id"], depois=novo)
            flash(f"{'Serviço' if prefixo == 'SRV' else 'Produto'} {novo['id']} criado.", "success")
        if gerar:
            flash(f"Código de barras interno gerado: {dados['codigo_barras']}", "info")
        return redirect(url_for("materiais.form", id_=novo["id"]))

    extras = {}
    if material:
        m = material
        if m["natureza"] == "produto":
            locs = {l["id"]: l for l in repos.localizacoes.listar()}
            lotes = {l["id"]: l for l in repos.lotes.listar()}
            saldos = sorted(repos.saldos.do_material(id_), key=lambda s: locs.get(s["localizacao_id"], {}).get("codigo", ""))
            extras["saldos"] = [{**s, "loc": locs.get(s["localizacao_id"], {}), "lote": lotes.get(s.get("lote_id"))} for s in saldos]
            extras["saldo_total"] = round(sum(s["quantidade"] for s in saldos), 3)
            promos = promocoes_vigentes(repos)
            extras["promo"] = promos.get(id_)
            extras["preco_caixa"] = preco_caixa(m, promos)
            extras["movimentos"] = sorted(repos.movimentacoes.listar(filtro=lambda x: x["material_id"] == id_),
                                          key=lambda x: x["data_hora"], reverse=True)[:15]
            extras["locs"] = locs
        extras["historico"] = sorted(repos.historico_precos.listar(lambda h: h["material_id"] == id_),
                                     key=lambda h: h["data_hora"], reverse=True)[:30]
        extras["fornecedores_nomes"] = {f["id"]: f.get("fantasia") or f["razao_social"] for f in repos.fornecedores.listar()}
    else:
        m = {"ativo": True, "unidade": "unidade", "fator_caixa": 1, "estoque_minimo": 0,
             "regra_preco": "markup", "valor_regra": 100, "base_preco": "principal",
             "tipo_id": request.args.get("tipo", ""), "codigo_barras": request.args.get("codigo", ""),
             "fornecedores": [], "fiscal": {}, "imagens": []}
    custo = custo_base(m) if m.get("natureza") == "produto" else None
    mg_valor, mg_pct = margem(m) if m.get("natureza") == "produto" else (None, None)
    return render_template("materiais/form.html", material=material, m=m, pode_editar=pode_editar, extras=extras,
                           custo=custo, margem_valor=mg_valor, margem_pct=mg_pct, **_contexto_form())


# ------------------------------------------------------------------ imagens
@bp.route("/<id_>/imagens", methods=["POST"])
@login_required
@requer_permissao("materiais.gerenciar")
def imagens_upload(id_):
    repo = current_app.repos.materiais
    material = repo.obter(id_) or abort(404)
    arquivos = [a for a in request.files.getlist("imagens") if a and a.filename]
    if not arquivos:
        flash("Selecione ao menos uma imagem.", "warning")
        return redirect(url_for("materiais.form", id_=id_) + "#imagens")
    lista = material.get("imagens", [])
    for a in arquivos:
        try:
            nome = svc_img.salvar_imagem(a, current_app.config["DATA_DIR"], id_)
            lista.append({"arquivo": nome, "principal": not any(i["principal"] for i in lista)})
        except ValueError as e:
            flash(str(e), "danger")
    repo.atualizar(id_, {"imagens": lista})
    registrar("material_imagens", "material", id_)
    flash("Imagens atualizadas.", "success")
    return redirect(url_for("materiais.form", id_=id_) + "#imagens")


@bp.route("/<id_>/imagens/<arquivo>/principal", methods=["POST"])
@login_required
@requer_permissao("materiais.gerenciar")
def imagem_principal(id_, arquivo):
    repo = current_app.repos.materiais
    material = repo.obter(id_) or abort(404)
    repo.atualizar(id_, {"imagens": [{**i, "principal": i["arquivo"] == arquivo} for i in material.get("imagens", [])]})
    return redirect(url_for("materiais.form", id_=id_) + "#imagens")


@bp.route("/<id_>/imagens/<arquivo>/remover", methods=["POST"])
@login_required
@requer_permissao("materiais.gerenciar")
def imagem_remover(id_, arquivo):
    repo = current_app.repos.materiais
    material = repo.obter(id_) or abort(404)
    lista = [i for i in material.get("imagens", []) if i["arquivo"] != arquivo]
    if lista and not any(i["principal"] for i in lista):
        lista[0]["principal"] = True
    repo.atualizar(id_, {"imagens": lista})
    svc_img.remover_imagem(current_app.config["DATA_DIR"], id_, arquivo)
    registrar("material_imagem_removida", "material", id_)
    flash("Imagem removida.", "info")
    return redirect(url_for("materiais.form", id_=id_) + "#imagens")


@bp.route("/<id_>/imagens/<arquivo>")
@login_required
def imagem(id_, arquivo):
    pasta = os.path.join(current_app.config["DATA_DIR"], "uploads", "materiais", os.path.basename(id_))
    return send_from_directory(pasta, os.path.basename(arquivo))


# ------------------------------------------------------- APIs (PDV/estoque)
def material_json(m, saldos, promos):
    img = next((i for i in m.get("imagens", []) if i.get("principal")), None)
    unit, original, promo_id = preco_efetivo(m, promos)
    return {
        "id": m["id"], "nome": m["nome"], "natureza": m["natureza"], "unidade": m.get("unidade"),
        "fator_caixa": m.get("fator_caixa") or 1, "codigo_barras": m.get("codigo_barras"),
        "ativo": m.get("ativo", True), "controla_validade": m.get("controla_validade", False),
        "localizacao_padrao_id": m.get("localizacao_padrao_id"),
        "preco": unit, "preco_original": original, "promo": bool(promo_id),
        "preco_caixa": preco_caixa(m, promos) if m["natureza"] == "produto" else None,
        "preco_referencia": m.get("preco_referencia"),
        "custo": custo_base(m) if m["natureza"] == "produto" else None,
        "saldo": saldos.get(m["id"], 0) if m["natureza"] == "produto" else None,
        "imagem": url_for("materiais.imagem", id_=m["id"], arquivo=img["arquivo"]) if img else None,
    }


@bp.route("/api/codigo/<path:codigo>")
@login_required
def api_codigo(codigo):
    """Busca exata por código de barras ou código interno."""
    if not current_user.tem("materiais.visualizar", "pdv.vender", "estoque.visualizar"):
        abort(403)
    repo = current_app.repos.materiais
    c = codigo.strip()
    m = repo.obter_por("codigo_barras", c) or repo.obter(c.upper())
    if not m:
        return jsonify({"encontrado": False}), 404
    return jsonify({"encontrado": True, "material": material_json(
        m, current_app.repos.saldos.por_material(), promocoes_vigentes(current_app.repos))})


@bp.route("/api/buscar")
@login_required
def api_buscar():
    """Busca por nome/código (para os seletores de item). ?natureza=produto&ativos=1"""
    if not current_user.tem("materiais.visualizar", "pdv.vender", "estoque.visualizar"):
        abort(403)
    q = request.args.get("q", "").strip()
    natureza = request.args.get("natureza", "")
    if len(q) < 2:
        return jsonify([])
    qn = normalizar(q)
    res = []
    for m in current_app.repos.materiais.listar():
        if natureza and m["natureza"] != natureza:
            continue
        if request.args.get("ativos", "1") == "1" and not m.get("ativo", True):
            continue
        if q == m.get("codigo_barras") or q.upper() == m["id"] or qn in normalizar(f"{m['id']} {m['nome']}"):
            res.append(m)
    res.sort(key=lambda m: (0 if q == m.get("codigo_barras") or q.upper() == m["id"] else 1, normalizar(m["nome"])))
    saldos, promos = current_app.repos.saldos.por_material(), promocoes_vigentes(current_app.repos)
    return jsonify([material_json(m, saldos, promos) for m in res[:25]])


# --------------------------------------------------------------- etiquetas
@bp.route("/etiquetas", methods=["POST"])
@login_required
@requer_permissao("materiais.visualizar")
def etiquetas():
    """Etiquetas A4 (modelo provisório até a definição da impressora — F10)."""
    ids = request.form.getlist("ids")
    try:
        copias = max(1, min(int(request.form.get("copias") or 1), 100))
    except ValueError:
        copias = 1
    promos = promocoes_vigentes(current_app.repos)
    itens = []
    for mid in ids:
        m = current_app.repos.materiais.obter(mid)
        if not m:
            continue
        unit, original, promo = preco_efetivo(m, promos)
        preco = formatar_moeda(unit) if unit else (formatar_moeda(m.get("preco_referencia")) if m.get("preco_referencia") else "")
        sufixo = "/m" if m.get("unidade") == "metro" and unit else ""
        itens += [{"titulo": m["nome"], "subtitulo": m["id"], "preco": preco + sufixo,
                   "preco_de": formatar_moeda(original) if promo else None,
                   "codigo": m.get("codigo_barras") or m["id"]}] * copias
    if not itens:
        flash("Selecione ao menos um item.", "warning")
        return redirect(request.referrer or url_for("materiais.lista"))
    buf = io.BytesIO()
    svc_pdf.etiquetas(buf, itens)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", download_name="etiquetas_materiais.pdf")


# -------------------------------------------------------------------- tipos
@bp.route("/tipos", methods=["GET", "POST"])
@login_required
@requer_permissao("materiais.visualizar")
def tipos():
    repo = current_app.repos.tipos
    if request.method == "POST":
        if not current_user.tem("materiais.gerenciar"):
            abort(403)
        id_ = request.form.get("id") or None
        nome = request.form.get("nome", "").strip()
        natureza = request.form.get("natureza", "produto")
        ativo = request.form.get("ativo") == "on"
        existente = next((t for t in repo.listar() if normalizar(t["nome"]) == normalizar(nome)), None)
        if not nome:
            flash("Informe o nome do tipo.", "danger")
        elif natureza not in ("produto", "servico"):
            flash("Natureza inválida.", "danger")
        elif existente and existente["id"] != id_:
            flash("Já existe um tipo com esse nome.", "danger")
        elif id_:
            atual = repo.obter(id_)
            em_uso = any(m.get("tipo_id") == id_ for m in current_app.repos.materiais.listar())
            if atual and atual["natureza"] != natureza and em_uso:
                flash("Tipo em uso: não é possível alterar a natureza.", "danger")
            else:
                repo.atualizar(id_, {"nome": nome, "natureza": natureza, "ativo": ativo})
                registrar("tipo_editado", "tipo_material", id_, antes=atual)
                flash("Tipo atualizado.", "success")
        else:
            novo = repo.adicionar({"nome": nome, "natureza": natureza, "ativo": True})
            registrar("tipo_criado", "tipo_material", novo["id"], depois=novo)
            flash("Tipo criado.", "success")
        return redirect(url_for("materiais.tipos"))
    uso = {}
    for m in current_app.repos.materiais.listar():
        uso[m.get("tipo_id")] = uso.get(m.get("tipo_id"), 0) + 1
    return render_template("materiais/tipos.html", tipos=_tipos(False), uso=uso)
