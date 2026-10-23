"""Controle de estoque: posição, compra, saídas, transferência, ajuste, inventário e alertas (seções 8 e 9)."""
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ...auditoria import registrar
from ...permissoes import requer_permissao
from ...services import estoque as est
from ...services.precos import calcular_preco_venda, registrar_historico
from ...utils import (agora_iso, dias_atras_iso, hoje_iso, normalizar, parse_data, parse_decimal, parse_moeda,
                      so_digitos)

bp = Blueprint("estoque", __name__, url_prefix="/estoque")


def _locs(ativas=True):
    return sorted([l for l in current_app.repos.localizacoes.listar() if l.get("ativo", True) or not ativas],
                  key=lambda l: l["codigo"])


def _ler_linhas(campos_extra=()):
    """Lê as linhas dinâmicas (item_id[], qtd[], unidade[] + extras). Retorna (linhas, erros)."""
    f = request.form
    ids = f.getlist("item_id")
    cols = {c: f.getlist(c) for c in ("qtd", "unidade") + tuple(campos_extra)}
    linhas, erros = [], []
    for i, mid in enumerate(ids):
        if not mid:
            continue
        n = i + 1
        m = current_app.repos.materiais.obter(mid)
        if not m:
            erros.append(f"Linha {n}: item não encontrado.")
            continue
        if m["natureza"] != "produto":
            erros.append(f"Linha {n}: {m['nome']} é serviço (sem estoque).")
            continue
        try:
            qtd = parse_decimal(cols["qtd"][i] if i < len(cols["qtd"]) else "")
        except ValueError:
            qtd = None
        if not qtd or qtd <= 0:
            erros.append(f"Linha {n}: quantidade inválida.")
            continue
        unidade = cols["unidade"][i] if i < len(cols["unidade"]) else "item"
        fator = (m.get("fator_caixa") or 1) if unidade == "caixa" else 1
        linha = {"n": n, "material": m, "qtd_informada": qtd, "unidade": unidade, "fator": fator,
                 "qtd": round(qtd * fator, 3)}
        for c in campos_extra:
            linha[c] = cols[c][i] if i < len(cols[c]) else ""
        linhas.append(linha)
    if not linhas and not erros:
        erros.append("Adicione ao menos um item.")
    return linhas, erros


def _form_ctx(**extra):
    return {"localizacoes": _locs(), **extra}


# ================================================================ posição
@bp.route("/")
@login_required
@requer_permissao("estoque.visualizar")
def posicao():
    repos = current_app.repos
    q = request.args.get("q", "").strip()
    tipo = request.args.get("tipo", "")
    status = request.args.get("status", "")
    loc_id = request.args.get("loc", "")
    qn, qd = normalizar(q), so_digitos(q)
    itens = est.posicao(repos)
    if loc_id:
        por_loc = {}
        for s in repos.saldos.listar(lambda s: s["localizacao_id"] == loc_id):
            por_loc[s["material_id"]] = round(por_loc.get(s["material_id"], 0) + s["quantidade"], 3)
        itens = [{**i, "saldo_loc": por_loc[i["material"]["id"]]} for i in itens if i["material"]["id"] in por_loc]
    if q:
        itens = [i for i in itens if (qd and qd == i["material"].get("codigo_barras"))
                 or qn in normalizar(f"{i['material']['id']} {i['material']['nome']}")]
    if tipo:
        itens = [i for i in itens if i["material"].get("tipo_id") == tipo]
    if status:
        itens = [i for i in itens if i["status"] == status]
    itens.sort(key=lambda i: normalizar(i["material"]["nome"]))
    totais = {"custo": sum(i["valor_custo"] for i in itens), "venda": sum(i["valor_venda"] for i in itens)}
    return render_template("estoque/posicao.html", itens=itens, totais=totais, tipos=repos.tipos.listar(),
                           localizacoes=_locs(False), filtros={"q": q, "tipo": tipo, "status": status, "loc": loc_id})


