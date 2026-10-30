"""Site do PlayGo (visão web), API JSON em /api/v1 e o aplicativo PWA em /app."""

import re
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import (
    agendador,
    arenas,
    armazenamento,
    atividades,
    auditoria,
    book,
    campeonatos,
    contas,
    convites,
    descoberta,
    detalhes,
    feed,
    grupos,
    locais,
    locais_importacao,
    midia,
    modalidades,
    notificacoes,
    planos,
    publicacoes,
    recebimentos,
    reservas,
    seguranca,
    termos,
    vagas,
)
from .. import serializadores as ser
from ..api.chaves import router as api_chaves
from ..api.elenco import router as api_elenco
from ..api.ensino import router as api_ensino
from ..api.instagram import router as api_instagram
from ..api.mural import router as api_mural
from ..api.planos import router as api_planos
from ..api.recebimentos import router as api_recebimentos
from ..api.v1 import router as api_v1
from ..api.youtube import router as api_youtube
from ..config import settings
from ..db import Session, agora, criar_tabelas
from ..deps import Atual, Opcional, PrecisaCompletar, PrecisaEntrar, Sessao
from ..erros import ErroNegocio, NaoEncontrado, PlanoNecessario, SemPermissao
from sqlalchemy import select

from ..models import CATEGORIAS, NIVEIS, NOME_NIVEL, PERIODOS, HorarioDivulgado, Quadra, Usuario

PASTA = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.migrar_ao_subir:
        criar_tabelas()
        with Session() as s:
            modalidades.semear(s)
    parar = threading.Event()
    if settings.agendador_na_web and not settings.em_serverless:
        threading.Thread(target=agendador.laco, args=(parar,), name="agendador", daemon=True).start()
    yield
    parar.set()


