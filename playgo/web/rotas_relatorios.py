"""Tela de relatórios do site: a arena de cada gestor e, para o administrador, a visão geral. A tela é o `relatorios.js`, que consulta a API."""

from fastapi import Request

from .. import relatorios
from ..deps import Atual, Sessao
from .app import _sem_plano, app, pagina


@app.get("/relatorios")
def tela_relatorios(request: Request, s: Sessao, usuario: Atual, arena: int | None = None):
    if not usuario.admin and (r := _sem_plano(request, s, usuario, "arena")) is not None:
        return r
    lista = relatorios.arenas_do_usuario(s, usuario)
    escolhida = arena if any(a["id"] == arena for a in lista) else (lista[0]["id"] if lista else None)
    return pagina(request, s, usuario, "relatorios.html", "relatorios", lista=lista, escolhida=escolhida)
