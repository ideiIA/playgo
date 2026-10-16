"""Telão do campeonato: a tela pública do placar (por link secreto) e a tela de controle da organização."""

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import campeonatos, detalhes, telao, youtube
from ..deps import Atual, Sessao
from ..erros import NaoEncontrado
from .app import app, pagina, templates


@app.get("/telao/{token}")
def tela_telao(token: str, request: Request, s: Sessao):
    """Sem login e sem menu: só o placar. O token é o segredo; 'no-store' impede que a TV guarde a página."""
    try:
        telao.dados(s, token)
    except NaoEncontrado as e:
        return templates.TemplateResponse(request, "telao.html", {"token": None, "erro": str(e)}, status_code=404, headers={"Cache-Control": "no-store"})
    return templates.TemplateResponse(request, "telao.html", {"token": token, "erro": None}, headers={"Cache-Control": "no-store"})


@app.get("/telao/{token}/dados")
def dados_telao(token: str, s: Sessao):
    try:
        return JSONResponse(telao.dados(s, token), headers={"Cache-Control": "no-store"})
    except NaoEncontrado as e:
        return JSONResponse({"detail": str(e)}, status_code=404, headers={"Cache-Control": "no-store"})


@app.get("/campeonatos/{campeonato_id}/telao")
def tela_controle_telao(campeonato_id: int, request: Request, s: Sessao, usuario: Atual):
    c = campeonatos.obter(s, campeonato_id)
    k = detalhes.campeonato(s, c, usuario)
    if not k["pode_telao"]:
        request.session["erro"] = "Só a organização e os mesários controlam o telão."
        return RedirectResponse(f"/campeonatos/{campeonato_id}/chaves", status_code=303)
    return pagina(request, s, usuario, "telao_controle.html", "campeonatos", k=k, p=telao.painel(s, c, usuario), yt=youtube.estado(s), declaracao=youtube.DECLARACAO)
