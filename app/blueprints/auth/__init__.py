from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ...auditoria import registrar
from ...models import Usuario
from ...permissoes import permissoes_por_modulo, requer_permissao
from ...utils import agora_iso

bp = Blueprint("auth", __name__)
SENHA_MIN = 1  # modo de teste


@bp.before_app_request
def forcar_troca_senha():
    if (current_user.is_authenticated and current_user.trocar_senha
            and request.endpoint not in ("auth.trocar_senha", "auth.logout", "static")):
        flash("Defina uma nova senha para continuar.", "warning")
        return redirect(url_for("auth.trocar_senha"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        repo = current_app.repos.usuarios
        login_txt = request.form.get("login", "").strip().lower()
        senha = request.form.get("senha", "")
        dados = repo.obter_por("login", login_txt)
        if not dados or not dados.get("ativo", True):
            flash("Usuário ou senha inválidos.", "danger")
            return render_template("auth/login.html", login=login_txt)
        if dados.get("bloqueado"):
            flash("Usuário bloqueado. Reinicie o sistema (python run.py) ou peça ao administrador para redefinir.", "danger")
            return render_template("auth/login.html", login=login_txt)
        if not check_password_hash(dados["senha_hash"], senha):
            tentativas = int(dados.get("tentativas_falhas", 0)) + 1
            bloquear = tentativas >= current_app.config["MAX_LOGIN_ATTEMPTS"]
            repo.atualizar(dados["id"], {"tentativas_falhas": tentativas, "bloqueado": bloquear})
            registrar("login_falha", "usuario", dados["id"], usuario_id=dados["id"])
            flash("Usuário bloqueado." if bloquear else "Usuário ou senha inválidos.", "danger")
            return render_template("auth/login.html", login=login_txt)
        dados = repo.atualizar(dados["id"], {"tentativas_falhas": 0, "ultimo_acesso": agora_iso()})
        session.permanent = True
        login_user(Usuario(dados))
        registrar("login", "usuario", dados["id"])
        session["mostrar_alertas"] = True
        destino = request.args.get("next")
        return redirect(destino if destino and destino.startswith("/") else url_for("main.index"))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    registrar("logout", "usuario", current_user.id)
    logout_user()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("site.landing"))


@bp.route("/trocar-senha", methods=["GET", "POST"])
@login_required
def trocar_senha():
    if request.method == "POST":
        atual, nova, confirmar = (request.form.get(k, "") for k in ("senha_atual", "nova_senha", "confirmar_senha"))
        dados = current_app.repos.usuarios.obter(current_user.id)
        if not check_password_hash(dados["senha_hash"], atual):
            flash("Senha atual incorreta.", "danger")
        elif len(nova) < SENHA_MIN:
            flash("Informe a nova senha.", "danger")
        elif nova != confirmar:
            flash("A confirmação não confere.", "danger")
        else:
            current_app.repos.usuarios.atualizar(current_user.id, {"senha_hash": generate_password_hash(nova), "trocar_senha": False})
            registrar("senha_alterada", "usuario", current_user.id)
            flash("Senha alterada. Obs.: usuários de usuarios_padrao.json voltam à senha do arquivo ao reiniciar.", "success")
            return redirect(url_for("main.index"))
    return render_template("auth/trocar_senha.html")


@bp.route("/usuarios")
@login_required
@requer_permissao("usuarios.visualizar")
def usuarios():
    lista = sorted(current_app.repos.usuarios.listar(), key=lambda u: u["nome"].lower())
    grupos = {g["id"]: g["nome"] for g in current_app.repos.grupos.listar()}
    return render_template("auth/usuarios.html", usuarios=lista, grupos=grupos)


@bp.route("/usuarios/novo", methods=["GET", "POST"])
@bp.route("/usuarios/<id_>/editar", methods=["GET", "POST"])
@login_required
@requer_permissao("usuarios.gerenciar")
def usuario_form(id_=None):
    repo = current_app.repos.usuarios
    usuario = repo.obter(id_) if id_ else None
    if id_ and not usuario:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("auth.usuarios"))
    grupos = [g for g in current_app.repos.grupos.listar() if g.get("ativo", True)]
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        login_txt = request.form.get("login", "").strip().lower()
        senha = request.form.get("senha", "")
        grupos_sel = request.form.getlist("grupos")
        ativo = request.form.get("ativo") == "on"
        erros = []
        if not nome:
            erros.append("Informe o nome.")
        if not login_txt:
            erros.append("Informe o login.")
        existente = repo.obter_por("login", login_txt)
        if existente and existente["id"] != id_:
            erros.append("Login já utilizado por outro usuário.")
        if not grupos_sel:
            erros.append("Selecione ao menos um grupo.")
        if not id_ and len(senha) < SENHA_MIN:
            erros.append("Informe a senha inicial.")
        if id_ == current_user.id and not ativo:
            erros.append("Você não pode inativar o próprio usuário.")
        if erros:
            for e in erros:
                flash(e, "danger")
            form = {"nome": nome, "login": login_txt, "grupos": grupos_sel, "ativo": ativo}
            return render_template("auth/usuario_form.html", usuario=usuario, form=form, grupos=grupos)
        if usuario:
            novo = repo.atualizar(id_, {"nome": nome, "login": login_txt, "grupos": grupos_sel, "ativo": ativo})
            registrar("usuario_editado", "usuario", id_, antes=usuario, depois=novo)
            flash("Usuário atualizado.", "success")
        else:
            novo = repo.adicionar({
                "nome": nome, "login": login_txt, "senha_hash": generate_password_hash(senha),
                "grupos": grupos_sel, "ativo": ativo, "bloqueado": False, "tentativas_falhas": 0,
                "trocar_senha": False, "ultimo_acesso": None, "criado_em": agora_iso(),
            })
            registrar("usuario_criado", "usuario", novo["id"], depois=novo)
            flash("Usuário criado.", "success")
        return redirect(url_for("auth.usuarios"))
    form = usuario or {"nome": "", "login": "", "grupos": [], "ativo": True}
    return render_template("auth/usuario_form.html", usuario=usuario, form=form, grupos=grupos)


