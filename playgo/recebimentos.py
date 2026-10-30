"""Recebimentos de parceiros (começando pela Arena) pela **subconta Asaas** — modelo BaaS / white label.

Uma subconta por parceiro, criada e controlada pelo PlayGo; a Arena nunca vê o painel do Asaas, tudo acontece aqui dentro. A cobrança é criada com a
apiKey da subconta (o dinheiro cai no saldo dela) e a comissão do PlayGo sai por split para a conta-mãe. O saque é um Pix da subconta, só para o
destino cadastrado e conferido pela validação de saque do Asaas (`validar_saque`).

Fases (`PLAYGO_RECEBIMENTOS_FASE`): `desligado` (padrão: nenhuma função chama o Asaas), `avaliacao` (só Arenas liberadas pelo admin, com os limites do
período de avaliação do Asaas) e `baas` (sem os bloqueios locais). A conta-mãe dos planos (cobranca.py) não muda.

Segurança: a apiKey da subconta e a chave Pix de saque ficam cifradas (Fernet) e nunca vão a log ou resposta; o CPF/CNPJ não é guardado (só os 4
últimos dígitos); o livro-razão (`LancamentoRecebedor`) é só de inserção; webhooks e saques são idempotentes. Documentação do Asaas consultada:
https://docs.asaas.com/reference/criar-subconta, /reference/verificar-documentos-pendentes, /reference/enviar-documentos,
/reference/consultar-situacao-cadastral-da-conta, /docs/mecanismo-para-validacao-de-saque-via-webhooks, /docs/gerenciamento-de-chaves-de-api-de-subcontas,
/docs/confirmação-anual-de-dados-comerciais-para-subcontas e /docs/split-de-pagamentos."""

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as SessaoORM

from . import arenas, auditoria, cobranca, notificacoes, seguranca
from .cobranca import TokenWebhookInvalido
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import (
    Arena,
    EventoRecebimento,
    LancamentoRecebedor,
    Quadra,
    Recebedor,
    Reserva,
    SaqueRecebedor,
    Usuario,
)

log = logging.getLogger(__name__)

FASES = ("desligado", "avaliacao", "baas")
# Período de Avaliação do Asaas (BaaS): https://docs.asaas.com/docs/detalhamento-do-fluxo-de-aprovação-de-subcontas
LIMITE_SUBCONTAS = 10
LIMITE_COBRANCAS_SUBCONTA = Decimal(2000)
LIMITE_DIAS = 60
FRACAO_BLOQUEIO = Decimal("0.8")  # bloqueio local preventivo a partir de 80% de cada limite
ESPERA_ONBOARDING_S = 15  # o Asaas pede >= 15 s entre criar a subconta e consultar as pendências
DIAS_ATUALIZACAO_DADOS = 365
AVISO_ATUALIZACAO_DIAS = 40  # o Asaas avisa 40 dias antes do vencimento (ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON)
TIPOS_EMPRESA = ("MEI", "LIMITED", "INDIVIDUAL", "ASSOCIATION")
TIPOS_DOCUMENTO = (
    "ALLOW_BANK_ACCOUNT_DEPOSIT_STATEMENT", "CUSTOM", "EMANCIPATION_OF_MINORS", "ENTREPRENEUR_REQUIREMENT", "IDENTIFICATION_SELFIE", "IDENTIFICATION",
    "INVOICE", "MEI_CERTIFICATE", "MINUTES_OF_CONSTITUTION", "MINUTES_OF_ELECTION", "POWER_OF_ATTORNEY", "SOCIAL_CONTRACT",
)
TIPOS_CHAVE_PIX = ("CPF", "CNPJ", "EMAIL", "PHONE", "EVP")
MAX_DOCUMENTO_BYTES = 10 * 1024 * 1024
EVENTOS_WEBHOOK = (
    "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "PAYMENT_REFUNDED", "PAYMENT_DELETED", "PAYMENT_OVERDUE", "PAYMENT_SPLIT_DONE",
    "TRANSFER_DONE", "TRANSFER_FAILED", "TRANSFER_CANCELLED",
    "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_APPROVED", "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_AWAITING_APPROVAL", "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_PENDING", "ACCOUNT_STATUS_BANK_ACCOUNT_INFO_REJECTED",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_APPROVED", "ACCOUNT_STATUS_COMMERCIAL_INFO_AWAITING_APPROVAL", "ACCOUNT_STATUS_COMMERCIAL_INFO_PENDING", "ACCOUNT_STATUS_COMMERCIAL_INFO_REJECTED",
    "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON", "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED",
    "ACCOUNT_STATUS_DOCUMENT_APPROVED", "ACCOUNT_STATUS_DOCUMENT_AWAITING_APPROVAL", "ACCOUNT_STATUS_DOCUMENT_PENDING", "ACCOUNT_STATUS_DOCUMENT_REJECTED",
    "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED", "ACCOUNT_STATUS_GENERAL_APPROVAL_AWAITING_APPROVAL", "ACCOUNT_STATUS_GENERAL_APPROVAL_PENDING", "ACCOUNT_STATUS_GENERAL_APPROVAL_REJECTED",
)
CENTAVO = Decimal("0.01")


# ---------------------------------------------------------------- fases, rodapé e criptografia


def fase() -> str:
    return settings.recebimentos_fase if settings.recebimentos_fase in FASES else "desligado"


def exigir_ligado() -> None:
    if fase() == "desligado":
        raise ErroNegocio("O recebimento pelo app ainda não está disponível. Em breve!")


def rodape() -> dict:
    """Aviso obrigatório (Resolução Conjunta nº 16/17): o Asaas é a instituição prestadora do serviço. Vai em toda resposta e tela de recebimentos."""
    links = []
    for item in settings.recebimentos_links_asaas.split(";"):
        texto, _, url = item.partition("|")
        if texto.strip() and url.strip().startswith("https://"):
            links.append({"texto": texto.strip(), "url": url.strip()})
    return {"texto": settings.recebimentos_texto_asaas, "links": links}


def com_rodape(dados: dict) -> dict:
    return dados | {"asaas": rodape()}


def _fernet() -> Fernet:
    chave = settings.recebimentos_chave.strip()
    if not chave:  # sem chave dedicada: derivada da chave de sessão (trocar a chave de sessão invalidaria as apiKeys guardadas)
        chave = base64.urlsafe_b64encode(hashlib.sha256(b"playgo-recebimentos|" + seguranca.chave_sessao().encode()).digest()).decode()
    return Fernet(chave.encode())


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("Recebimentos: não foi possível decifrar um segredo (a chave PLAYGO_RECEBIMENTOS_CHAVE mudou?)")
        raise ErroNegocio("Não foi possível usar as credenciais de recebimento. Fale com a administração.") from None


def _chave_api(rec: Recebedor) -> str:
    return decifrar(rec.api_key_cripto)


def _chamar(rec: Recebedor, metodo: str, caminho: str, json: dict | None = None, **extra) -> dict:
    """Chamada ao Asaas com a apiKey da SUBCONTA (nunca registrada em log)."""
    return cobranca._chamar(metodo, caminho, json, chave=_chave_api(rec), **extra)


