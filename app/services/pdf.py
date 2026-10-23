"""
Geração de PDFs (ReportLab):
- Comprovantes 80 mm (venda, devolução/troca, cancelamento, fechamento de caixa) — seção 3.7
- Relatórios em tabela (A4 paisagem)
- Etiquetas provisórias (A4) de materiais e localizações
- SVG de código de barras / QR Code para telas
"""
import os

from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from ..utils import formatar_data_hora, formatar_moeda

LARGURA = 80 * mm
MARGEM = 4 * mm
FONTE, FONTE_B = "Helvetica", "Helvetica-Bold"


# ======================================================== códigos (telas)
def desenho_codigo(valor, tipo="auto", largura=None, altura=None):
    valor = str(valor or "").strip()
    if not valor:
        return None
    if tipo == "qr":
        lado = largura or 120
        return createBarcodeDrawing("QR", value=valor, width=lado, height=lado, barBorder=1)
    if tipo == "auto" and valor.isdigit() and len(valor) == 13:
        d = createBarcodeDrawing("EAN13", value=valor[:12], barHeight=(altura or 40), humanReadable=True)
    elif tipo == "auto" and valor.isdigit() and len(valor) == 8:
        d = createBarcodeDrawing("EAN8", value=valor[:7], barHeight=(altura or 40), humanReadable=True)
    else:
        d = createBarcodeDrawing("Code128", value=valor, barHeight=(altura or 40), humanReadable=True)
    if largura:
        escala = largura / d.width
        d.scale(escala, 1)
        d.width = largura
    return d


def svg_codigo(valor, tipo="auto"):
    d = desenho_codigo(valor, tipo)
    if d is None:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
    return renderSVG.drawToString(d)


# ============================================================= cupom 80 mm
class Cupom:
    """Monta um comprovante 80 mm com altura dinâmica."""
    ALTURA_LINHA = 9.5

    def __init__(self):
        self.ops = []

    def texto(self, t, negrito=False, centro=False, tam=7.5):
        fonte = FONTE_B if negrito else FONTE
        for parte in simpleSplit(str(t), fonte, tam, LARGURA - 2 * MARGEM) or [""]:
            self.ops.append(("texto", parte, fonte, tam, centro))
        return self

    def par(self, esq, dir_, negrito=False, tam=7.5):
        fonte = FONTE_B if negrito else FONTE
        linhas = simpleSplit(str(esq), fonte, tam, LARGURA - 2 * MARGEM - 70) or [""]
        for i, l in enumerate(linhas):
            self.ops.append(("par", l, dir_ if i == len(linhas) - 1 else "", fonte, tam))
        return self

    def sep(self):
        self.ops.append(("sep",))
        return self

    def espaco(self, n=1):
        for _ in range(n):
            self.ops.append(("esp",))
        return self

    def qr(self, valor, lado=28 * mm):
        self.ops.append(("qr", valor, lado))
        return self

    def _altura(self):
        h = 2 * MARGEM
        for op in self.ops:
            if op[0] == "qr":
                h += op[2] + 4
            elif op[0] == "sep":
                h += 6
            else:
                h += self.ALTURA_LINHA
        return h

    def salvar(self, caminho):
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        altura = self._altura()
        c = canvas.Canvas(caminho, pagesize=(LARGURA, altura))
        y = altura - MARGEM
        for op in self.ops:
            if op[0] == "texto":
                _, t, fonte, tam, centro = op
                y -= self.ALTURA_LINHA
                c.setFont(fonte, tam)
                if centro:
                    c.drawCentredString(LARGURA / 2, y, t)
                else:
                    c.drawString(MARGEM, y, t)
            elif op[0] == "par":
                _, e, d, fonte, tam = op
                y -= self.ALTURA_LINHA
                c.setFont(fonte, tam)
                c.drawString(MARGEM, y, e)
                c.drawRightString(LARGURA - MARGEM, y, str(d))
            elif op[0] == "sep":
                y -= 6
                c.setDash(1, 2)
                c.line(MARGEM, y + 3, LARGURA - MARGEM, y + 3)
                c.setDash()
            elif op[0] == "esp":
                y -= self.ALTURA_LINHA
            elif op[0] == "qr":
                _, valor, lado = op
                y -= lado + 4
                d = createBarcodeDrawing("QR", value=valor, width=lado, height=lado, barBorder=0)
                renderPDF.draw(d, c, (LARGURA - lado) / 2, y + 2)
        c.showPage()
        c.save()
        return caminho


