"""API do chaveamento: sorteio, chaves, jogo ao vivo e mesários de cada campeonato.
Quem acompanha só lê (GET); sorteio, mesários e condução dos jogos ficam nos serviços de `chaves.py`."""

from datetime import datetime, time

from fastapi import APIRouter
from pydantic import BaseModel

from .. import campeonatos, chaves
from ..deps import AtualApi, Sessao

router = APIRouter(prefix="/api/v1/campeonatos")


class SorteioIn(BaseModel):
    formato: str
    grupos: int | None = None  # fase de grupos: quantos grupos
    classificam: int | None = None  # fase de grupos: quantos de cada grupo vão ao mata-mata
    cabecas: list[int] = []  # ids das equipes cabeças de chave, em ordem (a 1ª é a nº 1)


class MarcarIn(BaseModel):
    lado: str
    delta: int


class PlacarIn(BaseModel):
    a: int
    b: int


class LanceIn(BaseModel):
    texto: str
    equipe_id: int | None = None


class EncerrarIn(BaseModel):
    vencedor_id: int | None = None


class AgendarIn(BaseModel):
    inicio: datetime | None = None
    local: str | None = None


class RegrasPlacarIn(BaseModel):
    modo: str  # simples | sets
    melhor_de: int = 3
    pontos_set: int = 25
    pontos_tiebreak: int = 15
    diferenca: int = 2


class SetsIn(BaseModel):
    sets: list[list[int]]  # [[25,20],[18,25],[15,12]]


class AgendaLoteIn(BaseModel):
    escopo: str = "todos"  # todos | grupos | mata_mata | rodada:N
    inicio: datetime
    duracao_min: int
    intervalo_min: int = 0
    locais: list[str] = []  # quadras/campos; vários = jogos da mesma rodada ao mesmo tempo
    ate: time | None = None  # horário-limite do dia; o resto segue no dia seguinte
    sobrescrever: bool = True


class MesarioIn(BaseModel):
    usuario_id: int


@router.get("/{campeonato_id}/chaves")
def ver_chaves(campeonato_id: int, u: AtualApi, s: Sessao):
    return chaves.chaveamento(s, campeonatos.obter(s, campeonato_id), u)


@router.post("/{campeonato_id}/sorteio")
def sortear(campeonato_id: int, corpo: SorteioIn, u: AtualApi, s: Sessao):
    chaves.sortear(s, campeonato_id, u, corpo.formato, corpo.grupos, corpo.classificam, corpo.cabecas)
    return chaves.chaveamento(s, campeonatos.obter(s, campeonato_id), u)


@router.post("/{campeonato_id}/placar-regras")
def regras_do_placar(campeonato_id: int, corpo: RegrasPlacarIn, u: AtualApi, s: Sessao):
    chaves.configurar_placar(s, campeonato_id, u, corpo.modo, corpo.melhor_de, corpo.pontos_set, corpo.pontos_tiebreak, corpo.diferenca)
    return chaves.chaveamento(s, campeonatos.obter(s, campeonato_id), u)


@router.post("/{campeonato_id}/jogos/{jogo_id}/sets")
def lancar_sets(campeonato_id: int, jogo_id: int, corpo: SetsIn, u: AtualApi, s: Sessao):
    chaves.definir_sets(s, campeonato_id, jogo_id, u, corpo.sets)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/agenda")
def agenda_em_lote(campeonato_id: int, corpo: AgendaLoteIn, u: AtualApi, s: Sessao):
    return chaves.agendar_lote(s, campeonato_id, u, corpo.escopo, corpo.inicio, corpo.duracao_min, corpo.intervalo_min, corpo.locais, corpo.ate, corpo.sobrescrever)


@router.get("/{campeonato_id}/jogos/{jogo_id}")
def ver_jogo(campeonato_id: int, jogo_id: int, u: AtualApi, s: Sessao):
    return chaves.detalhe_jogo(s, campeonatos.obter(s, campeonato_id), jogo_id, u)


def _devolver(campeonato_id: int, jogo_id: int, u, s):
    s.expire_all()  # o jogo acabou de mudar numa transação própria; leia o estado novo, com a linha do tempo
    return chaves.detalhe_jogo(s, campeonatos.obter(s, campeonato_id), jogo_id, u)


@router.post("/{campeonato_id}/jogos/{jogo_id}/agendar")
def agendar(campeonato_id: int, jogo_id: int, corpo: AgendarIn, u: AtualApi, s: Sessao):
    chaves.agendar(s, campeonato_id, jogo_id, u, corpo.inicio, corpo.local)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/iniciar")
def iniciar(campeonato_id: int, jogo_id: int, u: AtualApi, s: Sessao):
    chaves.iniciar(s, campeonato_id, jogo_id, u)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/ponto")
def ponto(campeonato_id: int, jogo_id: int, corpo: MarcarIn, u: AtualApi, s: Sessao):
    chaves.marcar_ponto(s, campeonato_id, jogo_id, u, corpo.lado, corpo.delta)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/marcar")
def marcar(campeonato_id: int, jogo_id: int, corpo: MarcarIn, u: AtualApi, s: Sessao):
    chaves.marcar(s, campeonato_id, jogo_id, u, corpo.lado, corpo.delta)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/placar")
def placar(campeonato_id: int, jogo_id: int, corpo: PlacarIn, u: AtualApi, s: Sessao):
    chaves.definir_placar(s, campeonato_id, jogo_id, u, corpo.a, corpo.b)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/lance")
def lance(campeonato_id: int, jogo_id: int, corpo: LanceIn, u: AtualApi, s: Sessao):
    chaves.lance(s, campeonato_id, jogo_id, u, corpo.texto, corpo.equipe_id)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/encerrar")
def encerrar(campeonato_id: int, jogo_id: int, corpo: EncerrarIn, u: AtualApi, s: Sessao):
    chaves.encerrar(s, campeonato_id, jogo_id, u, corpo.vencedor_id)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/jogos/{jogo_id}/reabrir")
def reabrir(campeonato_id: int, jogo_id: int, u: AtualApi, s: Sessao):
    chaves.reabrir(s, campeonato_id, jogo_id, u)
    return _devolver(campeonato_id, jogo_id, u, s)


@router.post("/{campeonato_id}/mesarios", status_code=201)
def adicionar_mesario(campeonato_id: int, corpo: MesarioIn, u: AtualApi, s: Sessao):
    return chaves.adicionar_mesario(s, campeonato_id, u, corpo.usuario_id)


@router.delete("/{campeonato_id}/mesarios/{usuario_id}")
def remover_mesario(campeonato_id: int, usuario_id: int, u: AtualApi, s: Sessao):
    return chaves.remover_mesario(s, campeonato_id, u, usuario_id)
