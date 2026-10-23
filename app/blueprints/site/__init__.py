"""Página pública (landing). O sistema de gestão fica em /painel (login pelo menu lateral)."""
from flask import Blueprint, current_app, render_template

bp = Blueprint("site", __name__)


@bp.route("/")
def landing():
    cfg = current_app.repos.configuracao.obter() or {}
    empresa = cfg.get("empresa", {}) or {}
    return render_template("site/landing.html", empresa=empresa)