@bp.route("/movimentacoes")
@login_required
@requer_permissao("estoque.visualizar")
def movimentacoes():
    repos = current_app.repos
    inicio = request.args.get("inicio") or dias_atras_iso(30)
    fim = request.args.get("fim") or hoje_iso()
    tipo = request.args.get("tipo", "")
    material_id = request.args.get("material", "").strip().upper()
    movs = repos.movimentacoes.listar(inicio, fim, lambda m: (not tipo or m["tipo"] == tipo)
                                      and (not material_id or m["material_id"] == material_id))
    movs.sort(key=lambda m: (m["data_hora"], m["id"]), reverse=True)
    return render_template("estoque/movimentacoes.html", movs=movs[:1000], total=len(movs),
                           materiais={m["id"]: m for m in repos.materiais.listar()},
                           locs={l["id"]: l for l in repos.localizacoes.listar()},
                           lotes={l["id"]: l for l in repos.lotes.listar()},
                           usuarios={u["id"]: u["nome"] for u in repos.usuarios.listar()},
                           filtros={"inicio": inicio, "fim": fim, "tipo": tipo, "material": material_id})


# ================================================================== compra
@bp.route("/compra", methods=["GET", "POST"])
@login_required
@requer_permissao("estoque.compra")
def compra():
    repos = current_app.repos
    fornecedores = sorted([f for f in repos.fornecedores.listar() if f.get("ativo", True)],
                          key=lambda f: normalizar(f.get("fantasia") or f["razao_social"]))
    if request.method == "POST":
        f = request.form
        fornecedor = repos.fornecedores.obter(f.get("fornecedor_id", ""))
        linhas, erros = _ler_linhas(("preco", "loc", "validade"))
        if not fornecedor:
            erros.insert(0, "Selecione o fornecedor.")
        for l in linhas:
            try:
                l["preco_centavos"] = parse_moeda(l["preco"])
            except ValueError:
                l["preco_centavos"] = None
            if not l["preco_centavos"] or l["preco_centavos"] <= 0:
                erros.append(f"Linha {l['n']}: informe o preço unitário de compra.")
            if not l["loc"]:
                l["loc"] = l["material"].get("localizacao_padrao_id") or ""
            if not l["loc"]:
                erros.append(f"Linha {l['n']}: selecione a localização.")
            try:
                l["validade_iso"] = parse_data(l["validade"])
            except ValueError as e:
                erros.append(f"Linha {l['n']}: {e}")
                l["validade_iso"] = None
        if not erros:
            atualizar = f.get("atualizar_preco") == "on"
            try:
                with repos.store.transacao():
                    compra_id = repos.store.proximo_id("CMP")
                    with est.operacao(repos, current_user.id) as op:
                        for l in linhas:
                            custo_unit = round(l["preco_centavos"] / l["fator"])
                            op.entrada(l["material"], l["qtd"], l["loc"], "compra", validade=l["validade_iso"],
                                       valor_unitario=custo_unit, atualizar_custo=True, fornecedor_id=fornecedor["id"],
                                       documento=f.get("documento", "").strip(), compra_id=compra_id,
                                       observacao=f.get("observacao", "").strip())
                    if atualizar:
                        _atualizar_precos_compra(repos, linhas, fornecedor["id"])
                registrar("compra_lancada", "compra", compra_id, detalhe={"fornecedor": fornecedor["id"], "itens": len(linhas),
                                                                         "documento": f.get("documento")})
                flash(f"Compra {compra_id} lançada: {len(linhas)} item(ns).", "success")
                return redirect(url_for("estoque.movimentacoes", tipo="compra", inicio=hoje_iso()))
            except est.ErroEstoque as e:
                erros.append(str(e))
        for e in erros:
            flash(e, "danger")
        return render_template("estoque/compra.html", fornecedores=fornecedores, linhas=linhas, form=f, **_form_ctx())
    return render_template("estoque/compra.html", fornecedores=fornecedores, linhas=[], form={}, **_form_ctx())


