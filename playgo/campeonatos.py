"""Campeonatos e torneios (RF-007, RF-008, RF-021, RF-022): cadastro, inscrição de equipes e convites.
Sorteio, chaves, classificação e jogo ao vivo (RF-009) ficam em `chaves.py`."""

import re
import secrets
import zlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import armazenamento, auditoria, notificacoes, planos, seguranca
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .geo import distancia_sql, formatar_km
from .models import (
    C_ABERTO,
    C_CANCELADO,
    C_ENCERRADO,
    E_CANCELADA,
    E_CONFIRMADA,
    E_PENDENTE,
    E_RECUSADA,
    M_CONFIRMADO,
    M_CONVIDADO,
    M_RECUSADO,
    Arena,
    Campeonato,
    Equipe,
    EquipeMembro,
    Modalidade,
    Usuario,
    UsuarioModalidade,
)

ATIVAS_EQUIPE = (E_PENDENTE, E_CONFIRMADA)

# Esportes que se jogam em sets: código da modalidade → (melhor de, pontos do set, pontos do set decisivo). A organização pode mudar.
PLACAR_POR_SETS = {
    "volei": (3, 25, 15),
    "volei_de_areia": (3, 21, 15),
    "futevolei": (3, 18, 15),
    "beach_tennis": (3, 6, 10),
    "tenis": (3, 6, 10),
    "padel": (3, 6, 10),
}


# Esportes cuja contagem é em games (jogo = set de N games, 7-6 no tie-break; decisivo = super tie-break). A organização pode mudar.
PLACAR_EM_GAMES = {"beach_tennis", "tenis", "padel"}


@dataclass
class NovoCampeonato:
    modalidade_id: int
    nome: str
    data_inicio: date
    inscricao_ate: date
    max_equipes: int
    atletas_por_equipe: int = 5
    arena_id: int | None = None
    data_fim: date | None = None
    categoria: str | None = None
    local_nome: str = ""
    latitude: float | None = None
    longitude: float | None = None
    valor_inscricao: Decimal = Decimal(0)
    premiacao: str | None = None
    premiacao_valor: Decimal | None = None
    regulamento: str | None = None
    descricao: str | None = None


def obter(s: SessaoORM, campeonato_id: int) -> Campeonato:
    c = s.get(Campeonato, campeonato_id)
    if c is None:
        raise NaoEncontrado("Campeonato não encontrado.")
    return c


def pode_gerir(c: Campeonato, usuario: Usuario) -> bool:
    return usuario.admin or c.organizador_id == usuario.id or (c.arena is not None and usuario in c.arena.gestores)


def exigir_gestao(c: Campeonato, usuario: Usuario) -> None:
    if not pode_gerir(c, usuario):
        raise SemPermissao("Só quem organiza o campeonato pode fazer isso.")


def encerrados(
    s: SessaoORM, usuario: Usuario, q: str = "", de: date | None = None, ate: date | None = None, organizador: str = "", limite: int = 100,
) -> dict:
    """Campeonatos já encerrados, para a organização. O administrador vê todos (e filtra por organizador); organizadores e arenas
    veem só os seus (que criaram ou que acontecem em arena sua). `de`/`ate` filtram pela data final (ou inicial, sem final)."""
    if not planos.permissoes(s, usuario)["campeonato"]:
        raise SemPermissao("O histórico de campeonatos encerrados é para quem organiza campeonatos.")
    fim = func.coalesce(Campeonato.data_fim, Campeonato.data_inicio)
    cons = select(Campeonato, fim.label("fim")).where(Campeonato.status == C_ENCERRADO)
    if not usuario.admin:
        minhas_arenas = [a.id for a in usuario.arenas]
        meu = Campeonato.organizador_id == usuario.id
        cons = cons.where(meu | Campeonato.arena_id.in_(minhas_arenas) if minhas_arenas else meu)
    if q.strip():
        cons = cons.where(Campeonato.nome.ilike(f"%{q.strip()}%"))
    if de is not None:
        cons = cons.where(fim >= de)
    if ate is not None:
        cons = cons.where(fim <= ate)
    if organizador.strip() and usuario.admin:
        t = f"%{organizador.strip().lstrip('@')}%"
        cons = cons.join(Usuario, Usuario.id == Campeonato.organizador_id).where(Usuario.usuario.ilike(t) | Usuario.nome.ilike(t))
    linhas = list(s.execute(cons.order_by(fim.desc(), Campeonato.id.desc()).limit(limite)).unique())
    ids = [c.id for c, _ in linhas]
    equipes = dict(s.execute(select(Equipe.campeonato_id, func.count()).where(Equipe.campeonato_id.in_(ids or [0]), Equipe.status == E_CONFIRMADA).group_by(Equipe.campeonato_id)).all())
    itens = [
        {
            "id": c.id, "nome": c.nome, "modalidade": c.modalidade.nome, "icone": c.modalidade.icone, "categoria": c.categoria,
            "organizador": c.organizador.arroba, "arena": c.arena.nome if c.arena is not None else None, "local": c.local_nome,
            "data_inicio": c.data_inicio.isoformat(), "data_fim": f.isoformat(), "equipes": int(equipes.get(c.id, 0)), "formato": c.formato,
        }
        for c, f in linhas
    ]
    return {"itens": itens, "todos": usuario.admin, "total": len(itens)}


