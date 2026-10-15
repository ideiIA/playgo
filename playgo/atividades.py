"""Criar, ajustar e cancelar atividades (RF-001, RF-014, RF-019)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import arenas, auditoria, convites, match, notificacoes, planos
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import (
    ABERTA,
    ATIVAS,
    CANCELADA,
    CONFIRMADO,
    R_ATIVIDADE,
    Arena,
    Atividade,
    Grupo,
    GrupoMembro,
    HorarioDivulgado,
    Modalidade,
    Participacao,
    Quadra,
    Reserva,
    Usuario,
)
from .vagas import repor, travar

NIVEIS_VALIDOS = ("todos", "iniciante", "intermediario", "avancado")
CATEGORIAS_VALIDAS = ("misto", "masculino", "feminino")


@dataclass
class NovaAtividade:
    modalidade_id: int
    nome: str
    inicio: datetime
    max_participantes: int
    local_nome: str = ""
    latitude: float | None = None
    longitude: float | None = None
    arena_id: int | None = None
    quadra_id: int | None = None
    grupo_id: int | None = None
    duracao_min: int = 90
    nivel: str = "todos"
    categoria: str = "misto"
    idade_min: int | None = None
    idade_max: int | None = None
    valor: Decimal = Decimal(0)
    descricao: str | None = None
    regras: str | None = None
    percurso: str | None = None
    exige_aprovacao: bool = False  # legado: equivale a visibilidade='autorizados'
    visibilidade: str = "publica"  # publica | autorizados | link
    falta_gente: bool = False


def obter(s: SessaoORM, atividade_id: int) -> Atividade:
    a = s.get(Atividade, atividade_id)
    if a is None:
        raise NaoEncontrado("Atividade não encontrada.")
    return a


def _exigir_organizacao(a: Atividade, usuario: Usuario) -> None:
    if usuario.id != a.organizador_id and not usuario.admin:
        raise SemPermissao("Só quem organiza a atividade pode fazer isso.")


def definir_capa(s: SessaoORM, atividade_id: int, por: Usuario, dados: bytes) -> Atividade:
    """Arte/logo da atividade (capa do book). Substitui a anterior. Só o organizador."""
    a = obter(s, atividade_id)
    _exigir_organizacao(a, por)
    from . import armazenamento, midia

    novo = midia.salvar_capa(midia.processar_capa(dados))
    anterior = a.capa
    a.capa = novo
    auditoria.registrar(s, por.id, "atividade_capa", "atividade", a.id)
    s.commit()
    if anterior:
        armazenamento.remover(f"capas/{anterior}")
    return a


def definir_book_publicado(s: SessaoORM, atividade_id: int, por: Usuario, publicado: bool) -> Atividade:
    a = obter(s, atividade_id)
    _exigir_organizacao(a, por)
    a.book_publicado = publicado
    auditoria.registrar(s, por.id, "atividade_book", "atividade", a.id, publicado=publicado)
    s.commit()
    return a


def _pode_usar_quadra(s: SessaoORM, quadra: Quadra, usuario: Usuario, inicio: datetime) -> HorarioDivulgado | None:
    """Gestor da arena usa a quadra livremente; qualquer atleta só no horário que o gestor divulgou."""
    if usuario.admin or usuario in quadra.arena.gestores:
        return None
    h = s.scalar(select(HorarioDivulgado).where(HorarioDivulgado.quadra_id == quadra.id, HorarioDivulgado.inicio == inicio))
    if h is None:
        raise SemPermissao("Essa quadra só pode ser usada por quem a administra ou em horários divulgados pela arena.")
    return h


def criar(s: SessaoORM, organizador: Usuario, d: NovaAtividade) -> Atividade:
    modalidade = s.get(Modalidade, d.modalidade_id)
    if modalidade is None or not modalidade.ativo:
        raise ErroNegocio("Escolha uma modalidade.")
    if not d.nome.strip():
        raise ErroNegocio("Dê um nome à atividade.")
    if d.inicio < agora() - timedelta(minutes=5):
        raise ErroNegocio("A data e o horário precisam estar no futuro.")
    if d.max_participantes < 2:
        raise ErroNegocio("A atividade precisa de pelo menos 2 participantes.")
    if d.nivel not in NIVEIS_VALIDOS or d.categoria not in CATEGORIAS_VALIDAS:
        raise ErroNegocio("Nível ou categoria inválidos.")
    if d.visibilidade not in ("publica", "autorizados", "link"):
        raise ErroNegocio("Escolha quem pode entrar: pública, só autorizados ou só por link.")
    visibilidade = "autorizados" if (d.exige_aprovacao and d.visibilidade == "publica") else d.visibilidade
    if d.valor < 0:
        raise ErroNegocio("O valor não pode ser negativo.")
    planos.exigir(s, organizador, "atividade", max_participantes=d.max_participantes)  # organizar atividade é do Pro; as grandes pedem Organizador
    if d.duracao_min < 15:
        raise ErroNegocio("A duração mínima é de 15 minutos.")
    if d.idade_min is not None and d.idade_max is not None and d.idade_min > d.idade_max:
        raise ErroNegocio("A idade mínima não pode ser maior que a máxima.")

    arena: Arena | None = s.get(Arena, d.arena_id) if d.arena_id else None
    quadra: Quadra | None = s.get(Quadra, d.quadra_id) if d.quadra_id else None
    if quadra is not None:
        arena = quadra.arena
        if modalidade.codigo not in quadra.modalidades and quadra.modalidades:
            raise ErroNegocio(f"A quadra {quadra.nome} não é de {modalidade.nome}.")
    lat, lng, local = d.latitude, d.longitude, d.local_nome.strip()
    if arena is not None:
        lat, lng = (lat if lat is not None else arena.latitude), (lng if lng is not None else arena.longitude)
        local = local or (f"{arena.nome} — {quadra.nome}" if quadra else arena.nome)
    if lat is None or lng is None:
        raise ErroNegocio("Marque o local no mapa ou escolha uma arena." if modalidade.usa_quadra else "Marque o ponto de encontro no mapa.")
    if not local:
        raise ErroNegocio("Informe o local ou o ponto de encontro.")

    grupo = None
    if d.grupo_id:
        grupo = s.get(Grupo, d.grupo_id)
        vinculo = s.get(GrupoMembro, (grupo.id, organizador.id)) if grupo is not None else None
        if grupo is None or vinculo is None or vinculo.status != "ativo":
            raise SemPermissao("Só membros do grupo podem criar atividades nele.")

    usado = _pode_usar_quadra(s, quadra, organizador, d.inicio) if quadra is not None else None

    a = Atividade(
        organizador_id=organizador.id, modalidade_id=modalidade.id, grupo_id=grupo.id if grupo else None,
        arena_id=arena.id if arena else None, quadra_id=quadra.id if quadra else None,
        nome=d.nome.strip(), descricao=d.descricao, regras=d.regras, percurso=d.percurso,
        local_nome=local, latitude=lat, longitude=lng, inicio=d.inicio, duracao_min=d.duracao_min,
        max_participantes=d.max_participantes, nivel=d.nivel, categoria=d.categoria,
        idade_min=d.idade_min, idade_max=d.idade_max, valor=d.valor, exige_aprovacao=visibilidade == "autorizados",
        visibilidade=visibilidade, convite_token=convites.novo_token("a") if visibilidade == "link" else None,
        falta_gente=d.falta_gente, falta_gente_em=agora() if d.falta_gente else None,
    )
    s.add(a)
    s.flush()

    if quadra is not None:
        if usado is not None:
            s.delete(usado)  # o horário divulgado virou jogo
            s.flush()
        arenas.reservar(s, quadra.id, d.inicio, a.fim, R_ATIVIDADE, f"{modalidade.nome}", a.id)

    # O organizador joga: já ocupa a primeira vaga.
    s.add(Participacao(atividade_id=a.id, usuario_id=organizador.id, status=CONFIRMADO))
    a.confirmados = 1
    s.flush()

    if grupo is not None:
        for m in s.scalars(select(GrupoMembro).where(GrupoMembro.grupo_id == grupo.id, GrupoMembro.status == "ativo", GrupoMembro.usuario_id != organizador.id)):
            if m.usuario.notif_grupos:
                notificacoes.avisar(
                    s, m.usuario_id, "grupo", f"{modalidade.icone} {grupo.nome} criou uma atividade",
                    f"{a.nome} — {a.inicio.strftime('%d/%m às %H:%M')}", f"/atividades/{a.id}", a.id, f"grp:{a.id}",
                )
    auditoria.registrar(s, organizador.id, "atividade_criar", "atividade", a.id, visibilidade=visibilidade, falta_gente=a.falta_gente)
    s.commit()
    # Atletas próximos que combinam com a atividade (atividade por link é privada: ninguém é avisado)
    match.avisar_compativeis(s, a, "falta_gente" if a.falta_gente else "nova")
    s.commit()
    return a


def definir_falta_gente(s: SessaoORM, atividade_id: int, por: Usuario, ligado: bool) -> Atividade:
    """Liga/desliga o destaque 'Falta gente' (RF-014) e, ao ligar, procura atletas compatíveis (RF-015)."""
    a = travar(s, atividade_id)
    if a.organizador_id != por.id and not por.admin:
        raise SemPermissao("Só o organizador pode fazer isso.")
    if ligado:
        if a.status != ABERTA or a.inicio < agora():
            raise ErroNegocio("Só atividades que ainda vão acontecer podem pedir gente.")
        if a.vagas <= 0:
            raise ErroNegocio("A atividade já está completa.")
        a.falta_gente = True
        a.falta_gente_em = agora()
        s.flush()
        match.avisar_compativeis(s, a, "falta_gente")
    else:
        a.falta_gente = False
    s.commit()
    return a


def alterar_capacidade(s: SessaoORM, atividade_id: int, por: Usuario, novo_max: int) -> Atividade:
    a = travar(s, atividade_id)
    if a.organizador_id != por.id and not por.admin:
        raise SemPermissao("Só o organizador pode fazer isso.")
    if novo_max < a.confirmados:
        raise ErroNegocio(f"Já há {a.confirmados} confirmados; o limite não pode ficar abaixo disso.")
    a.max_participantes = novo_max
    repor(s, a)  # abriu vaga: chama a lista de espera
    s.commit()
    return a


def cancelar(s: SessaoORM, atividade_id: int, por: Usuario) -> Atividade:
    a = travar(s, atividade_id)
    if a.organizador_id != por.id and not por.admin:
        raise SemPermissao("Só o organizador pode cancelar.")
    if a.status != ABERTA:
        return a
    a.status = CANCELADA
    a.falta_gente = False
    for p in s.scalars(select(Participacao).where(Participacao.atividade_id == a.id, Participacao.status.in_(ATIVAS))):
        if p.usuario_id != por.id:
            notificacoes.avisar(s, p.usuario_id, "atividade", f"❌ {a.nome} foi cancelada", f"Estava marcada para {a.inicio.strftime('%d/%m às %H:%M')}.", f"/atividades/{a.id}", a.id, f"canc:{a.id}")
    for r in s.scalars(select(Reserva).where(Reserva.atividade_id == a.id)):
        s.delete(r)  # a quadra volta a ficar livre
    auditoria.registrar(s, por.id, "atividade_cancelar", "atividade", a.id)
    s.commit()
    return a


def encerrar_passadas(s: SessaoORM) -> int:
    """Marca como encerradas as atividades que já terminaram. Chamado pelo agendador."""
    n = 0
    for a in s.scalars(select(Atividade).where(Atividade.status == ABERTA, Atividade.inicio < agora())):
        if a.fim < agora():
            a.status = "encerrada"
            a.falta_gente = False
            n += 1
    if n:
        s.commit()
    return n
