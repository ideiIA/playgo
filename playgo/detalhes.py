"""Telas de detalhe (atividade, grupo, campeonato, arena) montadas uma vez e usadas pelo site e pela API."""

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import campeonatos, elenco, escopos, grupos, vagas
from . import serializadores as ser
from .db import agora
from .geo import haversine
from .models import (
    CONFIRMADO,
    ESPERA,
    PENDENTE,
    Arena,
    Atividade,
    Campeonato,
    Equipe,
    Grupo,
    HorarioDivulgado,
    Usuario,
)


def _pessoa(u: Usuario, extra: dict | None = None) -> dict:
    return {"usuario_id": u.id, "nome": u.nome, "arroba": u.arroba, "iniciais": u.iniciais, "foto_url": u.foto_url, **(extra or {})}


def _dist(usuario: Usuario | None, lat: float, lng: float) -> float | None:
    if usuario is None or not usuario.tem_localizacao:
        return None
    return haversine(usuario.latitude, usuario.longitude, lat, lng)


def atividade(s: SessaoORM, a: Atividade, usuario: Usuario | None) -> dict:
    n = agora()
    por_status = vagas.lista(s, a.id)
    minha = next((p for lst in por_status.values() for p in lst if usuario and p.usuario_id == usuario.id), None)
    sou_org = bool(usuario and (usuario.id == a.organizador_id or usuario.admin))
    d = ser.atividade(a, n, _dist(usuario, a.latitude, a.longitude), minha.status if minha else None)
    posicao = None
    if minha and minha.status == ESPERA:
        posicao = [p.usuario_id for p in por_status[ESPERA]].index(usuario.id) + 1
    d.update(
        {
            "capa_url": f"/capa/{a.capa}" if a.capa else None,
            "book_publicado": bool(a.book_publicado),
            "descricao": a.descricao,
            "regras": a.regras,
            "percurso": a.percurso,
            "duracao_min": a.duracao_min,
            "idade_min": a.idade_min,
            "idade_max": a.idade_max,
            "quadra_id": a.quadra_id,
            "visibilidade": a.visibilidade,
            "link": f"/convite/{a.convite_token}" if a.convite_token and sou_org else None,
            "mural_acesso": escopos.pode_ler(s, usuario, "atividade", a.id) if usuario else False,
            "sou_moderador": escopos.eh_moderador(s, usuario, "atividade", a.id) if usuario else False,
            "moderadores": [{"usuario_id": m.id, "arroba": m.arroba} for m in escopos.moderadores(s, "atividade", a.id)] if sou_org else [],
            "grupo": {"id": a.grupo.id, "nome": a.grupo.nome} if a.grupo else None,
            "sou_organizador": sou_org,
            "minha_participacao_id": minha.id if minha else None,
            "posicao_espera": posicao,
            "encerrada": a.status != "aberta" or a.fim < n,
            "participantes": [_pessoa(p.usuario, {"participacao_id": p.id, "organizador": p.usuario_id == a.organizador_id, "nivel": p.usuario.nivel_em(a.modalidade_id)}) for p in por_status[CONFIRMADO]],
            "espera": [_pessoa(p.usuario, {"participacao_id": p.id}) for p in por_status[ESPERA]] if sou_org else len(por_status[ESPERA]),
            "pendentes": [_pessoa(p.usuario, {"participacao_id": p.id}) for p in por_status[PENDENTE]] if sou_org else [],
        }
    )
    return d


def grupo(s: SessaoORM, g: Grupo, usuario: Usuario | None) -> dict:
    n = agora()
    sou = grupos.membro(s, g.id, usuario) if usuario else None
    ativos = [m for m in g.membros if m.status == "ativo"]
    sou_admin = bool(sou and sou.papel == "admin") or bool(usuario and usuario.admin)
    d = ser.grupo(g, len(ativos), _dist(usuario, g.latitude, g.longitude) if g.latitude is not None else None, sou is not None)
    d.update(
        {
            "sou_admin": sou_admin,
            "entrada": g.entrada,
            "pedido_pendente": bool(usuario and grupos.pedido(s, g.id, usuario)),
            "pedidos": [_pessoa(m.usuario, {"papel": m.papel}) for m in g.membros if m.status == "pendente"] if sou_admin else [],
            "membros_lista": [_pessoa(m.usuario, {"papel": m.papel}) for m in sorted(ativos, key=lambda m: (m.papel != "admin", m.usuario.nome))],
            "agenda": [ser.atividade(a, n, _dist(usuario, a.latitude, a.longitude)) for a in grupos.agenda(s, g.id)],
            "historico": [ser.atividade(a, n) for a in grupos.historico(s, g.id)],
            "avisos": [{"id": m.id, "texto": m.texto, "autor": m.usuario.arroba, "quando": ser.texto_quando(m.criado_em, n)} for m in grupos.avisos(s, g.id)],
        }
    )
    return d


