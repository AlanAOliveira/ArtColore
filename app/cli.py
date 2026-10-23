"""
Seed, dados de demonstração, backup e restore.
  python run.py             -> seed automático + backup diário + servidor
  python run.py --reset     -> apaga os dados e recria tudo
  python run.py --backup    -> gera backup e sai
  python -m flask --app run restore ARQUIVO.zip
"""
import glob
import json
import os
import shutil
import zipfile
from datetime import date, datetime, timedelta

import click
from werkzeug.security import generate_password_hash

from .permissoes import GRUPOS_PADRAO, TODAS
from .utils import agora_iso, gerar_ean13_interno, hoje_iso

TIPOS_MATERIAL = [
    ("Papelaria", "produto"), ("Presentes", "produto"), ("Eletrônicos", "produto"),
    ("Brinquedos", "produto"), ("Aviamentos", "produto"), ("Cosméticos", "produto"),
    ("Personalizados", "servico"),
]

CONFIG_PADRAO = {
    "empresa": {"nome_fantasia": "ArtColore Papelaria", "razao_social": "", "cnpj": "", "endereco": "", "telefone": ""},
    "prazo_devolucao_dias": 7,
    "alertas": {"dias_vencimento": 30, "fator_sugestao_compra": 2},
    "pdv": {"permitir_saldo_negativo": False},
}


def carregar_usuarios_padrao(app):
    caminho = app.config["USUARIOS_PADRAO_FILE"]
    if not os.path.exists(caminho):
        print(f"⚠ {caminho} não encontrado — nenhum usuário padrão criado.")
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f).get("usuarios", [])


def executar_seed(app, reset=False, echo=print):
    """Cria configuração, grupos e tipos; cria/ATUALIZA os usuários de usuarios_padrao.json."""
    repos = app.repos
    data_dir = app.config["DATA_DIR"]

    if reset:
        for f in glob.glob(os.path.join(data_dir, "*.json")):
            os.remove(f)
        for pasta in ("uploads", "movimentacoes", "comprovantes"):
            shutil.rmtree(os.path.join(data_dir, pasta), ignore_errors=True)
        echo("Dados apagados.")

    cfg = repos.configuracao.obter()
    if not cfg:
        repos.configuracao.salvar(CONFIG_PADRAO)
        echo("✔ configuracao.json")
    else:  # completa chaves novas
        alterou = False
        for k, v in CONFIG_PADRAO.items():
            if k not in cfg:
                cfg[k], alterou = v, True
        if alterou:
            repos.configuracao.salvar(cfg)

    ids_grupo = {}
    for nome, g in GRUPOS_PADRAO.items():
        existente = repos.grupos.obter_por("nome", nome)
        if existente:
            ids_grupo[nome] = existente["id"]
            if nome == "Administrador" and set(existente.get("permissoes", [])) != set(TODAS):
                repos.grupos.atualizar(existente["id"], {"permissoes": TODAS, "ativo": True})
                echo("✔ Administrador: permissões sincronizadas")
            continue
        novo = repos.grupos.adicionar({
            "nome": nome, "limite_desconto": g["limite_desconto"],
            "permissoes": g["permissoes"], "ativo": True, "criado_em": agora_iso(),
        })
        ids_grupo[nome] = novo["id"]
        echo(f"✔ grupo {nome}")

    for u in carregar_usuarios_padrao(app):
        login = str(u.get("login", "")).strip().lower()
        grupo = u.get("grupo", "Consulta")
        if not login or grupo not in ids_grupo:
            echo(f"⚠ usuário ignorado (login vazio ou grupo inválido): {u}")
            continue
        dados = {
            "nome": u.get("nome", login),
            "senha_hash": generate_password_hash(str(u.get("senha", ""))),
            "grupos": [ids_grupo[grupo]],
            "ativo": True, "bloqueado": False, "tentativas_falhas": 0, "trocar_senha": False,
        }
        existente = repos.usuarios.obter_por("login", login)
        if existente:
            repos.usuarios.atualizar(existente["id"], dados)
        else:
            repos.usuarios.adicionar({**dados, "login": login, "ultimo_acesso": None, "criado_em": agora_iso()})
            echo(f"✔ usuário {login}")
    echo("✔ usuários padrão sincronizados (usuarios_padrao.json)")

    if not repos.tipos.listar():
        repos.tipos.substituir_todos([
            {"id": f"TIP-{i:03d}", "nome": n, "natureza": nat, "ativo": True}
            for i, (n, nat) in enumerate(TIPOS_MATERIAL, start=1)])
        echo("✔ tipos_material.json")

    if not repos.materiais.listar():
        executar_seed_demo(app, echo)


# ---------------------------------------------------------------- dados demo
def _cpf_valido(base9):
    d = [int(x) for x in base9]
    for i in (9, 10):
        soma = sum(d[j] * ((i + 1) - j) for j in range(i))
        d.append((soma * 10) % 11 % 10)
    return "".join(map(str, d))


