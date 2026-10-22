"""Transforma o modelo em dicionários prontos para a tela (site) e para o JSON (app).
Um só lugar decide como a distância, o horário e o preço aparecem."""

from datetime import date, datetime
from decimal import Decimal

from . import urgencia
from .geo import formatar_km
from .models import NOME_NIVEL, Arena, Atividade, Campeonato, Grupo, HorarioDivulgado, Modalidade, Notificacao

_DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
_TIPOS_DUPLA = ("beach_tennis", "tenis", "padel", "futevolei")


def texto_quando(inicio: datetime, agora: datetime) -> str:
    hora = inicio.strftime("%H:%M")
    dias = (inicio.date() - agora.date()).days
    if dias == 0:
        return f"Hoje • {hora}"
    if dias == 1:
        return f"Amanhã • {hora}"
    if 1 < dias < 7:
        return f"{_DIAS[inicio.weekday()].capitalize()} • {hora}"
    return f"{inicio.strftime('%d/%m')} • {hora}"


def texto_data(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def valor_texto(v: Decimal | float | None, sufixo: str = "") -> str:
    if not v:
        return "Gratuito"
    v = Decimal(str(v))
    num = f"{v:.0f}" if v == v.to_integral() else f"{v:.2f}".replace(".", ",")
    return f"R$ {num}{sufixo}"


def modalidade(m: Modalidade | None) -> dict | None:
    if m is None:
        return None
    return {"id": m.id, "codigo": m.codigo, "nome": m.nome, "icone": m.icone, "cor": m.cor, "usa_quadra": m.usa_quadra, "vagas_padrao": m.vagas_padrao}


def rotulo_falta(vagas: int, max_participantes: int, codigo: str) -> str:
    """'FALTAM 2 JOGADORES', 'FALTA 1 JOGADOR', 'PROCURA 1 DUPLA' — o destaque da vitrine (RF-014)."""
    if vagas == 1 and codigo in _TIPOS_DUPLA and max_participantes <= 4:
        return "PROCURA 1 DUPLA"
    return f"FALTA{'M' if vagas > 1 else ''} {vagas} JOGADOR{'ES' if vagas > 1 else ''}"


def atividade(a: Atividade, agora: datetime, dist: float | None = None, minha: str | None = None) -> dict:
    minutos = (a.inicio - agora).total_seconds() / 60
    falta = a.falta_gente and a.vagas > 0
    return {
        "tipo": "atividade",
        "id": a.id,
        "nome": a.nome,
        "modalidade": modalidade(a.modalidade),
        "local_nome": a.local_nome,
        "arena_id": a.arena_id,
        "arena_nome": a.arena.nome if a.arena else None,
        "latitude": a.latitude,
        "longitude": a.longitude,
        "inicio": a.inicio.isoformat(timespec="minutes"),
        "quando": texto_quando(a.inicio, agora),
        "minutos_para_inicio": round(minutos),
        "contagem": urgencia.contagem_regressiva(minutos),
        "distancia_km": round(dist, 2) if dist is not None else None,
        "distancia": formatar_km(dist),
        "nivel": a.nivel,
        "nivel_nome": NOME_NIVEL.get(a.nivel, a.nivel),
        "categoria": a.categoria,
        "valor": float(a.valor or 0),
        "valor_texto": valor_texto(a.valor),
        "max_participantes": a.max_participantes,
        "confirmados": a.confirmados,
        "vagas": a.vagas,
        "falta_gente": falta,
        "rotulo_falta": rotulo_falta(a.vagas, a.max_participantes, a.modalidade.codigo) if falta else None,
        "exige_aprovacao": a.exige_aprovacao,
        "status": a.status,
        "organizador": {"id": a.organizador_id, "nome": a.organizador.nome, "arroba": a.organizador.arroba},
        "grupo_id": a.grupo_id,
        "sem_quadra": not a.modalidade.usa_quadra,
        "urgencia": urgencia.nota(minutos, dist, a.vagas, a.falta_gente),
        "minha_participacao": minha,
    }


def campeonato(c: Campeonato, agora: datetime, dist: float | None = None, equipes_ativas: int | None = None) -> dict:
    ativas = equipes_ativas if equipes_ativas is not None else sum(1 for e in c.equipes if e.status in ("pendente", "confirmada"))
    vagas = max(0, c.max_equipes - ativas)
    if c.status == "cancelado":
        situacao = "Cancelado"
    elif c.status == "encerrado":
        situacao = "Encerrado"
    elif c.status == "em_andamento":
        situacao = "Em andamento"
    elif c.inscricao_ate < agora.date():
        situacao = "Inscrições encerradas"
    elif vagas == 0:
        situacao = "Lotado"
    else:
        situacao = "Inscrições abertas"
    return {
        "tipo": "campeonato",
        "id": c.id,
        "nome": c.nome,
        "modalidade": modalidade(c.modalidade),
        "categoria": c.categoria,
        "local_nome": c.local_nome,
        "arena_id": c.arena_id,
        "latitude": c.latitude,
        "longitude": c.longitude,
        "data_inicio": c.data_inicio.isoformat(),
        "data_inicio_texto": texto_data(c.data_inicio),
        "data_fim_texto": texto_data(c.data_fim),
        "inscricao_ate_texto": texto_data(c.inscricao_ate),
        "premiacao": c.premiacao,
        "valor_inscricao": float(c.valor_inscricao or 0),
        "valor_inscricao_texto": valor_texto(c.valor_inscricao, "/equipe") if c.valor_inscricao else "Inscrição gratuita",
        "max_equipes": c.max_equipes,
        "equipes": ativas,
        "vagas": vagas,
        "atletas_por_equipe": c.atletas_por_equipe,
        "situacao": situacao,
        "inscricoes_abertas": situacao == "Inscrições abertas",
        "distancia_km": round(dist, 2) if dist is not None else None,
        "distancia": formatar_km(dist),
        "organizador": {"id": c.organizador_id, "nome": c.organizador.nome, "arroba": c.organizador.arroba},
    }


def arena(a: Arena, dist: float | None = None, horarios_hoje: int = 0) -> dict:
    return {
        "tipo": "arena",
        "id": a.id,
        "nome": a.nome,
        "endereco": a.endereco,
        "cidade": a.cidade,
        "latitude": a.latitude,
        "longitude": a.longitude,
        "estrutura": a.estrutura,
        "quadras": len([q for q in a.quadras if q.ativa]),
        "tipo_estabelecimento": a.tipo_estabelecimento or "arena",
        "distancia_km": round(dist, 2) if dist is not None else None,
        "distancia": formatar_km(dist),
        "horarios_disponiveis": horarios_hoje,
    }


def grupo(g: Grupo, membros: int, dist: float | None = None, sou_membro: bool = False, proxima: dict | None = None) -> dict:
    return {
        "tipo": "grupo",
        "id": g.id,
        "nome": g.nome,
        "modalidade": modalidade(g.modalidade),
        "descricao": g.descricao,
        "cidade": g.cidade,
        "local_habitual": g.local_habitual,
        "latitude": g.latitude,
        "longitude": g.longitude,
        "membros": membros,
        "distancia_km": round(dist, 2) if dist is not None else None,
        "distancia": formatar_km(dist),
        "sou_membro": sou_membro,
        "proxima": proxima,
    }


def horario(h: HorarioDivulgado, agora: datetime, dist: float | None = None) -> dict:
    q = h.quadra
    return {
        "tipo": "horario",
        "id": h.id,
        "quadra_id": q.id,
        "quadra_nome": q.nome,
        "arena_id": q.arena_id,
        "arena_nome": q.arena.nome,
        "modalidade": modalidade(h.modalidade),
        "latitude": q.arena.latitude,
        "longitude": q.arena.longitude,
        "inicio": h.inicio.isoformat(timespec="minutes"),
        "quando": texto_quando(h.inicio, agora),
        "valor": float(h.valor or 0),
        "valor_texto": valor_texto(h.valor, "/hora"),
        "distancia_km": round(dist, 2) if dist is not None else None,
        "distancia": formatar_km(dist),
    }


def notificacao(n: Notificacao, agora: datetime) -> dict:
    return {
        "id": n.id,
        "tipo": n.tipo,
        "titulo": n.titulo,
        "corpo": n.corpo,
        "link": n.link,
        "lida": n.lida,
        "quando": texto_quando(n.criado_em, agora),
    }