@bp.route("/usuarios/<id_>/resetar-senha", methods=["POST"])
@login_required
@requer_permissao("usuarios.gerenciar")
def usuario_resetar_senha(id_):
    nova = request.form.get("nova_senha", "")
    if len(nova) < SENHA_MIN:
        flash("Informe a nova senha.", "danger")
        return redirect(url_for("auth.usuario_form", id_=id_))
    current_app.repos.usuarios.atualizar(id_, {
        "senha_hash": generate_password_hash(nova), "bloqueado": False, "tentativas_falhas": 0})
    registrar("senha_resetada", "usuario", id_)
    flash("Senha redefinida e usuário desbloqueado.", "success")
    return redirect(url_for("auth.usuarios"))


@bp.route("/grupos")
@login_required
@requer_permissao("grupos.visualizar")
def grupos():
    lista = sorted(current_app.repos.grupos.listar(), key=lambda g: g["nome"].lower())
    usuarios = current_app.repos.usuarios.listar()
    contagem = {g["id"]: sum(1 for u in usuarios if g["id"] in u.get("grupos", [])) for g in lista}
    return render_template("auth/grupos.html", grupos=lista, contagem=contagem)


@bp.route("/grupos/novo", methods=["GET", "POST"])
@bp.route("/grupos/<id_>/editar", methods=["GET", "POST"])
@login_required
@requer_permissao("grupos.gerenciar")
def grupo_form(id_=None):
    repo = current_app.repos.grupos
    grupo = repo.obter(id_) if id_ else None
    if id_ and not grupo:
        flash("Grupo não encontrado.", "danger")
        return redirect(url_for("auth.grupos"))
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        perms = request.form.getlist("permissoes")
        ativo = request.form.get("ativo") == "on"
        try:
            limite = int(request.form.get("limite_desconto", "0") or 0)
        except ValueError:
            limite = -1
        erros = []
        if not nome:
            erros.append("Informe o nome do grupo.")
        existente = repo.obter_por("nome", nome)
        if existente and existente["id"] != id_:
            erros.append("Já existe um grupo com esse nome.")
        if not 0 <= limite <= 100:
            erros.append("Limite de desconto deve estar entre 0 e 100%.")
        if grupo and grupo["nome"] == "Administrador" and (not ativo or "grupos.gerenciar" not in perms):
            erros.append("O grupo Administrador deve permanecer ativo e com a permissão de gerenciar grupos.")
        if erros:
            for e in erros:
                flash(e, "danger")
            form = {"nome": nome, "limite_desconto": limite, "permissoes": perms, "ativo": ativo}
            return render_template("auth/grupo_form.html", grupo=grupo, form=form, modulos=permissoes_por_modulo())
        dados = {"nome": nome, "limite_desconto": limite, "permissoes": perms, "ativo": ativo}
        if grupo:
            novo = repo.atualizar(id_, dados)
            registrar("grupo_editado", "grupo", id_, antes=grupo, depois=novo)
            flash("Grupo atualizado.", "success")
        else:
            novo = repo.adicionar({**dados, "criado_em": agora_iso()})
            registrar("grupo_criado", "grupo", novo["id"], depois=novo)
            flash("Grupo criado.", "success")
        return redirect(url_for("auth.grupos"))
    form = grupo or {"nome": "", "limite_desconto": 0, "permissoes": [], "ativo": True}
    return render_template("auth/grupo_form.html", grupo=grupo, form=form, modulos=permissoes_por_modulo())


@bp.route("/auditoria")
@login_required
@requer_permissao("auditoria.visualizar")
def auditoria():
    q = request.args.get("q", "").strip().lower()
    regs = current_app.repos.auditoria.listar()
    if q:
        regs = [r for r in regs if q in f"{r.get('acao')} {r.get('entidade')} {r.get('entidade_id')}".lower()]
    regs.sort(key=lambda r: r.get("data_hora", ""), reverse=True)
    nomes = {u["id"]: u["nome"] for u in current_app.repos.usuarios.listar()}
    return render_template("auth/auditoria.html", registros=regs[:500], nomes=nomes, q=q)
