"""API dos avisos no celular (ver `push.py`). Tudo exige login: cada pessoa só mexe nos próprios aparelhos."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from .. import push
from ..deps import AtualApi, Sessao

router = APIRouter(prefix="/api/v1/push")


class Chaves(BaseModel):
    p256dh: str
    auth: str


class InscricaoIn(BaseModel):
    """O que `PushSubscription.toJSON()` do navegador devolve."""

    endpoint: str
    keys: Chaves


class EnderecoIn(BaseModel):
    endpoint: str


@router.get("/estado")
def estado(u: AtualApi, s: Sessao, resposta: Response, endpoint: str = ""):
    resposta.headers["Cache-Control"] = "no-store"
    return push.estado(s, u, endpoint or None)


@router.post("/inscrever", status_code=201)
def inscrever(corpo: InscricaoIn, request: Request, u: AtualApi, s: Sessao):
    push.inscrever(s, u, corpo.endpoint, corpo.keys.p256dh, corpo.keys.auth, request.headers.get("user-agent"))
    return push.estado(s, u, corpo.endpoint)


@router.post("/cancelar")
def cancelar(corpo: EnderecoIn, u: AtualApi, s: Sessao):
    push.cancelar(s, u, corpo.endpoint)
    return push.estado(s, u, None)


@router.post("/teste")
def teste(u: AtualApi, s: Sessao):
    return {"entregues": push.enviar_teste(s, u)}