def equipes_ativas(s: SessaoORM, c: Campeonato) -> int:
    """Consulta o banco: a coleção `c.equipes` pode estar desatualizada dentro da mesma sessão."""
    return s.scalar(select(func.count()).select_from(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE))) or 0


def criar(s: SessaoORM, usuario: Usuario, d: NovoCampeonato) -> Campeonato:
    modalidade = s.get(Modalidade, d.modalidade_id)
    if modalidade is None:
        raise ErroNegocio("Escolha a modalidade.")
    if not d.nome.strip():
        raise ErroNegocio("Dê um nome ao campeonato.")
    if d.max_equipes < 2:
        raise ErroNegocio("Um campeonato precisa de pelo menos 2 equipes.")
    planos.exigir(s, usuario, "campeonato")
    if d.atletas_por_equipe < 1:
        raise ErroNegocio("Informe quantos atletas formam uma equipe.")
    if d.data_fim and d.data_fim < d.data_inicio:
        raise ErroNegocio("A data final não pode ser anterior à inicial.")
    if d.valor_inscricao < 0:
        raise ErroNegocio("O valor da inscrição não pode ser negativo.")
    arena = s.get(Arena, d.arena_id) if d.arena_id else None
    if arena is not None and not usuario.admin and usuario not in arena.gestores:
        raise SemPermissao("Só gestores da arena criam campeonatos nela.")
    lat, lng = d.latitude, d.longitude
    local = d.local_nome.strip()
    if arena is not None:
        lat, lng, local = (lat if lat is not None else arena.latitude), (lng if lng is not None else arena.longitude), (local or arena.nome)
    if lat is None or lng is None or not local:
        raise ErroNegocio("Informe o local do campeonato (arena ou ponto no mapa).")
    c = Campeonato(
        organizador_id=usuario.id, arena_id=arena.id if arena else None, modalidade_id=d.modalidade_id,
        nome=d.nome.strip(), categoria=d.categoria, descricao=d.descricao, regulamento=d.regulamento,
        premiacao=d.premiacao, premiacao_valor=d.premiacao_valor, local_nome=local, latitude=lat, longitude=lng,
        data_inicio=d.data_inicio, data_fim=d.data_fim, inscricao_ate=d.inscricao_ate, max_equipes=d.max_equipes,
        atletas_por_equipe=d.atletas_por_equipe, valor_inscricao=d.valor_inscricao,
    )
    if modalidade.codigo in PLACAR_POR_SETS:
        c.placar_modo = "games" if modalidade.codigo in PLACAR_EM_GAMES else "sets"
        c.sets_melhor_de, c.pontos_set, c.pontos_tiebreak = PLACAR_POR_SETS[modalidade.codigo]
        c.diferenca_set = 2
    s.add(c)
    s.flush()
    planos.consumir_evento(s, usuario, c.id)  # quem comprou eventos avulsos gasta 1 crédito por campeonato
    _avisar_proximos(s, c)
    s.commit()
    return c


CAMPOS_EDITAVEIS = (
    "nome", "categoria", "descricao", "regulamento", "premiacao", "premiacao_valor", "local_nome", "data_inicio", "data_fim",
    "inscricao_ate", "max_equipes", "atletas_por_equipe", "valor_inscricao", "cadastro_elenco",
)


