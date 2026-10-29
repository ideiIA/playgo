"""Gestão B2B da arena: quadras, agenda, divulgação de horários ociosos e painel (RF-005/006/023/024/027)."""

from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as SessaoORM

from . import notificacoes, planos
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .geo import distancia_sql, formatar_km
from .models import (
    ABERTA,
    C_ABERTO,
    C_ANDAMENTO,
    CONFIRMADO,
    E_CONFIRMADA,
    M_CONFIRMADO,
    R_ATIVIDADE,
    R_RESERVA,
    Arena,
    Atividade,
    Campeonato,
    Equipe,
    EquipeMembro,
    HorarioDivulgado,
    Modalidade,
    Participacao,
    Quadra,
    Reserva,
    Usuario,
    UsuarioModalidade,
)
from .serializadores import texto_quando, valor_texto


# ---------------------------------------------------------------- cadastro


def exigir_gestor(arena: Arena, usuario: Usuario) -> None:
    if not usuario.admin and usuario not in arena.gestores:
        raise SemPermissao("Só os gestores desta arena podem fazer isso.")


def obter(s: SessaoORM, arena_id: int) -> Arena:
    a = s.get(Arena, arena_id)
    if a is None or not a.ativo:
        raise NaoEncontrado("Arena não encontrada.")
    return a


def minhas(s: SessaoORM, usuario: Usuario) -> list[Arena]:
    q = select(Arena).where(Arena.ativo).order_by(Arena.nome)
    if not usuario.admin:
        q = q.where(Arena.gestores.any(Usuario.id == usuario.id))
    return list(s.scalars(q))


TIPOS_ESTABELECIMENTO = {"arena": "Arena", "academia": "Academia", "clube": "Clube", "escola": "Escola"}
TIPOS_ESPACO = {"quadra": "Quadra", "sala": "Sala", "piscina": "Piscina", "campo": "Campo", "outro": "Outro espaço"}


def criar(
    s: SessaoORM,
    usuario: Usuario,
    nome: str,
    latitude: float,
    longitude: float,
    endereco: str = "",
    cidade: str | None = None,
    descricao: str | None = None,
    estrutura: str | None = None,
    regras: str | None = None,
    abre: time = time(6, 0),
    fecha: time = time(23, 0),
    tipo_estabelecimento: str = "arena",
) -> Arena:
    planos.exigir(s, usuario, "arena")
    if tipo_estabelecimento not in TIPOS_ESTABELECIMENTO:
        raise ErroNegocio("Escolha o tipo de estabelecimento: arena, academia, clube ou escola.")
    if not nome.strip():
        raise ErroNegocio("Informe o nome da arena.")
    if abre >= fecha:
        raise ErroNegocio("O horário de abertura precisa ser anterior ao de fechamento.")
    arena = Arena(
        nome=nome.strip(), latitude=latitude, longitude=longitude, endereco=endereco.strip(), cidade=cidade,
        descricao=descricao, estrutura=estrutura, regras=regras, abre=abre, fecha=fecha, tipo_estabelecimento=tipo_estabelecimento,
    )
    arena.gestores.append(usuario)
    usuario.gestor = True  # quem cadastra uma arena ganha a área Gestão
    s.add(arena)
    s.commit()
    return arena


def criar_quadra(s: SessaoORM, arena: Arena, usuario: Usuario, nome: str, modalidades: list[str], capacidade: int | None, valor_hora: Decimal, tipo: str = "quadra") -> Quadra:
    exigir_gestor(arena, usuario)
    planos.exigir(s, usuario, "arena")
    if not nome.strip():
        raise ErroNegocio("Informe o nome do espaço.")
    if tipo not in TIPOS_ESPACO:
        raise ErroNegocio("Escolha o tipo do espaço: quadra, sala, piscina, campo ou outro.")
    if valor_hora < 0:
        raise ErroNegocio("O valor da hora não pode ser negativo.")
    codigos = set(s.scalars(select(Modalidade.codigo).where(Modalidade.codigo.in_(modalidades or ["-"]))))
    q = Quadra(arena_id=arena.id, nome=nome.strip(), tipo=tipo, modalidades=sorted(codigos), capacidade=capacidade, valor_hora=valor_hora)
    s.add(q)
    s.commit()
    return q