def _atualizar_precos_compra(repos, linhas, fornecedor_id):
    """Atualiza o preço do fornecedor e recalcula o preço de venda (RN02)."""
    agora = agora_iso()
    for l in linhas:
        antes = repos.materiais.obter(l["material"]["id"])
        m = dict(antes)
        forn = [dict(x) for x in m.get("fornecedores", [])]
        unidade = "caixa" if l["unidade"] == "caixa" and (m.get("fator_caixa") or 1) > 1 else "item"
        linha = next((x for x in forn if x["fornecedor_id"] == fornecedor_id), None)
        if linha:
            linha.update(preco_compra=l["preco_centavos"], unidade_compra=unidade, atualizado_em=agora)
        else:
            forn.append({"fornecedor_id": fornecedor_id, "preco_compra": l["preco_centavos"], "unidade_compra": unidade,
                         "principal": not forn, "atualizado_em": agora})
        m["fornecedores"] = forn
        m["preco_venda"] = calcular_preco_venda(m)
        novo = repos.materiais.atualizar(m["id"], {"fornecedores": forn, "preco_venda": m["preco_venda"]})
        registrar_historico(repos, antes, novo, current_user.id, "compra")


# ================================================================== saídas
@bp.route("/saida/<tipo>", methods=["GET", "POST"])
@login_required
def saida(tipo):
    if tipo not in ("sucata", "doacao"):
        abort(404)
    if not current_user.tem(f"estoque.{tipo}"):
        abort(403)
    repos = current_app.repos
    if request.method == "POST":
        f = request.form
        linhas, erros = _ler_linhas(("loc",))
        motivo = f.get("motivo", "").strip()
        destinatario = f.get("destinatario", "").strip()
        if not motivo:
            erros.insert(0, "Informe o motivo.")
        if tipo == "doacao" and not destinatario:
            erros.insert(0, "Informe o destinatário da doação.")
        if not erros:
            try:
                with est.operacao(repos, current_user.id) as op:
                    for l in linhas:
                        op.saida(l["material"], l["qtd"], tipo, localizacao_id=l["loc"] or None,
                                 permitir_vencido=(tipo == "sucata"), motivo=motivo, destinatario=destinatario,
                                 observacao=f.get("observacao", "").strip())
                registrar(f"saida_{tipo}", "estoque", "", detalhe={"itens": [(l["material"]["id"], l["qtd"]) for l in linhas],
                                                                  "motivo": motivo, "destinatario": destinatario})
                flash(f"{est.TIPOS_MOV[tipo]} registrada: {len(linhas)} item(ns).", "success")
                return redirect(url_for("estoque.movimentacoes", tipo=tipo, inicio=hoje_iso()))
            except est.ErroEstoque as e:
                erros.append(str(e))
        for e in erros:
            flash(e, "danger")
        return render_template("estoque/saida.html", tipo=tipo, linhas=linhas, form=f, motivos=est.MOTIVOS_SUCATA, **_form_ctx())
    return render_template("estoque/saida.html", tipo=tipo, linhas=[], form={}, motivos=est.MOTIVOS_SUCATA, **_form_ctx())


# ============================================================ transferência
@bp.route("/transferencia", methods=["GET", "POST"])
@login_required
@requer_permissao("estoque.transferencia")
def transferencia():
    repos = current_app.repos
    if request.method == "POST":
        f = request.form
        linhas, erros = _ler_linhas()
        origem, destino = f.get("origem", ""), f.get("destino", "")
        if not origem or not destino:
            erros.insert(0, "Selecione origem e destino.")
        if not erros:
            try:
                with est.operacao(repos, current_user.id) as op:
                    for l in linhas:
                        op.transferir(l["material"], l["qtd"], origem, destino, f.get("observacao", "").strip())
                registrar("transferencia", "estoque", "", detalhe={"origem": origem, "destino": destino,
                                                                   "itens": [(l["material"]["id"], l["qtd"]) for l in linhas]})
                flash(f"Transferência registrada: {len(linhas)} item(ns).", "success")
                return redirect(url_for("estoque.movimentacoes", tipo="transferencia", inicio=hoje_iso()))
            except est.ErroEstoque as e:
                erros.append(str(e))
        for e in erros:
            flash(e, "danger")
        return render_template("estoque/transferencia.html", linhas=linhas, form=f, **_form_ctx())
    return render_template("estoque/transferencia.html", linhas=[], form={"origem": request.args.get("origem", "")}, **_form_ctx())


