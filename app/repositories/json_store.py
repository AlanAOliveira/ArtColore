"""
Camada de persistência em JSON.
- Escrita sob lock de arquivo (filelock) e atômica (.tmp + os.replace).
- Operações em vários arquivos: store.transacao() (lock global reentrante).
- IDs sequenciais em sequencias.json (ex.: USR-000001).
- Arquivo: {"versao_schema": N, "registros": [...]}.
Rotas nunca acessam arquivos diretamente: usar os repositórios.
"""
import json
import os
from contextlib import contextmanager
from copy import deepcopy

from filelock import FileLock

VERSAO_SCHEMA = 1


class JsonStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(os.path.join(self.data_dir, ".locks"), exist_ok=True)
        self._lock_global = FileLock(os.path.join(self.data_dir, ".locks", "_transacao.lock"))

    def _caminho(self, nome):
        return os.path.join(self.data_dir, f"{nome}.json")

    def _lock_arquivo(self, nome):
        return FileLock(os.path.join(self.data_dir, ".locks", f"{nome.replace('/', '_')}.lock"))

    def existe(self, nome):
        return os.path.exists(self._caminho(nome))

    @contextmanager
    def transacao(self):
        with self._lock_global:
            yield self

    def ler(self, nome):
        caminho = self._caminho(nome)
        if not os.path.exists(caminho):
            return []
        with self._lock_arquivo(nome):
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f).get("registros", [])

    def gravar(self, nome, registros):
        caminho = self._caminho(nome)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        tmp = caminho + ".tmp"
        with self._lock_arquivo(nome):
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"versao_schema": VERSAO_SCHEMA, "registros": registros}, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, caminho)

    def proximo_id(self, prefixo, digitos=6):
        with self._lock_arquivo("sequencias"):
            caminho = self._caminho("sequencias")
            seq = {}
            if os.path.exists(caminho):
                with open(caminho, "r", encoding="utf-8") as f:
                    seq = json.load(f).get("registros", {}) or {}
            seq[prefixo] = int(seq.get(prefixo, 0)) + 1
            tmp = caminho + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"versao_schema": VERSAO_SCHEMA, "registros": seq}, f, indent=2)
            os.replace(tmp, caminho)
            return f"{prefixo}-{seq[prefixo]:0{digitos}d}"


class Repositorio:
    """Repositório genérico: listar, obter, obter_por, adicionar, atualizar, substituir_todos."""
    nome_arquivo = ""
    prefixo_id = ""

    def __init__(self, store: JsonStore):
        self.store = store

    def listar(self, filtro=None):
        regs = self.store.ler(self.nome_arquivo)
        return deepcopy([r for r in regs if filtro(r)] if filtro else regs)

    def obter(self, id_):
        return self.obter_por("id", id_)

    def obter_por(self, campo, valor):
        for r in self.store.ler(self.nome_arquivo):
            if r.get(campo) == valor:
                return deepcopy(r)
        return None

    def adicionar(self, registro, prefixo=None):
        with self.store.transacao():
            regs = self.store.ler(self.nome_arquivo)
            if not registro.get("id"):
                registro["id"] = self.store.proximo_id(prefixo or self.prefixo_id)
            regs.append(registro)
            self.store.gravar(self.nome_arquivo, regs)
        return deepcopy(registro)

    def atualizar(self, id_, alteracoes):
        with self.store.transacao():
            regs = self.store.ler(self.nome_arquivo)
            for r in regs:
                if r.get("id") == id_:
                    r.update(alteracoes)
                    self.store.gravar(self.nome_arquivo, regs)
                    return deepcopy(r)
        raise KeyError(f"{self.nome_arquivo}: registro {id_} não encontrado")

    def substituir_todos(self, registros):
        with self.store.transacao():
            self.store.gravar(self.nome_arquivo, registros)
