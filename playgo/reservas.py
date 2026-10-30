"""Reserva de quadra feita pelo atleta: um horário divulgado vira reserva, paga pelo app (subconta Asaas da arena)
ou na própria arena, conforme a arena configurou. O cancelamento segue a política da arena (prazo e % devolvido).

A regra vive aqui; rota e tela só traduzem HTTP. O CPF/CNPJ de quem paga só passa para o Asaas (nunca é gravado)."""

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import arenas, auditoria, notificacoes, planos, recebimentos
from .db import agora
from .erros import ErroNegocio, NaoEncontrado
from .models import R_RESERVA, Arena, HorarioDivulgado, Quadra, Reserva, Usuario
from .serializadores import texto_quando

log = logging.getLogger(__name__)

PRAZO_PAGAMENTO_MIN = 30  # reserva online sem pagamento nesse tempo é liberada


def _recebimento_pronto(s: SessaoORM, arena: Arena) -> bool:
    if recebimentos.fase() == "desligado":
        return False
    rec = recebimentos._recebedor_da_arena(s, arena)
    return rec is not None and rec.ativo and rec.status_kyc == "aprovado"


def formas(s: SessaoORM, arena: Arena) -> list[str]:
    """Formas que o atleta pode escolher agora: a configuração da arena, limitada ao que ela realmente consegue cobrar."""
    saida = []
    if arena.pagamento_reserva in ("online", "ambos") and _recebimento_pronto(s, arena):
        saida.append("online")
    if arena.pagamento_reserva in ("local", "ambos"):
        saida.append("local")
    return saida


def texto_politica(arena: Arena) -> str:
    h, dentro, fora = arena.cancel_horas, arena.reembolso_pct, arena.reembolso_pct_fora
    prazo = f"{h} hora{'s' if h != 1 else ''}"
    if h == 0:
        return f"Cancelando antes do início, devolvemos {dentro}% do valor pago pelo app." if dentro == fora else f"Devolvemos {dentro}% do valor pago pelo app, cancelando antes do início."
    return f"Cancelando com {prazo} ou mais de antecedência, devolvemos {dentro}% do valor pago pelo app; depois disso, {fora}%."


def politica(s: SessaoORM, arena: Arena) -> dict:
    disponiveis = formas(s, arena)
    return {
        "pagamento": arena.pagamento_reserva, "formas": disponiveis, "aceita_reserva": bool(disponiveis),
        "cancel_horas": arena.cancel_horas, "reembolso_pct": arena.reembolso_pct, "reembolso_pct_fora": arena.reembolso_pct_fora,
        "texto": texto_politica(arena),
    }


def percentual_devolucao(arena: Arena, reserva: Reserva) -> int:
    horas = (reserva.inicio - agora()).total_seconds() / 3600
    return arena.reembolso_pct if horas >= arena.cancel_horas else arena.reembolso_pct_fora


def _dinheiro(v) -> Decimal:
    return recebimentos._dinheiro(v)


def _gestores(s: SessaoORM, arena: Arena, titulo: str, corpo: str, chave: str) -> None:
    for g in arena.gestores:
        notificacoes.avisar(s, g.id, "arena", titulo, corpo, f"/gestao?arena={arena.id}", None, f"{chave}:{g.id}")


def serializar(s: SessaoORM, r: Reserva) -> dict:
    quadra = s.get(Quadra, r.quadra_id)
    arena = quadra.arena
    n = agora()
    futura = r.inicio > n
    pendente = r.forma_pagamento == "online" and r.status_pagamento == "pendente"
    return {
        "id": r.id, "arena_id": arena.id, "arena_nome": arena.nome, "quadra_nome": quadra.nome,
        "inicio": r.inicio.isoformat(timespec="minutes"), "fim": r.fim.isoformat(timespec="minutes"), "quando": texto_quando(r.inicio, n),
        "valor": float(r.valor or 0), "forma_pagamento": r.forma_pagamento, "status_pagamento": r.status_pagamento,
        "link_pagamento": r.invoice_url if pendente and (r.invoice_url or "").startswith("https://") else None,
        "paga_ate": (r.criado_em + timedelta(minutes=PRAZO_PAGAMENTO_MIN)).isoformat(timespec="minutes") if pendente else None,
        "valor_reembolsado": float(r.valor_reembolsado) if r.valor_reembolsado else None,
        "pode_cancelar": futura,
        "devolucao_pct": percentual_devolucao(arena, r) if (futura and r.forma_pagamento == "online" and r.status_pagamento == "pago") else None,
        "politica": texto_politica(arena),
    }