# =================================================================== ajuste
@bp.route("/ajuste", methods=["GET", "POST"])
@login_required
@requer_permissao("estoque.ajuste")
def ajuste():
    repos = current_app.repos
    if request.method == "POST":
        f = request.form
        m = repos.materiais.obter(f.get("item_id", ""))
        loc = f.get("loc", "")
        motivo = f.get("motivo", "").strip()
        erros = []
        try:
            contado = parse_decimal(f.get("contado"))
        except ValueError:
            contado = None
        if not m or m["natureza"] != "produto":
            erros.append("Selecione um produto.")
        if not loc:
            erros.append("Selecione a localização.")
        if contado is None or contado < 0:
            erros.append("Informe a quantidade contada (≥ 0).")
        elif m and m.get("unidade") != "metro" and contado != int(contado):
            erros.append("Quantidade deve ser inteira.")
        if not motivo:
            erros.append("Informe o motivo.")
        if not erros:
            try:
                with est.operacao(repos, current_user.id) as op:
                    dif = op.ajustar(m, loc, contado, motivo)
                if dif:
                    registrar("ajuste_estoque", "material", m["id"], detalhe={"loc": loc, "contado": contado, "diferenca": dif})
                    flash(f"Ajuste registrado: diferença {dif:+g}.".replace(".", ","), "success")
                else:
                    flash("Saldo já confere — nenhum ajuste necessário.", "info")
                return redirect(url_for("materiais.form", id_=m["id"]))
            except est.ErroEstoque as e:
                erros.append(str(e))
        for e in erros:
            flash(e, "danger")
    m = repos.materiais.obter(request.args.get("material", "")) if request.method == "GET" else None
    return render_template("estoque/ajuste.html", material=m, loc_sel=request.args.get("loc", ""), **_form_ctx())


@bp.route("/api/saldo")
@login_required
@requer_permissao("estoque.visualizar")
def api_saldo():
    mid, loc = request.args.get("material", ""), request.args.get("loc", "")
    saldos = current_app.repos.saldos.do_material(mid)
    return jsonify({"total": round(sum(s["quantidade"] for s in saldos), 3),
                    "local": round(sum(s["quantidade"] for s in saldos if s["localizacao_id"] == loc), 3) if loc else None,
                    "por_local": {s["localizacao_id"]: s["quantidade"] for s in saldos}})


# ================================================================== alertas
@bp.route("/alertas")
@login_required
@requer_permissao("estoque.visualizar")
def alertas():
    repos = current_app.repos
    return render_template("estoque/alertas.html", estoque=est.alertas_estoque(repos),
                           validade=est.alertas_validade(repos), sugestao=est.sugestao_compra(repos))


# =============================================================== inventário
def _escopo_materiais(repos, inv):
    esc = inv["escopo"]
    mats = [m for m in repos.materiais.listar() if m["natureza"] == "produto"]
    if esc["tipo"] == "tipo":
        mats = [m for m in mats if m.get("tipo_id") == esc["valor"]]
    return {m["id"]: m for m in mats}


