"""
Aplica a página inicial pública (landing) ao projeto.
Uso (na pasta do projeto, onde está run.py):   python aplicar_landing.py
- "/"        -> página pública com login no menu lateral
- "/painel"  -> painel do sistema de gestão (antes era "/")
Pode ser executado mais de uma vez com segurança.
"""
import os
import re
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))


def ler(p):
    with open(os.path.join(RAIZ, p), encoding="utf-8") as f:
        return f.read()


def gravar(p, s):
    with open(os.path.join(RAIZ, p), "w", encoding="utf-8", newline="") as f:
        f.write(s)


for obrig in ("run.py", "app/__init__.py", "app/blueprints/main/__init__.py",
              "app/blueprints/site/__init__.py", "app/templates/site/landing.html"):
    if not os.path.exists(os.path.join(RAIZ, obrig)):
        sys.exit(f"ERRO: {obrig} não encontrado. Copie os arquivos do patch para a pasta do projeto (onde está run.py).")

# 1) Painel de gestão: "/" -> "/painel"
p = "app/blueprints/main/__init__.py"
s = ler(p)
if '@bp.route("/painel")' in s:
    print("= painel já está em /painel")
else:
    novo, n = re.subn(r'@bp\.route\(\s*["\']/["\']\s*\)', '@bp.route("/painel")', s, count=1)
    if not n:
        sys.exit(f"ERRO: rota '/' não encontrada em {p}.")
    gravar(p, novo)
    print("✔ painel movido para /painel")

# 2) Registrar o blueprint "site"
p = "app/__init__.py"
s = ler(p)
if "blueprints.site" in s:
    print("= blueprint site já registrado")
else:
    trecho = ("    from .blueprints.site import bp as site_bp\n"
              "    app.register_blueprint(site_bp)\n\n")
    idx = s.rfind("    return app")
    if idx == -1:
        sys.exit(f"ERRO: 'return app' não encontrado em {p}.")
    gravar(p, s[:idx] + trecho + s[idx:])
    print("✔ blueprint site registrado")

# 3) Ao sair, voltar para a página pública
p = "app/blueprints/auth/__init__.py"
if os.path.exists(os.path.join(RAIZ, p)):
    s = ler(p)
    i = s.find("def logout(")
    if i != -1:
        j = s.find('redirect(url_for("auth.login"))', i)
        k = s.find("\n@bp.route", i)
        if j != -1 and (k == -1 or j < k):
            s = s[:j] + 'redirect(url_for("site.landing"))' + s[j + len('redirect(url_for("auth.login"))'):]
            gravar(p, s)
            print("✔ logout redireciona para a página pública")
        else:
            print("= logout já ajustado")

print("Concluído. Reinicie: python run.py  ->  http://127.0.0.1:5000  (gestão em /painel)")