def executar_seed_demo(app, echo=print):
    """Localizações, fornecedores, clientes, produtos (com estoque inicial), serviços e uma promoção."""
    from .services.estoque import operacao
    from .services.precos import calcular_preco_venda

    repos = app.repos
    agora = agora_iso()
    tipos = {t["nome"]: t["id"] for t in repos.tipos.listar()}

    locs = {}
    for area, c, p, n, desc in [("LOJA", "1", "1", "1", "Cadernos e agendas"), ("LOJA", "1", "2", "1", "Canetas e lápis"),
                                ("LOJA", "2", "1", "1", "Presentes e brinquedos"), ("LOJA", "2", "2", "1", "Aviamentos"),
                                ("DEP", "1", "1", "1", "Estoque geral")]:
        codigo = f"{area}-C{c.zfill(2)}-P{p.zfill(2)}-N{n}"
        locs[desc] = repos.localizacoes.adicionar({"area": area, "corredor": c, "prateleira": p, "nivel": n,
                                                   "codigo": codigo, "descricao": desc, "ativo": True, "criado_em": agora})["id"]

    forn = {}
    for nome, fant, cnpj, contato, tel, prazo in [
        ("Distribuidora Papel Forte Ltda", "Papel Forte", "11222333000181", "Marcos", "1532221000", 5),
        ("Atacado Escolar Sorocaba Ltda", "Atacado Escolar", "45997418000153", "Juliana", "15998887777", 3),
        ("Armarinho Linha & Cia ME", "Linha & Cia", "", "Rosa", "15997776666", 7)]:
        forn[fant] = repos.fornecedores.adicionar({
            "razao_social": nome, "fantasia": fant, "documento": cnpj, "contato_nome": contato,
            "telefone": tel, "whatsapp": tel, "email": "", "endereco": "", "observacoes": "",
            "prazo_entrega": prazo, "ativo": True, "criado_em": agora})["id"]

    for base, nome, tel in [("529982247", "Maria da Silva", "15991234567"), ("123456789", "João Pereira", "15988887777"),
                            ("987654321", "Ana Souza", "1532345678")]:
        repos.clientes.adicionar({"cpf": _cpf_valido(base), "nome": nome, "telefone": tel, "criado_em": agora, "criado_por": None})

    def ean():
        return gerar_ean13_interno(int(repos.store.proximo_id("EAN").split("-")[1]))

    produtos = [
        # nome, tipo, unidade, fator, mínimo, fornecedor, compra (centavos), comprado por, markup %, localização, estoque inicial
        ("Caderno universitário 10 matérias", "Papelaria", "unidade", 1, 10, "Papel Forte", 1890, "item", 80, "Cadernos e agendas", 25),
        ("Caneta esferográfica azul", "Papelaria", "unidade", 50, 60, "Atacado Escolar", 4500, "caixa", 120, "Canetas e lápis", 137),
        ("Lápis preto nº 2", "Papelaria", "unidade", 72, 72, "Atacado Escolar", 5040, "caixa", 100, "Canetas e lápis", 40),
        ("Fita de cetim 10 mm vermelha", "Aviamentos", "metro", 1, 20, "Linha & Cia", 35, "item", 150, "Aviamentos", 100),
        ("Elástico chato 15 mm", "Aviamentos", "metro", 1, 30, "Linha & Cia", 42, "item", 140, "Aviamentos", 12.5),
        ("Caneca personalizada branca", "Presentes", "unidade", 1, 5, "Papel Forte", 1200, "item", 100, "Presentes e brinquedos", 8),
        ("Quebra-cabeça 500 peças", "Brinquedos", "unidade", 1, 3, "Atacado Escolar", 3500, "item", 70, "Presentes e brinquedos", 0),
        ("Fone de ouvido com fio", "Eletrônicos", "unidade", 1, 4, "Atacado Escolar", 1500, "item", 90, "Estoque geral", 10),
        ("Hidratante labial", "Cosméticos", "unidade", 12, 12, "Papel Forte", 3600, "caixa", 110, "Estoque geral", 24),
    ]
    ids = []
    for nome, tipo, un, fator, minimo, f, preco, por, mk, loc, _ in produtos:
        m = {"nome": nome, "descricao": "", "tipo_id": tipos.get(tipo), "natureza": "produto", "codigo_barras": ean(),
             "ativo": True, "fiscal": {}, "unidade": un, "fator_caixa": fator, "estoque_minimo": minimo,
             "controla_validade": tipo == "Cosméticos", "localizacao_padrao_id": locs.get(loc),
             "regra_preco": "markup", "valor_regra": mk, "base_preco": "principal",
             "fornecedores": [{"fornecedor_id": forn[f], "preco_compra": preco, "unidade_compra": por,
                               "principal": True, "atualizado_em": agora}],
             "preco_venda_caixa": None, "preco_referencia": None, "imagens": [], "custo_medio": None,
             "criado_em": agora, "criado_por": None}
        m["preco_venda"] = calcular_preco_venda(m)
        ids.append(repos.materiais.adicionar(m, prefixo="MAT")["id"])

    for nome, desc, ref in [("Montagem", "Montagem de kits, caixas e presentes", 1000),
                            ("Impressão", "Impressão de documentos, fotos e artes", 150),
                            ("Formatação", "Formatação de documentos e trabalhos", 2000),
                            ("Encadernação", "Espiral, capa dura ou térmica", 800)]:
        repos.materiais.adicionar({
            "nome": nome, "descricao": desc, "tipo_id": tipos.get("Personalizados"), "natureza": "servico",
            "codigo_barras": ean(), "ativo": True, "fiscal": {}, "unidade": "unidade", "fator_caixa": 1,
            "estoque_minimo": None, "controla_validade": False, "localizacao_padrao_id": None,
            "regra_preco": "manual", "base_preco": "principal", "valor_regra": None, "preco_venda": None,
            "preco_venda_caixa": None, "preco_referencia": ref, "fornecedores": [], "imagens": [],
            "criado_em": agora, "criado_por": None}, prefixo="SRV")

    # Estoque inicial (movimentações de Compra) — inclui um lote a vencer e um vencido
    hoje = date.fromisoformat(hoje_iso())
    with operacao(repos, None) as op:
        for mid, (nome, tipo, un, fator, minimo, f, preco, por, mk, loc, inicial) in zip(ids, produtos):
            if not inicial:
                continue
            custo = preco // fator if por == "caixa" else preco
            if tipo == "Cosméticos":
                op.entrada(mid, 12, locs[loc], "compra", validade=(hoje + timedelta(days=20)).isoformat(),
                           valor_unitario=custo, atualizar_custo=True, fornecedor_id=forn[f], documento="NF demo")
                op.entrada(mid, inicial - 12, locs[loc], "compra", validade=(hoje + timedelta(days=200)).isoformat(),
                           valor_unitario=custo, atualizar_custo=True, fornecedor_id=forn[f], documento="NF demo")
                continue
            op.entrada(mid, inicial, locs[loc], "compra", valor_unitario=custo, atualizar_custo=True,
                       fornecedor_id=forn[f], documento="NF demo")
        op.entrada(ids[1], 50, locs["Estoque geral"], "compra", valor_unitario=90, atualizar_custo=True,
                   fornecedor_id=forn["Atacado Escolar"], documento="NF demo")

    repos.promocoes.adicionar({"material_id": ids[0], "tipo": "percentual", "valor": 10,
                               "inicio": hoje.isoformat(), "fim": (hoje + timedelta(days=15)).isoformat(),
                               "ativo": True, "descricao": "Volta às aulas", "criado_em": agora, "criado_por": None})
    echo("✔ dados de demonstração (localizações, fornecedores, clientes, produtos com estoque, serviços, promoção)")