def _divergencias(repos, inv, zerar_nao_contados=False):
    mats = _escopo_materiais(repos, inv)
    esc = inv["escopo"]
    sistema = {}
    for s in repos.saldos.listar():
        if s["material_id"] not in mats:
            continue
        if esc["tipo"] == "localizacao" and s["localizacao_id"] != esc["valor"]:
            continue
        k = f"{s['material_id']}|{s['localizacao_id']}"
        sistema[k] = round(sistema.get(k, 0) + s["quantidade"], 3)
    contagens = inv.get("contagens", {})
    chaves = set(contagens) | (set(sistema) if zerar_nao_contados else set())
    locs = {l["id"]: l for l in repos.localizacoes.listar()}
    linhas = []
    for k in sorted(chaves):
        mid, lid = k.split("|")
        if mid not in mats:
            continue
        contado = contagens.get(k, 0 if zerar_nao_contados else None)
        sis = sistema.get(k, 0)
        m = mats[mid]
        custo = m.get("custo_medio") or 0
        linhas.append({"chave": k, "material": m, "loc": locs.get(lid, {"codigo": lid}), "sistema": sis,
                       "contado": contado, "diferenca": round(contado - sis, 3), "valor": round((contado - sis) * custo)})
    linhas.sort(key=lambda l: (l["loc"]["codigo"], normalizar(l["material"]["nome"])))
    return linhas


@bp.route("/inventarios", methods=["GET", "POST"])
@login_required
@requer_permissao("estoque.ajuste")
def inventarios():
    repos = current_app.repos
    if request.method == "POST":
        tipo = request.form.get("escopo", "geral")
        valor = request.form.get("valor_" + tipo, "") if tipo != "geral" else ""
        if tipo != "geral" and not valor:
            flash("Selecione o tipo/localização do inventário.", "danger")
            return redirect(url_for("estoque.inventarios"))
        inv = repos.inventarios.adicionar({"escopo": {"tipo": tipo, "valor": valor}, "status": "aberto",
                                           "descricao": request.form.get("descricao", "").strip(),
                                           "contagens": {}, "criado_em": agora_iso(), "criado_por": current_user.id})
        registrar("inventario_aberto", "inventario", inv["id"])
        return redirect(url_for("estoque.inventario", id_=inv["id"]))
    invs = sorted(repos.inventarios.listar(), key=lambda i: i["criado_em"], reverse=True)
    return render_template("estoque/inventarios.html", invs=invs, tipos=repos.tipos.listar(), localizacoes=_locs(),
                           locs={l["id"]: l for l in repos.localizacoes.listar()},
                           tipos_map={t["id"]: t for t in repos.tipos.listar()})


@bp.route("/inventarios/<id_>")
@login_required
@requer_permissao("estoque.ajuste")
def inventario(id_):
    repos = current_app.repos
    inv = repos.inventarios.obter(id_) or abort(404)
    mats = {m["id"]: m for m in repos.materiais.listar()}
    locs = {l["id"]: l for l in repos.localizacoes.listar()}
    contagens = [{"chave": k, "material": mats.get(k.split("|")[0], {}), "loc": locs.get(k.split("|")[1], {}), "qtd": v}
                 for k, v in inv.get("contagens", {}).items()]
    contagens.sort(key=lambda c: (c["loc"].get("codigo", ""), normalizar(c["material"].get("nome", ""))))
    loc_fixa = inv["escopo"]["valor"] if inv["escopo"]["tipo"] == "localizacao" else ""
    return render_template("estoque/inventario.html", inv=inv, contagens=contagens, localizacoes=_locs(),
                           loc_fixa=loc_fixa, locs=locs, tipos_map={t["id"]: t for t in repos.tipos.listar()})