def editar(s: SessaoORM, campeonato_id: int, por: Usuario, **campos) -> Campeonato:
    """Organização, gestor da arena ou administrador ajustam o campeonato (nome, datas, vagas, valor, regulamento…).
    As equipes são avisadas quando mudam datas ou local. Só os campos informados mudam."""
    c = s.scalar(select(Campeonato).where(Campeonato.id == campeonato_id).with_for_update(of=Campeonato))
    if c is None:
        raise NaoEncontrado("Campeonato não encontrado.")
    exigir_gestao(c, por)
    if c.status == C_CANCELADO:
        raise ErroNegocio("Este campeonato foi cancelado e não pode mais ser alterado.")
    novos = {k: v for k, v in campos.items() if k in CAMPOS_EDITAVEIS}
    if not novos:
        return c
    if "nome" in novos:
        novos["nome"] = (novos["nome"] or "").strip()
        if not novos["nome"]:
            raise ErroNegocio("Dê um nome ao campeonato.")
        novos["nome"] = novos["nome"][:150]
    for k in ("categoria", "descricao", "regulamento", "premiacao"):
        if k in novos:
            novos[k] = (novos[k] or "").strip() or None
    if "local_nome" in novos:
        novos["local_nome"] = (novos["local_nome"] or "").strip()[:200]
        if not novos["local_nome"]:
            raise ErroNegocio("Informe o local do campeonato.")
    inicio = novos.get("data_inicio", c.data_inicio)
    fim = novos.get("data_fim", c.data_fim)
    if fim and fim < inicio:
        raise ErroNegocio("A data final não pode ser anterior à inicial.")
    if "max_equipes" in novos:
        ativas = equipes_ativas(s, c)
        if novos["max_equipes"] < max(2, ativas):
            raise ErroNegocio(f"O campeonato precisa de pelo menos 2 equipes e já tem {ativas} inscrita{'s' if ativas != 1 else ''}.")
    if "atletas_por_equipe" in novos:
        contagens = s.execute(
            select(func.count()).select_from(EquipeMembro).join(Equipe, Equipe.id == EquipeMembro.equipe_id)
            .where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE), EquipeMembro.status != M_RECUSADO).group_by(EquipeMembro.equipe_id)
        ).scalars().all()
        maior = max(contagens, default=0)
        if novos["atletas_por_equipe"] < max(1, maior):
            raise ErroNegocio(f"Atletas por equipe não pode ficar abaixo de {max(1, maior)}: já há equipe com essa quantidade.")
    if "valor_inscricao" in novos and (novos["valor_inscricao"] is None or novos["valor_inscricao"] < 0):
        raise ErroNegocio("O valor da inscrição não pode ser negativo.")
    antes = {k: getattr(c, k) for k in novos}
    for k, v in novos.items():
        setattr(c, k, v)
    mudou = {k: v for k, v in novos.items() if antes[k] != v}
    if mudou:
        auditoria.registrar(s, por.id, "campeonato_editar", "campeonato", c.id, campos=",".join(sorted(mudou)))
        if {"data_inicio", "data_fim", "inscricao_ate", "local_nome"} & set(mudou):
            versao = zlib.crc32(f"{c.data_inicio}{c.data_fim}{c.inscricao_ate}{c.local_nome}".encode())
            for e in s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE))):
                if e.capitao_id != por.id:
                    notificacoes.avisar(s, e.capitao_id, "campeonato", f"📅 {c.nome}: datas ou local atualizados", f"Início em {c.data_inicio.strftime('%d/%m/%Y')} · {c.local_nome}.", f"/campeonatos/{c.id}", None, f"campedit:{c.id}:{e.id}:{versao}")
    s.commit()
    return c


# ---------------------------------------------------------------- regulamento em PDF

_SERIALIZADOR_PDF_SAL = "playgo-regulamento"
VALIDADE_URL_PDF_S = 600


def _serializador_pdf() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(seguranca.chave_sessao(), salt=_SERIALIZADOR_PDF_SAL)


def url_regulamento(c: Campeonato, usuario_id: int) -> str | None:
    """URL do PDF já autorizada para essa pessoa (vale poucos minutos): o link do app não manda o cabeçalho Bearer."""
    if not c.regulamento_arquivo:
        return None
    token = _serializador_pdf().dumps({"c": c.id, "u": usuario_id})
    return f"/campeonatos/{c.id}/regulamento.pdf?t={token}"


def usuario_do_token_pdf(token: str, campeonato_id: int) -> int | None:
    try:
        d = _serializador_pdf().loads(token, max_age=VALIDADE_URL_PDF_S)
    except BadSignature:
        return None
    return d["u"] if d.get("c") == campeonato_id else None


