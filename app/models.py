from flask import current_app
from flask_login import UserMixin


class Usuario(UserMixin):
    def __init__(self, dados):
        self.dados = dados
        self.id = dados["id"]
        self.nome = dados.get("nome", "")
        self.login = dados.get("login", "")
        self.ativo = dados.get("ativo", True)
        self.trocar_senha = dados.get("trocar_senha", False)
        self._permissoes = None
        self._limite_desconto = None

    @property
    def is_active(self):
        return self.ativo and not self.dados.get("bloqueado", False)

    def _carregar_grupos(self):
        perms, limite = set(), 0
        for gid in self.dados.get("grupos", []):
            g = current_app.repos.grupos.obter(gid)
            if g and g.get("ativo", True):
                perms.update(g.get("permissoes", []))
                limite = max(limite, int(g.get("limite_desconto", 0)))
        self._permissoes, self._limite_desconto = perms, limite

    @property
    def permissoes(self):
        if self._permissoes is None:
            self._carregar_grupos()
        return self._permissoes

    @property
    def limite_desconto(self):
        if self._limite_desconto is None:
            self._carregar_grupos()
        return self._limite_desconto

    def tem(self, *codigos):
        """tem('a') ou tem('a', 'b') ou tem(('a','b')) -> True se possuir qualquer uma."""
        lista = []
        for c in codigos:
            lista.extend(c if isinstance(c, (list, tuple)) else [c])
        return any(c in self.permissoes for c in lista)
