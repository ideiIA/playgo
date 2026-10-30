"""API JSON do aplicativo (PWA e, no futuro, apps nativos). Mesmos serviços do site, sem HTML.
Autenticação: `Authorization: Bearer <token>` (vem de /auth/entrar) ou o cookie de sessão do site."""

from datetime import date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import (
    arenas,
    book,
    locais,
    locais_importacao,
    atividades,
    campeonatos,
    contas,
    descoberta,
    detalhes,
    feed,
    google,
    grupos,
    midia,
    modalidades,
    notificacoes,
    planos,
    reservas,
    convites,
    seguranca,
    termos,
    vagas,
)
from .. import serializadores as ser
from ..db import agora
from ..deps import AtualApi, AtualApiLivre, Sessao
from ..erros import ErroNegocio

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------- corpos de requisição


class Cadastro(BaseModel):
    nome: str
    email: str
    senha: str
    usuario: str
    aceito_termos: bool = False
    consent_localizacao: bool = False


class Login(BaseModel):
    email: str
    senha: str


class PerfilIn(BaseModel):
    nome: str | None = None
    cidade: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    raio_km: int | None = None
    disponibilidade: list[str] | None = None
    sexo: str | None = None
    nascimento: date | None = None
    foto_url: str | None = None
    notif_vagas: bool | None = None
    notif_lembretes: bool | None = None
    notif_campeonatos: bool | None = None
    notif_grupos: bool | None = None
    notif_raio_km: int | None = None
    consent_localizacao: bool | None = None


class AceiteIn(BaseModel):
    aceito_termos: bool = False


class UsuarioIn(BaseModel):
    usuario: str


class EsporteIn(BaseModel):
    modalidade_id: int
    nivel: str = "intermediario"


class AtividadeIn(BaseModel):
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
    exige_aprovacao: bool = False
    visibilidade: str = "publica"
    falta_gente: bool = False


class Ligado(BaseModel):
    ligado: bool = True


class Capacidade(BaseModel):
    max_participantes: int


class GrupoIn(BaseModel):
    nome: str
    modalidade_id: int
    descricao: str | None = None
    cidade: str | None = None
    local_habitual: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    entrada: str = "aprovacao"  # livre | aprovacao


class MensagemIn(BaseModel):
    texto: str
    aviso: bool = False


class CampeonatoIn(BaseModel):
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


class StatusIn(BaseModel):
    status: str


class EquipeIn(BaseModel):
    nome: str
    convidados: list[int] = Field(default_factory=list)


class ConviteIn(BaseModel):
    usuario_id: int


class RespostaIn(BaseModel):
    aceitar: bool


class DecisaoIn(BaseModel):
    confirmar: bool


class ArenaIn(BaseModel):
    nome: str
    latitude: float
    longitude: float
    endereco: str = ""
    cidade: str | None = None
    descricao: str | None = None
    estrutura: str | None = None
    regras: str | None = None
    abre: time = time(6, 0)
    fecha: time = time(23, 0)
    tipo_estabelecimento: str = "arena"  # arena | academia | clube | escola
    pagamento_reserva: str | None = None  # online | local | ambos (só na edição)
    cancel_horas: int | None = None
    reembolso_pct: int | None = None
    reembolso_pct_fora: int | None = None


class LocalIn(BaseModel):
    tipo: str
    nome: str
    endereco: str
    latitude: float
    longitude: float
    horario_funcionamento: str = ""
    telefone: str = ""
    cidade: str | None = None
    email: str | None = None
    site: str | None = None
    instagram: str | None = None
    facebook: str | None = None
    maps_url: str | None = None
    regras: str | None = None
    descricao: str | None = None
    modalidades: list[str] = []


class QuadraIn(BaseModel):
    nome: str
    modalidades: list[str] = Field(default_factory=list)
    capacidade: int | None = None
    valor_hora: Decimal = Decimal(0)
    tipo: str = "quadra"  # quadra | sala | piscina | campo | outro


