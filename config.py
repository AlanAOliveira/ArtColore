import os
from datetime import timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "chave-de-teste")
    DATA_DIR = os.path.join(BASE_DIR, os.getenv("DATA_DIR", "data"))
    USUARIOS_PADRAO_FILE = os.path.join(BASE_DIR, "usuarios_padrao.json")
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.getenv("SESSION_MINUTES", "480")))
    SESSION_REFRESH_EACH_REQUEST = True
    MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "99"))
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024
    WTF_CSRF_TIME_LIMIT = None
    BACKUPS_MANTER = 30
    TIMEZONE = "America/Sao_Paulo"
