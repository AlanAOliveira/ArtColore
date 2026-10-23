"""Página inicial: atalhos + painel (seção 12.1) e gerador de códigos SVG."""
from flask import Blueprint, Response, current_app, flash, render_template, request, session
from flask_login import current_user, login_required

from ...services import estoque as est
from ...services import pdf as svc_pdf
from ...services.precos import promocoes_vigentes
from ...utils import hoje_iso, primeiro_dia_mes_iso

bp = Blueprint("main", __name__)

MODULOS = [
    ("PDV", "Vender, devolver e trocar", "bi-cart3", "pdv.vender", "pdv.index"),
    ("Materiais e serviços", "Cadastro, preços e imagens", "bi-box-seam", "materiais.visualizar", "materiais.lista"),
    ("Estoque", "Posição, compras e saídas", "bi-boxes", "estoque.visualizar", "estoque.posicao"),
    ("Clientes", "CPF, nome, telefone e créditos", "bi-people", "clientes.visualizar", "clientes.lista"),
    ("Fornecedores", "Cadastro e preços de compra", "bi-truck", "fornecedores.visualizar", "fornecedores.lista"),
    ("Localizações", "Endereçamento de prateleiras", "bi-geo-alt", ("localizacoes.gerenciar", "estoque.visualizar"), "localizacoes.lista"),
    ("Promoções", "Preços promocionais", "bi-percent", ("precos.gerenciar", "materiais.visualizar"), "precos.promocoes"),
    ("Relatórios", "Indicadores e exportações", "bi-graph-up", "relatorios.visualizar", "relatorios.index"),
]


def _painel(repos):
    hoje, mes = hoje_iso(), primeiro_dia_mes_iso()
    vendas = repos.vendas.listar(lambda v: v["data_hora"][:10] >= mes and v["status"] == "finalizada")
    v_hoje = [v for v in vendas if v["data_hora"][:10] == hoje]

    def kpi(lst):
        total = sum(v["total"] for v in lst)
        return {"qtd": len(lst), "total": total, "ticket": total // len(lst) if lst else 0}
    por_forma = {}
    for v in v_hoje:
        for p in v["pagamentos"]:
            por_forma[p["forma"]] = por_forma.get(p["forma"], 0) + p["valor"]
        if v.get("troco"):
            por_forma["dinheiro"] = por_forma.get("dinheiro", 0) - v["troco"]
    pos = est.posicao(repos)
    alertas = {"baixo": sum(1 for p in pos if p["status"] == "baixo"), "zerado": sum(1 for p in pos if p["status"] == "zerado")}
    val = est.alertas_validade(repos)
    devs = repos.devolucoes.listar(lambda d: d["data_hora"][:10] >= mes)
    saldos_cred = repos.creditos.saldos()
    movs = sorted(repos.movimentacoes.listar(primeiro_dia_mes_iso()), key=lambda m: m["data_hora"], reverse=True)[:8]
    return {
        "hoje": kpi(v_hoje), "mes": kpi(vendas), "por_forma": por_forma,
        "estoque_custo": sum(p["valor_custo"] for p in pos), "estoque_venda": sum(p["valor_venda"] for p in pos),
        "alertas": alertas, "vencidos": len(val["vencidos"]), "a_vencer": len(val["a_vencer"]), "dias_venc": val["dias"],
        "devolucoes_mes": len(devs), "credito_devolvido_mes": sum(d.get("credito_gerado", 0) for d in devs),
        "creditos_aberto": sum(v for v in saldos_cred.values() if v > 0),
        "promocoes": len(promocoes_vigentes(repos)), "movimentos": movs,
        "materiais": {m["id"]: m for m in repos.materiais.listar()},
    }


@bp.route("/painel")
@login_required
def index():
    repos = current_app.repos
    painel = _painel(repos) if current_user.tem("relatorios.visualizar") else None
    if session.pop("mostrar_alertas", False) and current_user.tem("estoque.visualizar"):
        a = est.alertas_estoque(repos)
        v = est.alertas_validade(repos)
        partes = []
        if a["zerado"]:
            partes.append(f"{len(a['zerado'])} item(ns) com estoque zerado")
        if a["baixo"]:
            partes.append(f"{len(a['baixo'])} com estoque baixo")
        if v["vencidos"]:
            partes.append(f"{len(v['vencidos'])} lote(s) vencido(s)")
        if v["a_vencer"]:
            partes.append(f"{len(v['a_vencer'])} lote(s) a vencer em {v['dias']} dias")
        if partes:
            flash("Alertas: " + ", ".join(partes) + ".", "warning")
    return render_template("main/index.html", modulos=MODULOS, painel=painel)


@bp.route("/codigo.svg")
@login_required
def codigo_svg():
    """Código de barras (auto: EAN-13/EAN-8/Code128) ou QR (?t=qr) em SVG."""
    svg = svc_pdf.svg_codigo(request.args.get("v", ""), request.args.get("t", "auto"))
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "max-age=86400"})