def validar_pdf(dados: bytes) -> None:
    """Confere vazio, tamanho e o conteúdo (o início `%PDF-`, não só a extensão). Chamado antes de criar ou alterar qualquer coisa."""
    limite = settings.limite_pdf_mb
    if not dados:
        raise ErroNegocio("O arquivo está vazio.")
    if len(dados) > limite * 1024 * 1024:
        raise ErroNegocio(f"O PDF passa de {limite} MB. Reduza o arquivo e envie de novo.")
    if not dados.startswith(b"%PDF-"):
        raise ErroNegocio("Envie um arquivo PDF.")


def definir_capa(s: SessaoORM, campeonato_id: int, por: Usuario, dados: bytes) -> Campeonato:
    """Arte/logo do campeonato (capa do book). Substitui a anterior. Só a organização."""
    c = obter(s, campeonato_id)
    exigir_gestao(c, por)
    if c.status == C_CANCELADO:
        raise ErroNegocio("Este campeonato foi cancelado e não pode mais ser alterado.")
    from . import midia

    novo = midia.salvar_capa(midia.processar_capa(dados))
    anterior = c.capa
    c.capa = novo
    auditoria.registrar(s, por.id, "campeonato_capa", "campeonato", c.id)
    s.commit()
    if anterior:
        armazenamento.remover(f"capas/{anterior}")
    return c


def definir_book_publicado(s: SessaoORM, campeonato_id: int, por: Usuario, publicado: bool) -> Campeonato:
    c = obter(s, campeonato_id)
    exigir_gestao(c, por)
    c.book_publicado = publicado
    auditoria.registrar(s, por.id, "campeonato_book", "campeonato", c.id, publicado=publicado)
    s.commit()
    return c


def anexar_regulamento(s: SessaoORM, campeonato_id: int, por: Usuario, dados: bytes, nome_original: str | None) -> Campeonato:
    """Guarda o PDF do regulamento (substitui o anterior). Só a organização."""
    c = obter(s, campeonato_id)
    exigir_gestao(c, por)
    if c.status == C_CANCELADO:
        raise ErroNegocio("Este campeonato foi cancelado e não pode mais ser alterado.")
    validar_pdf(dados)
    nome = re.sub(r"[^\w .()\-]", "", (nome_original or "regulamento.pdf").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip()[:150] or "regulamento.pdf"
    if not nome.lower().endswith(".pdf"):
        nome += ".pdf"
    anterior = c.regulamento_arquivo
    rel = f"regulamentos/{c.id}/{secrets.token_hex(16)}.pdf"
    armazenamento.salvar(rel, dados, "application/pdf")
    c.regulamento_arquivo, c.regulamento_nome, c.regulamento_em = rel, nome, agora()
    auditoria.registrar(s, por.id, "campeonato_regulamento_pdf", "campeonato", c.id, arquivo=nome, bytes=len(dados))
    for e in s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE))):
        if e.capitao_id != por.id:
            notificacoes.avisar(s, e.capitao_id, "campeonato", f"📄 Regulamento de {c.nome} atualizado", "A organização enviou o regulamento em PDF.", f"/campeonatos/{c.id}", None, f"regpdf:{c.id}:{e.id}:{secrets.token_hex(3)}")
    s.commit()
    armazenamento.remover(anterior)
    return c


def remover_regulamento(s: SessaoORM, campeonato_id: int, por: Usuario) -> Campeonato:
    c = obter(s, campeonato_id)
    exigir_gestao(c, por)
    anterior = c.regulamento_arquivo
    if anterior:
        c.regulamento_arquivo = c.regulamento_nome = c.regulamento_em = None
        auditoria.registrar(s, por.id, "campeonato_regulamento_pdf_remover", "campeonato", c.id)
        s.commit()
        armazenamento.remover(anterior)
    return c


