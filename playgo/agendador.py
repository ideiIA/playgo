"""Rotinas periódicas: lembrete de jogo, reforço de 'falta gente' perto do início e encerramento
das atividades que já terminaram. Roda dentro da interface ou pelo comando `agendador`."""

import logging
import threading
from datetime import timedelta

from sqlalchemy import select

from . import atividades, instagram, match, notificacoes, planos, recebimentos, reservas, youtube
from . import urgencia
from .config import settings
from .db import Session, agora
from .models import ABERTA, CONFIRMADO, Atividade, Participacao

log = logging.getLogger(__name__)


def lembretes(s) -> int:
    """'Seu jogo começa em 2 horas': um aviso por pessoa e por atividade."""
    n = agora()
    limite = n + timedelta(minutes=settings.lembrete_antecedencia_min)
    avisos = 0
    q = select(Atividade).where(Atividade.status == ABERTA, Atividade.inicio > n, Atividade.inicio <= limite)
    for a in s.scalars(q):
        minutos = (a.inicio - n).total_seconds() / 60
        for p in s.scalars(select(Participacao).where(Participacao.atividade_id == a.id, Participacao.status == CONFIRMADO)):
            if not p.usuario.notif_lembretes:
                continue
            if notificacoes.avisar(
                s, p.usuario_id, "lembrete", f"{a.modalidade.icone} {a.nome} {urgencia.contagem_regressiva(minutos)}",
                f"{a.local_nome}", f"/atividades/{a.id}", a.id, f"lembr:{a.id}",
            ):
                avisos += 1
    s.commit()
    return avisos


def reforcar_urgentes(s) -> int:
    """Quem ainda pede gente na última hora volta a ser avisado, uma vez (RF-016)."""
    n = agora()
    total = 0
    q = select(Atividade).where(Atividade.status == ABERTA, Atividade.falta_gente, Atividade.inicio > n, Atividade.inicio <= n + timedelta(minutes=60))
    for a in s.scalars(q):
        if a.vagas > 0:
            total += match.avisar_compativeis(s, a, "urgente", chave="urg60")
    s.commit()
    return total


def ciclo() -> dict:
    with Session() as s:
        resultado = {
            "encerradas": atividades.encerrar_passadas(s),
            "lembretes": lembretes(s),
            "urgentes": reforcar_urgentes(s),
            "planos": planos.avisar_vencimentos(s),
            "recebimentos": recebimentos.avisar_atualizacao_dados(s) if recebimentos.fase() != "desligado" else 0,
            "reservas": reservas.expirar_pendentes(s) if recebimentos.fase() != "desligado" else 0,
            "instagram": instagram.publicar_aprovadas(s),
            "youtube": youtube.manutencao(s),
        }
    return resultado


def laco(parar: threading.Event) -> None:
    while not parar.is_set():
        try:
            r = ciclo()
            if any(r.values()):
                log.info("agendador: %s", r)
        except Exception:  # um ciclo com erro não derruba o servidor
            log.exception("agendador: falha no ciclo")
        parar.wait(settings.intervalo_agendador_s)