def reservar_horario(s: SessaoORM, usuario: Usuario, horario_id: int, forma: str | None = None, pagador: dict | None = None) -> dict:
    """O atleta reserva um horário divulgado. Online: já cria a cobrança e a reserva segura o horário por PRAZO_PAGAMENTO_MIN minutos."""
    planos.exigir(s, usuario, "basico")
    h = s.scalar(select(HorarioDivulgado).where(HorarioDivulgado.id == horario_id).with_for_update(of=HorarioDivulgado))
    if h is None:
        raise ErroNegocio("Esse horário não está mais disponível.")
    quadra = s.get(Quadra, h.quadra_id)
    arena = quadra.arena
    if not arena.ativo or not quadra.ativa:
        raise ErroNegocio("Esse horário não está mais disponível.")
    if h.inicio <= agora():
        raise ErroNegocio("Esse horário já passou.")
    valor = _dinheiro(h.valor if h.valor is not None else quadra.valor_hora)
    disponiveis = formas(s, arena)
    if valor <= 0:
        forma = "local"  # nada a cobrar
    else:
        if not disponiveis:
            raise ErroNegocio("Esta arena ainda não aceita reservas pelo app. Fale direto com a arena.")
        if forma is None and len(disponiveis) == 1:
            forma = disponiveis[0]
        if forma not in disponiveis:
            raise ErroNegocio("Escolha como pagar: " + " ou ".join("pelo app" if f == "online" else "na arena" for f in disponiveis) + ".")
    inicio, fim = h.inicio, h.fim
    try:
        r = arenas.reservar(s, quadra.id, inicio, fim, R_RESERVA, f"@{usuario.usuario or usuario.nome}")
        r.usuario_id, r.forma_pagamento, r.valor = usuario.id, forma, valor
        resposta = {}
        if forma == "online":
            rec = recebimentos._recebedor_obrigatorio(s, arena, lock=True)
            resposta = recebimentos._emitir_cobranca(s, r, quadra, rec, valor, pagador or {}, vence_em_dias=1)
        auditoria.registrar(s, usuario.id, "reserva_cliente", "reserva", r.id, forma=forma, valor=str(valor))
        _gestores(s, arena, "🏟️ Nova reserva", f"{r.rotulo} reservou {quadra.nome} em {texto_quando(inicio, agora()).lower()}"
                  + (" (pagamento pelo app)." if forma == "online" else " (pagamento na arena)."), f"reserva-nova:{r.id}")
        s.commit()
    except Exception:
        s.rollback()
        raise
    return serializar(s, r) | ({"asaas": resposta.get("asaas")} if resposta else {})


def minhas(s: SessaoORM, usuario: Usuario) -> list[dict]:
    q = select(Reserva).where(Reserva.usuario_id == usuario.id, Reserva.fim > agora()).order_by(Reserva.inicio)
    return [serializar(s, r) for r in s.scalars(q)]


def _cancelar_cobranca_pendente(s: SessaoORM, r: Reserva) -> None:
    rec = recebimentos._recebedor_da_reserva(s, r)
    if rec is not None and r.asaas_payment_id:
        recebimentos._chamar(rec, "DELETE", f"/payments/{r.asaas_payment_id}")


def cancelar(s: SessaoORM, usuario: Usuario, reserva_id: int) -> dict:
    """O atleta cancela a própria reserva. Paga pelo app: devolve a parte definida pela arena. Na arena: só libera o horário."""
    r = s.scalar(select(Reserva).where(Reserva.id == reserva_id, Reserva.usuario_id == usuario.id).with_for_update(of=Reserva))
    if r is None:
        raise NaoEncontrado("Reserva não encontrada.")
    if r.inicio <= agora():
        raise ErroNegocio("Essa reserva já começou e não pode mais ser cancelada.")
    quadra = s.get(Quadra, r.quadra_id)
    arena = quadra.arena
    pago, devolvido, pct, forma = _dinheiro(r.valor), Decimal(0), None, r.forma_pagamento
    try:
        if r.forma_pagamento == "online" and r.status_pagamento == "pendente":
            _cancelar_cobranca_pendente(s, r)
        elif r.forma_pagamento == "online" and r.status_pagamento == "pago":
            pct = percentual_devolucao(arena, r)
            if pct > 0:
                devolvido = recebimentos.reembolsar_reserva(s, r, (pago * pct / 100))
        auditoria.registrar(s, usuario.id, "reserva_cancelar", "reserva", r.id, forma=r.forma_pagamento, pago=str(pago), devolvido=str(devolvido))
        _gestores(s, arena, "Reserva cancelada", f"{r.rotulo} cancelou {quadra.nome} em {texto_quando(r.inicio, agora()).lower()}."
                  + (f" Devolvido R$ {devolvido:.2f}." if devolvido else ""), f"reserva-cancelada:{r.id}")
        s.delete(r)
        s.commit()
    except Exception:
        s.rollback()
        raise
    return {"cancelada": True, "devolvido": float(devolvido), "devolucao_pct": pct, "mantido_pela_arena": float(pago - devolvido) if forma == "online" and pct is not None else 0.0}


def expirar_pendentes(s: SessaoORM) -> int:
    """Libera o horário das reservas online que não foram pagas a tempo (o agendador chama)."""
    limite = agora() - timedelta(minutes=PRAZO_PAGAMENTO_MIN)
    q = select(Reserva).where(Reserva.usuario_id.is_not(None), Reserva.forma_pagamento == "online", Reserva.status_pagamento == "pendente", Reserva.criado_em < limite)
    total = 0
    for r in list(s.scalars(q)):
        try:
            _cancelar_cobranca_pendente(s, r)  # falhou (ex.: acabou de ser paga)? a reserva fica e o webhook acerta
        except ErroNegocio:
            log.warning("Reserva %s: não foi possível cancelar a cobrança vencida; mantida.", r.id)
            continue
        quadra = s.get(Quadra, r.quadra_id)
        notificacoes.avisar(s, r.usuario_id, "arena", "Reserva liberada", f"Você não pagou {quadra.nome} em {r.inicio:%d/%m às %H:%M} a tempo, e o horário foi liberado.", "/reservas", None, f"reserva-expirou:{r.id}")
        s.delete(r)
        total += 1
    s.commit()
    return total
