"""API dos recebimentos de parceiros (Arena) pela subconta Asaas — ver `recebimentos.py`. Só a gestão da arena (ou o admin) lê e mexe nos dados dela.
Respostas com dinheiro nunca vão para cache. Os dois webhooks (eventos e validação de saque) usam o token próprio dos parceiros."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import recebimentos
from ..cobranca import TokenWebhookInvalido
from ..deps import AtualApi, Sessao
from ..erros import ErroNegocio

router = APIRouter(prefix="/api/v1/recebimentos")


class AtivarIn(BaseModel):
    """Dados do POST /v3/accounts. Vão ao Asaas e são descartados (só os 4 últimos dígitos do CPF/CNPJ ficam)."""

    nome: str
    email: str
    cpf_cnpj: str
    telefone: str
    cep: str
    endereco: str
    numero: str
    bairro: str
    complemento: str | None = None
    renda_mensal: Decimal
    data_nascimento: str | None = None  # pessoa física
    tipo_empresa: str | None = None  # pessoa jurídica: MEI | LIMITED | INDIVIDUAL | ASSOCIATION


class DestinoIn(BaseModel):
    tipo: str  # CPF | CNPJ | EMAIL | PHONE | EVP
    chave: str
    titularidade_confirmada: bool = False


class SaqueIn(BaseModel):
    valor: Decimal
    chave_idempotencia: str | None = None  # a tela manda um uuid por clique: o duplo clique não saca duas vezes


class PagadorIn(BaseModel):
    nome: str
    cpf_cnpj: str
    email: str | None = None


class CobrarIn(BaseModel):
    valor: Decimal | None = None  # vazio = o valor já registrado na reserva
    pagador: PagadorIn
    metodo: str = "pix"  # pix | cartao


class LiberarIn(BaseModel):
    liberada: bool = True


def _sem_cache(resposta: Response) -> None:
    resposta.headers["Cache-Control"] = "no-store"


@router.get("/arenas/{arena_id}")
def ver(arena_id: int, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.ver(s, arena_id, u)


@router.post("/arenas/{arena_id}/ativar")
def ativar(arena_id: int, corpo: AtivarIn, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.ativar_recebimento(s, arena_id, u, corpo.model_dump())


@router.post("/arenas/{arena_id}/situacao")
def situacao(arena_id: int, resposta: Response, u: AtualApi, s: Sessao):
    """Consulta de novo as pendências e a aprovação (a tela chama depois dos 15 s de espera e ao voltar do link seguro)."""
    _sem_cache(resposta)
    return recebimentos.atualizar_situacao(s, arena_id, u)


@router.post("/arenas/{arena_id}/documentos")
async def documento(arena_id: int, resposta: Response, u: AtualApi, s: Sessao, grupo_id: Annotated[str, Form()], tipo: Annotated[str, Form()], arquivo: Annotated[UploadFile, File()]):
    """Repassa o arquivo ao Asaas sem gravar em disco (fica só em memória durante o envio)."""
    _sem_cache(resposta)
    conteudo = await arquivo.read(recebimentos.MAX_DOCUMENTO_BYTES + 1)
    if len(conteudo) > recebimentos.MAX_DOCUMENTO_BYTES:
        raise ErroNegocio("Envie um arquivo de até 10 MB.")
    return recebimentos.enviar_documento(s, arena_id, u, grupo_id, tipo, arquivo.filename or "documento", conteudo, arquivo.content_type or "")


@router.put("/arenas/{arena_id}/destino")
def destino(arena_id: int, corpo: DestinoIn, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.definir_destino(s, arena_id, u, corpo.tipo, corpo.chave, corpo.titularidade_confirmada)


@router.post("/arenas/{arena_id}/dados")
def atualizar_dados(arena_id: int, corpo: AtivarIn, resposta: Response, u: AtualApi, s: Sessao):
    """Atualização anual dos dados cadastrais exigida pelo Asaas."""
    _sem_cache(resposta)
    return recebimentos.atualizar_dados_cadastrais(s, arena_id, u, corpo.model_dump())


@router.get("/arenas/{arena_id}/saldo")
def saldo(arena_id: int, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.saldo(s, arena_id, u)


@router.get("/arenas/{arena_id}/extrato")
def extrato(arena_id: int, resposta: Response, u: AtualApi, s: Sessao, inicio: date | None = None, fim: date | None = None):
    _sem_cache(resposta)
    return recebimentos.extrato(s, arena_id, u, inicio, fim)


@router.get("/arenas/{arena_id}/saques")
def listar_saques(arena_id: int, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.saques(s, arena_id, u)


@router.post("/arenas/{arena_id}/saques")
def sacar(arena_id: int, corpo: SaqueIn, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.solicitar_saque(s, arena_id, u, corpo.valor, corpo.chave_idempotencia)


@router.post("/reservas/{reserva_id}/cobrar")
def cobrar(reserva_id: int, corpo: CobrarIn, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.criar_cobranca_reserva(s, reserva_id, u, corpo.valor, corpo.pagador.model_dump(), corpo.metodo)


@router.post("/reservas/{reserva_id}/cancelar-cobranca")
def cancelar_cobranca(reserva_id: int, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.cancelar_cobranca_reserva(s, reserva_id, u)


# ---------------------------------------------------------------- administração


@router.get("/admin/painel")
def painel(resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.painel_admin(s, u)


@router.post("/admin/arenas/{arena_id}/liberar")
def liberar(arena_id: int, corpo: LiberarIn, u: AtualApi, s: Sessao):
    return recebimentos.liberar_arena(s, u, arena_id, corpo.liberada)


@router.post("/admin/arenas/{arena_id}/rotacionar-chave")
def rotacionar(arena_id: int, resposta: Response, u: AtualApi, s: Sessao):
    _sem_cache(resposta)
    return recebimentos.rotacionar_chave(s, u, arena_id)


# ---------------------------------------------------------------- Asaas chama aqui (sem login: o token no cabeçalho é a autenticação)


async def _corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except ValueError:  # teste de conexão ou corpo vazio
        return {}
    return corpo if isinstance(corpo, dict) else {}


@router.post("/asaas")
async def webhook(request: Request, s: Sessao, asaas_access_token: str = Header(default="")):
    """Webhook das subcontas: cobranças das reservas, transferências (saques) e situação do cadastro. Responde 200 depois de validar o token."""
    try:
        return {"resultado": recebimentos.processar_webhook(s, asaas_access_token, await _corpo(request))}
    except TokenWebhookInvalido as e:
        return JSONResponse({"detail": str(e)}, status_code=401)


@router.post("/asaas/validar-saque")
async def validar_saque(request: Request, s: Sessao, asaas_access_token: str = Header(default="")):
    """Mecanismo de validação de saque do Asaas: responde {"status": "APPROVED"} ou {"status": "REFUSED", "refuseReason": ...}. Toda operação que o PlayGo não registrou é recusada."""
    try:
        return recebimentos.validar_saque(s, asaas_access_token, await _corpo(request))
    except TokenWebhookInvalido as e:
        return JSONResponse({"detail": str(e)}, status_code=401)