def _cabecalho(cp, empresa, titulo):
    cp.texto(empresa.get("nome_fantasia") or "Papelaria", negrito=True, centro=True, tam=10)
    if empresa.get("razao_social"):
        cp.texto(empresa["razao_social"], centro=True)
    if empresa.get("cnpj"):
        cp.texto(f"CNPJ {empresa['cnpj']}", centro=True)
    if empresa.get("endereco"):
        cp.texto(empresa["endereco"], centro=True)
    if empresa.get("telefone"):
        cp.texto(f"Tel. {empresa['telefone']}", centro=True)
    cp.sep()
    cp.texto(titulo, negrito=True, centro=True, tam=9)
    cp.texto("DOCUMENTO SEM VALOR FISCAL", negrito=True, centro=True)
    cp.sep()


def _qtd_txt(item):
    q = item["quantidade"]
    q = f"{q:g}".replace(".", ",")
    un = {"metro": "m", "caixa": "cx"}.get(item.get("unidade_venda"), "un")
    return f"{q} {un}"


FORMAS = {"dinheiro": "Dinheiro", "pix": "Pix", "debito": "Cartão de débito", "credito": "Cartão de crédito",
          "credito_cliente": "Crédito do cliente", "outros": "Outros"}


def _pagamentos(cp, pagamentos):
    for p in pagamentos:
        rot = FORMAS.get(p["forma"], p["forma"])
        if p.get("parcelas") and int(p["parcelas"]) > 1:
            rot += f" {p['parcelas']}x"
        cp.par(rot, formatar_moeda(p["valor"]))
        if p.get("nsu"):
            cp.texto(f"  NSU/Aut.: {p['nsu']}", tam=6.5)


def comprovante_venda(caminho, venda, ctx):
    """ctx: empresa, operador, cliente (nome, cpf_mascarado), prazo_troca, saldo_credito."""
    cp = Cupom()
    _cabecalho(cp, ctx["empresa"], "COMPROVANTE DE VENDA")
    cp.par(f"Venda {venda['id']}", formatar_data_hora(venda["data_hora"]), negrito=True)
    cp.texto(f"Operador: {ctx['operador']}")
    if ctx.get("cliente"):
        cp.texto(f"Cliente: {ctx['cliente']['nome']} - CPF {ctx['cliente']['cpf']}")
    cp.sep()
    for i, it in enumerate(venda["itens"], 1):
        cp.texto(f"{i:02d} {it['codigo']} {it['nome']}", negrito=True)
        unit = formatar_moeda(it["preco_unitario"])
        if it.get("preco_original") and it["preco_original"] != it["preco_unitario"]:
            unit += f" (de {formatar_moeda(it['preco_original'])} PROMO)"
        cp.par(f"   {_qtd_txt(it)} x {unit}", formatar_moeda(it["valor_bruto"]))
        if it.get("desconto"):
            cp.par("   Desconto no item", "-" + formatar_moeda(it["desconto"]))
        if it.get("observacao"):
            cp.texto(f"   Obs.: {it['observacao']}", tam=6.5)
    cp.sep()
    cp.par("Subtotal", formatar_moeda(venda["subtotal"]))
    if venda.get("desconto_total"):
        cp.par("Desconto", "-" + formatar_moeda(venda["desconto_total"]))
    cp.par("TOTAL", formatar_moeda(venda["total"]), negrito=True, tam=10)
    cp.sep()
    _pagamentos(cp, venda["pagamentos"])
    if venda.get("troco"):
        cp.par("Troco", formatar_moeda(venda["troco"]))
    if ctx.get("saldo_credito") is not None and venda.get("credito_utilizado"):
        cp.par("Saldo de crédito restante", formatar_moeda(ctx["saldo_credito"]))
    cp.sep()
    cp.texto(f"Trocas e devoluções até {ctx['prazo_troca']} mediante este comprovante.", centro=True, tam=6.5)
    cp.texto("Devoluções geram crédito na loja (não reembolsável em dinheiro).", centro=True, tam=6.5)
    cp.qr(venda["id"])
    cp.texto(venda["id"], centro=True)
    return cp.salvar(caminho)


