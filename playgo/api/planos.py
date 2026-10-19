"""API de planos e mensalidades: o plano da pessoa, teste grátis, assinatura (Asaas), cancelamento, webhook e o painel do administrador."""

from datetime import date
from decimal import Decimal

from fastapi.responses import JSONResponse
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from .. import cobranca, planos, tutoriais
from ..deps import AtualApi, Sessao
from ..erros import ErroNegocio, SemPermissao

router = APIRouter(prefix="/api/v1")


class PlanoIn(BaseModel):
    plano: str


class AssinarIn(BaseModel):
    plano: str
    cpf_cnpj: str
    ciclo: str = "mensal"  # mensal | anual


class EventosIn(BaseModel):
    quantidade: int
    cpf_cnpj: str


class RegrasIn(BaseModel):
    nome: str | None = None
    valor_mensal: Decimal | None = None
    valor_anual: Decimal | None = None  # vazio/0 = não oferece anual
    valor_evento: Decimal | None = None  # preço de 1 evento avulso (Organizador)
    max_participantes: int | None = None
    max_atividades_abertas: int | None = None
    pode_campeonato: bool | None = None
    pode_arena: bool | None = None
    pode_atividade: bool | None = None


class ConcederIn(BaseModel):
    plano: str
    ate: date | None = None
    inicio: date | None = None  # quando o acesso começa a valer (padrão: hoje)


@router.get("/planos")
def meu_plano(u: AtualApi, s: Sessao):
    return planos.resumo(s, u) | {"cobranca_ativa": cobranca.configurado()}


@router.post("/planos/teste")
def iniciar_teste(corpo: PlanoIn, u: AtualApi, s: Sessao):
    if corpo.plano not in ("pro", "organizador", "arena"):
        raise ErroNegocio("Escolha o plano Pro, Organizador ou Arena.")
    if not planos._iniciar_teste(s, u, corpo.plano):
        raise ErroNegocio("O teste grátis deste plano já foi usado ou você já tem acesso a ele.")
    s.commit()
    return planos.resumo(s, u) | {"cobranca_ativa": cobranca.configurado()}


@router.post("/planos/assinar")
def assinar(corpo: AssinarIn, u: AtualApi, s: Sessao):
    return cobranca.assinar(s, u, corpo.plano, corpo.cpf_cnpj, corpo.ciclo)


@router.post("/planos/eventos")
def comprar_eventos(corpo: EventosIn, u: AtualApi, s: Sessao):
    return cobranca.comprar_eventos(s, u, corpo.quantidade, corpo.cpf_cnpj)


@router.post("/planos/cancelar")
def cancelar(u: AtualApi, s: Sessao):
    return cobranca.cancelar(s, u)


@router.post("/cobranca/asaas")
async def webhook_asaas(request: Request, s: Sessao, asaas_access_token: str = Header(default="")):
    """Webhook do Asaas (configure em Integrações > Webhooks, com o mesmo token de PLAYGO_ASAAS_WEBHOOK_TOKEN)."""
    try:
        corpo = await request.json()
    except ValueError:  # teste de conexão ou corpo vazio: não é erro nosso nem motivo para o Asaas pausar a fila
        corpo = {}
    try:
        return {"resultado": cobranca.processar_webhook(s, asaas_access_token, corpo)}
    except cobranca.TokenWebhookInvalido as e:
        return JSONResponse({"detail": str(e)}, status_code=401)


# ---------------------------------------------------------------- administrador


@router.get("/admin/planos")
def admin_planos(u: AtualApi, s: Sessao):
    if not u.admin:
        raise SemPermissao("Só administradores veem os planos.")
    return [planos._dict(r) for r in planos.todas(s)]


@router.put("/admin/planos/{codigo}")
def admin_salvar_plano(codigo: str, corpo: RegrasIn, u: AtualApi, s: Sessao):
    dados = corpo.model_dump(exclude_unset=True)
    return planos.salvar_regras(s, u, codigo, **{k: v for k, v in dados.items()})


@router.post("/admin/usuarios/{usuario_id}/plano")
def admin_conceder_plano(usuario_id: int, corpo: ConcederIn, u: AtualApi, s: Sessao):
    return planos.conceder(s, u, usuario_id, corpo.plano, corpo.ate, corpo.inicio)


# ---------------------------------------------------------------- tutoriais (o app mostra o mesmo conteúdo do site)


@router.get("/tutoriais")
def tutoriais_lista(u: AtualApi, s: Sessao):
    return tutoriais.lista(s, u)


@router.get("/tutoriais/{codigo}")
def tutorial(codigo: str, u: AtualApi, s: Sessao, embutido: bool = False):
    return tutoriais.html(s, u, codigo, embutido)