class PagadorReservaIn(BaseModel):
    nome: str = ""
    cpf_cnpj: str = ""
    email: str | None = None


class ReservarHorarioIn(BaseModel):
    forma: str | None = None  # online | local (vazio = a única forma da arena)
    pagador: PagadorReservaIn | None = None  # só para pagar pelo app; o CPF/CNPJ vai ao Asaas e não é guardado


class ReservaIn(BaseModel):
    inicio: datetime
    fim: datetime
    rotulo: str = ""
    tipo: str = "reserva"


class DivulgarIn(BaseModel):
    inicio: datetime
    fim: datetime
    valor: Decimal | None = None
    modalidade_id: int | None = None


class LidasIn(BaseModel):
    ids: list[int] | None = None


def _filtros(
    raio: float | None, modalidades: str, quando: str | None, periodo: str | None, nivel: str | None,
    preco: str | None, com_vagas: bool, q: str, ordem: str, tipos: str,
) -> descoberta.Filtros:
    return descoberta.Filtros(
        raio_km=raio or None, modalidades=[m for m in modalidades.split(",") if m], quando=quando or None,
        periodo=periodo or None, nivel=nivel or None, preco=preco or None, com_vagas=com_vagas, q=q, ordem=ordem,
        tipos=tuple(t for t in tipos.split(",") if t in descoberta.TIPOS) or descoberta.TIPOS,
    )


# ---------------------------------------------------------------- conta


@router.get("/auth/provedores")
def provedores():
    return {"google": google.configurado()}


@router.post("/auth/cadastro")
def cadastro(corpo: Cadastro, s: Sessao):
    u = contas.cadastrar(s, corpo.nome, corpo.email, corpo.senha, corpo.usuario, corpo.aceito_termos, corpo.consent_localizacao)
    return {"token": seguranca.gerar_token(u.id), "usuario": contas.dados_publicos(u)}


@router.post("/auth/entrar")
def entrar(corpo: Login, s: Sessao):
    u = contas.autenticar(s, corpo.email, corpo.senha)
    if u is None:
        raise HTTPException(401, "E-mail ou senha incorretos.")
    return {"token": seguranca.gerar_token(u.id), "usuario": contas.dados_publicos(u)}


@router.get("/me")
def me(u: AtualApiLivre):
    return contas.dados_publicos(u)


@router.get("/termos")
def ver_termos():
    """Texto vigente dos termos e da política (público: o cadastro mostra antes de existir conta)."""
    return {"versao": termos.VERSAO, "declaracoes": termos.DECLARACOES, "termos_de_uso": termos.termos_de_uso(), "politica_de_privacidade": termos.politica_de_privacidade()}


@router.post("/me/aceitar-termos")
def aceitar_termos(corpo: AceiteIn, u: AtualApiLivre, s: Sessao):
    contas.aceitar_termos(s, u, corpo.aceito_termos)
    return contas.dados_publicos(u)


@router.put("/me/usuario")
def definir_usuario(corpo: UsuarioIn, u: AtualApiLivre, s: Sessao):
    contas.definir_usuario(s, u, corpo.usuario)
    return contas.dados_publicos(u)


@router.patch("/me")
def atualizar_me(corpo: PerfilIn, u: AtualApi, s: Sessao):
    contas.atualizar_perfil(s, u, **corpo.model_dump(exclude_unset=True))
    return contas.dados_publicos(u)


@router.post("/me/foto")
def enviar_foto(u: AtualApi, s: Sessao, arquivo: UploadFile = File()):
    """multipart/form-data, campo `arquivo`. Recorta em quadrado e remove metadados."""
    contas.definir_foto(s, u, midia.ler_upload(arquivo))
    return contas.dados_publicos(u)


@router.delete("/me/foto")
def apagar_foto(u: AtualApi, s: Sessao):
    contas.remover_foto(s, u)
    return contas.dados_publicos(u)


@router.put("/me/esportes")
def definir_esportes(corpo: list[EsporteIn], u: AtualApi, s: Sessao):
    contas.definir_esportes(s, u, {e.modalidade_id: e.nivel for e in corpo})
    return contas.dados_publicos(u)