def mensagem(m, n) -> dict:
    return {"id": m.id, "texto": m.texto, "aviso": m.aviso, "autor_id": m.usuario_id, "autor": m.usuario.arroba, "iniciais": m.usuario.iniciais, "quando": ser.texto_quando(m.criado_em, n), "hora": m.criado_em.strftime("%H:%M")}


def campeonato(s: SessaoORM, c: Campeonato, usuario: Usuario | None) -> dict:
    n = agora()
    d = ser.campeonato(c, n, _dist(usuario, c.latitude, c.longitude))
    gere = bool(usuario and campeonatos.pode_gerir(c, usuario))

    def equipe(e: Equipe) -> dict:
        ativos = [m for m in e.membros if m.status != "recusado"]
        return {
            "id": e.id, "nome": e.nome, "status": e.status, "capitao": e.capitao.arroba, "capitao_id": e.capitao_id,
            "completa": len([m for m in ativos if m.status == "confirmado"]) >= c.atletas_por_equipe,
            "componentes": contagem.get(e.id, 0),
            "elenco_acesso": bool(c.cadastro_elenco and usuario and (gere or e.capitao_id == usuario.id)),
            "membros": [_pessoa(m.usuario, {"status": m.status}) for m in ativos],
        }

    from . import chaves  # import local: chaves usa este módulo indiretamente

    pode_telao = bool(usuario and chaves.pode_pontuar(s, c, usuario))
    visiveis = [e for e in c.equipes if e.status in ("pendente", "confirmada")]
    contagem = elenco.contagens(s, [e.id for e in visiveis])
    d.update(
        {
            "capa_url": f"/capa/{c.capa}" if c.capa else None,
            "book_publicado": bool(c.book_publicado),
            "pode_telao": pode_telao,
            "transmissao_permitida": bool(c.transmissao_permitida),
            "descricao": c.descricao,
            "regulamento": c.regulamento,
            "cadastro_elenco": bool(c.cadastro_elenco),
            "regulamento_pdf": {"nome": c.regulamento_nome, "url": campeonatos.url_regulamento(c, usuario.id) if usuario else None, "em": c.regulamento_em.isoformat(timespec="minutes")} if c.regulamento_arquivo else None,
            "edicao": {
                "nome": c.nome, "categoria": c.categoria, "descricao": c.descricao, "regulamento": c.regulamento, "premiacao": c.premiacao,
                "premiacao_valor": float(c.premiacao_valor) if c.premiacao_valor is not None else None, "local_nome": c.local_nome,
                "data_inicio": c.data_inicio.isoformat(), "data_fim": c.data_fim.isoformat() if c.data_fim else None, "inscricao_ate": c.inscricao_ate.isoformat(),
                "max_equipes": c.max_equipes, "atletas_por_equipe": c.atletas_por_equipe, "valor_inscricao": float(c.valor_inscricao or 0), "cadastro_elenco": bool(c.cadastro_elenco),
            } if gere else None,
            "premiacao_valor": float(c.premiacao_valor) if c.premiacao_valor else None,
            "status": c.status,
            "sou_gestor": gere,
            "mural_acesso": escopos.pode_ler(s, usuario, "campeonato", c.id) if usuario else False,
            "sou_moderador": escopos.eh_moderador(s, usuario, "campeonato", c.id) if usuario else False,
            "moderadores": [{"usuario_id": m.id, "arroba": m.arroba} for m in escopos.moderadores(s, "campeonato", c.id)] if gere else [],
            "equipes_lista": [equipe(e) for e in visiveis],
            "pendentes": campeonatos.contar_pendentes(s, c) if gere else 0,
            "minha_situacao": campeonatos.minha_situacao(s, c, usuario) if usuario else {"estado": "fora", "equipe_id": None},
        }
    )
    return d


def arena_publica(s: SessaoORM, a: Arena, usuario: Usuario | None) -> dict:
    n = agora()
    horarios = s.scalars(
        select(HorarioDivulgado).join(HorarioDivulgado.quadra).where(HorarioDivulgado.quadra.has(arena_id=a.id), HorarioDivulgado.inicio >= n).order_by(HorarioDivulgado.inicio).limit(30)
    ).unique()
    d = ser.arena(a, _dist(usuario, a.latitude, a.longitude))
    d.update(
        {
            "descricao": a.descricao,
            "regras": a.regras,
            "abre": a.abre.strftime("%H:%M"),
            "fecha": a.fecha.strftime("%H:%M"),
            "sou_gestor": bool(usuario and (usuario.admin or usuario in a.gestores)),
            "quadras_lista": [{"id": q.id, "nome": q.nome, "tipo": q.tipo or "quadra", "modalidades": q.modalidades, "capacidade": q.capacidade, "valor_hora": float(q.valor_hora or 0), "valor_texto": ser.valor_texto(q.valor_hora, "/hora")} for q in a.quadras if q.ativa],
            "horarios": [ser.horario(h, n, d["distancia_km"]) for h in horarios],
        }
    )
    return d
