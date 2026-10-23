import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")


# ------------------------------------------------------------------ datas
def agora():
    return datetime.now(TZ)


def agora_iso():
    return agora().isoformat(timespec="seconds")


def hoje_iso():
    return agora().date().isoformat()


def dias_atras_iso(n):
    return (agora().date() - timedelta(days=n)).isoformat()


def primeiro_dia_mes_iso():
    return agora().date().replace(day=1).isoformat()


def parse_data(texto):
    """'2026-10-21' ou '21/10/2026' -> 'AAAA-MM-DD' ou None."""
    t = (texto or "").strip()
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Data inválida: {texto}")


def formatar_data_hora(iso):
    if not iso or type(iso).__name__.endswith("Undefined"):
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def formatar_data(iso):
    if not iso or type(iso).__name__.endswith("Undefined"):
        return "—"
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def dias_entre(inicio_iso, fim_iso):
    return (date.fromisoformat(fim_iso[:10]) - date.fromisoformat(inicio_iso[:10])).days


# ---------------------------------------------------------------- números
def _vazio(v):
    """None, '' ou jinja2.Undefined (campo inexistente no template)."""
    return v is None or v == "" or type(v).__name__.endswith("Undefined")


def _para_decimal(texto):
    """Aceita '1.234,56', '1234,56', '1234.56', 'R$ 12,50'."""
    if texto is None:
        return None
    t = str(texto).strip().replace("R$", "").replace(" ", "").replace("%", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ValueError(f"Valor inválido: {texto}")


def parse_moeda(texto):
    """'12,50' -> 1250 (centavos). Vazio -> None."""
    d = _para_decimal(texto)
    if d is None:
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_decimal(texto, casas=3):
    d = _para_decimal(texto)
    if d is None:
        return None
    return float(round(d, casas))


def arred(valor):
    """Arredonda centavos (float/Decimal) para int, meio para cima."""
    return int(Decimal(str(valor)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def formatar_moeda(centavos):
    if _vazio(centavos):
        return "—"
    texto = f"{abs(int(centavos)) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if int(centavos) < 0 else ''}R$ {texto}"


def moeda_input(centavos):
    if _vazio(centavos):
        return ""
    return f"{int(centavos) / 100:.2f}".replace(".", ",")


def formatar_numero(valor, casas=3):
    """2.5 -> '2,5'; 3.0 -> '3'; 1234.5 -> '1.234,5'."""
    if _vazio(valor):
        return ""
    v = round(float(valor), casas)
    texto = f"{v:,.{casas}f}"
    if casas:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_qtd(qtd, material=None):
    """Quantidade com unidade; caixa: '2 cx + 37 un'."""
    if _vazio(qtd):
        return "—"
    if material and material.get("unidade") == "metro":
        return f"{formatar_numero(qtd)} m"
    fator = (material or {}).get("fator_caixa") or 1
    q = int(round(qtd))
    if fator > 1 and abs(q) >= fator:
        cx, un = divmod(abs(q), fator)
        sinal = "-" if q < 0 else ""
        return f"{sinal}{cx} cx" + (f" + {un} un" if un else "") + f" ({q} un)"
    return f"{q} un"


# ---------------------------------------------------------- documentos
def so_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def validar_cpf(cpf):
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(c[j]) * ((i + 1) - j) for j in range(i))
        if (soma * 10) % 11 % 10 != int(c[i]):
            return False
    return True


def validar_cnpj(cnpj):
    c = so_digitos(cnpj)
    if len(c) != 14 or c == c[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(c[i]) * pesos[i] for i in range(pos))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if dv != int(c[pos]):
            return False
    return True


def formatar_cpf(cpf):
    c = so_digitos(cpf)
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}" if len(c) == 11 else (cpf or "")


def mascarar_cpf(cpf):
    c = so_digitos(cpf)
    return f"***.{c[3:6]}.{c[6:9]}-**" if len(c) == 11 else "—"


def formatar_documento(doc):
    c = so_digitos(doc)
    if len(c) == 14:
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    return formatar_cpf(c) if len(c) == 11 else (doc or "")


def formatar_telefone(tel):
    t = so_digitos(tel)
    if len(t) == 11:
        return f"({t[:2]}) {t[2:7]}-{t[7:]}"
    if len(t) == 10:
        return f"({t[:2]}) {t[2:6]}-{t[6:]}"
    return tel or ""


def mascarar_telefone(tel):
    t = so_digitos(tel)
    return f"({t[:2]}) *****-{t[-4:]}" if len(t) >= 10 else "—"


def normalizar(texto):
    """Minúsculas e sem acentos — para buscas."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return t.lower().strip()


# ------------------------------------------------------------ códigos
def ean13_digito(doze):
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(doze))
    return str((10 - soma % 10) % 10)


def validar_ean13(codigo):
    c = so_digitos(codigo)
    return len(c) == 13 and c == codigo and ean13_digito(c[:12]) == c[12]


def gerar_ean13_interno(sequencial):
    """Prefixo 2 (uso interno) + 11 dígitos + dígito verificador."""
    doze = f"2{int(sequencial):011d}"
    return doze + ean13_digito(doze)
