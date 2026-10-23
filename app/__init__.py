from flask import Flask, render_template
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect

from config import Config
from . import utils as u
from .repositories import Repos

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.repos = Repos(app.config["DATA_DIR"])

    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Faça login para acessar esta página."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def carregar_usuario(user_id):
        from .models import Usuario
        dados = app.repos.usuarios.obter(user_id)
        return Usuario(dados) if dados else None

    from .blueprints.admin import bp as admin_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.clientes import bp as clientes_bp
    from .blueprints.estoque import bp as estoque_bp
    from .blueprints.fornecedores import bp as fornecedores_bp
    from .blueprints.localizacoes import bp as localizacoes_bp
    from .blueprints.main import bp as main_bp
    from .blueprints.materiais import bp as materiais_bp
    from .blueprints.pdv import bp as pdv_bp
    from .blueprints.precos import bp as precos_bp
    from .blueprints.relatorios import bp as relatorios_bp
    for bp in (auth_bp, main_bp, materiais_bp, fornecedores_bp, clientes_bp, localizacoes_bp,
               estoque_bp, pdv_bp, precos_bp, relatorios_bp, admin_bp):
        app.register_blueprint(bp)

    app.jinja_env.filters.update({
        "data_hora": u.formatar_data_hora,
        "data": u.formatar_data,
        "moeda": u.formatar_moeda,
        "moeda_input": u.moeda_input,
        "numero": u.formatar_numero,
        "qtd": u.formatar_qtd,
        "cpf": u.formatar_cpf,
        "cpf_mascara": u.mascarar_cpf,
        "telefone": u.formatar_telefone,
        "telefone_mascara": u.mascarar_telefone,
        "documento": u.formatar_documento,
    })

    @app.context_processor
    def contexto_global():
        from .services.estoque import TIPOS_MOV
        from .services.pdf import FORMAS
        cfg = app.repos.configuracao.obter()
        ctx = {"empresa_nome": cfg.get("empresa", {}).get("nome_fantasia", "Papelaria"),
               "TIPOS_MOV": TIPOS_MOV, "FORMAS": FORMAS, "hoje": u.hoje_iso()}
        if current_user.is_authenticated:
            caixa = app.repos.caixas.aberto()
            ctx["caixa_aberto"] = caixa
        return ctx

    @app.errorhandler(403)
    def erro_403(e):
        return render_template("erro.html", codigo=403, mensagem="Você não tem permissão para acessar esta página."), 403

    @app.errorhandler(404)
    def erro_404(e):
        return render_template("erro.html", codigo=404, mensagem="Página não encontrada."), 404

    @app.errorhandler(413)
    def erro_413(e):
        return render_template("erro.html", codigo=413, mensagem="Arquivo muito grande (máx. 25 MB por envio)."), 413

    from . import cli
    cli.registrar(app)
    from .blueprints.site import bp as site_bp
    app.register_blueprint(site_bp)

    return app