# ------------------------------------------------------------------- backup
def executar_backup(app):
    data_dir = app.config["DATA_DIR"]
    destino = os.path.join(data_dir, "backups")
    os.makedirs(destino, exist_ok=True)
    caminho = os.path.join(destino, f"backup_{datetime.now():%Y-%m-%d_%H%M%S}.zip")
    with app.repos.store.transacao():
        with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
            for raiz, dirs, arquivos in os.walk(data_dir):
                dirs[:] = [d for d in dirs if d not in ("backups", ".locks")]
                for a in arquivos:
                    if not a.endswith(".tmp"):
                        completo = os.path.join(raiz, a)
                        z.write(completo, os.path.relpath(completo, data_dir))
    manter = app.config.get("BACKUPS_MANTER", 30)
    antigos = sorted(glob.glob(os.path.join(destino, "backup_*.zip")))[:-manter]
    for a in antigos:
        os.remove(a)
    return caminho


def backup_diario(app):
    """Cria um backup por dia (na inicialização), mantendo os últimos N."""
    destino = os.path.join(app.config["DATA_DIR"], "backups")
    if glob.glob(os.path.join(destino, f"backup_{date.today():%Y-%m-%d}_*.zip")):
        return None
    caminho = executar_backup(app)
    print("✔ backup diário:", caminho)
    return caminho


def restaurar_backup(app, arquivo):
    data_dir = app.config["DATA_DIR"]
    with app.repos.store.transacao():
        for item in os.listdir(data_dir):
            if item in ("backups", ".locks", ".gitkeep"):
                continue
            p = os.path.join(data_dir, item)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        with zipfile.ZipFile(arquivo) as z:
            z.extractall(data_dir)


def registrar(app):
    @app.cli.command("seed")
    @click.option("--reset", is_flag=True)
    def seed(reset):
        executar_seed(app, reset=reset, echo=click.echo)

    @app.cli.command("backup")
    def backup():
        click.echo(f"Backup criado: {executar_backup(app)}")

    @app.cli.command("restore")
    @click.argument("arquivo")
    def restore(arquivo):
        if not os.path.exists(arquivo):
            arquivo = os.path.join(app.config["DATA_DIR"], "backups", arquivo)
        if not os.path.exists(arquivo):
            raise click.ClickException("Arquivo de backup não encontrado.")
        restaurar_backup(app, arquivo)
        click.echo("Restauração concluída.")