@router.get("/modalidades")
def listar_modalidades(s: Sessao):
    return [ser.modalidade(m) for m in modalidades.ativas(s)]


@router.get("/atletas")
def buscar_atletas(u: AtualApi, s: Sessao, q: str = Query(min_length=2)):
    """Busca pelo @usuario (prefixo), para convidar gente. Não expõe nome real nem e-mail."""
    return [{"id": x.id, "usuario": x.usuario, "arroba": x.arroba, "iniciais": x.iniciais} for x in contas.buscar_por_usuario(s, q, u.id)]


# ---------------------------------------------------------------- descoberta


@router.get("/feed")
def obter_feed(u: AtualApi, s: Sessao, lat: float | None = None, lng: float | None = None):
    return feed.montar(s, u, lat, lng)


@router.get("/explorar")
def explorar(
    u: AtualApi, s: Sessao, lat: float | None = None, lng: float | None = None, raio: float | None = 10,
    modalidades: str = "", quando: str | None = None, periodo: str | None = None, nivel: str | None = None,
    preco: str | None = None, com_vagas: bool = False, q: str = "", ordem: str = "relevancia", tipos: str = "",
):
    return descoberta.explorar(s, u, lat, lng, _filtros(raio, modalidades, quando, periodo, nivel, preco, com_vagas, q, ordem, tipos))


# ---------------------------------------------------------------- atividades


@router.post("/atividades", status_code=201)
def criar_atividade(corpo: AtividadeIn, u: AtualApi, s: Sessao):
    a = atividades.criar(s, u, atividades.NovaAtividade(**corpo.model_dump()))
    return detalhes.atividade(s, a, u)


@router.get("/atividades/{atividade_id}")
def ver_atividade(atividade_id: int, u: AtualApi, s: Sessao, t: str | None = None):
    a = atividades.obter(s, atividade_id)
    convites.exigir_acesso_atividade(s, u, a, t)
    return detalhes.atividade(s, a, u)


@router.post("/atividades/{atividade_id}/entrar")
def entrar_atividade(atividade_id: int, u: AtualApi, s: Sessao, t: str | None = None):
    vagas.entrar(s, atividade_id, u, token=t)
    return detalhes.atividade(s, atividades.obter(s, atividade_id), u)


@router.post("/atividades/{atividade_id}/sair")
def sair_atividade(atividade_id: int, u: AtualApi, s: Sessao):
    vagas.sair(s, atividade_id, u)
    return detalhes.atividade(s, atividades.obter(s, atividade_id), u)


@router.post("/atividades/{atividade_id}/falta-gente")
def falta_gente(atividade_id: int, corpo: Ligado, u: AtualApi, s: Sessao):
    return detalhes.atividade(s, atividades.definir_falta_gente(s, atividade_id, u, corpo.ligado), u)


@router.post("/atividades/{atividade_id}/capacidade")
def capacidade(atividade_id: int, corpo: Capacidade, u: AtualApi, s: Sessao):
    return detalhes.atividade(s, atividades.alterar_capacidade(s, atividade_id, u, corpo.max_participantes), u)


@router.post("/atividades/{atividade_id}/cancelar")
def cancelar_atividade(atividade_id: int, u: AtualApi, s: Sessao):
    return detalhes.atividade(s, atividades.cancelar(s, atividade_id, u), u)


@router.post("/atividades/{atividade_id}/participacoes/{participacao_id}/{acao}")
def decidir_participacao(atividade_id: int, participacao_id: int, acao: str, u: AtualApi, s: Sessao):
    funcoes = {"aprovar": vagas.aprovar, "recusar": vagas.recusar, "remover": vagas.remover}
    if acao not in funcoes:
        raise HTTPException(404, "Ação desconhecida.")
    funcoes[acao](s, atividade_id, participacao_id, u)
    return detalhes.atividade(s, atividades.obter(s, atividade_id), u)


