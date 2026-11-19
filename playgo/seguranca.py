"""Senhas (scrypt, só biblioteca padrão), chave de assinatura e token do app."""

import base64
import hashlib
import hmac
import os
import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import RAIZ, settings

SENHA_MINIMA = 8
# Versões do hash: "scrypt" (N=2^14, o primeiro formato) continua sendo aceito; "scrypt2" (N=2^15) é o atual.
# Quem entra com um hash antigo tem o hash refeito no formato atual (`precisa_atualizar`).
_PARAMS = {"scrypt": (2**14, 8, 1), "scrypt2": (2**15, 8, 1)}
VERSAO_ATUAL = "scrypt2"
_MEMORIA = 96 * 1024 * 1024  # teto de memória do scrypt (N=2^15, r=8 usa ~32 MiB)


def _derivar(senha: str, sal: bytes, versao: str = VERSAO_ATUAL) -> bytes:
    n, r, p = _PARAMS[versao]
    return hashlib.scrypt(senha.encode(), salt=sal, n=n, r=r, p=p, dklen=32, maxmem=_MEMORIA)


def gerar_hash(senha: str) -> str:
    sal = os.urandom(16)
    return VERSAO_ATUAL + "$" + base64.b64encode(sal).decode() + "$" + base64.b64encode(_derivar(senha, sal)).decode()


def conferir(senha: str, armazenado: str) -> bool:
    try:
        versao, sal, esperado = armazenado.split("$")
        if versao not in _PARAMS:
            return False
        return hmac.compare_digest(_derivar(senha, base64.b64decode(sal), versao), base64.b64decode(esperado))
    except (ValueError, KeyError):
        return False


def precisa_atualizar(armazenado: str) -> bool:
    """True para hash feito com parâmetros mais fracos que os atuais (refazer quando a pessoa entra com a senha certa)."""
    return not armazenado.startswith(VERSAO_ATUAL + "$")


def chave_sessao() -> str:
    """Chave do .env ou, na falta, uma gerada na primeira execução e guardada fora do git."""
    if settings.chave_sessao:
        return settings.chave_sessao
    if settings.em_serverless:
        raise RuntimeError("Defina PLAYGO_CHAVE_SESSAO: o ambiente serverless não tem disco para guardar uma chave gerada.")
    arquivo = RAIZ / ".chave_sessao"
    if not arquivo.exists():
        arquivo.write_text(secrets.token_urlsafe(48), encoding="ascii")
    return arquivo.read_text(encoding="ascii").strip()


def _serializador() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(chave_sessao(), salt="playgo-token-app")


def gerar_token(usuario_id: int) -> str:
    """Token Bearer do aplicativo: o mesmo que o cookie de sessão faz para o site."""
    return _serializador().dumps(usuario_id)


def ler_token(token: str) -> int | None:
    try:
        return _serializador().loads(token, max_age=settings.dias_token_app * 86400)
    except BadSignature:
        return None
