"""Telas do site para receber pelo app: a gestão da arena (saldo, saque, cadastro) e o painel do administrador. A tela em si é o `recebimentos.js`."""

from fastapi import Request

from .. import arenas, recebimentos
from ..deps import Atual, Sessao
from ..erros import NaoEncontrado
from .app import app, pagina


@app.get("/gestao/recebimentos")
def tela_recebimentos(request: Request, s: Sessao, usuario: Atual, arena: int | None = None):
    lista = arenas.minhas(s, usuario)
    escolhida = next((a for a in lista if a.id == arena), lista[0] if lista else None)
    if escolhida is None:
        raise NaoEncontrado("Cadastre uma arena antes de ativar os recebimentos.")
    return pagina(request, s, usuario, "recebimentos.html", "gestao", arena=escolhida, fase=recebimentos.fase(), rodape=recebimentos.rodape())


@app.get("/administracao/recebimentos")
def tela_recebimentos_admin(request: Request, s: Sessao, usuario: Atual):
    if not usuario.admin:
        raise NaoEncontrado("Página não encontrada.")
    return pagina(request, s, usuario, "recebimentos_admin.html", "administracao", rodape=recebimentos.rodape())
