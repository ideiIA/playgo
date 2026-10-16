"""API do YouTube do PlayGo: estado da conexão do canal oficial e privacidade das transmissões (administração)."""

from fastapi import APIRouter
from pydantic import BaseModel

from .. import youtube
from ..deps import AtualApi, Sessao

router = APIRouter(prefix="/api/v1/youtube")


class PrivacidadeIn(BaseModel):
    privacidade: str


@router.get("/estado")
def estado(u: AtualApi, s: Sessao):
    if not u.admin:
        return {"conectado": youtube.conectado(s), "declaracao": youtube.DECLARACAO}
    return youtube.estado(s) | {"declaracao": youtube.DECLARACAO}


@router.post("/privacidade")
def privacidade(corpo: PrivacidadeIn, u: AtualApi, s: Sessao):
    return youtube.definir_privacidade(s, u, corpo.privacidade)


@router.post("/desconectar")
def desconectar(u: AtualApi, s: Sessao):
    return youtube.desconectar(s, u)
