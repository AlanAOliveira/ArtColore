from flask import current_app, has_request_context, request
from flask_login import current_user

from .utils import agora_iso


def registrar(acao, entidade="", entidade_id="", antes=None, depois=None, usuario_id=None, detalhe=None):
    def limpar(d):
        return {k: v for k, v in d.items() if k != "senha_hash"} if isinstance(d, dict) else d

    if usuario_id is None and has_request_context() and current_user.is_authenticated:
        usuario_id = current_user.id
    current_app.repos.auditoria.adicionar({
        "usuario_id": usuario_id, "acao": acao, "entidade": entidade, "entidade_id": entidade_id,
        "antes": limpar(antes), "depois": limpar(depois), "detalhe": detalhe,
        "ip": request.remote_addr if has_request_context() else None,
        "data_hora": agora_iso(),
    })
