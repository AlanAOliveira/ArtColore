"""Configurações da empresa/sistema e backups."""
import glob
import os

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory,
                   url_for)
from flask_login import login_required

from ...auditoria import registrar
from ...cli import executar_backup, restaurar_backup
from ...permissoes import requer_permissao

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/configuracao", methods=["GET", "POST"])
@login_required
@requer_permissao("configuracao.gerenciar")
def configuracao():
    repo = current_app.repos.configuracao
    cfg = repo.obter()
    if request.method == "POST":
        f = request.form
        antes = dict(cfg)
        cfg["empresa"] = {k: f.get(k, "").strip() for k in ("nome_fantasia", "razao_social", "cnpj", "endereco", "telefone")}
        try:
            cfg["prazo_devolucao_dias"] = max(0, int(f.get("prazo_devolucao_dias") or 7))
            cfg["alertas"] = {"dias_vencimento": max(1, int(f.get("dias_vencimento") or 30)),
                              "fator_sugestao_compra": max(1.0, float((f.get("fator_sugestao_compra") or "2").replace(",", ".")))}
        except ValueError:
            flash("Valores numéricos inválidos.", "danger")
            return render_template("admin/configuracao.html", cfg=cfg)
        repo.salvar(cfg)
        registrar("configuracao_alterada", "configuracao", "", antes=antes, depois=cfg)
        flash("Configurações salvas.", "success")
        return redirect(url_for("admin.configuracao"))
    return render_template("admin/configuracao.html", cfg=cfg)


def _pasta():
    p = os.path.join(current_app.config["DATA_DIR"], "backups")
    os.makedirs(p, exist_ok=True)
    return p


@bp.route("/backups", methods=["GET", "POST"])
@login_required
@requer_permissao("configuracao.gerenciar")
def backups():
    if request.method == "POST":
        if request.form.get("acao") == "restaurar":
            nome = os.path.basename(request.form.get("arquivo", ""))
            caminho = os.path.join(_pasta(), nome)
            if not os.path.exists(caminho) or request.form.get("confirmar") != "RESTAURAR":
                flash("Digite RESTAURAR para confirmar.", "danger")
            else:
                executar_backup(current_app)  # segurança: backup antes de restaurar
                restaurar_backup(current_app, caminho)
                registrar("backup_restaurado", "backup", nome)
                flash(f"Backup {nome} restaurado (um backup do estado anterior foi criado).", "success")
        else:
            caminho = executar_backup(current_app)
            registrar("backup_criado", "backup", os.path.basename(caminho))
            flash(f"Backup criado: {os.path.basename(caminho)}", "success")
        return redirect(url_for("admin.backups"))
    arquivos = []
    for p in sorted(glob.glob(os.path.join(_pasta(), "backup_*.zip")), reverse=True):
        arquivos.append({"nome": os.path.basename(p), "tamanho": os.path.getsize(p)})
    return render_template("admin/backups.html", arquivos=arquivos)


@bp.route("/backups/<nome>")
@login_required
@requer_permissao("configuracao.gerenciar")
def backup_download(nome):
    nome = os.path.basename(nome)
    if not nome.startswith("backup_") or not os.path.exists(os.path.join(_pasta(), nome)):
        abort(404)
    return send_from_directory(_pasta(), nome, as_attachment=True)