app = FastAPI(title="PlayGo", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware, secret_key=seguranca.chave_sessao(), session_cookie="playgo_sessao", max_age=30 * 24 * 3600, same_site="lax", https_only=settings.cookie_seguro
)
class _EstaticosAtuais(StaticFiles):
    """Scripts e estilos sempre revalidados (ETag): quem atualiza o servidor não fica com o JavaScript antigo no navegador."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html", ".json")) or path in ("", ".", "/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _EstaticosAtuais(directory=PASTA / "static"), name="static")
app.mount("/app", _EstaticosAtuais(directory=PASTA.parent / "app", html=True), name="pwa")
app.include_router(api_v1)
app.include_router(api_mural)
app.include_router(api_planos)
app.include_router(api_recebimentos)
app.include_router(api_chaves)
app.include_router(api_elenco)
app.include_router(api_ensino)
app.include_router(api_instagram)
app.include_router(api_youtube)
templates = Jinja2Templates(directory=PASTA / "templates")


@app.middleware("http")
async def _contexto_de_auditoria(request: Request, call_next):
    """Guarda IP, porta e aparelho da requisição para a trilha de interações (resguardo legal)."""
    auditoria.definir_contexto(request)
    return await call_next(request)


# ---------------------------------------------------------------- erros


@app.exception_handler(PrecisaEntrar)
def _ir_para_login(request: Request, _: PrecisaEntrar):
    return RedirectResponse("/entrar", status_code=303)


@app.exception_handler(PrecisaCompletar)
def _completar_cadastro(request: Request, erro: PrecisaCompletar):
    return RedirectResponse("/cadastro/completar" if erro.pendencia == "usuario" else "/termos/aceitar", status_code=303)


@app.exception_handler(PlanoNecessario)
def _plano_necessario(request: Request, erro: PlanoNecessario):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": {"codigo": "plano_necessario", "plano": erro.plano, "mensagem": str(erro)}}, status_code=402)
    request.session["erro"] = str(erro)
    return RedirectResponse(f"/planos?precisa={erro.plano}", status_code=303)


@app.exception_handler(ErroNegocio)
def _erro_de_negocio(request: Request, erro: ErroNegocio):
    codigo = 404 if isinstance(erro, NaoEncontrado) else 403 if isinstance(erro, SemPermissao) else 400
    if request.url.path.startswith(("/api/", "/midia/", "/foto/", "/capa/")) or request.url.path.endswith("/regulamento.pdf"):
        return JSONResponse({"detail": str(erro)}, status_code=codigo)
    # No site, a mensagem vai para a página de onde a pessoa veio
    request.session["erro"] = str(erro)
    destino = request.headers.get("referer") or "/"
    return RedirectResponse("/" if codigo == 404 else destino, status_code=303)


def pagina(request: Request, s, usuario: Usuario, nome: str, aba: str = "", **contexto):
    """Renderiza uma página do site já com o que o menu e o topo precisam."""
    base = {
        "usuario": usuario,
        "aba": aba,
        "nao_lidas": notificacoes.nao_lidas(s, usuario),
        "erro": request.session.pop("erro", None),
        "ok": request.session.pop("ok", None),
        "modalidades": modalidades.ativas(s),
        "niveis": NIVEIS,
        "niveis_nome": NOME_NIVEL,
        "categorias": CATEGORIAS,
        "periodos": PERIODOS,
        "hoje": agora().date().isoformat(),
        "fila_moderacao": publicacoes.contar_fila(s) if usuario.equipe_moderacao else 0,
        "convites_pendentes": len(convites.pendentes(s, usuario)),
        "pode": planos.permissoes(s, usuario),
        "aviso_plano": planos.aviso_vencimento(s, usuario),
        "tut_sub": request.url.path.rsplit("/", 1)[-1] if request.url.path.startswith("/tutorial/") else "",
    }
    return templates.TemplateResponse(request, nome, base | contexto)


_SEM_PLANO = {
    "atividade": "Organizar atividades faz parte do plano Pro. Veja os planos.",
    "campeonato": "Criar campeonatos faz parte do plano Organizador. Veja os planos.",
    "arena": "Cadastrar e gerir arenas faz parte do plano Arena. Veja os planos.",
    "grupo": "Criar e gerir grupos faz parte do plano Pro (ou superior). Veja os planos.",
    "comunidade": "Criar comunidades é para os planos Organizador e Arena; qualquer usuário pode participar delas. Veja os planos.",
}


def _sem_plano(request: Request, s, usuario: Usuario, recurso: str) -> RedirectResponse | None:
    """Se o plano da pessoa não libera o recurso, leva à tela de planos (as telas de criação não aparecem no menu)."""
    if planos.permissoes(s, usuario)[recurso]:
        return None
    request.session["erro"] = _SEM_PLANO[recurso]
    return RedirectResponse("/planos", status_code=303)


def voltar(request: Request, padrao: str = "/") -> RedirectResponse:
    return RedirectResponse(request.headers.get("referer") or padrao, status_code=303)


def _data_ou_none(texto: str | None):
    try:
        return date.fromisoformat((texto or "").strip()) if (texto or "").strip() else None
    except ValueError:
        return None


def _num(texto: str | None, tipo=Decimal):
    """Aceita '25', '25,50' e '' (vira None)."""
    t = (texto or "").strip().replace(",", ".")
    if not t:
        return None
    try:
        return tipo(t)
    except (InvalidOperation, ValueError):
        raise ErroNegocio("Número inválido.") from None


def _data(texto: str | None) -> date | None:
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        raise ErroNegocio("Data inválida.") from None


def _hora(texto: str) -> time:
    try:
        return time.fromisoformat(texto)
    except ValueError:
        raise ErroNegocio("Horário inválido.") from None


# ---------------------------------------------------------------- acesso


@app.get("/entrar")
def tela_entrar(request: Request, usuario: Opcional):
    if usuario:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "entrar.html", {"modo": "entrar", "erro": request.session.pop("erro", None)})


@app.post("/entrar")
def entrar(request: Request, s: Sessao, email: str = Form(), senha: str = Form()):
    u = contas.autenticar(s, email, senha)
    if u is None:
        return templates.TemplateResponse(request, "entrar.html", {"modo": "entrar", "erro": "E-mail ou senha incorretos.", "email": email}, status_code=400)
    request.session["usuario_id"] = u.id
    return RedirectResponse("/", status_code=303)


@app.get("/cadastro")
def tela_cadastro(request: Request, usuario: Opcional):
    if usuario:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "entrar.html", {"modo": "cadastro", "declaracoes": termos.DECLARACOES})


@app.post("/cadastro")
def cadastro(
    request: Request, s: Sessao, nome: str = Form(), email: str = Form(), senha: str = Form(), usuario: str = Form(""), aceito_termos: str = Form(""),
    consent_localizacao: str = Form(""),
):
    try:
        u = contas.cadastrar(s, nome, email, senha, usuario, bool(aceito_termos), bool(consent_localizacao))
    except ErroNegocio as e:
        return templates.TemplateResponse(
            request, "entrar.html", {"modo": "cadastro", "erro": str(e), "nome": nome, "email": email, "usuario": usuario, "declaracoes": termos.DECLARACOES}, status_code=400
        )
    request.session["usuario_id"] = u.id
    request.session["ok"] = "Conta criada! Conte quais esportes você pratica para receber indicações."
    return RedirectResponse("/perfil", status_code=303)


@app.post("/sair")
def sair(request: Request):
    request.session.clear()
    return RedirectResponse("/entrar", status_code=303)


# ---------------------------------------------------------------- início e explorar


@app.get("/saude")
def saude(s: Sessao):
    """Verificação de saúde para o monitoramento do contêiner e do balanceador: responde se o banco está de pé."""
    from sqlalchemy import text

    s.execute(text("SELECT 1"))
    return {"ok": True}


@app.get("/")
def inicio(request: Request, s: Sessao, usuario: Opcional):
    """Sem login, a página de divulgação (com o caminho para criar conta, entrar e abrir o app); com login, o início."""
    if usuario is None:
        return templates.TemplateResponse(
            request, "landing.html",
            {"planos": planos.vitrine(s), "modalidades": modalidades.ativas(s), "teste_dias": settings.teste_dias, "ano": agora().year},
        )
    pendencia = contas.pendencias(usuario)
    if pendencia:
        raise PrecisaCompletar(pendencia)
    return pagina(request, s, usuario, "home.html", "home", feed=feed.montar(s, usuario))


@app.get("/explorar")
def explorar(
    request: Request, s: Sessao, usuario: Atual, q: str = "", modalidades: str = "", raio: float | None = None, quando: str = "",
    periodo: str = "", nivel: str = "", preco: str = "", com_vagas: str = "", ordem: str = "relevancia",
):
    raio = settings.raio_padrao_km if raio is None else (raio or None)
    f = descoberta.Filtros(
        raio_km=raio, modalidades=[m for m in modalidades.split(",") if m], quando=quando or None, periodo=periodo or None,
        nivel=nivel or None, preco=preco or None, com_vagas=bool(com_vagas), q=q, ordem=ordem,
    )
    res = descoberta.explorar(s, usuario, f=f)
    qs_sem_ordem = urlencode({k: v for k, v in request.query_params.items() if k != "ordem"})
    return pagina(request, s, usuario, "explorar.html", "explorar", f=f, res=res, busca=q, qs_sem_ordem=qs_sem_ordem)


# ---------------------------------------------------------------- atividades


@app.get("/atividades/nova")
def nova_atividade(request: Request, s: Sessao, usuario: Atual):
    if (r := _sem_plano(request, s, usuario, "atividade")) is not None:
        return r
    p = request.query_params
    v = {k: p.get(k) for k in ("modalidade_id", "grupo_id", "arena_id", "quadra_id")}
    v["modalidade_id"] = int(v["modalidade_id"]) if v["modalidade_id"] else None
    if p.get("inicio"):
        try:
            d = datetime.fromisoformat(p["inicio"])
            v["data"], v["hora"] = d.date().isoformat(), d.strftime("%H:%M")
        except ValueError:
            pass
    # Vindo de um horário da arena: só as modalidades da quadra servem (senão a 1ª da lista, ex.: futebol, é recusada).
    quadra = s.get(Quadra, int(v["quadra_id"])) if (v["quadra_id"] or "").isdigit() else None
    codigos = list(quadra.modalidades or []) if quadra is not None else []
    if quadra is not None:
        v["quadra_nome"] = f"{quadra.arena.nome} — {quadra.nome}"
        if not v["modalidade_id"]:
            v["modalidade_id"] = next((m.id for m in modalidades.ativas(s) if m.codigo in codigos), None)
    la, ln, informada = descoberta.localizacao(usuario)
    lista = descoberta.buscar_arenas(s, la, ln, descoberta.Filtros(raio_km=None), limite=40)
    esportes_da_arena: dict[int, set[str]] = {}
    for qd in s.scalars(select(Quadra).where(Quadra.ativa, Quadra.arena_id.in_([a["id"] for a in lista] or [0]))):
        esportes_da_arena.setdefault(qd.arena_id, set()).update(qd.modalidades or [])
    esportes_da_arena = {k: sorted(x) for k, x in esportes_da_arena.items()}
    return pagina(
        request, s, usuario, "atividade_nova.html", "criar", v=v, arenas=lista, meus_grupos=grupos.meus(s, usuario),
        centro={"latitude": la, "longitude": ln, "informada": informada}, codigos_quadra=codigos, esportes_da_arena=esportes_da_arena,
    )


@app.post("/atividades")
def criar_atividade(
    s: Sessao, usuario: Atual, modalidade_id: int = Form(), nome: str = Form(), data: str = Form(), hora: str = Form(),
    max_participantes: int = Form(), local_nome: str = Form(""), latitude: str = Form(""), longitude: str = Form(""),
    arena_id: str = Form(""), quadra_id: str = Form(""), grupo_id: str = Form(""), duracao_min: int = Form(90), nivel: str = Form("todos"),
    categoria: str = Form("misto"), idade_min: str = Form(""), idade_max: str = Form(""), valor: str = Form(""), descricao: str = Form(""),
    regras: str = Form(""), percurso: str = Form(""), exige_aprovacao: str = Form(""), visibilidade: str = Form("publica"), falta_gente: str = Form(""),
    capa: UploadFile | None = File(None),
):
    imagem = midia.ler_upload(capa) if capa is not None and capa.filename else b""
    if imagem:
        midia.processar_capa(imagem)  # antes de criar: uma imagem inválida não deixa a atividade pela metade
    d = _data(data)
    if d is None:
        raise ErroNegocio("Informe a data.")
    a = atividades.criar(
        s, usuario,
        atividades.NovaAtividade(
            modalidade_id=modalidade_id, nome=nome, inicio=datetime.combine(d, _hora(hora)), max_participantes=max_participantes,
            local_nome=local_nome, latitude=_num(latitude, float), longitude=_num(longitude, float),
            arena_id=int(arena_id) if arena_id else None, quadra_id=int(quadra_id) if quadra_id else None, grupo_id=int(grupo_id) if grupo_id else None,
            duracao_min=duracao_min, nivel=nivel, categoria=categoria, idade_min=_num(idade_min, int), idade_max=_num(idade_max, int),
            valor=_num(valor) or Decimal(0), descricao=descricao or None, regras=regras or None, percurso=percurso or None,
            exige_aprovacao=bool(exige_aprovacao), visibilidade=visibilidade, falta_gente=bool(falta_gente),
        ),
    )
    if imagem:
        atividades.definir_capa(s, a.id, usuario, imagem)
    return RedirectResponse(f"/atividades/{a.id}", status_code=303)


@app.get("/atividades/{atividade_id}/book")
def book_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual):
    a = atividades.obter(s, atividade_id)
    return pagina(request, s, usuario, "book.html", "explorar", b=book.atividade(s, a, usuario))


@app.post("/atividades/{atividade_id}/book/publicar")
def publicar_book_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual, publicar: str = Form("")):
    atividades.definir_book_publicado(s, atividade_id, usuario, bool(publicar))
    request.session["ok"] = "Book publicado: todos os usuários podem ver." if publicar else "Book voltou a ser rascunho."
    return RedirectResponse(f"/atividades/{atividade_id}/book", status_code=303)


@app.post("/atividades/{atividade_id}/capa")
def trocar_capa_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual, capa: UploadFile = File()):
    atividades.definir_capa(s, atividade_id, usuario, midia.ler_upload(capa))
    request.session["ok"] = "Capa atualizada."
    return voltar(request, f"/atividades/{atividade_id}/book")


@app.get("/atividades/{atividade_id}")
def ver_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual, t: str = ""):
    a = atividades.obter(s, atividade_id)
    convites.exigir_acesso_atividade(s, usuario, a, t or None)
    return pagina(request, s, usuario, "atividade.html", "explorar", a=detalhes.atividade(s, a, usuario))


@app.post("/atividades/{atividade_id}/entrar")
def entrar_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual):
    p = vagas.entrar(s, atividade_id, usuario)
    request.session["ok"] = {"confirmado": "Você está confirmado!", "espera": "Lotado: você entrou na lista de espera.", "pendente": "Pedido enviado ao organizador."}.get(p.status, "Pronto.")
    return voltar(request, f"/atividades/{atividade_id}")


@app.post("/atividades/{atividade_id}/sair")
def sair_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual):
    vagas.sair(s, atividade_id, usuario)
    request.session["ok"] = "Você saiu da atividade."
    return voltar(request, f"/atividades/{atividade_id}")


@app.post("/atividades/{atividade_id}/falta-gente")
def falta_gente(atividade_id: int, request: Request, s: Sessao, usuario: Atual, ligado: str = Form("1")):
    atividades.definir_falta_gente(s, atividade_id, usuario, ligado == "1")
    request.session["ok"] = "Atividade em destaque. Estamos avisando atletas compatíveis por perto." if ligado == "1" else "Destaque removido."
    return RedirectResponse(f"/atividades/{atividade_id}", status_code=303)


@app.post("/atividades/{atividade_id}/capacidade")
def capacidade(atividade_id: int, s: Sessao, usuario: Atual, max_participantes: int = Form()):
    atividades.alterar_capacidade(s, atividade_id, usuario, max_participantes)
    return RedirectResponse(f"/atividades/{atividade_id}", status_code=303)


@app.post("/atividades/{atividade_id}/cancelar")
def cancelar_atividade(atividade_id: int, request: Request, s: Sessao, usuario: Atual):
    atividades.cancelar(s, atividade_id, usuario)
    request.session["ok"] = "Atividade cancelada; os participantes foram avisados."
    return RedirectResponse(f"/atividades/{atividade_id}", status_code=303)


@app.post("/atividades/{atividade_id}/participacoes/{participacao_id}/{acao}")
def decidir_participacao(atividade_id: int, participacao_id: int, acao: str, s: Sessao, usuario: Atual):
    funcao = {"aprovar": vagas.aprovar, "recusar": vagas.recusar, "remover": vagas.remover}.get(acao)
    if funcao is None:
        raise NaoEncontrado("Ação desconhecida.")
    funcao(s, atividade_id, participacao_id, usuario)
    return RedirectResponse(f"/atividades/{atividade_id}", status_code=303)


# ---------------------------------------------------------------- grupos


@app.get("/grupos")
def listar_grupos(request: Request, s: Sessao, usuario: Atual):
    la, ln, _ = descoberta.localizacao(usuario)
    todos = descoberta.buscar_grupos(s, usuario, la, ln, descoberta.Filtros(raio_km=None))
    return pagina(request, s, usuario, "grupos.html", "grupos", g={"meus": [x for x in todos if x["sou_membro"]], "descobrir": [x for x in todos if not x["sou_membro"]]})


@app.get("/grupos/novo")
def novo_grupo(request: Request, s: Sessao, usuario: Atual):
    if (r := _sem_plano(request, s, usuario, "grupo")) is not None:
        return r
    la, ln, informada = descoberta.localizacao(usuario)
    return pagina(request, s, usuario, "grupo_novo.html", "grupos", centro={"latitude": la, "longitude": ln, "informada": informada})


@app.post("/grupos")
def criar_grupo(
    s: Sessao, usuario: Atual, nome: str = Form(), modalidade_id: int = Form(), cidade: str = Form(""), local_habitual: str = Form(""),
    descricao: str = Form(""), latitude: str = Form(""), longitude: str = Form(""), entrada: str = Form("aprovacao"),
):
    g = grupos.criar(s, usuario, nome, modalidade_id, descricao or None, cidade or None, local_habitual or None, _num(latitude, float), _num(longitude, float), entrada)
    return RedirectResponse(f"/grupos/{g.id}", status_code=303)


@app.get("/grupos/{grupo_id}")
def ver_grupo(grupo_id: int, request: Request, s: Sessao, usuario: Atual):
    g = grupos.obter(s, grupo_id)
    d = detalhes.grupo(s, g, usuario)
    n = agora()
    msgs = [detalhes.mensagem(m, n) for m in grupos.mensagens(s, g.id)] if d["sou_membro"] else []
    return pagina(request, s, usuario, "grupo.html", "grupos", g=d, mensagens=msgs)


@app.post("/grupos/{grupo_id}/entrar")
def entrar_grupo(grupo_id: int, request: Request, s: Sessao, usuario: Atual):
    m = grupos.entrar(s, grupo_id, usuario)
    if m.status == "pendente":
        request.session["ok"] = "Pedido enviado. Um administrador do grupo vai avaliar."
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


@app.post("/grupos/{grupo_id}/pedidos/{alvo_id}/{acao}")
def decidir_pedido_grupo(grupo_id: int, alvo_id: int, acao: str, s: Sessao, usuario: Atual):
    grupos.decidir_pedido(s, grupo_id, alvo_id, usuario, acao == "aceitar")
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


@app.post("/grupos/{grupo_id}/membros/{alvo_id}/remover")
def remover_do_grupo(grupo_id: int, alvo_id: int, s: Sessao, usuario: Atual):
    grupos.remover(s, grupo_id, alvo_id, usuario)
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


@app.post("/grupos/{grupo_id}/sair")
def sair_grupo(grupo_id: int, s: Sessao, usuario: Atual):
    grupos.sair(s, grupo_id, usuario)
    return RedirectResponse("/grupos", status_code=303)


@app.post("/grupos/{grupo_id}/mensagem")
def postar(grupo_id: int, s: Sessao, usuario: Atual, texto: str = Form(), aviso: str = Form("")):
    grupos.postar(s, grupo_id, usuario, texto, bool(aviso))
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


@app.post("/grupos/{grupo_id}/promover/{alvo_id}")
def promover(grupo_id: int, alvo_id: int, s: Sessao, usuario: Atual):
    grupos.promover(s, grupo_id, alvo_id, usuario)
    return RedirectResponse(f"/grupos/{grupo_id}", status_code=303)


# ---------------------------------------------------------------- campeonatos


@app.get("/campeonatos")
def listar_campeonatos(request: Request, s: Sessao, usuario: Atual, modalidades: str = "", raio: float | None = None, quando: str = "", com_vagas: str = ""):
    f = descoberta.Filtros(
        raio_km=(settings.raio_padrao_km * 3 if raio is None else (raio or None)), modalidades=[m for m in modalidades.split(",") if m],
        quando=quando or None, com_vagas=bool(com_vagas),
    )
    la, ln, _ = descoberta.localizacao(usuario)
    enc = None
    if planos.permissoes(s, usuario)["campeonato"]:
        p = request.query_params
        enc = campeonatos.encerrados(s, usuario, p.get("enc_q", ""), _data_ou_none(p.get("enc_de")), _data_ou_none(p.get("enc_ate")), p.get("enc_org", ""), situacao=p.get("enc_st", "todos"))
        enc["filtros"] = {"q": p.get("enc_q", ""), "de": p.get("enc_de", ""), "ate": p.get("enc_ate", ""), "org": p.get("enc_org", ""), "st": p.get("enc_st", "todos")}
    return pagina(request, s, usuario, "campeonatos.html", "campeonatos", f=f, lista=descoberta.buscar_campeonatos(s, la, ln, f), enc=enc)


@app.get("/tutorial")
def tutorial_indice(request: Request, s: Sessao, usuario: Atual):
    """Menu Tutoriais: lista os guias que a pessoa pode ver."""
    return pagina(request, s, usuario, "tutorial_indice.html", "tutorial")


@app.get("/tutorial/transmissao")
def tutorial_transmissao(request: Request, s: Sessao, usuario: Atual):
    """Como transmitir os jogos ao vivo. Aberto a qualquer usuário logado: mesários também precisam dele."""
    return pagina(request, s, usuario, "tutorial_transmissao.html", "tutorial")


@app.get("/tutorial/campeonatos")
def tutorial_campeonatos(request: Request, s: Sessao, usuario: Atual):
    """Passo a passo da área de campeonatos: só para quem pode criá-los (plano Organizador/Arena e administrador)."""
    if (r := _sem_plano(request, s, usuario, "campeonato")) is not None:
        return r
    return pagina(request, s, usuario, "tutorial_campeonatos.html", "tutorial")


@app.get("/campeonatos/novo")
def novo_campeonato(request: Request, s: Sessao, usuario: Atual):
    if (r := _sem_plano(request, s, usuario, "campeonato")) is not None:
        return r
    la, ln, informada = descoberta.localizacao(usuario)
    return pagina(request, s, usuario, "campeonato_novo.html", "campeonatos", minhas_arenas=arenas.minhas(s, usuario), centro={"latitude": la, "longitude": ln, "informada": informada})


@app.post("/campeonatos")
def criar_campeonato(
    s: Sessao, usuario: Atual, nome: str = Form(), modalidade_id: int = Form(), data_inicio: str = Form(), inscricao_ate: str = Form(),
    max_equipes: int = Form(), atletas_por_equipe: int = Form(5), arena_id: str = Form(""), data_fim: str = Form(""), categoria: str = Form(""),
    local_nome: str = Form(""), latitude: str = Form(""), longitude: str = Form(""), valor_inscricao: str = Form(""), premiacao: str = Form(""),
    regulamento: str = Form(""), descricao: str = Form(""), regulamento_pdf: UploadFile | None = File(None), capa: UploadFile | None = File(None),
):
    imagem = midia.ler_upload(capa) if capa is not None and capa.filename else b""
    if imagem:
        midia.processar_capa(imagem)
    pdf = regulamento_pdf.file.read(settings.limite_pdf_mb * 1024 * 1024 + 1) if regulamento_pdf is not None and regulamento_pdf.filename else b""
    if pdf:
        campeonatos.validar_pdf(pdf)  # antes de criar: um PDF inválido não deixa um campeonato pela metade
    c = campeonatos.criar(
        s, usuario,
        campeonatos.NovoCampeonato(
            modalidade_id=modalidade_id, nome=nome, data_inicio=_data(data_inicio), inscricao_ate=_data(inscricao_ate), max_equipes=max_equipes,
            atletas_por_equipe=atletas_por_equipe, arena_id=int(arena_id) if arena_id else None, data_fim=_data(data_fim), categoria=categoria or None,
            local_nome=local_nome, latitude=_num(latitude, float), longitude=_num(longitude, float), valor_inscricao=_num(valor_inscricao) or Decimal(0),
            premiacao=premiacao or None, regulamento=regulamento or None, descricao=descricao or None,
        ),
    )
    if pdf:
        campeonatos.anexar_regulamento(s, c.id, usuario, pdf, regulamento_pdf.filename)
    return RedirectResponse(f"/campeonatos/{c.id}", status_code=303)


@app.get("/capa/{nome}")
def capa_do_evento(nome: str):
    """Arte/logo de campeonato ou atividade. O nome é aleatório (32 hex): quem não recebeu a URL não a adivinha."""
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", nome):
        raise NaoEncontrado("Imagem não encontrada.")
    return armazenamento.servir(f"capas/{nome}", "image/jpeg", "public, max-age=86400")


@app.get("/campeonatos/{campeonato_id}/book")
def book_campeonato(campeonato_id: int, request: Request, s: Sessao, usuario: Atual):
    c = campeonatos.obter(s, campeonato_id)
    return pagina(request, s, usuario, "book.html", "campeonatos", b=book.campeonato(s, c, usuario))


@app.post("/campeonatos/{campeonato_id}/book/publicar")
def publicar_book_campeonato(campeonato_id: int, request: Request, s: Sessao, usuario: Atual, publicar: str = Form("")):
    campeonatos.definir_book_publicado(s, campeonato_id, usuario, bool(publicar))
    request.session["ok"] = "Book publicado: todos os usuários podem ver." if publicar else "Book voltou a ser rascunho."
    return RedirectResponse(f"/campeonatos/{campeonato_id}/book", status_code=303)


@app.post("/campeonatos/{campeonato_id}/capa")
def trocar_capa_campeonato(campeonato_id: int, request: Request, s: Sessao, usuario: Atual, capa: UploadFile = File()):
    campeonatos.definir_capa(s, campeonato_id, usuario, midia.ler_upload(capa))
    request.session["ok"] = "Capa atualizada."
    return voltar(request, f"/campeonatos/{campeonato_id}/book")


@app.get("/campeonatos/{campeonato_id}")
def ver_campeonato(campeonato_id: int, request: Request, s: Sessao, usuario: Atual):
    c = campeonatos.obter(s, campeonato_id)
    return pagina(request, s, usuario, "campeonato.html", "campeonatos", k=detalhes.campeonato(s, c, usuario))


@app.post("/campeonatos/{campeonato_id}/status")
def status_campeonato(campeonato_id: int, s: Sessao, usuario: Atual, status: str = Form()):
    campeonatos.definir_status(s, campeonato_id, usuario, status)
    return RedirectResponse(f"/campeonatos/{campeonato_id}", status_code=303)


@app.post("/campeonatos/{campeonato_id}/equipes")
def inscrever_equipe(campeonato_id: int, request: Request, s: Sessao, usuario: Atual, nome: str = Form()):
    campeonatos.inscrever_equipe(s, campeonato_id, usuario, nome)
    request.session["ok"] = "Equipe inscrita! Convide os jogadores e aguarde a confirmação do organizador."
    return RedirectResponse(f"/campeonatos/{campeonato_id}", status_code=303)


@app.post("/equipes/{equipe_id}/convidar")
def convidar(equipe_id: int, request: Request, s: Sessao, usuario: Atual, usuario_id: int = Form()):
    campeonatos.convidar(s, equipe_id, usuario, usuario_id)
    request.session["ok"] = "Convite enviado."
    return voltar(request)


@app.post("/equipes/{equipe_id}/responder")
def responder_convite(equipe_id: int, s: Sessao, usuario: Atual, aceitar: str = Form("1")):
    m = campeonatos.responder_convite(s, equipe_id, usuario, aceitar == "1")
    return RedirectResponse(f"/campeonatos/{m.equipe.campeonato_id}", status_code=303)


@app.post("/equipes/{equipe_id}/decidir")
def decidir_equipe(equipe_id: int, s: Sessao, usuario: Atual, confirmar: str = Form("1")):
    e = campeonatos.decidir_equipe(s, equipe_id, usuario, confirmar == "1")
    return RedirectResponse(f"/campeonatos/{e.campeonato_id}", status_code=303)


@app.post("/equipes/{equipe_id}/cancelar")
def cancelar_equipe(equipe_id: int, s: Sessao, usuario: Atual):
    from ..models import Equipe

    e = s.get(Equipe, equipe_id)
    campeonatos.cancelar_equipe(s, equipe_id, usuario)
    return RedirectResponse(f"/campeonatos/{e.campeonato_id}", status_code=303)


# ---------------------------------------------------------------- arenas e gestão


@app.get("/horarios/{horario_id}/reservar")
def reservar_horario_tela(horario_id: int, request: Request, s: Sessao, usuario: Atual):
    h = s.get(HorarioDivulgado, horario_id)
    if h is None or h.inicio <= agora():
        raise ErroNegocio("Esse horário não está mais disponível.")
    arena = h.quadra.arena
    pol, hor = reservas.politica(s, arena), ser.horario(h, agora())
    if not pol["formas"] and not hor["valor"]:
        pol["formas"] = ["local"]  # horário sem valor: nada a cobrar, a reserva só segura o horário
    return pagina(request, s, usuario, "reservar.html", "explorar", h=hor, ar=arena, pol=pol)


@app.post("/horarios/{horario_id}/reservar")
def reservar_horario(horario_id: int, request: Request, s: Sessao, usuario: Atual, forma: str = Form(""), nome: str = Form(""), cpf_cnpj: str = Form(""), email: str = Form("")):
    r = reservas.reservar_horario(s, usuario, horario_id, forma or None, {"nome": nome, "cpf_cnpj": cpf_cnpj, "email": email})
    request.session["ok"] = "Reserva feita! Pague pelo link para confirmar." if r["link_pagamento"] else "Reserva feita! O pagamento é combinado direto com a arena."
    return RedirectResponse("/reservas", status_code=303)


@app.get("/reservas")
def minhas_reservas(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "reservas.html", "reservas", lista=reservas.minhas(s, usuario))


@app.post("/reservas/{reserva_id}/cancelar")
def cancelar_reserva(reserva_id: int, request: Request, s: Sessao, usuario: Atual):
    r = reservas.cancelar(s, usuario, reserva_id)
    request.session["ok"] = "Reserva cancelada." + (f" Devolvemos R$ {r['devolvido']:.2f}.".replace(".", ",") if r["devolvido"] else "")
    return RedirectResponse("/reservas", status_code=303)


@app.get("/arenas/{arena_id}")
def ver_arena(arena_id: int, request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "arena.html", "explorar", ar=detalhes.arena_publica(s, arenas.obter(s, arena_id), usuario))


# ---------------------------------------------------------------- guia de locais (Explorar) e sua gestão


@app.get("/locais/{local_id}")
def ver_local(local_id: int, request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "local.html", "explorar", lc=locais.serializar(locais.obter(s, local_id)))


@app.get("/gestao/locais")
def gestao_locais(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "locais_gestao.html", "locais", lista=[locais.serializar(x) for x in locais.todos(s, usuario)], tipos=locais.TIPOS)


def _centro_local(usuario):
    la, ln, informada = descoberta.localizacao(usuario)
    return {"latitude": la, "longitude": ln, "informada": informada}


@app.get("/gestao/locais/importar")
def importar_locais_form(request: Request, s: Sessao, usuario: Atual):
    locais._exigir_admin(usuario)
    return pagina(request, s, usuario, "locais_importar.html", "locais", tipos=locais.TIPOS, resultado=None)


@app.get("/gestao/locais/modelo.csv")
def modelo_locais(s: Sessao, usuario: Atual):
    locais._exigir_admin(usuario)
    return Response(locais_importacao.modelo_csv(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="modelo-locais-playgo.csv"'})


@app.post("/gestao/locais/importar")
async def importar_locais(request: Request, s: Sessao, usuario: Atual, tipo: str = Form("auto"), incompleto: str = Form(""), arquivo: UploadFile | None = File(None)):
    locais._exigir_admin(usuario)
    dados = await arquivo.read() if arquivo is not None else b""
    resultado = locais_importacao.importar(s, usuario, arquivo.filename if arquivo else "", dados, tipo or "auto", permitir_incompleto=bool(incompleto))
    return pagina(request, s, usuario, "locais_importar.html", "locais", tipos=locais.TIPOS, resultado=resultado)


@app.get("/gestao/locais/novo")
def novo_local(request: Request, s: Sessao, usuario: Atual):
    locais._exigir_admin(usuario)
    return pagina(request, s, usuario, "local_form.html", "locais", lc=None, centro=_centro_local(usuario), tipos=locais.TIPOS)


def _dados_local(tipo, nome, endereco, cidade, latitude, longitude, horario_funcionamento, telefone, email, site, regras, descricao, modalidades, instagram="", facebook="", maps_url="") -> dict:
    return dict(
        tipo=tipo, nome=nome, endereco=endereco, cidade=cidade, latitude=_num(latitude, float), longitude=_num(longitude, float),
        horario_funcionamento=horario_funcionamento, telefone=telefone, email=email, site=site, instagram=instagram, facebook=facebook, maps_url=maps_url,
        regras=regras, descricao=descricao, modalidades=modalidades,
    )


@app.post("/gestao/locais")
def criar_local(
    request: Request, s: Sessao, usuario: Atual, tipo: str = Form(""), nome: str = Form(""), endereco: str = Form(""), cidade: str = Form(""),
    latitude: str = Form(""), longitude: str = Form(""), horario_funcionamento: str = Form(""), telefone: str = Form(""), email: str = Form(""),
    site: str = Form(""), regras: str = Form(""), descricao: str = Form(""), modalidades: list[str] = Form(default=[]),
    instagram: str = Form(""), facebook: str = Form(""), maps_url: str = Form(""),
):
    locais.criar(s, usuario, **_dados_local(tipo, nome, endereco, cidade, latitude, longitude, horario_funcionamento, telefone, email, site, regras, descricao, modalidades, instagram, facebook, maps_url))
    request.session["ok"] = "Local cadastrado no guia."
    return RedirectResponse("/gestao/locais", status_code=303)


@app.get("/gestao/locais/{local_id}/editar")
def editar_local_form(local_id: int, request: Request, s: Sessao, usuario: Atual):
    locais._exigir_admin(usuario)
    lc = locais.serializar(locais.obter(s, local_id, incluir_inativos=True))
    return pagina(request, s, usuario, "local_form.html", "locais", lc=lc, centro={"latitude": lc["latitude"], "longitude": lc["longitude"], "informada": True}, tipos=locais.TIPOS)


@app.post("/gestao/locais/{local_id}")
def editar_local(
    local_id: int, request: Request, s: Sessao, usuario: Atual, tipo: str = Form(""), nome: str = Form(""), endereco: str = Form(""), cidade: str = Form(""),
    latitude: str = Form(""), longitude: str = Form(""), horario_funcionamento: str = Form(""), telefone: str = Form(""), email: str = Form(""),
    site: str = Form(""), regras: str = Form(""), descricao: str = Form(""), modalidades: list[str] = Form(default=[]),
    instagram: str = Form(""), facebook: str = Form(""), maps_url: str = Form(""),
):
    locais.editar(s, usuario, local_id, **_dados_local(tipo, nome, endereco, cidade, latitude, longitude, horario_funcionamento, telefone, email, site, regras, descricao, modalidades, instagram, facebook, maps_url))
    request.session["ok"] = "Local atualizado."
    return RedirectResponse("/gestao/locais", status_code=303)


@app.post("/gestao/locais/{local_id}/ativo")
def ativar_local(local_id: int, request: Request, s: Sessao, usuario: Atual, ativo: str = Form("")):
    locais.definir_ativo(s, usuario, local_id, ativo == "1")
    return RedirectResponse("/gestao/locais", status_code=303)



@app.get("/gestao")
def gestao(request: Request, s: Sessao, usuario: Atual, arena: int | None = None, dia: str = ""):
    if (r := _sem_plano(request, s, usuario, "arena")) is not None:
        return r
    lista = arenas.minhas(s, usuario)
    escolhida = next((a for a in lista if a.id == arena), lista[0] if lista else None)
    if escolhida is None:
        return pagina(request, s, usuario, "gestao.html", "gestao", arena=None)
    d = _data(dia) or agora().date()
    from ..models import Campeonato
    from sqlalchemy import select

    camps = [ser.campeonato(c, agora()) for c in s.scalars(select(Campeonato).where(Campeonato.arena_id == escolhida.id, Campeonato.status.in_(("aberto", "em_andamento"))).order_by(Campeonato.data_inicio))]
    return pagina(
        request, s, usuario, "gestao.html", "gestao", arena=escolhida, arenas=lista, p=arenas.indicadores(s, escolhida, d),
        agenda=arenas.agenda_do_dia(s, escolhida, d), dia=d, dia_texto=d.strftime("%d/%m/%Y"), anterior=(d - timedelta(days=1)).isoformat(),
        proximo=(d + timedelta(days=1)).isoformat(), campeonatos=camps, rec_fase=recebimentos.fase(),
    )


@app.get("/gestao/arenas/nova")
def nova_arena(request: Request, s: Sessao, usuario: Atual):
    if (r := _sem_plano(request, s, usuario, "arena")) is not None:
        return r
    la, ln, informada = descoberta.localizacao(usuario)
    return pagina(request, s, usuario, "arena_nova.html", "gestao", centro={"latitude": la, "longitude": ln, "informada": informada})


@app.post("/gestao/arenas")
def criar_arena(
    s: Sessao, usuario: Atual, nome: str = Form(), latitude: str = Form(""), longitude: str = Form(""), endereco: str = Form(""), cidade: str = Form(""),
    abre: str = Form("06:00"), fecha: str = Form("23:00"), estrutura: str = Form(""), descricao: str = Form(""), regras: str = Form(""), tipo_estabelecimento: str = Form("arena"),
):
    lat, lng = _num(latitude, float), _num(longitude, float)
    if lat is None or lng is None:
        raise ErroNegocio("Marque a localização da arena no mapa.")
    a = arenas.criar(s, usuario, nome, lat, lng, endereco, cidade or None, descricao or None, estrutura or None, regras or None, _hora(abre), _hora(fecha), tipo_estabelecimento)
    return RedirectResponse(f"/gestao?arena={a.id}", status_code=303)


@app.get("/gestao/arenas/{arena_id}/editar")
def editar_arena_tela(arena_id: int, request: Request, s: Sessao, usuario: Atual):
    if (r := _sem_plano(request, s, usuario, "arena")) is not None:
        return r
    a = arenas.obter(s, arena_id)
    arenas.exigir_gestor(a, usuario)
    return pagina(
        request, s, usuario, "arena_editar.html", "gestao", arena=a, centro={"latitude": a.latitude, "longitude": a.longitude, "informada": True},
        online_pronto=reservas._recebimento_pronto(s, a),
    )


@app.post("/gestao/arenas/{arena_id}/editar")
def editar_arena(
    arena_id: int, s: Sessao, usuario: Atual, nome: str = Form(), latitude: str = Form(""), longitude: str = Form(""), endereco: str = Form(""), cidade: str = Form(""),
    abre: str = Form("06:00"), fecha: str = Form("23:00"), estrutura: str = Form(""), descricao: str = Form(""), regras: str = Form(""), tipo_estabelecimento: str = Form("arena"),
    pagamento_reserva: str = Form("local"), cancel_horas: int = Form(24), reembolso_pct: int = Form(100), reembolso_pct_fora: int = Form(0),
):
    lat, lng = _num(latitude, float), _num(longitude, float)
    if lat is None or lng is None:
        raise ErroNegocio("Marque a localização da arena no mapa.")
    a = arenas.editar(s, arenas.obter(s, arena_id), usuario, nome, lat, lng, endereco, cidade or None, descricao or None, estrutura or None, regras or None, _hora(abre), _hora(fecha), tipo_estabelecimento, pagamento_reserva, cancel_horas, reembolso_pct, reembolso_pct_fora)
    return RedirectResponse(f"/gestao?arena={a.id}", status_code=303)


def _volta_gestao(arena_id: int | str, dia: str = "") -> RedirectResponse:
    return RedirectResponse(f"/gestao?arena={arena_id}", status_code=303)


@app.post("/gestao/{arena_id}/quadras")
def criar_quadra(arena_id: int, request: Request, s: Sessao, usuario: Atual, nome: str = Form(), tipo: str = Form("quadra"), valor_hora: str = Form(""), capacidade: str = Form(""), modalidades: list[str] = Form(default=[])):
    arenas.criar_quadra(s, arenas.obter(s, arena_id), usuario, nome, modalidades, _num(capacidade, int), _num(valor_hora) or Decimal(0), tipo)
    request.session["ok"] = "Espaço cadastrado."
    return _volta_gestao(arena_id)


@app.post("/gestao/{arena_id}/divulgar-ociosos")
def divulgar_ociosos(arena_id: int, request: Request, s: Sessao, usuario: Atual):
    n = arenas.divulgar_ociosos(s, arenas.obter(s, arena_id), usuario)
    request.session["ok"] = f"{n} horário{'s' if n != 1 else ''} publicado{'s' if n != 1 else ''} para atletas próximos! 🚀"
    return _volta_gestao(arena_id)


@app.post("/gestao/quadras/{quadra_id}/divulgar")
def divulgar(quadra_id: int, request: Request, s: Sessao, usuario: Atual, inicio: str = Form(), arena: int = Form()):
    ini = datetime.fromisoformat(inicio)
    arenas.divulgar(s, quadra_id, ini, ini + timedelta(hours=1), None, None, usuario)
    request.session["ok"] = "Horário divulgado para atletas próximos! 🚀"
    return _volta_gestao(arena)


@app.post("/gestao/quadras/{quadra_id}/reservar")
def reservar(quadra_id: int, s: Sessao, usuario: Atual, inicio: str = Form(), rotulo: str = Form(""), arena: int = Form()):
    arenas.quadra_do_gestor(s, quadra_id, usuario)
    planos.exigir(s, usuario, "arena")
    ini = datetime.fromisoformat(inicio)
    arenas.reservar(s, quadra_id, ini, ini + timedelta(hours=1), "reserva", rotulo.strip() or "Reservada")
    s.commit()
    return _volta_gestao(arena)


@app.post("/gestao/reservas/{reserva_id}/liberar")
def liberar(reserva_id: int, s: Sessao, usuario: Atual, arena: int = Form()):
    arenas.liberar(s, reserva_id, usuario)
    return _volta_gestao(arena)


@app.post("/gestao/horarios/{horario_id}/remover")
def retirar_horario(horario_id: int, s: Sessao, usuario: Atual, arena: int = Form()):
    arenas.despublicar(s, horario_id, usuario)
    return _volta_gestao(arena)


# ---------------------------------------------------------------- perfil e notificações


@app.get("/perfil")
def perfil(request: Request, s: Sessao, usuario: Atual):
    from .. import serializadores
    from ..models import ATIVAS, Atividade, Participacao
    from sqlalchemy import select

    n = agora()
    linhas = s.execute(select(Atividade, Participacao.status).join(Participacao, Participacao.atividade_id == Atividade.id).where(Participacao.usuario_id == usuario.id, Participacao.status.in_(ATIVAS)).order_by(Atividade.inicio.desc()).limit(60)).unique()
    itens = [serializadores.atividade(a, n, None, st) for a, st in linhas]
    proximas = sorted([i for i in itens if i["minutos_para_inicio"] >= -90 and i["status"] == "aberta"], key=lambda i: i["inicio"])
    historico = [i for i in itens if i["minutos_para_inicio"] < -90 or i["status"] != "aberta"]
    return pagina(request, s, usuario, "perfil.html", "", proximas=proximas, historico=historico)


@app.post("/perfil")
async def salvar_perfil(request: Request, s: Sessao, usuario: Atual):
    f = await request.form()
    contas.atualizar_perfil(
        s, usuario, nome=f.get("nome"), cidade=f.get("cidade", ""), raio_km=int(f.get("raio_km") or usuario.raio_km), disponibilidade=f.getlist("disponibilidade"),
        sexo=f.get("sexo", ""), nascimento=_data(f.get("nascimento")), notif_vagas=bool(f.get("notif_vagas")), notif_lembretes=bool(f.get("notif_lembretes")),
        notif_campeonatos=bool(f.get("notif_campeonatos")), notif_grupos=bool(f.get("notif_grupos")), notif_raio_km=int(f.get("notif_raio_km") or usuario.notif_raio_km),
    )
    esportes = {int(k[8:]): v for k, v in f.items() if k.startswith("esporte_") and v}
    contas.definir_esportes(s, usuario, esportes)
    request.session["ok"] = "Perfil salvo."
    return RedirectResponse("/perfil", status_code=303)


@app.get("/notificacoes")
def ver_notificacoes(request: Request, s: Sessao, usuario: Atual):
    n = agora()
    itens = [ser.notificacao(x, n) for x in notificacoes.listar(s, usuario)]
    return pagina(request, s, usuario, "notificacoes.html", "", itens=itens)


@app.post("/notificacoes/lidas")
def marcar_lidas(s: Sessao, usuario: Atual):
    notificacoes.marcar_lidas(s, usuario)
    return RedirectResponse("/notificacoes", status_code=303)


# Rotas do mural, comunidades, termos e privacidade vivem em outro módulo (importado aqui no fim para evitar ciclo).
from . import rotas_chaves, rotas_ensino, rotas_google, rotas_instagram, rotas_mural, rotas_recebimentos, rotas_telao, rotas_youtube  # noqa: E402,F401