@router.get("/minhas-atividades")
def minhas_atividades(u: AtualApi, s: Sessao):
    from ..models import ATIVAS, Atividade, Participacao

    n = agora()
    linhas = s.execute(
        select(Atividade, Participacao.status).join(Participacao, Participacao.atividade_id == Atividade.id).where(Participacao.usuario_id == u.id, Participacao.status.in_(ATIVAS)).order_by(Atividade.inicio.desc()).limit(100)
    ).unique()
    itens = [ser.atividade(a, n, None, st) for a, st in linhas]
    return {"proximas": sorted([i for i in itens if i["minutos_para_inicio"] >= -90 and i["status"] == "aberta"], key=lambda i: i["inicio"]), "historico": [i for i in itens if i["minutos_para_inicio"] < -90 or i["status"] != "aberta"]}


# ---------------------------------------------------------------- grupos


@router.get("/grupos")
def listar_grupos(u: AtualApi, s: Sessao, lat: float | None = None, lng: float | None = None, raio: float | None = None, q: str = ""):
    la, ln, _ = descoberta.localizacao(u, lat, lng)
    todos = descoberta.buscar_grupos(s, u, la, ln, descoberta.Filtros(raio_km=raio or None, q=q))
    return {"meus": [g for g in todos if g["sou_membro"]], "descobrir": [g for g in todos if not g["sou_membro"]]}


@router.post("/grupos", status_code=201)
def criar_grupo(corpo: GrupoIn, u: AtualApi, s: Sessao):
    g = grupos.criar(s, u, **corpo.model_dump())
    return detalhes.grupo(s, g, u)


@router.get("/grupos/{grupo_id}")
def ver_grupo(grupo_id: int, u: AtualApi, s: Sessao):
    return detalhes.grupo(s, grupos.obter(s, grupo_id), u)


@router.post("/grupos/{grupo_id}/entrar")
def entrar_grupo(grupo_id: int, u: AtualApi, s: Sessao):
    grupos.entrar(s, grupo_id, u)
    return detalhes.grupo(s, grupos.obter(s, grupo_id), u)


@router.post("/grupos/{grupo_id}/sair")
def sair_grupo(grupo_id: int, u: AtualApi, s: Sessao):
    grupos.sair(s, grupo_id, u)
    return {"ok": True}


@router.post("/grupos/{grupo_id}/pedidos/{usuario_id}/aceitar")
def aceitar_pedido(grupo_id: int, usuario_id: int, u: AtualApi, s: Sessao):
    grupos.decidir_pedido(s, grupo_id, usuario_id, u, True)
    return detalhes.grupo(s, grupos.obter(s, grupo_id), u)


@router.post("/grupos/{grupo_id}/pedidos/{usuario_id}/recusar")
def recusar_pedido(grupo_id: int, usuario_id: int, u: AtualApi, s: Sessao):
    grupos.decidir_pedido(s, grupo_id, usuario_id, u, False)
    return detalhes.grupo(s, grupos.obter(s, grupo_id), u)


@router.post("/grupos/{grupo_id}/membros/{usuario_id}/remover")
def remover_membro(grupo_id: int, usuario_id: int, u: AtualApi, s: Sessao):
    grupos.remover(s, grupo_id, usuario_id, u)
    return detalhes.grupo(s, grupos.obter(s, grupo_id), u)


@router.post("/grupos/{grupo_id}/promover/{usuario_id}")
def promover(grupo_id: int, usuario_id: int, u: AtualApi, s: Sessao):
    grupos.promover(s, grupo_id, usuario_id, u)
    return detalhes.grupo(s, grupos.obter(s, grupo_id), u)


@router.get("/grupos/{grupo_id}/mensagens")
def mensagens(grupo_id: int, u: AtualApi, s: Sessao, depois_de: int = 0):
    if grupos.membro(s, grupo_id, u) is None:
        raise HTTPException(403, "Entre no grupo para ver a conversa.")
    n = agora()
    return [detalhes.mensagem(m, n) for m in grupos.mensagens(s, grupo_id, depois_de)]