def _dinheiro(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- avaliação (limites do Asaas)


def avaliacao(s: SessaoORM) -> dict:
    """Contadores do Período de Avaliação: só subcontas de PRODUÇÃO contam. Cobranças = reservas com cobrança criada (pendente, paga ou estornada)."""
    prod = list(s.scalars(select(Recebedor).where(Recebedor.ambiente == "producao").order_by(Recebedor.criado_em)))
    primeira = prod[0].criado_em if prod else None
    dias_usados = (agora() - primeira).days if primeira else 0
    totais: dict[int, Decimal] = {}
    for r in prod:
        total = s.scalar(
            select(func.coalesce(func.sum(Reserva.valor), 0)).join(Quadra, Quadra.id == Reserva.quadra_id)
            .where(Quadra.arena_id == r.arena_id, Reserva.status_pagamento.in_(("pendente", "pago", "estornado")))
        )
        totais[r.id] = _dinheiro(total)
    maior = max(totais.values(), default=Decimal(0))
    return {
        "fase": fase(), "ambiente": settings.asaas_ambiente,
        "subcontas": len(prod), "limite_subcontas": LIMITE_SUBCONTAS,
        "maior_cobranca_subconta": maior, "limite_cobrancas_subconta": LIMITE_COBRANCAS_SUBCONTA,
        "primeira_subconta_em": primeira, "dias_usados": dias_usados, "dias_restantes": max(0, LIMITE_DIAS - dias_usados) if primeira else LIMITE_DIAS, "limite_dias": LIMITE_DIAS,
        "por_subconta": {str(k): v for k, v in totais.items()},
        "bloqueio_em": "80%",
    }


def _checar_nova_subconta(s: SessaoORM, arena: Arena) -> None:
    if fase() != "avaliacao":
        return
    if not arena.recebimento_liberado:
        raise ErroNegocio("Esta arena ainda não foi liberada para receber pelo app. Fale com a administração do PlayGo.")
    if settings.asaas_ambiente != "producao":
        return  # o sandbox não conta na avaliação
    a = avaliacao(s)
    if a["subcontas"] >= int(LIMITE_SUBCONTAS * FRACAO_BLOQUEIO):
        raise ErroNegocio(f"O período de avaliação do Asaas permite até {LIMITE_SUBCONTAS} subcontas e já usamos {a['subcontas']}: novas ativações ficam em espera até a conclusão da avaliação.")
    if a["primeira_subconta_em"] and a["dias_usados"] >= int(LIMITE_DIAS * FRACAO_BLOQUEIO):
        raise ErroNegocio("O período de avaliação do Asaas está perto do fim (60 dias): novas ativações ficam em espera até a conclusão da avaliação.")


def _checar_nova_cobranca(s: SessaoORM, rec: Recebedor, valor: Decimal) -> None:
    if fase() != "avaliacao" or rec.ambiente != "producao":
        return
    a = avaliacao(s)
    if a["primeira_subconta_em"] and a["dias_usados"] >= int(LIMITE_DIAS * FRACAO_BLOQUEIO):
        raise ErroNegocio("O período de avaliação do Asaas está perto do fim: novas cobranças ficam em espera até a conclusão da avaliação. Fale com a administração.")
    usado = Decimal(a["por_subconta"].get(str(rec.id), 0))
    if usado + valor > LIMITE_COBRANCAS_SUBCONTA * FRACAO_BLOQUEIO:
        raise ErroNegocio(f"Durante a avaliação do Asaas cada subconta pode cobrar até R$ {LIMITE_COBRANCAS_SUBCONTA:.0f} (já são R$ {usado:.2f}). Para não travar uma reserva no meio, novas cobranças aguardam a conclusão da avaliação.")


def liberar_arena(s: SessaoORM, admin: Usuario, arena_id: int, liberada: bool = True) -> dict:
    """Fase de avaliação: o admin escolhe quais Arenas podem criar subconta."""
    if not admin.admin:
        raise SemPermissao("Só administradores liberam recebimentos.")
    arena = s.get(Arena, arena_id)
    if arena is None:
        raise NaoEncontrado("Arena não encontrada.")
    arena.recebimento_liberado = bool(liberada)
    auditoria.registrar(s, admin.id, "recebimento_liberar" if liberada else "recebimento_bloquear", "arena", arena.id)
    s.commit()
    return {"arena_id": arena.id, "liberada": arena.recebimento_liberado}


def painel_admin(s: SessaoORM, admin: Usuario) -> dict:
    if not admin.admin:
        raise SemPermissao("Só administradores veem o painel de recebimentos.")
    recebedores = {r.arena_id: r for r in s.scalars(select(Recebedor))}
    linhas = []
    for a in s.scalars(select(Arena).where(Arena.ativo).order_by(Arena.nome)):
        r = recebedores.get(a.id)
        linhas.append({
            "arena_id": a.id, "arena": a.nome, "liberada": a.recebimento_liberado,
            "recebedor": None if r is None else {"id": r.id, "status_kyc": r.status_kyc, "ambiente": r.ambiente, "ativo": r.ativo, "criado_em": r.criado_em, "saldo": saldo_interno(s, r.id), "precisa_atualizar_dados": precisa_atualizar(r)},
        })
    return com_rodape({"fase": fase(), "avaliacao": avaliacao(s), "arenas": linhas})


# ---------------------------------------------------------------- estado do recebedor


def precisa_atualizar(rec: Recebedor) -> bool:
    """A atualização anual dos dados cadastrais (Asaas) está para vencer ou venceu."""
    if rec.dados_atualizados_em is None:
        return False
    return agora() >= rec.dados_atualizados_em + timedelta(days=DIAS_ATUALIZACAO_DADOS - AVISO_ATUALIZACAO_DIAS)


def saldo_interno(s: SessaoORM, recebedor_id: int) -> Decimal:
    return _dinheiro(s.scalar(select(func.coalesce(func.sum(LancamentoRecebedor.valor_liquido), 0)).where(LancamentoRecebedor.recebedor_id == recebedor_id)))


def _destino(rec: Recebedor) -> dict | None:
    if not rec.destino_cripto:
        return None
    return {"tipo": rec.destino_tipo, "chave": rec.destino_mascara, "titularidade_confirmada": rec.destino_confirmado}


def estado(rec: Recebedor | None, arena: Arena | None = None) -> dict:
    """O que a tela mostra. Nunca inclui a apiKey nem a chave Pix inteira."""
    base = {"fase": fase(), "liberada": bool(arena.recebimento_liberado) if arena is not None else None}
    if rec is None:
        return com_rodape(base | {"ativo": False, "recebedor": None})
    espera = max(0, int((rec.consultar_apos - agora()).total_seconds()) + 1) if rec.consultar_apos and rec.consultar_apos > agora() else 0
    return com_rodape(base | {"ativo": rec.ativo, "recebedor": {
        "id": rec.id, "arena_id": rec.arena_id, "status_kyc": rec.status_kyc, "ambiente": rec.ambiente, "documentos_pendentes": rec.documentos_pendentes or [],
        "motivo_recusa": rec.motivo_recusa, "aguardar_s": espera, "documento_final": rec.documento_final, "email_mascara": rec.email_mascara, "destino": _destino(rec),
        "dados_atualizados_em": rec.dados_atualizados_em, "precisa_atualizar_dados": precisa_atualizar(rec), "pode_cobrar": rec.ativo and rec.status_kyc == "aprovado",
    }})


def _arena_do_gestor(s: SessaoORM, arena_id: int, u: Usuario) -> Arena:
    arena = arenas.obter(s, arena_id)
    arenas.exigir_gestor(arena, u)
    return arena


def _recebedor_da_arena(s: SessaoORM, arena: Arena, lock: bool = False) -> Recebedor | None:
    q = select(Recebedor).where(Recebedor.arena_id == arena.id)
    return s.scalar(q.with_for_update(of=Recebedor) if lock else q)


def _recebedor_obrigatorio(s: SessaoORM, arena: Arena, lock: bool = False) -> Recebedor:
    rec = _recebedor_da_arena(s, arena, lock)
    if rec is None or not rec.ativo:
        raise ErroNegocio("Esta arena ainda não ativou o recebimento pelo app.")
    return rec


def ver(s: SessaoORM, arena_id: int, u: Usuario) -> dict:
    """Tela inicial dos recebimentos de uma arena. Só gestores da arena (ou admin)."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    return estado(_recebedor_da_arena(s, arena), arena)


# ---------------------------------------------------------------- ativação da subconta


def _digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def _payload_cadastro(dados: dict) -> tuple[dict, str]:
    """Valida os dados do cliente e monta o corpo do Asaas (campos de https://docs.asaas.com/reference/criar-subconta). Devolve (corpo, cpf/cnpj)."""
    doc = cobranca._cpf_cnpj(dados.get("cpf_cnpj", ""))
    nome, email = (dados.get("nome") or "").strip(), (dados.get("email") or "").strip()
    if len(nome) < 3 or "@" not in email:
        raise ErroNegocio("Informe o nome (ou razão social) e um e-mail válido.")
    telefone, cep = _digitos(dados.get("telefone")), _digitos(dados.get("cep"))
    if len(telefone) not in (10, 11):
        raise ErroNegocio("Informe o celular com DDD.")
    if len(cep) != 8:
        raise ErroNegocio("Informe o CEP com 8 dígitos.")
    endereco, numero, bairro = (dados.get("endereco") or "").strip(), (dados.get("numero") or "").strip(), (dados.get("bairro") or "").strip()
    if not endereco or not numero or not bairro:
        raise ErroNegocio("Informe endereço, número e bairro.")
    try:
        renda = Decimal(str(dados.get("renda_mensal")))
    except (ArithmeticError, ValueError):
        renda = Decimal(0)
    if renda <= 0:
        raise ErroNegocio("Informe o faturamento (ou a renda) mensal aproximado.")
    corpo: dict = {"name": nome, "email": email, "cpfCnpj": doc, "mobilePhone": telefone, "incomeValue": float(renda), "address": endereco, "addressNumber": numero, "province": bairro, "postalCode": cep}
    if dados.get("complemento"):
        corpo["complement"] = str(dados["complemento"]).strip()
    if len(doc) == 14:
        tipo = str(dados.get("tipo_empresa") or "").upper()
        if tipo not in TIPOS_EMPRESA:
            raise ErroNegocio("Informe o tipo da empresa: MEI, Limitada, Empresa individual ou Associação.")
        corpo["companyType"] = tipo
    else:
        try:
            nasc = date.fromisoformat(str(dados.get("data_nascimento") or ""))
        except ValueError:
            raise ErroNegocio("Informe a data de nascimento do titular.") from None
        if nasc >= agora().date() - timedelta(days=18 * 365):
            raise ErroNegocio("O titular precisa ter 18 anos ou mais.")
        corpo["birthDate"] = nasc.isoformat()
    return corpo, doc


def _webhook_subconta(email: str) -> dict:
    """Webhooks cadastrados junto com a subconta (campo `webhooks` do POST /v3/accounts): cobranças, transferências e situação da conta."""
    token = settings.asaas_webhook_token_parceiros
    if len(token) < 32:
        raise ErroNegocio("O servidor ainda não tem PLAYGO_ASAAS_WEBHOOK_TOKEN_PARCEIROS (32 a 255 caracteres). Fale com a administração.")
    if not settings.url_publica:
        raise ErroNegocio("O servidor ainda não conhece o próprio endereço (PLAYGO_URL_PUBLICA). Fale com a administração.")
    return {
        "name": "PlayGo recebimentos", "url": settings.url_publica.rstrip("/") + "/api/v1/recebimentos/asaas", "email": settings.recebimentos_email_alertas or email,
        "enabled": True, "interrupted": False, "apiVersion": 3, "authToken": token, "sendType": "NON_SEQUENTIALLY", "events": list(EVENTOS_WEBHOOK),
    }


def ativar_recebimento(s: SessaoORM, arena_id: int, u: Usuario, dados: dict) -> dict:
    """Cria a subconta da arena (POST /v3/accounts) já com os webhooks. Só gestor da arena ou admin. O CPF/CNPJ vai ao Asaas e é descartado."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    if _recebedor_da_arena(s, arena) is not None:
        raise ErroNegocio("Esta arena já ativou o recebimento pelo app.")
    _checar_nova_subconta(s, arena)
    corpo, doc = _payload_cadastro(dados)
    corpo["webhooks"] = [_webhook_subconta(corpo["email"])]
    resp = cobranca._chamar("POST", "/accounts", corpo)  # conta-mãe
    token = resp.get("accessToken") if isinstance(resp.get("accessToken"), dict) else {}
    api_key = token.get("apiKey") or resp.get("apiKey")
    if not resp.get("id") or not resp.get("walletId") or not api_key:
        log.error("Recebimentos: o Asaas criou a subconta %s mas não devolveu walletId/apiKey", resp.get("id"))
        raise ErroNegocio("O sistema de pagamento criou a conta mas não devolveu as credenciais. Fale com a administração.")
    rec = Recebedor(
        arena_id=arena.id, asaas_account_id=str(resp["id"]), wallet_id=str(resp["walletId"]), api_key_cripto=cifrar(api_key), api_key_id=token.get("id"),
        ambiente="producao" if settings.asaas_ambiente == "producao" else "sandbox", status_kyc="criando", documento_final=doc[-4:], email_mascara=_mascara_chave("EMAIL", corpo["email"]),
        consultar_apos=agora() + timedelta(seconds=ESPERA_ONBOARDING_S), dados_atualizados_em=agora(),
    )
    s.add(rec)
    auditoria.registrar(s, u.id, "recebimento_ativar", "arena", arena.id, ambiente=rec.ambiente)
    s.commit()
    return estado(rec, arena)


# ---------------------------------------------------------------- KYC: pendências e documentos


def _pendencia(g: dict) -> dict:
    return {
        "id": g.get("id"), "tipo": g.get("type"), "titulo": g.get("title"), "descricao": g.get("description"), "status": g.get("status"),
        "onboarding_url": g.get("onboardingUrl"), "onboarding_expira": g.get("onboardingUrlExpirationDate"),
    }


def _aplicar_situacao(rec: Recebedor, general: str | None, grupos: list[dict], rejeicao: str | None) -> None:
    if general == "APPROVED":
        rec.status_kyc, rec.documentos_pendentes, rec.motivo_recusa = "aprovado", [], None
        return
    pendentes = [_pendencia(g) for g in grupos if g.get("status") in ("NOT_SENT", "REJECTED")]
    rec.documentos_pendentes = pendentes
    if rejeicao:
        rec.status_kyc, rec.motivo_recusa = "recusado", rejeicao[:300]
    elif pendentes:
        rec.status_kyc, rec.motivo_recusa = "documentos_pendentes", None
    else:
        rec.status_kyc, rec.motivo_recusa = "em_analise", None


def atualizar_situacao(s: SessaoORM, arena_id: int, u: Usuario) -> dict:
    """Consulta as pendências (GET /v3/myAccount/documents) e a situação geral (GET /v3/myAccount/status) com a apiKey da subconta.
    Antes de 15 s da criação devolve o estado com `aguardar_s` (a tela tenta de novo, sem bloquear a requisição)."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    if rec.consultar_apos and agora() < rec.consultar_apos:
        return estado(rec, arena)
    try:
        status = _chamar(rec, "GET", "/myAccount/status")
        docs = _chamar(rec, "GET", "/myAccount/documents")
    except ErroNegocio:
        return estado(rec, arena)  # o Asaas ainda processa a conta: a tela tenta de novo
    _aplicar_situacao(rec, status.get("general"), docs.get("data") or [], docs.get("rejectReasons"))
    s.commit()
    return estado(rec, arena)


def enviar_documento(s: SessaoORM, arena_id: int, u: Usuario, grupo_id: str, tipo: str, nome_arquivo: str, conteudo: bytes, content_type: str) -> dict:
    """Repassa um documento ao Asaas (POST /v3/myAccount/documents/{id}, multipart) sem gravar o arquivo em disco, em log ou no banco.
    Grupos com `onboardingUrl` NÃO aceitam envio pela API: a Arena usa o link."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    grupo = next((p for p in (rec.documentos_pendentes or []) if p.get("id") == grupo_id), None)
    if grupo is None:
        raise ErroNegocio("Esse documento não está pendente. Atualize a tela.")
    if grupo.get("onboarding_url"):
        raise ErroNegocio("Esse documento é enviado pelo link do Asaas: use o botão \"Enviar pelo link seguro\".")
    if tipo not in TIPOS_DOCUMENTO:
        raise ErroNegocio("Tipo de documento inválido.")
    if not conteudo or len(conteudo) > MAX_DOCUMENTO_BYTES:
        raise ErroNegocio("Envie um arquivo de até 10 MB.")
    if not (conteudo.startswith(b"%PDF-") or conteudo[:3] == b"\xff\xd8\xff" or conteudo[:8] == b"\x89PNG\r\n\x1a\n"):
        raise ErroNegocio("Envie o documento em PDF, JPG ou PNG.")
    try:
        _chamar(rec, "POST", f"/myAccount/documents/{grupo_id}", arquivos={"documentFile": ("documento", conteudo, content_type or "application/octet-stream")}, dados={"type": tipo})
    except ErroNegocio as e:
        if "via API" not in str(e):
            raise
        raise ErroNegocio("O Asaas não aceita esse documento por aqui. Abra o e-mail que o Asaas enviou ao endereço do cadastro (veja também o spam) "
                          "e envie por lá; depois toque em \"Atualizar situação\".") from None
    auditoria.registrar(s, u.id, "recebimento_documento", "arena", arena.id, tipo=tipo)  # só o tipo: nunca o nome nem o conteúdo
    rec.consultar_apos = None
    s.commit()
    return atualizar_situacao(s, arena_id, u)


# ---------------------------------------------------------------- destino de saque e dados cadastrais


def _mascara_chave(tipo: str, chave: str) -> str:
    if tipo in ("CPF", "CNPJ"):
        return "•••" + _digitos(chave)[-4:]
    if tipo == "EMAIL":
        local, _, dominio = chave.partition("@")
        return (local[:1] + "•••@" + dominio)
    if tipo == "PHONE":
        return "•••" + _digitos(chave)[-4:]
    return chave[:4] + "•••" + chave[-4:]


def definir_destino(s: SessaoORM, arena_id: int, u: Usuario, tipo: str, chave: str, titularidade_confirmada: bool) -> dict:
    """Cadastra a chave Pix de saque. Precisa ser da mesma titularidade da subconta: com chave CPF/CNPJ conferimos os 4 últimos dígitos do documento
    da conta; nos demais tipos a Arena declara a titularidade (e o Asaas valida no saque)."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    tipo, chave = (tipo or "").upper(), (chave or "").strip()
    if tipo not in TIPOS_CHAVE_PIX or not chave:
        raise ErroNegocio("Escolha o tipo da chave Pix e informe a chave.")
    if not titularidade_confirmada:
        raise ErroNegocio("Confirme que a chave Pix é da mesma titularidade da conta de recebimento.")
    if tipo in ("CPF", "CNPJ"):
        d = _digitos(chave)
        if len(d) != (11 if tipo == "CPF" else 14):
            raise ErroNegocio(f"{tipo} inválido.")
        if rec.documento_final and d[-4:] != rec.documento_final:
            raise ErroNegocio("A chave Pix precisa ser do mesmo titular da conta de recebimento.")
        chave = d
    elif tipo == "EMAIL" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", chave):
        raise ErroNegocio("E-mail inválido.")
    elif tipo == "PHONE":
        d = _digitos(chave)
        if len(d) not in (10, 11, 12, 13):
            raise ErroNegocio("Telefone inválido.")
        chave = "+55" + d if not d.startswith("55") else "+" + d
    elif tipo == "EVP" and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", chave):
        raise ErroNegocio("Chave aleatória inválida.")
    rec.destino_tipo, rec.destino_cripto, rec.destino_mascara, rec.destino_confirmado = tipo, cifrar(chave), _mascara_chave(tipo, chave), True
    auditoria.registrar(s, u.id, "recebimento_destino", "arena", arena.id, tipo=tipo)
    s.commit()
    return estado(rec, arena)


def atualizar_dados_cadastrais(s: SessaoORM, arena_id: int, u: Usuario, dados: dict) -> dict:
    """Atualização anual exigida pelo Asaas (POST /v3/myAccount/commercialInfo com a apiKey da subconta; todos os campos de novo).
    https://docs.asaas.com/docs/confirmação-anual-de-dados-comerciais-para-subcontas. Os dados vão ao Asaas e são descartados."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    corpo, doc = _payload_cadastro(dados)
    if rec.documento_final and doc[-4:] != rec.documento_final:
        raise ErroNegocio("O CPF/CNPJ informado não é o da conta de recebimento.")
    corpo.pop("name", None)  # o nome não faz parte dos dados comerciais
    corpo["personType"] = "JURIDICA" if len(doc) == 14 else "FISICA"  # o Asaas exige o tipo de pessoa
    if corpo["personType"] == "JURIDICA":  # em PJ o nome da empresa tem de ser um dos que o Asaas oferece (availableCompanyNames)
        try:
            atual = _chamar(rec, "GET", "/myAccount/commercialInfo")
        except ErroNegocio:
            atual = {}
        nomes = atual.get("availableCompanyNames") or []
        nome_empresa = atual.get("companyName") or (nomes[0] if nomes else None)
        if nome_empresa:
            corpo["companyName"] = nome_empresa
    _chamar(rec, "POST", "/myAccount/commercialInfo", corpo)
    rec.dados_atualizados_em = agora()
    auditoria.registrar(s, u.id, "recebimento_dados_atualizados", "arena", arena.id)
    s.commit()
    return estado(rec, arena)


# ---------------------------------------------------------------- cobrança da reserva


def _comissao(valor: Decimal) -> Decimal:
    return _dinheiro(valor * settings.recebimentos_comissao_percentual / 100 + settings.recebimentos_comissao_fixa)


def _split(valor: Decimal) -> list[dict]:
    pct, fixo = settings.recebimentos_comissao_percentual, settings.recebimentos_comissao_fixa
    if pct <= 0 and fixo <= 0:
        return []
    carteira = settings.asaas_wallet_id
    if not carteira:
        raise ErroNegocio("O servidor ainda não tem PLAYGO_ASAAS_WALLET_ID (carteira do PlayGo para a comissão). Fale com a administração.")
    item: dict = {"walletId": carteira}
    if pct > 0:
        item["percentualValue"] = float(pct)
    if fixo > 0:
        item["fixedValue"] = float(fixo)
    return [item]


def criar_cobranca_reserva(s: SessaoORM, reserva_id: int, u: Usuario, valor: Decimal | None, pagador: dict) -> dict:
    """Cria a cobrança (Pix, boleto ou cartão: billingType UNDEFINED) na subconta da arena, com split da comissão, e devolve o link. Só com KYC aprovado."""
    exigir_ligado()
    reserva = s.scalar(select(Reserva).where(Reserva.id == reserva_id).with_for_update(of=Reserva))
    if reserva is None:
        raise NaoEncontrado("Reserva não encontrada.")
    quadra = s.get(Quadra, reserva.quadra_id)
    arena = _arena_do_gestor(s, quadra.arena_id, u)
    rec = _recebedor_obrigatorio(s, arena, lock=True)
    if rec.status_kyc != "aprovado":
        raise ErroNegocio("O cadastro de recebimento da arena ainda não foi aprovado.")
    if reserva.status_pagamento in ("pendente", "pago"):
        raise ErroNegocio("Esta reserva já tem cobrança." if reserva.status_pagamento == "pendente" else "Esta reserva já foi paga.")
    valor = _dinheiro(valor if valor is not None else reserva.valor)
    if valor <= 0:
        raise ErroNegocio("Informe o valor da cobrança.")
    nome, email = (pagador.get("nome") or "").strip(), (pagador.get("email") or "").strip()
    if len(nome) < 3:
        raise ErroNegocio("Informe o nome de quem vai pagar.")
    doc = cobranca._cpf_cnpj(pagador.get("cpf_cnpj", ""))
    _checar_nova_cobranca(s, rec, valor)
    split = _split(valor)
    cliente = _chamar(rec, "POST", "/customers", {"name": nome, "email": email or None, "cpfCnpj": doc, "externalReference": f"reserva:{reserva.id}", "notificationDisabled": False})
    vencimento = min(agora().date() + timedelta(days=3), max(agora().date() + timedelta(days=1), reserva.inicio.date()))
    corpo = {
        "customer": cliente["id"], "billingType": "UNDEFINED", "value": float(valor), "dueDate": vencimento.isoformat(),
        "description": f"Reserva {quadra.nome} — {reserva.inicio:%d/%m %H:%M}", "externalReference": f"reserva:{reserva.id}",
    }
    if split:
        corpo["split"] = split
    pg = _chamar(rec, "POST", "/payments", corpo)
    reserva.valor, reserva.comissao = valor, (_comissao(valor) if split else Decimal(0))
    reserva.status_pagamento, reserva.asaas_payment_id, reserva.invoice_url = "pendente", pg["id"], pg.get("invoiceUrl")
    auditoria.registrar(s, u.id, "reserva_cobrar", "reserva", reserva.id, valor=str(valor))
    s.commit()
    return com_rodape({"link_pagamento": reserva.invoice_url, "valor": valor, "comissao": reserva.comissao, "status_pagamento": reserva.status_pagamento})


def cancelar_cobranca_reserva(s: SessaoORM, reserva_id: int, u: Usuario) -> dict:
    exigir_ligado()
    reserva = s.scalar(select(Reserva).where(Reserva.id == reserva_id).with_for_update(of=Reserva))
    if reserva is None:
        raise NaoEncontrado("Reserva não encontrada.")
    arena = _arena_do_gestor(s, s.get(Quadra, reserva.quadra_id).arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    if reserva.status_pagamento != "pendente" or not reserva.asaas_payment_id:
        raise ErroNegocio("Só dá para cancelar uma cobrança que ainda está pendente.")
    _chamar(rec, "DELETE", f"/payments/{reserva.asaas_payment_id}")
    reserva.status_pagamento = "cancelado"
    auditoria.registrar(s, u.id, "reserva_cobranca_cancelar", "reserva", reserva.id)
    s.commit()
    return com_rodape({"status_pagamento": reserva.status_pagamento})


# ---------------------------------------------------------------- saldo, extrato e livro-razão


def _lancar(s: SessaoORM, rec: Recebedor, tipo: str, bruto: Decimal, liquido: Decimal, referencia: str | None, chave: str) -> bool:
    """Acrescenta ao livro-razão. A `chave` é única por recebedor: o mesmo fato (evento reenviado) não duplica."""
    try:
        with s.begin_nested():
            s.add(LancamentoRecebedor(recebedor_id=rec.id, tipo=tipo, valor_bruto=_dinheiro(bruto), valor_liquido=_dinheiro(liquido), referencia=referencia, chave=chave[:100]))
            s.flush()
        return True
    except IntegrityError:
        return False


def saldo(s: SessaoORM, arena_id: int, u: Usuario) -> dict:
    """Saldo do livro-razão e o saldo do Asaas (GET /v3/finance/balance), sinalizando divergência (cartão ainda a liberar, taxas, ajustes…)."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    interno = saldo_interno(s, rec.id)
    asaas, divergencia = None, False
    if rec.status_kyc == "aprovado":
        try:
            asaas = _dinheiro(_chamar(rec, "GET", "/finance/balance").get("balance"))
            divergencia = abs(asaas - interno) > CENTAVO
        except ErroNegocio:
            asaas = None
    return com_rodape({"saldo": interno, "saldo_asaas": asaas, "divergencia": divergencia, "diferenca": (asaas - interno) if asaas is not None else None})


def extrato(s: SessaoORM, arena_id: int, u: Usuario, inicio: date | None = None, fim: date | None = None, limite: int = 200) -> dict:
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    q = select(LancamentoRecebedor).where(LancamentoRecebedor.recebedor_id == rec.id)
    if inicio:
        q = q.where(LancamentoRecebedor.criado_em >= datetime.combine(inicio, datetime.min.time()))
    if fim:
        q = q.where(LancamentoRecebedor.criado_em < datetime.combine(fim + timedelta(days=1), datetime.min.time()))
    linhas = list(s.scalars(q.order_by(LancamentoRecebedor.id.desc()).limit(min(max(limite, 1), 500))))
    return com_rodape({"lancamentos": [{"id": x.id, "tipo": x.tipo, "valor_bruto": x.valor_bruto, "valor_liquido": x.valor_liquido, "referencia": x.referencia, "criado_em": x.criado_em} for x in linhas], "saldo": saldo_interno(s, rec.id)})


# ---------------------------------------------------------------- saque


def solicitar_saque(s: SessaoORM, arena_id: int, u: Usuario, valor: Decimal, chave_idempotencia: str | None = None) -> dict:
    """Pix da subconta para o destino cadastrado (POST /v3/transfers com a apiKey da subconta). O pedido é gravado e debitado ANTES de chamar o Asaas,
    para a validação de saque encontrá-lo; falha da transferência devolve o valor. Idempotente contra duplo clique."""
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena, lock=True)
    valor = _dinheiro(valor)
    chave = (chave_idempotencia or f"auto:{rec.id}:{valor}:{int(time.time() // 60)}")[:60]
    existente = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.chave_idempotencia == chave))
    if existente is not None:
        return com_rodape(_saque_dict(existente) | {"repetido": True})
    if rec.status_kyc != "aprovado":
        raise ErroNegocio("O cadastro de recebimento da arena ainda não foi aprovado.")
    if not rec.destino_cripto or not rec.destino_confirmado:
        raise ErroNegocio("Cadastre antes a chave Pix de destino do saque.")
    if valor < settings.recebimentos_saque_minimo:
        raise ErroNegocio(f"O saque mínimo é R$ {settings.recebimentos_saque_minimo:.2f}.")
    if valor > saldo_interno(s, rec.id):
        raise ErroNegocio("Saldo insuficiente para esse saque.")
    chave_pix = decifrar(rec.destino_cripto)
    saque = SaqueRecebedor(recebedor_id=rec.id, valor=valor, destino_mascara=rec.destino_mascara, chave_idempotencia=chave, destino_selo=_selo(chave_pix, rec.destino_tipo))
    s.add(saque)
    s.flush()
    _lancar(s, rec, "saque", valor, -valor, f"saque:{saque.id}", f"saque:{saque.id}")
    auditoria.registrar(s, u.id, "recebimento_saque", "arena", arena.id, valor=str(valor), saque=saque.id)
    s.commit()  # 1) o pedido (status "solicitado") e o débito existem ANTES de falar com o Asaas: a validação de saque (~5 s depois) precisa achá-lo
    try:
        tr = _chamar(rec, "POST", "/transfers", {
            "value": float(valor), "pixAddressKey": chave_pix, "pixAddressKeyType": rec.destino_tipo,
            # `description` volta no payload da validação de saque (TRANSFER); `externalReference` não — ver `_validar_transferencia`.
            "description": f"{DESCRICAO_SAQUE}{saque.id}", "externalReference": f"saque:{saque.id}",
        })
    except Exception as e:  # nenhum saque "fantasma": falhou, o valor volta ao saldo e uma validação posterior é recusada
        s.rollback()
        saque = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.id == saque.id).with_for_update().execution_options(populate_existing=True))
        _falhar_saque(s, rec, saque, str(e)[:200] if isinstance(e, ErroNegocio) else "Falha ao falar com o Asaas")
        s.commit()
        raise
    # 2) grava o id da transferência já; a validação pode ter chegado antes e gravado o mesmo id (por isso relê travando a linha)
    saque = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.id == saque.id).with_for_update().execution_options(populate_existing=True))
    if saque.asaas_transfer_id is None and tr.get("id"):
        saque.asaas_transfer_id = str(tr["id"])
    if saque.status == "solicitado":
        saque.status = "enviado"
    if tr.get("status") == "DONE" and saque.status != "falhou":
        saque.status, saque.concluido_em = "concluido", agora()
    s.commit()
    return com_rodape(_saque_dict(saque))