def quadra_do_gestor(s: SessaoORM, quadra_id: int, usuario: Usuario) -> Quadra:
    q = s.get(Quadra, quadra_id)
    if q is None:
        raise NaoEncontrado("Quadra não encontrada.")
    exigir_gestor(q.arena, usuario)
    return q


# ---------------------------------------------------------------- reservas


def _conflito(s: SessaoORM, quadra_id: int, inicio: datetime, fim: datetime) -> Reserva | None:
    return s.scalar(select(Reserva).where(Reserva.quadra_id == quadra_id, Reserva.inicio < fim, Reserva.fim > inicio).limit(1))


def reservar(
    s: SessaoORM,
    quadra_id: int,
    inicio: datetime,
    fim: datetime,
    tipo: str = R_RESERVA,
    rotulo: str = "",
    atividade_id: int | None = None,
) -> Reserva:
    """Ocupa a quadra. Trava a linha da quadra: dois pedidos para o mesmo horário não passam os dois."""
    if fim <= inicio:
        raise ErroNegocio("O fim precisa ser depois do início.")
    quadra = s.scalar(select(Quadra).where(Quadra.id == quadra_id).with_for_update())
    if quadra is None or not quadra.ativa:
        raise NaoEncontrado("Quadra não encontrada.")
    if _conflito(s, quadra_id, inicio, fim):
        raise ErroNegocio("A quadra já está ocupada nesse horário.")
    r = Reserva(quadra_id=quadra_id, inicio=inicio, fim=fim, tipo=tipo, rotulo=rotulo, atividade_id=atividade_id)
    s.add(r)
    # O horário deixou de estar ocioso: sai da vitrine.
    s.execute(delete(HorarioDivulgado).where(HorarioDivulgado.quadra_id == quadra_id, HorarioDivulgado.inicio < fim, HorarioDivulgado.fim > inicio))
    s.flush()
    return r


def liberar(s: SessaoORM, reserva_id: int, usuario: Usuario) -> None:
    r = s.get(Reserva, reserva_id)
    if r is None:
        raise NaoEncontrado("Reserva não encontrada.")
    exigir_gestor(r.quadra.arena, usuario)
    if r.tipo == R_ATIVIDADE:
        raise ErroNegocio("Esse horário pertence a uma atividade. Cancele a atividade para liberar a quadra.")
    s.delete(r)
    s.commit()


# ---------------------------------------------------------------- agenda


def _janela(arena: Arena, dia: date) -> tuple[datetime, datetime]:
    return datetime.combine(dia, arena.abre), datetime.combine(dia, arena.fecha)


def agenda_do_dia(s: SessaoORM, arena: Arena, dia: date) -> dict:
    """Grade horário × quadra (RF-006). Cada célula: livre | ocupado | divulgado."""
    abre, fecha = _janela(arena, dia)
    quadras = [q for q in arena.quadras if q.ativa]
    ids = [q.id for q in quadras] or [0]
    reservas = list(s.scalars(select(Reserva).where(Reserva.quadra_id.in_(ids), Reserva.inicio < fecha, Reserva.fim > abre).order_by(Reserva.inicio)))
    divulgados = list(s.scalars(select(HorarioDivulgado).where(HorarioDivulgado.quadra_id.in_(ids), HorarioDivulgado.inicio >= abre, HorarioDivulgado.inicio < fecha)))
    agora_ = agora()
    linhas = []
    h = abre.replace(minute=0)
    while h < fecha:
        h2 = h + timedelta(hours=1)
        celulas = []
        for q in quadras:
            r = next((x for x in reservas if x.quadra_id == q.id and x.inicio < h2 and x.fim > h), None)
            d = next((x for x in divulgados if x.quadra_id == q.id and x.inicio < h2 and x.fim > h), None)
            if r:
                celula = {
                    "estado": "ocupado", "rotulo": r.rotulo or ("Reservada" if r.tipo == R_RESERVA else "Bloqueada"), "reserva_id": r.id, "tipo": r.tipo, "atividade_id": r.atividade_id,
                    "status_pagamento": r.status_pagamento, "invoice_url": r.invoice_url, "valor_hora": float(r.valor or q.valor_hora or 0),
                }
            elif d:
                celula = {"estado": "divulgado", "rotulo": "Divulgado", "horario_id": d.id}
            else:
                celula = {"estado": "livre", "rotulo": "Livre"}
            celula["quadra_id"] = q.id
            celula["passado"] = h2 <= agora_
            celulas.append(celula)
        linhas.append({"hora": h.strftime("%H:%M"), "inicio": h.isoformat(timespec="minutes"), "celulas": celulas})
        h = h2
    return {"dia": dia.isoformat(), "quadras": [{"id": q.id, "nome": q.nome, "modalidades": q.modalidades, "valor_hora": float(q.valor_hora or 0)} for q in quadras], "linhas": linhas}