def comprovante_devolucao(caminho, dev, ctx):
    cp = Cupom()
    titulo = "COMPROVANTE DE TROCA" if dev["tipo"] == "troca" else "COMPROVANTE DE DEVOLUÇÃO"
    _cabecalho(cp, ctx["empresa"], titulo)
    cp.par(dev["id"], formatar_data_hora(dev["data_hora"]), negrito=True)
    cp.texto(f"Venda original: {dev['venda_id']}")
    cp.texto(f"Operador: {ctx['operador']}")
    cp.texto(f"Cliente: {ctx['cliente']['nome']} - CPF {ctx['cliente']['cpf']}")
    cp.texto(f"Motivo: {dev['motivo']}")
    cp.sep()
    cp.texto("ITENS DEVOLVIDOS", negrito=True)
    for it in dev["itens_devolvidos"]:
        cp.texto(f"{it['codigo']} {it['nome']}")
        cond = f" [{it['condicao']}]" if it.get("condicao") else ""
        cp.par(f"   {it['quantidade']:g} un/m{cond}".replace(".", ","), formatar_moeda(it["valor"]))
    cp.par("Total devolvido", formatar_moeda(dev["valor_devolvido"]), negrito=True)
    if dev["tipo"] == "troca":
        cp.sep()
        cp.texto("ITENS LEVADOS", negrito=True)
        for it in dev["itens_novos"]:
            cp.texto(f"{it['codigo']} {it['nome']}")
            cp.par(f"   {_qtd_txt(it)} x {formatar_moeda(it['preco_unitario'])}", formatar_moeda(it["valor_bruto"]))
        cp.par("Total levado", formatar_moeda(dev["valor_novos"]), negrito=True)
        cp.sep()
        if dev["diferenca"] > 0:
            cp.par("Diferença paga pelo cliente", formatar_moeda(dev["diferenca"]), negrito=True)
            _pagamentos(cp, dev.get("pagamentos", []))
            if dev.get("troco"):
                cp.par("Troco", formatar_moeda(dev["troco"]))
    cp.sep()
    if dev.get("credito_gerado"):
        cp.par("CRÉDITO GERADO", formatar_moeda(dev["credito_gerado"]), negrito=True, tam=9)
    cp.par("Saldo de crédito do cliente", formatar_moeda(ctx["saldo_credito"]), negrito=True)
    cp.texto("Crédito vinculado ao CPF, sem validade, não conversível em dinheiro.", centro=True, tam=6.5)
    cp.qr(dev["id"])
    return cp.salvar(caminho)


def comprovante_cancelamento(caminho, venda, ctx):
    cp = Cupom()
    _cabecalho(cp, ctx["empresa"], "CANCELAMENTO DE VENDA")
    cp.par(ctx["cancelamento_id"], formatar_data_hora(venda["cancelada_em"]), negrito=True)
    cp.texto(f"Venda cancelada: {venda['id']} de {formatar_data_hora(venda['data_hora'])}")
    cp.texto(f"Autorizado por: {ctx['autorizador']}")
    cp.texto(f"Motivo: {venda.get('motivo_cancelamento') or '-'}")
    cp.sep()
    for it in venda["itens"]:
        cp.par(f"{it['codigo']} {it['nome']} ({_qtd_txt(it)})", formatar_moeda(it["valor_liquido"]))
    cp.sep()
    cp.par("TOTAL ESTORNADO", formatar_moeda(venda["total"]), negrito=True)
    _pagamentos(cp, venda["pagamentos"])
    if venda.get("credito_utilizado"):
        cp.texto("Crédito do cliente utilizado foi devolvido ao saldo.", tam=6.5)
    cp.texto("Itens retornados ao estoque.", tam=6.5)
    return cp.salvar(caminho)


def fechamento_caixa(caminho, caixa, resumo, ctx):
    cp = Cupom()
    _cabecalho(cp, ctx["empresa"], "FECHAMENTO DE CAIXA")
    cp.texto(f"Caixa {caixa['id']} - Operador: {ctx['operador']}", negrito=True)
    cp.par("Abertura", formatar_data_hora(caixa["abertura"]))
    cp.par("Fechamento", formatar_data_hora(caixa.get("fechamento")))
    cp.sep()
    cp.par("Vendas finalizadas", str(resumo["qtd_vendas"]))
    cp.par("Vendas canceladas", str(resumo["qtd_canceladas"]))
    cp.par("Total vendido (líquido)", formatar_moeda(resumo["total_vendas"]), negrito=True)
    cp.sep()
    cp.texto("RECEBIMENTOS POR FORMA", negrito=True)
    for forma, valor in resumo["por_forma"].items():
        cp.par(FORMAS.get(forma, forma), formatar_moeda(valor))
    cp.sep()
    cp.texto("DINHEIRO EM CAIXA", negrito=True)
    cp.par("Valor inicial", formatar_moeda(caixa["valor_inicial"]))
    cp.par("+ Recebido em dinheiro (líq. troco)", formatar_moeda(resumo["dinheiro_liquido"]))
    cp.par("+ Suprimentos", formatar_moeda(resumo["suprimentos"]))
    cp.par("- Sangrias", formatar_moeda(resumo["sangrias"]))
    cp.par("Esperado", formatar_moeda(resumo["esperado"]), negrito=True)
    cp.par("Contado", formatar_moeda(caixa.get("valor_contado")), negrito=True)
    cp.par("Diferença", formatar_moeda(resumo["diferenca"]), negrito=True)
    for mv in caixa.get("movimentos", []):
        cp.texto(f"  {mv['tipo'].capitalize()} {formatar_moeda(mv['valor'])} - {mv.get('motivo', '')}", tam=6.5)
    cp.sep()
    cp.par("Devoluções/trocas", str(resumo["qtd_devolucoes"]))
    cp.par("Créditos gerados", formatar_moeda(resumo["creditos_gerados"]))
    cp.par("Créditos utilizados", formatar_moeda(resumo["creditos_utilizados"]))
    cp.espaco(2)
    cp.texto("_______________________________", centro=True)
    cp.texto("Assinatura do operador", centro=True)
    return cp.salvar(caminho)


