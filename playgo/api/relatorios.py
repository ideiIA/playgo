"""API dos relatórios (ver `relatorios.py`): por arena (gestor ou admin) e geral (só admin). Dinheiro e dados de clientes nunca vão para cache."""

import csv
import io
from datetime import date

from fastapi import APIRouter, Response

from .. import relatorios
from ..deps import AtualApi, Sessao

router = APIRouter(prefix="/api/v1/relatorios")


@router.get("/arenas")
def minhas_arenas(u: AtualApi, s: Sessao, resposta: Response):
    resposta.headers["Cache-Control"] = "no-store"
    return {"arenas": relatorios.arenas_do_usuario(s, u), "admin": bool(u.admin)}


@router.get("/arenas/{arena_id}")
def da_arena(arena_id: int, u: AtualApi, s: Sessao, resposta: Response, inicio: date | None = None, fim: date | None = None):
    resposta.headers["Cache-Control"] = "no-store"
    return relatorios.arena(s, u, arena_id, inicio, fim)


@router.get("/arenas/{arena_id}/reservas.csv")
def csv_da_arena(arena_id: int, u: AtualApi, s: Sessao, inicio: date | None = None, fim: date | None = None):
    saida = io.StringIO()
    csv.writer(saida, delimiter=";").writerows(relatorios.reservas_csv(s, u, arena_id, inicio, fim))
    return Response("﻿" + saida.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="reservas-arena-{arena_id}.csv"', "Cache-Control": "no-store"})


@router.get("/plataforma")
def da_plataforma(u: AtualApi, s: Sessao, resposta: Response, inicio: date | None = None, fim: date | None = None):
    resposta.headers["Cache-Control"] = "no-store"
    return relatorios.plataforma(s, u, inicio, fim)