@router.post("/grupos/{grupo_id}/mensagens", status_code=201)
def postar(grupo_id: int, corpo: MensagemIn, u: AtualApi, s: Sessao):
    return detalhes.mensagem(grupos.postar(s, grupo_id, u, corpo.texto, corpo.aviso), agora())


# ---------------------------------------------------------------- campeonatos


@router.post("/campeonatos", status_code=201)
def criar_campeonato(corpo: CampeonatoIn, u: AtualApi, s: Sessao):
    c = campeonatos.criar(s, u, campeonatos.NovoCampeonato(**corpo.model_dump()))
    return detalhes.campeonato(s, c, u)


@router.get("/campeonatos/encerrados")
def campeonatos_encerrados(u: AtualApi, s: Sessao, q: str = "", de: date | None = None, ate: date | None = None, organizador: str = "", situacao: str = "encerrado"):
    return campeonatos.encerrados(s, u, q, de, ate, organizador, situacao=situacao)


@router.get("/campeonatos/{campeonato_id}/book")
def book_campeonato(campeonato_id: int, u: AtualApi, s: Sessao):
    return book.campeonato(s, campeonatos.obter(s, campeonato_id), u)


@router.post("/campeonatos/{campeonato_id}/book/publicar")
def publicar_book_campeonato(campeonato_id: int, u: AtualApi, s: Sessao, publicado: bool = True):
    c = campeonatos.definir_book_publicado(s, campeonato_id, u, publicado)
    return {"publicado": c.book_publicado}


@router.post("/campeonatos/{campeonato_id}/capa")
def capa_campeonato(campeonato_id: int, u: AtualApi, s: Sessao, arquivo: UploadFile = File()):
    c = campeonatos.definir_capa(s, campeonato_id, u, midia.ler_upload(arquivo))
    return {"capa_url": f"/capa/{c.capa}"}


@router.get("/atividades/{atividade_id}/book")
def book_atividade(atividade_id: int, u: AtualApi, s: Sessao):
    return book.atividade(s, atividades.obter(s, atividade_id), u)


@router.post("/atividades/{atividade_id}/book/publicar")
def publicar_book_atividade(atividade_id: int, u: AtualApi, s: Sessao, publicado: bool = True):
    a = atividades.definir_book_publicado(s, atividade_id, u, publicado)
    return {"publicado": a.book_publicado}


@router.post("/atividades/{atividade_id}/capa")
def capa_atividade(atividade_id: int, u: AtualApi, s: Sessao, arquivo: UploadFile = File()):
    a = atividades.definir_capa(s, atividade_id, u, midia.ler_upload(arquivo))
    return {"capa_url": f"/capa/{a.capa}"}


@router.get("/campeonatos/{campeonato_id}")
def ver_campeonato(campeonato_id: int, u: AtualApi, s: Sessao):
    return detalhes.campeonato(s, campeonatos.obter(s, campeonato_id), u)


@router.post("/campeonatos/{campeonato_id}/status")
def status_campeonato(campeonato_id: int, corpo: StatusIn, u: AtualApi, s: Sessao):
    return detalhes.campeonato(s, campeonatos.definir_status(s, campeonato_id, u, corpo.status), u)


@router.post("/campeonatos/{campeonato_id}/equipes", status_code=201)
def inscrever_equipe(campeonato_id: int, corpo: EquipeIn, u: AtualApi, s: Sessao):
    campeonatos.inscrever_equipe(s, campeonato_id, u, corpo.nome, corpo.convidados)
    return detalhes.campeonato(s, campeonatos.obter(s, campeonato_id), u)


@router.post("/equipes/{equipe_id}/convidar")
def convidar(equipe_id: int, corpo: ConviteIn, u: AtualApi, s: Sessao):
    campeonatos.convidar(s, equipe_id, u, corpo.usuario_id)
    return {"ok": True}


