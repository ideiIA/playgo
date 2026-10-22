"""Páginas do ensino (site): Central "Aulas e Treinamentos", perfil do profissional, oferta e "Minhas aulas".
O conteúdo é montado por `static/ensino.js` (o mesmo módulo do app) a partir da API."""

from fastapi import Request

from .. import modalidades
from ..deps import Atual, Sessao
from .app import app, pagina


def _mods(s) -> list[dict]:
    return [{"id": m.id, "codigo": m.codigo, "nome": m.nome, "icone": m.icone} for m in modalidades.ativas(s)]


@app.get("/aulas")
def central_aulas(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "aulas.html", "aulas", mods=_mods(s))


@app.get("/profissionais/{perfil_id}")
def pagina_profissional(perfil_id: int, request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "aulas_detalhe.html", "aulas", mods=_mods(s), tela="profissional", oid=perfil_id)


@app.get("/ofertas/{oferta_id}")
def pagina_oferta(oferta_id: int, request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "aulas_detalhe.html", "aulas", mods=_mods(s), tela="oferta", oid=oferta_id)


@app.get("/ensino")
def minhas_aulas(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "ensino.html", "ensino", mods=_mods(s))
