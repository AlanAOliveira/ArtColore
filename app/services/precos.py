"""Preços: custo base, regra de venda, promoções e histórico (seções 5.1–5.3). Valores em centavos."""
from decimal import ROUND_HALF_UP, Decimal

from ..utils import agora_iso, hoje_iso


def _arred(x):
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def custo_por_unidade(linha, fator_caixa):
    preco = int(linha.get("preco_compra") or 0)
    if linha.get("unidade_compra") == "caixa" and (fator_caixa or 1) > 1:
        return _arred(Decimal(preco) / Decimal(fator_caixa))
    return preco


def custo_base(material):
    """Custo de 1 item/metro conforme 'base_preco': principal | menor | ultima."""
    linhas = [l for l in material.get("fornecedores", []) if l.get("preco_compra")]
    if not linhas:
        return material.get("custo_medio")
    fator = material.get("fator_caixa") or 1
    base = material.get("base_preco")
    if base == "menor":
        return min(custo_por_unidade(l, fator) for l in linhas)
    if base == "ultima":
        return custo_por_unidade(max(linhas, key=lambda l: l.get("atualizado_em") or ""), fator)
    principal = next((l for l in linhas if l.get("principal")), linhas[0])
    return custo_por_unidade(principal, fator)


def calcular_preco_venda(material):
    regra = material.get("regra_preco", "manual")
    if regra == "manual":
        return material.get("preco_venda")
    custo = custo_base(material)
    if custo is None:
        return material.get("preco_venda")
    valor = material.get("valor_regra") or 0
    if regra == "markup":
        return _arred(Decimal(custo) * (1 + Decimal(str(valor)) / 100))
    if regra == "fixo":
        return custo + int(valor)
    return material.get("preco_venda")


def margem(material, preco=None):
    custo = custo_base(material)
    venda = material.get("preco_venda") if preco is None else preco
    if not custo or venda is None:
        return None, None
    m = venda - custo
    return m, round(m * 100 / custo, 1)


# ------------------------------------------------------------- promoções
def preco_promocional(promo, preco):
    tipo, valor = promo["tipo"], promo["valor"]
    if tipo == "valor":
        return int(valor)
    if tipo == "percentual":
        return max(0, _arred(Decimal(preco) * (1 - Decimal(str(valor)) / 100)))
    if tipo == "absoluto":
        return max(0, preco - int(valor))
    return preco


def promo_vigente(promo, data=None):
    data = data or hoje_iso()
    return promo.get("ativo", True) and promo["inicio"] <= data <= promo["fim"]


def promocoes_vigentes(repos, data=None):
    """{material_id: promo} — escolhe a de menor preço se houver várias."""
    res = {}
    materiais = None
    for p in repos.promocoes.listar(lambda p: promo_vigente(p, data)):
        if materiais is None:
            materiais = {m["id"]: m for m in repos.materiais.listar()}
        m = materiais.get(p["material_id"])
        if not m or not m.get("preco_venda"):
            continue
        p["preco"] = preco_promocional(p, m["preco_venda"])
        if p["material_id"] not in res or p["preco"] < res[p["material_id"]]["preco"]:
            res[p["material_id"]] = p
    return res


def preco_efetivo(material, promos):
    """(preco_unitario, preco_original, promo_id) para produto. Serviço -> (None, None, None)."""
    if material.get("natureza") == "servico":
        return None, None, None
    original = material.get("preco_venda")
    p = promos.get(material["id"])
    if p:
        return p["preco"], original, p["id"]
    return original, original, None


def preco_caixa(material, promos):
    """Preço de 1 caixa fechada (com promoção proporcional, se houver)."""
    fator = material.get("fator_caixa") or 1
    unit, original, promo = preco_efetivo(material, promos)
    if fator <= 1 or unit is None:
        return None
    if material.get("preco_venda_caixa") and not promo:
        return material["preco_venda_caixa"]
    if material.get("preco_venda_caixa") and promo:
        return min(material["preco_venda_caixa"], unit * fator)
    return unit * fator


# ------------------------------------------------------------- histórico
def registrar_historico(repos, antes, depois, usuario_id, origem):
    """Compara preços de venda e de compra (por fornecedor) e grava variações."""
    if not antes or depois.get("natureza") == "servico":
        return
    agora = agora_iso()
    regs = []

    def add(campo, ant, novo, fornecedor_id=None):
        if ant == novo or novo is None:
            return
        var_abs = (novo - ant) if ant is not None else None
        var_pct = round(var_abs * 100 / ant, 2) if ant else None
        regs.append({"material_id": depois["id"], "campo": campo, "fornecedor_id": fornecedor_id,
                     "anterior": ant, "novo": novo, "var_abs": var_abs, "var_pct": var_pct,
                     "origem": origem, "usuario_id": usuario_id, "data_hora": agora})

    add("preco_venda", antes.get("preco_venda"), depois.get("preco_venda"))
    add("preco_venda_caixa", antes.get("preco_venda_caixa"), depois.get("preco_venda_caixa"))
    ant_f = {l["fornecedor_id"]: l.get("preco_compra") for l in antes.get("fornecedores", [])}
    for l in depois.get("fornecedores", []):
        if l["fornecedor_id"] in ant_f:
            add("preco_compra", ant_f[l["fornecedor_id"]], l.get("preco_compra"), l["fornecedor_id"])
    for r in regs:
        repos.historico_precos.adicionar(r)