def _avisar_proximos(s: SessaoORM, c: Campeonato) -> None:
    """'🏆 Foram abertas inscrições para um campeonato de Beach Tennis próximo de você.'"""
    dist = distancia_sql(Usuario.latitude, Usuario.longitude, c.latitude, c.longitude)
    q = (
        select(Usuario, dist)
        .join(UsuarioModalidade, UsuarioModalidade.usuario_id == Usuario.id)
        .where(
            UsuarioModalidade.modalidade_id == c.modalidade_id,
            Usuario.ativo, Usuario.notif_campeonatos, Usuario.latitude.is_not(None),
            Usuario.id != c.organizador_id, dist <= Usuario.notif_raio_km,
        )
        .limit(500)
    )
    for u, km in s.execute(q).unique():
        notificacoes.avisar(
            s, u.id, "campeonato", f"🏆 Inscrições abertas: {c.nome}",
            f"Campeonato de {c.modalidade.nome} a {formatar_km(km)} de você, a partir de {c.data_inicio.strftime('%d/%m')}.",
            f"/campeonatos/{c.id}", None, f"camp:{c.id}",
        )


def definir_status(s: SessaoORM, campeonato_id: int, por: Usuario, status: str) -> Campeonato:
    c = obter(s, campeonato_id)
    exigir_gestao(c, por)
    if status not in ("aberto", "em_andamento", "encerrado", "cancelado"):
        raise ErroNegocio("Situação inválida.")
    c.status = status
    if status == C_CANCELADO:
        for e in c.equipes:
            if e.status in ATIVAS_EQUIPE:
                notificacoes.avisar(s, e.capitao_id, "campeonato", f"❌ {c.nome} foi cancelado", "", f"/campeonatos/{c.id}", None, f"campcanc:{c.id}:{e.id}")
    s.commit()
    return c


# ---------------------------------------------------------------- equipes


def _equipes_do_atleta(s: SessaoORM, c: Campeonato, usuario_id: int) -> list[Equipe]:
    """Uma pessoa pode estar em mais de uma equipe do mesmo campeonato (ex.: categorias ou duplas diferentes)."""
    return list(s.scalars(
        select(Equipe)
        .join(EquipeMembro, EquipeMembro.equipe_id == Equipe.id)
        .where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE), EquipeMembro.usuario_id == usuario_id, EquipeMembro.status == M_CONFIRMADO)
        .order_by(Equipe.id)
    ))


def inscrever_equipe(s: SessaoORM, campeonato_id: int, capitao: Usuario, nome: str, convidados: list[int] | None = None) -> Equipe:
    """O capitão cadastra a equipe (RF-008) e já convida os jogadores. A inscrição fica pendente até o organizador confirmar."""
    c = s.scalar(select(Campeonato).where(Campeonato.id == campeonato_id).with_for_update(of=Campeonato))
    if c is None:
        raise NaoEncontrado("Campeonato não encontrado.")
    if c.status != C_ABERTO or c.inscricao_ate < agora().date():
        raise ErroNegocio("As inscrições deste campeonato estão encerradas.")
    if equipes_ativas(s, c) >= c.max_equipes:
        raise ErroNegocio("Todas as vagas do campeonato foram preenchidas.")
    if not nome.strip():
        raise ErroNegocio("Dê um nome à equipe.")
    if s.scalar(select(Equipe.id).where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE), func.lower(Equipe.nome) == nome.strip().lower())):
        raise ErroNegocio("Já existe uma equipe com esse nome neste campeonato.")
    e = Equipe(campeonato_id=c.id, capitao_id=capitao.id, nome=nome.strip())
    e.membros.append(EquipeMembro(usuario_id=capitao.id, status=M_CONFIRMADO))
    s.add(e)
    s.flush()
    for uid in convidados or []:
        convidar(s, e.id, capitao, uid, _commit=False)
    notificacoes.avisar(s, c.organizador_id, "equipe", f"Nova equipe em {c.nome}: {e.nome}", f"Capitão: {capitao.arroba}", f"/campeonatos/{c.id}", None, f"eq:{e.id}")
    s.commit()
    return e