def _saque_dict(sq: SaqueRecebedor) -> dict:
    return {"id": sq.id, "valor": sq.valor, "status": sq.status, "destino": sq.destino_mascara, "motivo": sq.motivo, "criado_em": sq.criado_em, "concluido_em": sq.concluido_em}


def _falhar_saque(s: SessaoORM, rec: Recebedor, saque: SaqueRecebedor, motivo: str) -> None:
    if saque.status == "falhou":
        return
    saque.status, saque.motivo, saque.concluido_em = "falhou", motivo, agora()
    _lancar(s, rec, "saque_falho", saque.valor, saque.valor, f"saque:{saque.id}", f"saque_falho:{saque.id}")


def saques(s: SessaoORM, arena_id: int, u: Usuario) -> dict:
    exigir_ligado()
    arena = _arena_do_gestor(s, arena_id, u)
    rec = _recebedor_obrigatorio(s, arena)
    linhas = s.scalars(select(SaqueRecebedor).where(SaqueRecebedor.recebedor_id == rec.id).order_by(SaqueRecebedor.id.desc()).limit(50))
    return com_rodape({"saques": [_saque_dict(x) for x in linhas]})


# ---------------------------------------------------------------- rotação da apiKey (administração)


def rotacionar_chave(s: SessaoORM, admin: Usuario, arena_id: int) -> dict:
    """Emite uma nova apiKey da subconta (POST /v3/accounts/{id}/accessTokens, com a chave da conta-mãe), confere que funciona e desativa a antiga.
    Exige a liberação temporária em Integrações > Chaves de API > Gerenciamento de chaves de subcontas (2 h) e o IP do servidor na whitelist."""
    exigir_ligado()
    if not admin.admin:
        raise SemPermissao("Só administradores trocam a chave de uma subconta.")
    arena = s.get(Arena, arena_id)
    rec = _recebedor_da_arena(s, arena) if arena is not None else None
    if rec is None:
        raise NaoEncontrado("Esta arena não tem recebimento ativado.")
    nova = cobranca._chamar("POST", f"/accounts/{rec.asaas_account_id}/accessTokens", {"name": f"playgo-{agora():%Y%m%d%H%M}", "expirationDate": (agora() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")})
    if not nova.get("apiKey"):
        raise ErroNegocio("O Asaas não devolveu a nova chave. Tente de novo.")
    cobranca._chamar("GET", "/finance/balance", chave=nova["apiKey"])  # a nova chave precisa funcionar antes de trocar
    antiga_id = rec.api_key_id
    rec.api_key_cripto, rec.api_key_id = cifrar(nova["apiKey"]), nova.get("id")
    auditoria.registrar(s, admin.id, "recebimento_rotacionar_chave", "arena", arena.id)
    s.commit()
    if antiga_id:
        try:
            cobranca._chamar("PUT", f"/accounts/{rec.asaas_account_id}/accessTokens/{antiga_id}", {"enabled": False})
        except ErroNegocio:
            log.warning("Recebimentos: a chave antiga da subconta %s não foi desativada; desative-a no Asaas", rec.asaas_account_id)
    return estado(rec, arena)


# ---------------------------------------------------------------- webhooks


def _token_ok(token: str) -> None:
    esperado = settings.asaas_webhook_token_parceiros
    if not esperado:
        log.error("Webhook de parceiros recusado: PLAYGO_ASAAS_WEBHOOK_TOKEN_PARCEIROS não está configurado no servidor.")
        raise TokenWebhookInvalido("O servidor não tem PLAYGO_ASAAS_WEBHOOK_TOKEN_PARCEIROS configurado.")
    if not secrets.compare_digest(token or "", esperado):
        log.warning("Webhook de parceiros recusado: token %s.", "vazio" if not token else "diferente do configurado")
        raise TokenWebhookInvalido("Token do webhook inválido.")


def _gestores_ids(arena: Arena) -> list[int]:
    return [g.id for g in arena.gestores]


def _avisar_gestores(s: SessaoORM, arena: Arena, titulo: str, corpo: str, chave: str) -> None:
    for uid in _gestores_ids(arena):
        notificacoes.avisar(s, uid, "arena", titulo, corpo + " " + settings.recebimentos_texto_asaas, f"/app/#/arena/{arena.id}/recebimentos", None, f"{chave}:{uid}")


def _reserva_da_cobranca(s: SessaoORM, pagamento: dict) -> Reserva | None:
    """A reserva só é achada pelo id da cobrança que NÓS criamos (não basta a externalReference): evita que um evento forjado mexa em outra Arena."""
    if not pagamento.get("id"):
        return None
    return s.scalar(select(Reserva).where(Reserva.asaas_payment_id == str(pagamento["id"])).with_for_update(of=Reserva))


def _recebedor_da_reserva(s: SessaoORM, reserva: Reserva) -> Recebedor | None:
    return s.scalar(select(Recebedor).join(Quadra, Quadra.arena_id == Recebedor.arena_id).where(Quadra.id == reserva.quadra_id))


def _evento_pagamento(s: SessaoORM, evento: str, corpo: dict, pagamento: dict) -> str:
    reserva = _reserva_da_cobranca(s, pagamento)
    if reserva is None:
        return "ignorado"
    rec = _recebedor_da_reserva(s, reserva)
    conta = (corpo.get("account") or {}).get("id") if isinstance(corpo.get("account"), dict) else None
    if rec is None or (conta and conta != rec.asaas_account_id):
        log.warning("Webhook de parceiros: cobrança %s não pertence à conta %s", pagamento.get("id"), conta)
        return "ignorado"
    pid, arena = str(pagamento["id"]), s.get(Arena, rec.arena_id)
    bruto = _dinheiro(pagamento.get("value") if pagamento.get("value") is not None else reserva.valor)
    if evento in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"):
        if reserva.status_pagamento in ("estornado", "cancelado"):
            return "ignorado"
        liquido = _dinheiro(pagamento.get("netValue")) if pagamento.get("netValue") is not None else bruto
        taxa = max(Decimal(0), bruto - liquido)
        comissao = _comissao(liquido) if (reserva.comissao or 0) > 0 else Decimal(0)
        _lancar(s, rec, "recebimento", bruto, bruto, pid, f"{pid}:recebimento")
        if taxa > 0:  # só desconta da Arena quando a taxa é dela; senão fica registrada, sem efeito no saldo
            _lancar(s, rec, "taxa_asaas", taxa, -taxa if settings.recebimentos_taxa_por == "arena" else Decimal(0), pid, f"{pid}:taxa")
        if comissao > 0:
            _lancar(s, rec, "comissao", comissao, -comissao, pid, f"{pid}:comissao")
        if reserva.status_pagamento != "pago":
            reserva.status_pagamento = "pago"
            _avisar_gestores(s, arena, "✅ Reserva paga", f"A reserva de {reserva.inicio:%d/%m %H:%M} foi paga (R$ {bruto:.2f}).", f"reserva-paga:{reserva.id}")
        return "ok"
    if evento == "PAYMENT_REFUNDED":
        if reserva.status_pagamento == "pago":
            recebida = s.scalar(select(LancamentoRecebedor).where(LancamentoRecebedor.recebedor_id == rec.id, LancamentoRecebedor.chave == f"{pid}:comissao"))
            comissao = recebida.valor_bruto if recebida is not None else Decimal(0)  # o split também é estornado: a Arena recupera a comissão
            _lancar(s, rec, "estorno", bruto, -(bruto - comissao), pid, f"{pid}:estorno")
            _avisar_gestores(s, arena, "Reserva estornada", f"O pagamento da reserva de {reserva.inicio:%d/%m %H:%M} foi estornado.", f"reserva-estorno:{reserva.id}")
        if reserva.status_pagamento != "cancelado":
            reserva.status_pagamento = "estornado"
        return "ok"
    if evento == "PAYMENT_DELETED":
        if reserva.status_pagamento == "pendente":
            reserva.status_pagamento = "cancelado"
        return "ok"
    return "ok"  # PAYMENT_SPLIT_DONE e PAYMENT_OVERDUE: nada a lançar (a comissão já entrou no pagamento; o atraso aparece na reserva)


def _evento_transferencia(s: SessaoORM, evento: str, transferencia: dict) -> str:
    tid = str(transferencia.get("id") or "")
    saque = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.asaas_transfer_id == tid).with_for_update(of=SaqueRecebedor)) if tid else None
    if saque is None:
        return "ignorado"
    rec = s.get(Recebedor, saque.recebedor_id)
    arena = s.get(Arena, rec.arena_id)
    if evento == "TRANSFER_DONE":
        if saque.status != "concluido" and saque.status != "falhou":
            saque.status, saque.concluido_em = "concluido", agora()
            _avisar_gestores(s, arena, "💸 Saque concluído", f"O saque de R$ {saque.valor:.2f} foi concluído.", f"saque-ok:{saque.id}")
    elif saque.status != "falhou":  # TRANSFER_FAILED / TRANSFER_CANCELLED: o valor volta ao saldo
        _falhar_saque(s, rec, saque, str(transferencia.get("failReason") or "Transferência não concluída")[:200])
        _avisar_gestores(s, arena, "Saque não concluído", f"O saque de R$ {saque.valor:.2f} não foi concluído e o valor voltou ao seu saldo.", f"saque-falha:{saque.id}")
    return "ok"


def _evento_conta(s: SessaoORM, evento: str, corpo: dict) -> str:
    conta = corpo.get("account") if isinstance(corpo.get("account"), dict) else {}
    rec = s.scalar(select(Recebedor).where(Recebedor.asaas_account_id == str(conta.get("id") or ""))) if conta.get("id") else None
    if rec is None:
        return "ignorado"
    arena = s.get(Arena, rec.arena_id)
    if evento == "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED":
        rec.status_kyc, rec.documentos_pendentes, rec.motivo_recusa = "aprovado", [], None
        _avisar_gestores(s, arena, "✅ Recebimento aprovado", "Seu cadastro foi aprovado: você já pode cobrar reservas pelo app.", f"kyc-ok:{rec.id}")
    elif evento == "ACCOUNT_STATUS_GENERAL_APPROVAL_REJECTED":
        rec.status_kyc = "recusado"
        _avisar_gestores(s, arena, "Cadastro de recebimento recusado", "Abra a tela de recebimentos para ver o motivo.", f"kyc-rec:{rec.id}")
    elif evento == "ACCOUNT_STATUS_GENERAL_APPROVAL_AWAITING_APPROVAL" and rec.status_kyc != "aprovado":
        rec.status_kyc = "em_analise"
    elif evento in ("ACCOUNT_STATUS_GENERAL_APPROVAL_PENDING", "ACCOUNT_STATUS_DOCUMENT_PENDING", "ACCOUNT_STATUS_DOCUMENT_REJECTED") and rec.status_kyc != "aprovado":
        rec.status_kyc, rec.consultar_apos = "documentos_pendentes", None  # a tela busca a lista de pendências na próxima abertura
        _avisar_gestores(s, arena, "Documentos pendentes", "O Asaas precisa de documentos para liberar seu recebimento.", f"kyc-doc:{rec.id}:{agora():%Y%m%d}")
    elif evento in ("ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON", "ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRED"):
        rec.dados_atualizados_em = agora() - timedelta(days=DIAS_ATUALIZACAO_DADOS - AVISO_ATUALIZACAO_DIAS)  # força o aviso e a tela de atualização
        _avisar_gestores(s, arena, "Atualize seus dados cadastrais", "O Asaas exige a atualização anual dos dados do recebimento.", f"dados:{rec.id}:{agora():%Y%m}")
    return "ok"


def processar_webhook(s: SessaoORM, token: str, corpo: dict) -> str:
    """Webhook das subcontas (`/api/v1/recebimentos/asaas`, token próprio). Idempotente por evento; depois de validar o token responde sempre 'ok'."""
    _token_ok(token)
    if not isinstance(corpo, dict):
        return "ignorado"
    evento = str(corpo.get("event") or "")
    pagamento = corpo.get("payment") if isinstance(corpo.get("payment"), dict) else {}
    transferencia = corpo.get("transfer") if isinstance(corpo.get("transfer"), dict) else {}
    conta = corpo.get("account") if isinstance(corpo.get("account"), dict) else {}
    referencia = pagamento.get("id") or transferencia.get("id") or conta.get("id")
    chave = str(corpo.get("id") or f"{evento}:{referencia}:{pagamento.get('status') or transferencia.get('status') or ''}")[:120]
    if not evento:
        return "ignorado"
    try:
        try:
            with s.begin_nested():
                s.add(EventoRecebimento(chave=chave))
                s.flush()
        except IntegrityError:
            return "duplicado"
        if evento.startswith("PAYMENT_"):
            resultado = _evento_pagamento(s, evento, corpo, pagamento)
        elif evento.startswith("TRANSFER_"):
            resultado = _evento_transferencia(s, evento, transferencia)
        elif evento.startswith("ACCOUNT_STATUS_"):
            resultado = _evento_conta(s, evento, corpo)
        else:
            resultado = "ignorado"
        s.commit()
        return resultado
    except Exception:  # erro nosso não pode gerar reenvio em loop; fica no log para investigar
        s.rollback()
        log.exception("Webhook de parceiros: falha ao processar %s", evento)
        return "erro"


# ---------------------------------------------------------------- validação de saque (o Asaas chama o nosso servidor)

DESCRICAO_SAQUE = "PlayGo saque:"  # `description` da transferência = nosso id interno do saque (volta no payload da validação)
JANELA_CASAMENTO = timedelta(minutes=2)  # saque sem id gravado só é casado por valor/destino se foi criado há menos que isso
_RE_SAQUE = re.compile(r"saque:(\d+)")


def _canonica(tipo: str, chave: str) -> str:
    chave = (chave or "").strip()
    if tipo in ("EMAIL", "EVP"):
        return chave.lower()
    digitos = re.sub(r"\D", "", chave)
    if tipo == "PHONE" and len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    return digitos


def _selo_tipo(tipo: str, chave: str) -> str:
    material = (settings.recebimentos_chave.strip() or seguranca.chave_sessao()).encode()
    return hmac.new(material, b"destino-saque|" + _canonica(tipo, chave).encode(), hashlib.sha256).hexdigest()


def _selo(chave: str, tipo: str) -> str:
    """Selo (HMAC) da chave Pix: guardado no pedido para a validação conferir o destino sem decifrar nada nem depender do cadastro atual."""
    return _selo_tipo(tipo, chave)


def _selos_possiveis(chave: str) -> set[str]:
    """O payload traz a chave sem o tipo: aceita se bater como qualquer tipo."""
    return {_selo_tipo(t, chave) for t in TIPOS_CHAVE_PIX}


def _recusa(motivo: str) -> dict:
    return {"status": "REFUSED", "refuseReason": motivo[:200]}


def _registrar_validacao(s: SessaoORM, resposta: dict, motivo: str | None, saque_id: int | None) -> None:
    """Cada validação (aprovada ou recusada) fica em EventoRecebimento: só resultado, motivo e id do saque — nunca chave Pix, valor de conta ou apiKey."""
    razao = motivo or resposta.get("refuseReason")
    s.add(EventoRecebimento(
        chave=f"validacao:{secrets.token_hex(10)}", tipo="validacao_saque", saque_id=saque_id,
        resultado="aprovado" if resposta["status"] == "APPROVED" else "recusado", motivo=razao[:200] if razao else None,
    ))


def validar_saque(s: SessaoORM, token: str, corpo: dict) -> dict:
    """Mecanismo de validação de saque (https://docs.asaas.com/docs/mecanismo-para-validacao-de-saque-via-webhooks): o Asaas faz POST aqui ~5 s depois de
    cada transferência e na liquidação de cada split. Confere com o que NÓS registramos e responde APPROVED ou REFUSED. Tudo que não registramos é recusado,
    e qualquer erro interno também (nunca sem resposta válida). Só o token inválido sai como exceção (vira 401 na API)."""
    _token_ok(token)
    saque_id = None
    try:
        if exigir_ligado_silencioso() is False:
            resposta = _recusa("Recebimentos desligados.")
        else:
            corpo = corpo if isinstance(corpo, dict) else {}
            tipo = str(corpo.get("type") or "")
            if tipo == "TRANSFER":
                resposta, saque_id = _validar_transferencia(s, corpo.get("transfer") if isinstance(corpo.get("transfer"), dict) else {})
            elif tipo == "PAYMENT_SPLIT":
                resposta = _validar_split(s, corpo.get("paymentSplit") if isinstance(corpo.get("paymentSplit"), dict) else {})
            else:
                resposta = _recusa(f"Operação {tipo or 'desconhecida'} não é feita pelo PlayGo."[:120])
        _registrar_validacao(s, resposta, None, saque_id)
        s.commit()
    except Exception as e:  # noqa: BLE001 — erro interno: recusa (fail-safe) e deixa só o tipo do erro no log, sem payload
        s.rollback()
        log.error("Validação de saque: erro interno (%s); operação recusada.", type(e).__name__)
        resposta = _recusa("Erro interno ao validar a operação.")
        try:
            _registrar_validacao(s, resposta, f"erro {type(e).__name__}", saque_id)
            s.commit()
        except Exception:  # noqa: BLE001 — a resposta ao Asaas vale mais que o registro
            s.rollback()
    if resposta["status"] == "REFUSED":
        log.warning("Validação de saque recusada: %s", resposta.get("refuseReason"))
    return resposta


def exigir_ligado_silencioso() -> bool:
    return fase() != "desligado"


def _divergencia(saque: SaqueRecebedor, rec: Recebedor, tr: dict) -> str | None:
    """Confere payload × registro: valor, tipo da operação, destino (selo da chave Pix) e titularidade (4 últimos dígitos do documento da conta)."""
    if _dinheiro(tr.get("value")) != saque.valor:
        return "Valor diferente do saque registrado."
    if str(tr.get("operationType") or "PIX") != "PIX":
        return "Só Pix é permitido."
    conta = tr.get("bankAccount") if isinstance(tr.get("bankAccount"), dict) else {}
    chave = conta.get("pixAddressKey")
    if not chave:
        return "Destino do saque não informado."
    if not saque.destino_selo or saque.destino_selo not in _selos_possiveis(str(chave)):
        return "Destino diferente do registrado."
    doc = re.sub(r"\D", "", str(conta.get("cpfCnpj") or ""))
    if len(doc) >= 11 and rec.documento_final and doc[-4:] != rec.documento_final:
        return "Titular diferente do da conta."
    return None


def _validar_transferencia(s: SessaoORM, tr: dict) -> tuple[dict, int | None]:
    """Casamento com o pedido, nesta ordem, com a linha travada (FOR UPDATE): (a) id da transferência; (b) nosso id interno na `description`;
    (c) sem id gravado: valor + destino + criado há < 2 min, só se houver EXATAMENTE um candidato. O payload do TRANSFER não traz a subconta de origem
    nem `externalReference` (docs: validação de saque), então a subconta não entra no casamento — o que desambigua é o destino. Nunca escolhe entre candidatos."""
    tid = str(tr.get("id") or "")
    if not tid:
        return _recusa("Transferência sem identificador."), None
    saque = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.asaas_transfer_id == tid).with_for_update())
    if saque is None:
        achado = _RE_SAQUE.search(str(tr.get("description") or ""))
        if achado:
            saque = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.id == int(achado.group(1))).with_for_update())
            if saque is not None and saque.asaas_transfer_id not in (None, tid):
                return _recusa("Saque já associado a outra transferência."), saque.id
    if saque is None:
        conta = tr.get("bankAccount") if isinstance(tr.get("bankAccount"), dict) else {}
        chave = conta.get("pixAddressKey")
        if not chave:
            return _recusa("Saque não identificado."), None
        candidatos = list(s.scalars(select(SaqueRecebedor).where(
            SaqueRecebedor.asaas_transfer_id.is_(None), SaqueRecebedor.status.in_(("solicitado", "enviado")), SaqueRecebedor.validacao.is_(None),
            SaqueRecebedor.valor == _dinheiro(tr.get("value")), SaqueRecebedor.destino_selo.in_(_selos_possiveis(str(chave))),
            SaqueRecebedor.criado_em >= agora() - JANELA_CASAMENTO,
        ).order_by(SaqueRecebedor.id).with_for_update()))
        if not candidatos:
            return _recusa("Saque não identificado."), None
        if len(candidatos) > 1:
            return _recusa("Saque ambíguo."), None
        saque = candidatos[0]
    rec = s.get(Recebedor, saque.recebedor_id)
    if saque.status == "falhou":
        return _recusa("Saque cancelado ou com falha."), saque.id
    if saque.validacao == "recusado":
        return _recusa("Saque já recusado."), saque.id
    motivo = _divergencia(saque, rec, tr) if rec is not None and rec.ativo else "Recebedor inativo."
    if motivo:
        if saque.validacao is None:
            saque.validacao = "recusado"
        return _recusa(motivo), saque.id
    if saque.status not in ("solicitado", "enviado") and saque.validacao != "aprovado":
        return _recusa("Saque já finalizado."), saque.id
    if saque.asaas_transfer_id is None:
        saque.asaas_transfer_id = tid
    saque.validacao = "aprovado"  # repetir a validação devolve a mesma resposta, sem aprovar outro saque
    return {"status": "APPROVED"}, saque.id