@bp.route("/inventarios/<id_>/contar", methods=["POST"])
@login_required
@requer_permissao("estoque.ajuste")
def inventario_contar(id_):
    repos = current_app.repos
    dados = request.get_json(silent=True) or {}
    with repos.store.transacao():
        inv = repos.inventarios.obter(id_)
        if not inv or inv["status"] != "aberto":
            return jsonify({"ok": False, "erro": "Inventário não está aberto."}), 400
        loc = dados.get("localizacao_id", "")
        if not repos.localizacoes.obter(loc):
            return jsonify({"ok": False, "erro": "Selecione/leia a localização."}), 400
        if inv["escopo"]["tipo"] == "localizacao" and loc != inv["escopo"]["valor"]:
            return jsonify({"ok": False, "erro": "Localização fora do escopo deste inventário."}), 400
        codigo = str(dados.get("codigo", "")).strip()
        m = repos.materiais.obter_por("codigo_barras", codigo) or repos.materiais.obter(codigo.upper())
        if not m or m["natureza"] != "produto":
            return jsonify({"ok": False, "erro": f"Produto não encontrado: {codigo}"}), 404
        if m["id"] not in _escopo_materiais(repos, inv):
            return jsonify({"ok": False, "erro": f"{m['nome']} está fora do escopo deste inventário."}), 400
        try:
            qtd = float(str(dados.get("quantidade", 1)).replace(",", "."))
        except ValueError:
            return jsonify({"ok": False, "erro": "Quantidade inválida."}), 400
        if m.get("unidade") != "metro" and qtd != int(qtd):
            return jsonify({"ok": False, "erro": "Quantidade deve ser inteira."}), 400
        k = f"{m['id']}|{loc}"
        contagens = inv.get("contagens", {})
        if dados.get("modo") == "remover":
            contagens.pop(k, None)
        else:
            novo = qtd if dados.get("modo") == "definir" else round(contagens.get(k, 0) + qtd, 3)
            if novo < 0:
                return jsonify({"ok": False, "erro": "Quantidade não pode ficar negativa."}), 400
            contagens[k] = round(novo, 3)
        repos.inventarios.atualizar(id_, {"contagens": contagens})
    return jsonify({"ok": True, "chave": k, "material": {"id": m["id"], "nome": m["nome"]}, "quantidade": contagens.get(k)})


@bp.route("/inventarios/<id_>/revisar", methods=["GET", "POST"])
@login_required
@requer_permissao("estoque.ajuste")
def inventario_revisar(id_):
    repos = current_app.repos
    inv = repos.inventarios.obter(id_) or abort(404)
    zerar = request.values.get("zerar") == "1"
    linhas = _divergencias(repos, inv, zerar)
    if request.method == "POST":
        if inv["status"] != "aberto":
            flash("Inventário não está aberto.", "danger")
            return redirect(url_for("estoque.inventario", id_=id_))
        acao = request.form.get("acao")
        if acao == "cancelar":
            repos.inventarios.atualizar(id_, {"status": "cancelado", "concluido_em": agora_iso(), "concluido_por": current_user.id})
            registrar("inventario_cancelado", "inventario", id_)
            flash("Inventário cancelado.", "info")
            return redirect(url_for("estoque.inventarios"))
        try:
            ajustes = []
            with est.operacao(repos, current_user.id) as op:
                for l in linhas:
                    if abs(l["diferenca"]) > 1e-9:
                        dif = op.ajustar(l["material"], l["chave"].split("|")[1], l["contado"],
                                         f"Inventário {id_}", inventario_id=id_)
                        ajustes.append({"chave": l["chave"], "sistema": l["sistema"], "contado": l["contado"], "diferenca": dif})
            repos.inventarios.atualizar(id_, {"status": "concluido", "concluido_em": agora_iso(),
                                              "concluido_por": current_user.id, "ajustes": ajustes, "zerar_nao_contados": zerar})
            registrar("inventario_concluido", "inventario", id_, detalhe={"ajustes": len(ajustes)})
            flash(f"Inventário concluído: {len(ajustes)} ajuste(s) gerado(s).", "success")
            return redirect(url_for("estoque.inventarios"))
        except est.ErroEstoque as e:
            flash(str(e), "danger")
    return render_template("estoque/inventario_revisar.html", inv=inv, linhas=linhas, zerar=zerar,
                           total_valor=sum(l["valor"] for l in linhas))