def convidar(s: SessaoORM, equipe_id: int, por: Usuario, usuario_id: int, _commit: bool = True) -> EquipeMembro:
    e = s.get(Equipe, equipe_id)
    if e is None:
        raise NaoEncontrado("Equipe não encontrada.")
    if e.capitao_id != por.id:
        raise SemPermissao("Só o capitão convida jogadores.")
    if e.status not in ATIVAS_EQUIPE:
        raise ErroNegocio("Esta equipe não está mais inscrita.")
    alvo = s.get(Usuario, usuario_id)
    if alvo is None or not alvo.ativo:
        raise NaoEncontrado("Atleta não encontrado.")
    c = e.campeonato
    m = s.get(EquipeMembro, (e.id, alvo.id))
    if m is not None and m.status != M_RECUSADO:
        return m
    ocupados = s.scalar(select(func.count()).select_from(EquipeMembro).where(EquipeMembro.equipe_id == e.id, EquipeMembro.status != M_RECUSADO)) or 0
    if ocupados >= c.atletas_por_equipe:
        raise ErroNegocio(f"A equipe já tem os {c.atletas_por_equipe} atletas.")
    if m is None:
        m = EquipeMembro(equipe_id=e.id, usuario_id=alvo.id, status=M_CONVIDADO)
        s.add(m)
    else:
        m.status = M_CONVIDADO
    notificacoes.avisar(s, alvo.id, "equipe", f"🏆 {por.nome} convidou você para a equipe {e.nome}", f"Campeonato: {c.nome}", f"/campeonatos/{c.id}", None, f"conv:{e.id}:{alvo.id}:{agora().strftime('%d%H%M')}")
    if _commit:
        s.commit()
    return m


def responder_convite(s: SessaoORM, equipe_id: int, usuario: Usuario, aceitar: bool) -> EquipeMembro:
    m = s.get(EquipeMembro, (equipe_id, usuario.id))
    if m is None or m.status != M_CONVIDADO:
        raise NaoEncontrado("Convite não encontrado.")
    m.status = M_CONFIRMADO if aceitar else M_RECUSADO
    notificacoes.avisar(
        s, m.equipe.capitao_id, "equipe", f"{usuario.nome} {'aceitou' if aceitar else 'recusou'} o convite para {m.equipe.nome}", "", f"/campeonatos/{m.equipe.campeonato_id}", None, f"resp:{equipe_id}:{usuario.id}:{agora().strftime('%d%H%M%S')}"
    )
    s.commit()
    return m


def decidir_equipe(s: SessaoORM, equipe_id: int, por: Usuario, confirmar: bool) -> Equipe:
    e = s.get(Equipe, equipe_id)
    if e is None:
        raise NaoEncontrado("Equipe não encontrada.")
    exigir_gestao(e.campeonato, por)
    e.status = E_CONFIRMADA if confirmar else E_RECUSADA
    notificacoes.avisar(
        s, e.capitao_id, "equipe", f"{'✅ Inscrição confirmada' if confirmar else '❌ Inscrição recusada'}: {e.nome}", e.campeonato.nome, f"/campeonatos/{e.campeonato_id}", None, f"dec:{e.id}:{e.status}"
    )
    s.commit()
    return e


def cancelar_equipe(s: SessaoORM, equipe_id: int, por: Usuario) -> None:
    e = s.get(Equipe, equipe_id)
    if e is None:
        raise NaoEncontrado("Equipe não encontrada.")
    if e.capitao_id != por.id and not pode_gerir(e.campeonato, por):
        raise SemPermissao("Só o capitão cancela a inscrição.")
    e.status = E_CANCELADA
    s.commit()


def minha_situacao(s: SessaoORM, c: Campeonato, usuario: Usuario) -> dict:
    """Onde o atleta está neste campeonato. Pode estar em várias equipes e ter vários convites ao mesmo tempo.
    `estado`/`equipe_id` seguem como resumo (primeira equipe, senão primeiro convite, senão fora)."""
    equipes = _equipes_do_atleta(s, c, usuario.id)
    convites = list(s.scalars(
        select(EquipeMembro).join(Equipe, Equipe.id == EquipeMembro.equipe_id)
        .where(Equipe.campeonato_id == c.id, Equipe.status.in_(ATIVAS_EQUIPE), EquipeMembro.usuario_id == usuario.id, EquipeMembro.status == M_CONVIDADO)
        .order_by(EquipeMembro.equipe_id)
    ))
    lista = [{"id": e.id, "nome": e.nome, "papel": "capitao" if e.capitao_id == usuario.id else "jogador"} for e in equipes]
    pendentes = [{"equipe_id": m.equipe_id, "equipe": m.equipe.nome} for m in convites]
    if equipes:
        estado, eid = lista[0]["papel"], equipes[0].id
    elif convites:
        estado, eid = "convidado", convites[0].equipe_id
    else:
        estado, eid = "fora", None
    return {"estado": estado, "equipe_id": eid, "equipes": lista, "convites": pendentes}


def contar_pendentes(s: SessaoORM, c: Campeonato) -> int:
    return s.scalar(select(func.count()).select_from(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == E_PENDENTE)) or 0