def _validar_split(s: SessaoORM, sp: dict) -> dict:
    """Só libera o split de cobrança que o PlayGo criou (reserva existente com `reserva:{id}`, comissão esperada) e só para a carteira do PlayGo."""
    pagamento = sp.get("payment")
    pid = str(pagamento.get("id") if isinstance(pagamento, dict) else pagamento or "")
    reserva = s.scalar(select(Reserva).where(Reserva.asaas_payment_id == pid)) if pid else None
    if reserva is None:
        return _recusa("Split de cobrança que o PlayGo não criou.")
    if not settings.asaas_wallet_id or str(sp.get("walletId") or "") != settings.asaas_wallet_id:
        return _recusa("Carteira de destino do split diferente da do PlayGo.")
    referencias = {str(r) for r in (sp.get("externalReference"), pagamento.get("externalReference") if isinstance(pagamento, dict) else None) if r}
    if referencias and referencias != {f"reserva:{reserva.id}"}:
        return _recusa("Referência da cobrança diferente da reserva.")
    if (reserva.comissao or 0) <= 0 or reserva.status_pagamento in ("cancelado", "estornado"):
        return _recusa("Cobrança sem comissão esperada para o PlayGo.")
    pct, fixo = sp.get("percentualValue"), sp.get("fixedValue")
    if pct and _dinheiro(pct) != _dinheiro(settings.recebimentos_comissao_percentual):
        return _recusa("Percentual do split diferente do combinado.")
    if fixo and _dinheiro(fixo) != _dinheiro(settings.recebimentos_comissao_fixa):
        return _recusa("Valor fixo do split diferente do combinado.")
    return {"status": "APPROVED"}


# ---------------------------------------------------------------- rotina periódica


def avisar_atualizacao_dados(s: SessaoORM) -> int:
    """Lembrete da atualização anual dos dados cadastrais (rodar no ciclo do agendador)."""
    n = 0
    for rec in s.scalars(select(Recebedor).where(Recebedor.ativo)):
        if precisa_atualizar(rec):
            _avisar_gestores(s, s.get(Arena, rec.arena_id), "Atualize seus dados cadastrais", "Abra Receber pelo app > Atualizar dados: o Asaas exige a confirmação anual.", f"dados:{rec.id}:{agora():%Y%m}")
            n += 1
    s.commit()
    return n