# ---------------------------------------------------------------- divulgação (RF-024)


def _publico_do_horario(s: SessaoORM, h: HorarioDivulgado) -> list[tuple[Usuario, float]]:
    q = h.quadra
    arena = q.arena
    ids = [h.modalidade_id] if h.modalidade_id else list(s.scalars(select(Modalidade.id).where(Modalidade.codigo.in_(q.modalidades or ["-"]))))
    if not ids:
        return []
    dist = distancia_sql(Usuario.latitude, Usuario.longitude, arena.latitude, arena.longitude)
    consulta = (
        select(Usuario, dist)
        .where(
            Usuario.ativo,
            Usuario.notif_vagas,
            Usuario.latitude.is_not(None),
            dist <= Usuario.notif_raio_km,
            Usuario.id.in_(select(UsuarioModalidade.usuario_id).where(UsuarioModalidade.modalidade_id.in_(ids))),
        )
        .limit(500)
    )
    return [(u, km) for u, km in s.execute(consulta).unique()]


def divulgar(s: SessaoORM, quadra_id: int, inicio: datetime, fim: datetime, valor: Decimal | None, modalidade_id: int | None, usuario: Usuario, avisar: bool = True) -> HorarioDivulgado:
    """Transforma um horário livre em oportunidade pública e avisa os atletas próximos que jogam o esporte."""
    quadra = quadra_do_gestor(s, quadra_id, usuario)
    planos.exigir(s, usuario, "arena")
    if fim <= inicio:
        raise ErroNegocio("O fim precisa ser depois do início.")
    if inicio < agora() - timedelta(minutes=30):
        raise ErroNegocio("Esse horário já passou.")
    if _conflito(s, quadra_id, inicio, fim):
        raise ErroNegocio("A quadra está ocupada nesse horário.")
    existente = s.scalar(select(HorarioDivulgado).where(HorarioDivulgado.quadra_id == quadra_id, HorarioDivulgado.inicio == inicio))
    if existente:
        return existente
    if modalidade_id is None and len(quadra.modalidades) == 1:
        modalidade_id = s.scalar(select(Modalidade.id).where(Modalidade.codigo == quadra.modalidades[0]))
    h = HorarioDivulgado(quadra_id=quadra_id, inicio=inicio, fim=fim, modalidade_id=modalidade_id, valor=valor if valor is not None else quadra.valor_hora)
    s.add(h)
    s.flush()
    if avisar:
        n = agora()
        for u, km in _publico_do_horario(s, h):
            m = h.modalidade.nome if h.modalidade else "Quadra"
            icone = h.modalidade.icone if h.modalidade else "🏟️"
            quando = texto_quando(inicio, n).lower().replace(" • ", " às ")
            notificacoes.avisar(
                s, u.id, "arena", f"{icone} Quadra disponível {quando}",
                f"{m} • {quadra.arena.nome} • {formatar_km(km)} • {valor_texto(h.valor, '/hora')}",
                f"/arenas/{quadra.arena_id}", None, f"hd:{h.id}",
            )
    s.commit()
    return h


