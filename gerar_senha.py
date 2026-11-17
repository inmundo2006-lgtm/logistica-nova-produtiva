# -*- coding: utf-8 -*-
"""Gera o senha_sha256 de um usuário para colar nos Secrets.  Uso: python gerar_senha.py"""
import getpass, hashlib
s = getpass.getpass("Senha: ")
if s != getpass.getpass("Repita: "):
    raise SystemExit("As senhas não conferem.")
print('senha_sha256 = "%s"' % hashlib.sha256(s.encode("utf-8")).hexdigest())
