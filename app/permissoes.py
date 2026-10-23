"""Catálogo de permissões, grupos padrão, decorator e autorização de supervisor (seção 11)."""
from functools import wraps

from flask import abort, current_app
from flask_login import current_user
from werkzeug.security import check_password_hash

PERMISSOES = {
    "usuarios.visualizar": ("Administração", "Visualizar usuários"),
    "usuarios.gerenciar": ("Administração", "Criar/editar/inativar usuários"),
    "grupos.visualizar": ("Administração", "Visualizar grupos"),
    "grupos.gerenciar": ("Administração", "Criar/editar grupos e permissões"),
    "auditoria.visualizar": ("Administração", "Visualizar auditoria"),
    "configuracao.gerenciar": ("Administração", "Configurações e backups"),
    "materiais.visualizar": ("Cadastros", "Visualizar materiais e serviços"),
    "materiais.gerenciar": ("Cadastros", "Criar/editar materiais e serviços"),
    "fornecedores.visualizar": ("Cadastros", "Visualizar fornecedores"),
    "fornecedores.gerenciar": ("Cadastros", "Criar/editar fornecedores"),
    "clientes.visualizar": ("Cadastros", "Visualizar clientes"),
    "clientes.gerenciar": ("Cadastros", "Criar/editar clientes"),
    "clientes.ver_dados_completos": ("Cadastros", "Ver CPF/telefone sem máscara"),
    "clientes.ajustar_credito": ("Cadastros", "Ajustar crédito de cliente"),
    "localizacoes.gerenciar": ("Cadastros", "Criar/editar localizações"),
    "estoque.visualizar": ("Estoque", "Consultar estoque e movimentações"),
    "estoque.compra": ("Estoque", "Lançar compra"),
    "estoque.transferencia": ("Estoque", "Transferir entre localizações"),
    "estoque.sucata": ("Estoque", "Lançar sucata/descarte"),
    "estoque.doacao": ("Estoque", "Lançar doação"),
    "estoque.ajuste": ("Estoque", "Ajuste de inventário / inventários"),
    "pdv.vender": ("PDV", "Operar caixa e vender"),
    "pdv.desconto_acima_limite": ("PDV", "Desconto acima do limite do grupo"),
    "pdv.cancelar_venda": ("PDV", "Cancelar venda finalizada"),
    "pdv.devolucao": ("PDV", "Devolução"),
    "pdv.troca": ("PDV", "Troca"),
    "pdv.devolucao_fora_prazo": ("PDV", "Devolução/troca fora do prazo"),
    "precos.gerenciar": ("Preços", "Promoções e preços"),
    "precos.reajuste_massa": ("Preços", "Reajuste em massa"),
    "relatorios.visualizar": ("Relatórios", "Painel e relatórios"),
}
TODAS = list(PERMISSOES)

GRUPOS_PADRAO = {
    "Administrador": {"limite_desconto": 100, "permissoes": TODAS},
    "Gerente": {"limite_desconto": 30, "permissoes": [p for p in TODAS if p not in ("usuarios.gerenciar", "grupos.gerenciar", "configuracao.gerenciar")]},
    "Estoquista": {"limite_desconto": 0, "permissoes": [
        "materiais.visualizar", "fornecedores.visualizar", "localizacoes.gerenciar", "estoque.visualizar",
        "estoque.compra", "estoque.transferencia", "estoque.sucata", "estoque.doacao", "estoque.ajuste"]},
    "Vendedor / Caixa": {"limite_desconto": 5, "permissoes": [
        "materiais.visualizar", "clientes.visualizar", "clientes.gerenciar", "estoque.visualizar",
        "pdv.vender", "pdv.devolucao", "pdv.troca"]},
    "Consulta": {"limite_desconto": 0, "permissoes": [
        p for p in TODAS if p.endswith(".visualizar") and not p.startswith(("usuarios", "grupos", "auditoria"))]},
}


def permissoes_por_modulo():
    modulos = {}
    for codigo, (modulo, desc) in PERMISSOES.items():
        modulos.setdefault(modulo, []).append((codigo, desc))
    return modulos


def requer_permissao(*codigos):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.tem(*codigos):
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def permissoes_do_usuario(dados):
    perms = set()
    for gid in dados.get("grupos", []):
        g = current_app.repos.grupos.obter(gid)
        if g and g.get("ativo", True):
            perms.update(g.get("permissoes", []))
    return perms


def autorizar(permissao, supervisor=None):
    """Retorna o id de quem autoriza (usuário atual ou supervisor) ou None.
    supervisor = {"login": ..., "senha": ...} (senha de gerente no PDV)."""
    if current_user.tem(permissao):
        return current_user.id
    if supervisor and supervisor.get("login"):
        dados = current_app.repos.usuarios.obter_por("login", str(supervisor["login"]).strip().lower())
        if (dados and dados.get("ativo", True) and not dados.get("bloqueado")
                and check_password_hash(dados["senha_hash"], str(supervisor.get("senha", "")))
                and permissao in permissoes_do_usuario(dados)):
            return dados["id"]
    return None