# =========================================================== relatório A4
def relatorio_tabela(caminho_ou_buffer, titulo, colunas, linhas, subtitulo="", totais=None):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    doc = SimpleDocTemplate(caminho_ou_buffer, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title=titulo)
    est = getSampleStyleSheet()
    cel = est["BodyText"].clone("cel", fontSize=7, leading=8.5)
    dados = [[Paragraph(f"<b>{c}</b>", cel) for c in colunas]]
    for l in linhas:
        dados.append([Paragraph(str("" if v is None else v).replace("&", "&amp;").replace("<", "&lt;"), cel) for v in l])
    if totais:
        dados.append([Paragraph(f"<b>{'' if v is None else v}</b>", cel) for v in totais])
    t = Table(dados, repeatRows=1)
    estilo = [("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecef")),
              ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if totais:
        estilo.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8f9fa")))
    t.setStyle(TableStyle(estilo))
    elementos = [Paragraph(titulo, est["Title"])]
    if subtitulo:
        elementos.append(Paragraph(subtitulo, est["Normal"]))
    elementos += [Spacer(1, 4 * mm), t]
    doc.build(elementos)


# ============================================== etiquetas (modelo provisório)
def etiquetas(buffer, itens, colunas=3, linhas=8):
    """itens: [{titulo, subtitulo, preco, preco_de, codigo, qr}] — A4, 3×8 (≈ 70×37 mm)."""
    c = canvas.Canvas(buffer, pagesize=A4)
    larg_p, alt_p = A4
    mx, my = 5 * mm, 10 * mm
    w, h = (larg_p - 2 * mx) / colunas, (alt_p - 2 * my) / linhas
    for i, it in enumerate(itens):
        pos = i % (colunas * linhas)
        if i and pos == 0:
            c.showPage()
        col, lin = pos % colunas, pos // colunas
        x, y = mx + col * w, alt_p - my - (lin + 1) * h
        c.setStrokeColor(colors.lightgrey)
        c.rect(x + 1, y + 1, w - 2, h - 2)
        tx = x + 3 * mm
        c.setFont(FONTE_B, 8)
        for j, parte in enumerate(simpleSplit(it.get("titulo", ""), FONTE_B, 8, w - 6 * mm)[:2]):
            c.drawString(tx, y + h - 5 * mm - j * 9, parte)
        if it.get("subtitulo"):
            c.setFont(FONTE, 6.5)
            c.drawString(tx, y + h - 13 * mm, it["subtitulo"][:60])
        if it.get("preco"):
            c.setFont(FONTE_B, 12)
            c.drawRightString(x + w - 3 * mm, y + h - 14 * mm, it["preco"])
            if it.get("preco_de"):
                c.setFont(FONTE, 7)
                c.drawRightString(x + w - 3 * mm, y + h - 17.5 * mm, f"de {it['preco_de']}")
        if it.get("qr"):
            lado = 18 * mm
            d = createBarcodeDrawing("QR", value=it["qr"], width=lado, height=lado, barBorder=0)
            renderPDF.draw(d, c, x + w - lado - 2 * mm, y + 2 * mm)
        if it.get("codigo"):
            d = desenho_codigo(it["codigo"], altura=9 * mm)
            if d:
                largura_max = w - 6 * mm - (20 * mm if it.get("qr") else 0)
                if d.width > largura_max:
                    esc = largura_max / d.width
                    d2 = Drawing(largura_max, d.height * esc)
                    d.scale(esc, esc)
                    d2.add(d)
                    d = d2
                renderPDF.draw(d, c, tx, y + 2 * mm)
    c.showPage()
    c.save()


def nome_arquivo(prefixo_id, data_hora):
    """VEN-000001 + 2026-10-21T14:35:12 -> ('2026/10', 'VEN-000001_2026-10-21_143512.pdf')."""
    d = data_hora[:10]
    hora = data_hora[11:19].replace(":", "")
    return f"{d[:4]}/{d[5:7]}", f"{prefixo_id}_{d}_{hora}.pdf"


def caminho_comprovante(data_dir, prefixo_id, data_hora):
    pasta, nome = nome_arquivo(prefixo_id, data_hora)
    rel = f"comprovantes/{pasta}/{nome}"
    return rel, os.path.join(data_dir, *rel.split("/"))

