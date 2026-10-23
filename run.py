"""
Execução local (sem ambiente virtual):
    python run.py            -> sincroniza dados de teste, backup diário e inicia em http://127.0.0.1:5000
    python run.py --reset    -> apaga os dados de data/ e recria tudo (com demonstração)
    python run.py --backup   -> cria data/backups/backup_AAAA-MM-DD_HHMMSS.zip e sai
"""
import sys

from app import create_app
from app.cli import backup_diario, executar_backup, executar_seed

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        if "--backup" in sys.argv:
            print("Backup criado:", executar_backup(app))
            sys.exit(0)
        executar_seed(app, reset="--reset" in sys.argv)
        backup_diario(app)

    app.run(host="127.0.0.1", port=5000, debug=True)