def despublicar(s: SessaoORM, horario_id: int, usuario: Usuario) -> None:
    h = s.get(HorarioDivulgado, horario_id)
    if h is None:
        return
    exigir_gestor(h.quadra.arena, usuario)
    s.delete(h)
    s.commit()


def divulgar_ociosos(s: SessaoORM, arena: Arena, usuario: Usuario, dia: date | None = None) -> int:
    """Publica de uma vez todos os horários livres que ainda restam no dia (o botão 'Divulgar horário')."""
    exigir_gestor(arena, usuario)
    planos.exigir(s, usuario, "arena")
    n = agora()
    dia = dia or n.date()
    abre, fecha = _janela(arena, dia)
    h = max(abre.replace(minute=0), (n + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) if dia == n.date() else abre)
    total = 0
    quadras = [q for q in arena.quadras if q.ativa]
    while h < fecha:
        h2 = h + timedelta(hours=1)
        for q in quadras:
            if not _conflito(s, q.id, h, h2) and not s.scalar(select(HorarioDivulgado.id).where(HorarioDivulgado.quadra_id == q.id, HorarioDivulgado.inicio == h)):
                divulgar(s, q.id, h, h2, None, None, usuario)
                total += 1
        h = h2
    return total


def horarios_publicos(s: SessaoORM, lat: float, lng: float, raio_km: float | None, ate: datetime, limite: int = 30, modalidade_ids: list[int] | None = None) -> list[tuple[HorarioDivulgado, float]]:
    from .geo import caixa_sql

    dist = distancia_sql(Arena.latitude, Arena.longitude, lat, lng)
    q = (
        select(HorarioDivulgado, dist)
        .join(Quadra, Quadra.id == HorarioDivulgado.quadra_id)
        .join(Arena, Arena.id == Quadra.arena_id)
        .where(HorarioDivulgado.inicio >= agora(), HorarioDivulgado.inicio <= ate, Arena.ativo, Quadra.ativa)
        .order_by(HorarioDivulgado.inicio, dist)
        .limit(limite)
    )
    if raio_km:
        q = q.where(caixa_sql(Arena.latitude, Arena.longitude, lat, lng, raio_km), dist <= raio_km)
    if modalidade_ids:
        q = q.where(HorarioDivulgado.modalidade_id.in_(modalidade_ids))
    return [(h, km) for h, km in s.execute(q).unique()]


# ---------------------------------------------------------------- painel (RF-027)


