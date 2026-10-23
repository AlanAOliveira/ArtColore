# Usado pelo Apache + mod_wsgi na EC2 (fase de implantação)
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from app import create_app  # noqa: E402

application = create_app()