@router.post("/equipes/{equipe_id}/responder")
def responder_convite(equipe_id: int, corpo: RespostaIn, u: AtualApi, s: Sessao):
    m = campeonatos.responder_convite(s, equipe_id, u, corpo.aceitar)
    return detalhes.campeonato(s, m.equipe.campeonato, u)


@router.post("/equipes/{equipe_id}/decidir")
def decidir_equipe(equipe_id: int, corpo: DecisaoIn, u: AtualApi, s: Sessao):
    e = campeonatos.decidir_equipe(s, equipe_id, u, corpo.confirmar)
    return detalhes.campeonato(s, e.campeonato, u)


@router.post("/equipes/{equipe_id}/cancelar")
def cancelar_equipe(equipe_id: int, u: AtualApi, s: Sessao):
    from ..models import Equipe

    e = s.get(Equipe, equipe_id)
    campeonatos.cancelar_equipe(s, equipe_id, u)
    return detalhes.campeonato(s, e.campeonato, u)


# ---------------------------------------------------------------- guia de locais


@router.get("/locais/{local_id}")
def ver_local(local_id: int, u: AtualApi, s: Sessao):
    return locais.serializar(locais.obter(s, local_id))


@router.get("/gestao/locais")
def gestao_locais(u: AtualApi, s: Sessao):
    return [locais.serializar(x) for x in locais.todos(s, u)]


@router.post("/gestao/locais/importar")
async def importar_locais(u: AtualApi, s: Sessao, tipo: str = Form("auto"), incompleto: bool = Form(True), arquivo: UploadFile = File(...)):
    return locais_importacao.importar(s, u, arquivo.filename or "", await arquivo.read(), tipo or "auto", permitir_incompleto=incompleto)


@router.post("/gestao/locais", status_code=201)
def criar_local(corpo: LocalIn, u: AtualApi, s: Sessao):
    return locais.serializar(locais.criar(s, u, **corpo.model_dump()))


@router.put("/gestao/locais/{local_id}")
def editar_local(local_id: int, corpo: LocalIn, u: AtualApi, s: Sessao):
    return locais.serializar(locais.editar(s, u, local_id, **corpo.model_dump()))


@router.post("/gestao/locais/{local_id}/ativo")
def ativar_local(local_id: int, u: AtualApi, s: Sessao, ativo: bool = True):
    return locais.serializar(locais.definir_ativo(s, u, local_id, ativo))


# ---------------------------------------------------------------- arenas (público)


@router.get("/arenas/{arena_id}")
def ver_arena(arena_id: int, u: AtualApi, s: Sessao):
    return detalhes.arena_publica(s, arenas.obter(s, arena_id), u)


# ---------------------------------------------------------------- gestão (B2B)


@router.get("/gestao/arenas")
def minhas_arenas(u: AtualApi, s: Sessao):
    return [ser.arena(a) | {"abre": a.abre.strftime("%H:%M"), "fecha": a.fecha.strftime("%H:%M"), "descricao": a.descricao, "regras": a.regras,
                                  "pagamento_reserva": a.pagamento_reserva, "cancel_horas": a.cancel_horas, "reembolso_pct": a.reembolso_pct, "reembolso_pct_fora": a.reembolso_pct_fora} for a in arenas.minhas(s, u)]


@router.post("/gestao/arenas", status_code=201)
def criar_arena(corpo: ArenaIn, u: AtualApi, s: Sessao):
    a = arenas.criar(s, u, **corpo.model_dump(exclude={"pagamento_reserva", "cancel_horas", "reembolso_pct", "reembolso_pct_fora"}))
    return detalhes.arena_publica(s, a, u)


@router.put("/gestao/arenas/{arena_id}")
def editar_arena(arena_id: int, corpo: ArenaIn, u: AtualApi, s: Sessao):
    a = arenas.editar(s, arenas.obter(s, arena_id), u, **corpo.model_dump())
    return detalhes.arena_publica(s, a, u)


