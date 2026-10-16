"""Conexão do canal oficial do YouTube do PlayGo (um administrador autoriza uma vez, com a conta dona do canal)."""

from fastapi import Request
from fastapi.responses import RedirectResponse

from .. import youtube
from ..deps import Atual, Sessao
from ..erros import ErroNegocio
from .app import app


@app.get("/youtube/conectar")
def youtube_conectar(request: Request, usuario: Atual):
    if not usuario.admin:
        request.session["erro"] = "Só administradores conectam o YouTube."
        return RedirectResponse("/", status_code=303)
    try:
        import secrets

        estado = secrets.token_urlsafe(24)
        request.session["yt_state"] = estado
        return RedirectResponse(youtube.url_autorizacao(estado), status_code=303)
    except ErroNegocio as e:
        request.session["erro"] = str(e)
        return RedirectResponse("/administracao", status_code=303)


@app.get("/youtube/retorno")
def youtube_retorno(request: Request, s: Sessao, usuario: Atual, code: str = "", state: str = "", error: str = ""):
    esperado = request.session.pop("yt_state", None)
    if not usuario.admin:
        return RedirectResponse("/", status_code=303)
    if error or not code or not esperado or state != esperado:
        request.session["erro"] = "A conexão com o YouTube foi cancelada ou expirou. Tente de novo."
        return RedirectResponse("/administracao", status_code=303)
    try:
        e = youtube.conectar(s, usuario, code)
        request.session["ok"] = f"YouTube conectado: {e['canal']}."
    except ErroNegocio as ex:
        request.session["erro"] = str(ex)
    return RedirectResponse("/administracao", status_code=303)