def indicadores(s: SessaoORM, arena: Arena, dia: date | None = None) -> dict:
    n = agora()
    dia = dia or n.date()
    abre, fecha = _janela(arena, dia)
    quadras = [q for q in arena.quadras if q.ativa]
    ids = [q.id for q in quadras] or [0]
    horas_janela = (fecha - abre).total_seconds() / 3600

    reservas = list(s.scalars(select(Reserva).where(Reserva.quadra_id.in_(ids), Reserva.inicio < fecha, Reserva.fim > abre)))
    ocupado_h = 0.0
    por_hora: dict[int, float] = {}
    for r in reservas:
        ini, fim = max(r.inicio, abre), min(r.fim, fecha)
        ocupado_h += (fim - ini).total_seconds() / 3600
        h = ini.replace(minute=0, second=0, microsecond=0)
        while h < fim:
            sobreposto = (min(fim, h + timedelta(hours=1)) - max(ini, h)).total_seconds() / 3600
            por_hora[h.hour] = por_hora.get(h.hour, 0.0) + sobreposto
            h += timedelta(hours=1)
    capacidade_h = len(quadras) * horas_janela
    ocupacao = round(100 * ocupado_h / capacidade_h) if capacidade_h else 0

    # Horários ociosos que ainda dá tempo de vender hoje
    proxima_hora = max(abre, n.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)) if dia == n.date() else abre
    ociosos = divulgados = 0
    divulgados_ids = {(d.quadra_id, d.inicio) for d in s.scalars(select(HorarioDivulgado).where(HorarioDivulgado.quadra_id.in_(ids), HorarioDivulgado.inicio >= abre, HorarioDivulgado.inicio < fecha))}
    h = proxima_hora
    while h < fecha:
        h2 = h + timedelta(hours=1)
        for q in quadras:
            if any(r.quadra_id == q.id and r.inicio < h2 and r.fim > h for r in reservas):
                continue
            if (q.id, h) in divulgados_ids:
                divulgados += 1
            else:
                ociosos += 1
        h = h2

    ativ_arena = select(Atividade.id).where(Atividade.arena_id == arena.id)
    eventos_atividades = s.scalar(select(func.count()).select_from(Atividade).where(Atividade.arena_id == arena.id, Atividade.status == ABERTA, Atividade.inicio >= n)) or 0
    campeonatos = s.scalar(select(func.count()).select_from(Campeonato).where(Campeonato.arena_id == arena.id, Campeonato.status.in_((C_ABERTO, C_ANDAMENTO)))) or 0

    # Atletas alcançados: quem já confirmou presença em jogo da arena ou jogou campeonato dela
    de_jogos = select(Participacao.usuario_id).where(Participacao.atividade_id.in_(ativ_arena), Participacao.status == CONFIRMADO)
    de_camp = (
        select(EquipeMembro.usuario_id)
        .join(Equipe, Equipe.id == EquipeMembro.equipe_id)
        .join(Campeonato, Campeonato.id == Equipe.campeonato_id)
        .where(Campeonato.arena_id == arena.id, EquipeMembro.status == M_CONFIRMADO, Equipe.status.in_(("pendente", E_CONFIRMADA)))
    )
    alcancados = s.scalar(select(func.count()).select_from(de_jogos.union(de_camp).subquery())) or 0

    # Clientes recorrentes: 2 ou mais participações confirmadas em jogos da arena
    recorrentes = s.scalar(
        select(func.count()).select_from(
            select(Participacao.usuario_id).where(Participacao.atividade_id.in_(ativ_arena), Participacao.status == CONFIRMADO).group_by(Participacao.usuario_id).having(func.count() >= 2).subquery()
        )
    ) or 0

    # Modalidades mais procuradas e dias de maior demanda (últimos 30 dias + próximos)
    desde = n - timedelta(days=30)
    contagem = Counter()
    nomes: dict[int, tuple[str, str]] = {}
    for a in s.scalars(select(Atividade).where(Atividade.arena_id == arena.id, Atividade.inicio >= desde, Atividade.status != "cancelada")):
        contagem[a.modalidade_id] += max(1, a.confirmados)
        nomes[a.modalidade_id] = (a.modalidade.nome, a.modalidade.icone)
    modalidades = [{"nome": nomes[i][0], "icone": nomes[i][1], "total": t} for i, t in contagem.most_common(5)]
    por_dia = Counter()
    for r in s.scalars(select(Reserva).where(Reserva.quadra_id.in_(ids), Reserva.inicio >= desde)):
        por_dia[r.inicio.weekday()] += 1
    dias = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")

    return {
        "dia": dia.isoformat(),
        "reservas_hoje": sum(1 for r in reservas if r.tipo in (R_RESERVA, R_ATIVIDADE) and abre <= r.inicio < fecha),
        "ocupacao_pct": ocupacao,
        "eventos_ativos": eventos_atividades + campeonatos,
        "campeonatos": campeonatos,
        "atletas_alcancados": alcancados,
        "horarios_ociosos": ociosos,
        "horarios_divulgados": divulgados,
        "clientes_recorrentes": recorrentes,
        "modalidades_procuradas": modalidades,
        "ocupacao_por_hora": [{"hora": f"{h:02d}h", "pct": round(100 * por_hora.get(h, 0) / len(quadras)) if quadras else 0} for h in range(abre.hour, fecha.hour)],
        "demanda_por_dia": [{"dia": dias[d], "total": por_dia.get(d, 0)} for d in range(7)],
    }