@router.post("/gestao/arenas/{arena_id}/quadras", status_code=201)
def criar_quadra(arena_id: int, corpo: QuadraIn, u: AtualApi, s: Sessao):
    arenas.criar_quadra(s, arenas.obter(s, arena_id), u, **corpo.model_dump())
    return detalhes.arena_publica(s, arenas.obter(s, arena_id), u)


def _dia(dia: date | None) -> date:
    return dia or agora().date()


@router.get("/gestao/arenas/{arena_id}/painel")
def painel(arena_id: int, u: AtualApi, s: Sessao, dia: date | None = None):
    a = arenas.obter(s, arena_id)
    arenas.exigir_gestor(a, u)
    return arenas.indicadores(s, a, _dia(dia))


@router.get("/gestao/arenas/{arena_id}/agenda")
def agenda(arena_id: int, u: AtualApi, s: Sessao, dia: date | None = None):
    a = arenas.obter(s, arena_id)
    arenas.exigir_gestor(a, u)
    return arenas.agenda_do_dia(s, a, _dia(dia))


@router.post("/gestao/arenas/{arena_id}/divulgar-ociosos")
def divulgar_ociosos(arena_id: int, u: AtualApi, s: Sessao):
    return {"divulgados": arenas.divulgar_ociosos(s, arenas.obter(s, arena_id), u)}


@router.post("/gestao/quadras/{quadra_id}/reservas", status_code=201)
def reservar(quadra_id: int, corpo: ReservaIn, u: AtualApi, s: Sessao):
    arenas.quadra_do_gestor(s, quadra_id, u)
    planos.exigir(s, u, "arena")
    if corpo.tipo not in ("reserva", "bloqueio"):
        raise ErroNegocio("Tipo de reserva inválido.")
    r = arenas.reservar(s, quadra_id, corpo.inicio, corpo.fim, corpo.tipo, corpo.rotulo)
    s.commit()
    return {"id": r.id}


@router.delete("/gestao/reservas/{reserva_id}")
def liberar(reserva_id: int, u: AtualApi, s: Sessao):
    arenas.liberar(s, reserva_id, u)
    return {"ok": True}


@router.post("/gestao/quadras/{quadra_id}/divulgar", status_code=201)
def divulgar(quadra_id: int, corpo: DivulgarIn, u: AtualApi, s: Sessao):
    h = arenas.divulgar(s, quadra_id, corpo.inicio, corpo.fim, corpo.valor, corpo.modalidade_id, u)
    return {"id": h.id}


@router.delete("/gestao/horarios/{horario_id}")
def despublicar(horario_id: int, u: AtualApi, s: Sessao):
    arenas.despublicar(s, horario_id, u)
    return {"ok": True}


@router.post("/horarios/{horario_id}/reservar", status_code=201)
def reservar_horario(horario_id: int, corpo: ReservarHorarioIn, u: AtualApi, s: Sessao):
    return reservas.reservar_horario(s, u, horario_id, corpo.forma, corpo.pagador.model_dump() if corpo.pagador else None)


@router.get("/me/reservas")
def minhas_reservas(u: AtualApi, s: Sessao):
    return reservas.minhas(s, u)


@router.post("/reservas/{reserva_id}/cancelar")
def cancelar_reserva(reserva_id: int, u: AtualApi, s: Sessao):
    return reservas.cancelar(s, u, reserva_id)


# ---------------------------------------------------------------- notificações


@router.get("/notificacoes")
def listar_notificacoes(u: AtualApi, s: Sessao):
    n = agora()
    return {"nao_lidas": notificacoes.nao_lidas(s, u), "itens": [ser.notificacao(x, n) for x in notificacoes.listar(s, u)]}


@router.get("/notificacoes/contagem")
def contagem(u: AtualApi, s: Sessao):
    return {"nao_lidas": notificacoes.nao_lidas(s, u)}


@router.post("/notificacoes/lidas")
def marcar_lidas(corpo: LidasIn, u: AtualApi, s: Sessao):
    notificacoes.marcar_lidas(s, u, corpo.ids)
    return {"nao_lidas": notificacoes.nao_lidas(s, u)}
