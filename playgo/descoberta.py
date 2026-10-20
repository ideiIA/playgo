"""Descoberta por geolocalização (RF-003, RF-010, RF-011, RF-022): atividades, campeonatos, arenas,
grupos e horários de quadra ao redor do atleta, com os filtros do mapa."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import arenas as srv_arenas
from . import locais as srv_locais
from . import planos
from . import serializadores as ser
from .config import settings
from .db import agora
from .geo import caixa_sql, distancia_sql
from .models import (
    ABERTA,
    ATIVAS,
    C_ABERTO,
    C_ANDAMENTO,
    Arena,
    Atividade,
    Campeonato,
    Grupo,
    GrupoMembro,
    HorarioDivulgado,
    Modalidade,
    Participacao,
    Quadra,
    Usuario,
)

TIPOS = ("atividade", "campeonato", "arena", "grupo", "horario", "local")
RAIOS = (2, 5, 10, 20)  # None = toda a cidade


@dataclass
class Filtros:
    raio_km: float | None = settings.raio_padrao_km
    modalidades: list[str] = field(default_factory=list)  # códigos
    quando: str | None = None  # hoje | amanha | fim_de_semana
    periodo: str | None = None  # manha | tarde | noite
    nivel: str | None = None
    preco: str | None = None  # gratis | pago
    com_vagas: bool = False
    q: str = ""
    ordem: str = "relevancia"  # relevancia | distancia | horario
    tipos: tuple[str, ...] = TIPOS


def localizacao(usuario: Usuario | None, lat: float | None = None, lng: float | None = None) -> tuple[float, float, bool]:
    """(lat, lng, informada): o ponto da busca. A posição do aparelho vence a do perfil; sem nenhuma, a cidade padrão."""
    if lat is not None and lng is not None:
        return lat, lng, True
    if usuario is not None and usuario.tem_localizacao:
        return usuario.latitude, usuario.longitude, True
    return settings.latitude_padrao, settings.longitude_padrao, False


def janela(quando: str | None, n: datetime) -> tuple[datetime, datetime]:
    inicio = n - timedelta(minutes=settings.tolerancia_inicio_min)
    hoje = datetime.combine(n.date(), time.min)
    if quando == "hoje":
        return inicio, hoje + timedelta(days=1)
    if quando == "amanha":
        return hoje + timedelta(days=1), hoje + timedelta(days=2)
    if quando == "fim_de_semana":
        if n.weekday() >= 5:
            return inicio, hoje + timedelta(days=7 - n.weekday())  # até segunda de manhã
        sabado = hoje + timedelta(days=5 - n.weekday())
        return sabado, sabado + timedelta(days=2)
    return inicio, hoje + timedelta(days=15)


def _minha_participacao(s: SessaoORM, usuario: Usuario | None, ids: list[int]) -> dict[int, str]:
    if usuario is None or not ids:
        return {}
    return dict(s.execute(select(Participacao.atividade_id, Participacao.status).where(Participacao.usuario_id == usuario.id, Participacao.atividade_id.in_(ids), Participacao.status.in_(ATIVAS))).all())


def buscar_atividades(s: SessaoORM, usuario: Usuario | None, lat: float, lng: float, f: Filtros, limite: int | None = None) -> list[dict]:
    n = agora()
    ini, fim = janela(f.quando, n)
    dist = distancia_sql(Atividade.latitude, Atividade.longitude, lat, lng)
    q = (
        select(Atividade, dist)
        .join(Modalidade, Modalidade.id == Atividade.modalidade_id)
        .where(Atividade.status == ABERTA, Atividade.visibilidade != "link", Atividade.inicio >= ini, Atividade.inicio < fim)
    )
    if f.raio_km:
        q = q.where(caixa_sql(Atividade.latitude, Atividade.longitude, lat, lng, f.raio_km), dist <= f.raio_km)
    if f.modalidades:
        q = q.where(Modalidade.codigo.in_(f.modalidades))
    if f.nivel:
        q = q.where(Atividade.nivel.in_((f.nivel, "todos")))
    if f.preco == "gratis":
        q = q.where(Atividade.valor == 0)
    elif f.preco == "pago":
        q = q.where(Atividade.valor > 0)
    if f.com_vagas:
        q = q.where(Atividade.confirmados < Atividade.max_participantes)
    if f.periodo == "manha":
        q = q.where(extract("hour", Atividade.inicio) < 12)
    elif f.periodo == "tarde":
        q = q.where(extract("hour", Atividade.inicio).between(12, 17))
    elif f.periodo == "noite":
        q = q.where(extract("hour", Atividade.inicio) >= 18)
    if f.q.strip():
        t = f"%{f.q.strip()}%"
        q = q.where(or_(Atividade.nome.ilike(t), Atividade.local_nome.ilike(t), Modalidade.nome.ilike(t)))
    # Pega mais do que o necessário e ordena em Python: a nota de urgência combina quatro fatores
    q = q.order_by(Atividade.inicio).limit(300)
    linhas = list(s.execute(q).unique())
    minha = _minha_participacao(s, usuario, [a.id for a, _ in linhas])
    itens = [ser.atividade(a, n, km, minha.get(a.id)) for a, km in linhas]
    if f.ordem == "distancia":
        itens.sort(key=lambda i: (i["distancia_km"] is None, i["distancia_km"], i["inicio"]))
    elif f.ordem == "horario":
        itens.sort(key=lambda i: i["inicio"])
    else:
        itens.sort(key=lambda i: (-i["urgencia"], i["inicio"]))
    return itens[: limite or settings.limite_resultados]


def buscar_campeonatos(s: SessaoORM, lat: float, lng: float, f: Filtros, limite: int | None = None) -> list[dict]:
    n = agora()
    dist = distancia_sql(Campeonato.latitude, Campeonato.longitude, lat, lng)
    ini, fim = janela(f.quando, n)
    q = (
        select(Campeonato, dist)
        .join(Modalidade, Modalidade.id == Campeonato.modalidade_id)
        .where(or_(Campeonato.status == C_ANDAMENTO, and_(Campeonato.status == C_ABERTO, func.coalesce(Campeonato.data_fim, Campeonato.data_inicio) >= n.date())))  # em andamento fica na lista até a organização encerrar, mesmo passada a data
    )
    if f.quando:
        q = q.where(Campeonato.data_inicio >= ini.date(), Campeonato.data_inicio < fim.date())
    if f.raio_km:
        q = q.where(caixa_sql(Campeonato.latitude, Campeonato.longitude, lat, lng, f.raio_km), dist <= f.raio_km)
    if f.modalidades:
        q = q.where(Modalidade.codigo.in_(f.modalidades))
    if f.preco == "gratis":
        q = q.where(Campeonato.valor_inscricao == 0)
    elif f.preco == "pago":
        q = q.where(Campeonato.valor_inscricao > 0)
    if f.q.strip():
        t = f"%{f.q.strip()}%"
        q = q.where(or_(Campeonato.nome.ilike(t), Campeonato.local_nome.ilike(t), Modalidade.nome.ilike(t)))
    if f.com_vagas:
        q = q.where(Campeonato.inscricao_ate >= n.date())
    q = q.order_by(Campeonato.data_inicio, dist).limit(limite or settings.limite_resultados)
    itens = [ser.campeonato(c, n, km) for c, km in s.execute(q).unique()]
    if f.com_vagas:
        itens = [i for i in itens if i["inscricoes_abertas"]]
    return itens


def buscar_arenas(s: SessaoORM, lat: float, lng: float, f: Filtros, limite: int | None = None) -> list[dict]:
    dist = distancia_sql(Arena.latitude, Arena.longitude, lat, lng)
    q = select(Arena, dist).where(Arena.ativo)
    if f.raio_km:
        q = q.where(caixa_sql(Arena.latitude, Arena.longitude, lat, lng, f.raio_km), dist <= f.raio_km)
    if f.modalidades:
        q = q.where(Arena.quadras.any(Quadra.modalidades.overlap(f.modalidades)))
    if f.q.strip():
        t = f"%{f.q.strip()}%"
        q = q.where(or_(Arena.nome.ilike(t), Arena.cidade.ilike(t), Arena.endereco.ilike(t)))
    q = q.order_by(dist).limit(limite or settings.limite_resultados)
    linhas = list(s.execute(q).unique())
    n = agora()
    fim_do_dia = datetime.combine(n.date(), time.min) + timedelta(days=1)
    abertos = dict(
        s.execute(
            select(Quadra.arena_id, func.count())
            .join(HorarioDivulgado, HorarioDivulgado.quadra_id == Quadra.id)
            .where(Quadra.arena_id.in_([a.id for a, _ in linhas] or [0]), HorarioDivulgado.inicio >= n, HorarioDivulgado.inicio < fim_do_dia)
            .group_by(Quadra.arena_id)
        ).all()
    )
    itens = [ser.arena(a, km, abertos.get(a.id, 0)) for a, km in linhas]
    premium = planos.arenas_premium(s, [i["id"] for i in itens])
    for i in itens:
        i["premium"] = i["id"] in premium  # arena com plano pago: pino com coroa no mapa
    return itens


def buscar_grupos(s: SessaoORM, usuario: Usuario | None, lat: float, lng: float, f: Filtros, limite: int | None = None) -> list[dict]:
    dist = distancia_sql(Grupo.latitude, Grupo.longitude, lat, lng)
    membros = select(func.count()).where(GrupoMembro.grupo_id == Grupo.id, GrupoMembro.status == "ativo").correlate(Grupo).scalar_subquery()
    q = select(Grupo, dist, membros).join(Modalidade, Modalidade.id == Grupo.modalidade_id).where(Grupo.ativo)
    if f.raio_km:
        q = q.where(Grupo.latitude.is_not(None), caixa_sql(Grupo.latitude, Grupo.longitude, lat, lng, f.raio_km), dist <= f.raio_km)
    if f.modalidades:
        q = q.where(Modalidade.codigo.in_(f.modalidades))
    if f.q.strip():
        t = f"%{f.q.strip()}%"
        q = q.where(or_(Grupo.nome.ilike(t), Grupo.cidade.ilike(t), Modalidade.nome.ilike(t)))
    q = q.order_by(membros.desc()).limit(limite or settings.limite_resultados)
    meus = set(s.scalars(select(GrupoMembro.grupo_id).where(GrupoMembro.usuario_id == usuario.id, GrupoMembro.status == "ativo"))) if usuario else set()
    return [ser.grupo(g, int(m), km, g.id in meus) for g, km, m in s.execute(q).unique()]


def buscar_horarios(s: SessaoORM, lat: float, lng: float, f: Filtros, limite: int = 30) -> list[dict]:
    n = agora()
    ate = janela(f.quando, n)[1] if f.quando else n + timedelta(days=2)
    ids = list(s.scalars(select(Modalidade.id).where(Modalidade.codigo.in_(f.modalidades)))) if f.modalidades else None
    return [ser.horario(h, n, km) for h, km in srv_arenas.horarios_publicos(s, lat, lng, f.raio_km, ate, limite, ids)]


def explorar(s: SessaoORM, usuario: Usuario | None, lat: float | None = None, lng: float | None = None, f: Filtros | None = None) -> dict:
    """Tudo o que há perto do atleta, por tipo, mais a lista única de pontos para o mapa."""
    f = f or Filtros()
    la, ln, informada = localizacao(usuario, lat, lng)
    saida: dict = {"centro": {"latitude": la, "longitude": ln, "informada": informada}, "atividades": [], "campeonatos": [], "arenas": [], "grupos": [], "horarios": [], "locais": []}
    if "atividade" in f.tipos:
        saida["atividades"] = buscar_atividades(s, usuario, la, ln, f)
    if "campeonato" in f.tipos:
        saida["campeonatos"] = buscar_campeonatos(s, la, ln, f)
    if "arena" in f.tipos:
        saida["arenas"] = buscar_arenas(s, la, ln, f)
    if "grupo" in f.tipos:
        saida["grupos"] = buscar_grupos(s, usuario, la, ln, f)
    if "horario" in f.tipos:
        saida["horarios"] = buscar_horarios(s, la, ln, f)
    if "local" in f.tipos:
        saida["locais"] = srv_locais.listar(s, la, ln, f.raio_km, f.q, None, f.modalidades)
    saida["pontos"] = pontos_do_mapa(saida)
    saida["total"] = sum(len(saida[k]) for k in ("atividades", "campeonatos", "arenas", "grupos", "horarios", "locais"))
    return saida


def _rotulo_pino(i: dict) -> str:
    m = i.get("modalidade") or {}
    icone = m.get("icone", "🏟️")
    t = i["tipo"]
    if t == "atividade":
        if i["falta_gente"]:
            return f"🔥 {m['nome']} • falta{'m' if i['vagas'] > 1 else ''} {i['vagas']}"
        return f"{icone} {m['nome']} • {i['vagas']} vaga{'s' if i['vagas'] != 1 else ''}"
    if t == "campeonato":
        return f"🏆 {i['nome']}"
    if t == "arena":
        return f"🏟️ {i['nome']}"
    if t == "grupo":
        return f"{icone} {i['nome']}"
    if t == "local":
        return f"{i['icone']} {i['nome']}"
    return f"{icone} Quadra livre • {i['quando']}"


def pontos_do_mapa(res: dict) -> list[dict]:
    pontos = []
    for chave in ("atividades", "campeonatos", "arenas", "grupos", "horarios", "locais"):
        for i in res[chave]:
            if i.get("latitude") is None:
                continue
            pontos.append({
                "tipo": i["tipo"], "id": i["id"], "latitude": i["latitude"], "longitude": i["longitude"], "rotulo": _rotulo_pino(i), "distancia": i["distancia"],
                "urgente": bool(i.get("falta_gente")), "arena_id": i.get("arena_id"), "premium": bool(i.get("premium")) if i["tipo"] == "arena" else False,
                "icone": i.get("icone") if i["tipo"] == "local" else None, "cor": i.get("cor") if i["tipo"] == "local" else None,  # pino próprio por tipo de local
            })
    return pontos
