"""Upload de imagens com compressão (seção 4.2): máx. 1024 px, JPEG ~200 KB."""
import os
import uuid

EXTENSOES = {".jpg", ".jpeg", ".png", ".webp"}
LADO_MAX = 1024

try:
    from PIL import Image, ImageOps
    PIL_OK = True
except ImportError:
    PIL_OK = False


def pasta_material(data_dir, material_id):
    p = os.path.join(data_dir, "uploads", "materiais", material_id)
    os.makedirs(p, exist_ok=True)
    return p


def salvar_imagem(arquivo, data_dir, material_id):
    nome_original = arquivo.filename or ""
    ext = os.path.splitext(nome_original)[1].lower()
    if ext not in EXTENSOES:
        raise ValueError(f"{nome_original}: formato não suportado (use JPG, PNG ou WEBP).")
    pasta = pasta_material(data_dir, material_id)
    if PIL_OK:
        try:
            img = ImageOps.exif_transpose(Image.open(arquivo.stream))
            if img.mode != "RGB":
                fundo = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGBA")
                    fundo.paste(img, mask=img.split()[-1])
                else:
                    fundo.paste(img.convert("RGB"))
                img = fundo
            img.thumbnail((LADO_MAX, LADO_MAX))
            nome = f"{uuid.uuid4().hex[:12]}.jpg"
            caminho = os.path.join(pasta, nome)
            qualidade = 85
            while True:
                img.save(caminho, "JPEG", quality=qualidade, optimize=True)
                if os.path.getsize(caminho) <= 200 * 1024 or qualidade <= 50:
                    break
                qualidade -= 10
            return nome
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"{nome_original}: não foi possível processar a imagem ({e}).")
    nome = f"{uuid.uuid4().hex[:12]}{ext}"
    arquivo.save(os.path.join(pasta, nome))
    return nome


def remover_imagem(data_dir, material_id, nome):
    caminho = os.path.join(pasta_material(data_dir, material_id), os.path.basename(nome))
    if os.path.exists(caminho):
        os.remove(caminho)
