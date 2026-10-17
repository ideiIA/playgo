"""Cobrança das mensalidades pelo Asaas (Pix, boleto e cartão, com assinatura recorrente).

Fluxo: a pessoa escolhe o plano e informa CPF/CNPJ → criamos o cliente e a assinatura mensal no Asaas → ela paga pelo link
da cobrança (a própria página do Asaas) → o Asaas avisa por webhook → estendemos o acesso.
O CPF/CNPJ vai direto ao Asaas e **não é guardado** por nós (só o id do cliente). A chave do Asaas é do servidor.

Sandbox: https://sandbox.asaas.com (chave começa com $aact_hmlg_). Produção: https://www.asaas.com."""

import logging
import re
import secrets
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, notificacoes, planos
from .config import settings
from .db import agora
from .erros import SemPermissao, ErroNegocio, NaoEncontrado
from .models import Assinatura, CreditoEvento, Pagamento, Usuario

log = logging.getLogger(__name__)


class TokenWebhookInvalido(SemPermissao):
    """O webhook chegou com o token ausente ou diferente do configurado (a API responde 401, não 404, para o erro ser óbvio no Asaas)."""
PAGO = ("CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH")


def _http() -> httpx.Client:
    """Cliente HTTP do Asaas. Os testes substituem esta função por um transporte simulado."""
    return httpx.Client(timeout=30)


def configurado() -> bool:
    return bool(settings.asaas_api_key)


def _base() -> str:
    return "https://api-sandbox.asaas.com/v3" if settings.asaas_ambiente != "producao" else "https://api.asaas.com/v3"


def _chamar(metodo: str, caminho: str, json: dict | None = None) -> dict:
    if not configurado():
        raise ErroNegocio("A cobrança ainda não está ativa neste ambiente. Fale com a administração.")
    try:
        with _http() as c:
            r = c.request(metodo, _base() + caminho, json=json, headers={"access_token": settings.asaas_api_key, "User-Agent": "PlayGo"})
    except httpx.HTTPError as e:
        log.error("Asaas indisponível: %s", e)
        raise ErroNegocio("Não foi possível falar com o sistema de pagamento agora. Tente de novo em instantes.") from None
    if r.status_code >= 400:
        try:
            erros = r.json().get("errors") or []
            detalhe = "; ".join(e.get("description", "") for e in erros if e.get("description"))
        except ValueError:
            detalhe = ""
        log.warning("Asaas %s %s -> %s %s", metodo, caminho, r.status_code, detalhe)
        raise ErroNegocio(detalhe or "O sistema de pagamento recusou o pedido. Confira os dados e tente de novo.")
    return r.json() if r.content else {}


def _cpf_cnpj(texto: str) -> str:
    d = re.sub(r"\D", "", texto or "")
    if len(d) not in (11, 14):
        raise ErroNegocio("Informe um CPF (11 dígitos) ou CNPJ (14 dígitos) válido.")
    return d


def assinar(s: SessaoORM, u: Usuario, plano: str, cpf_cnpj: str, ciclo: str = "mensal") -> dict:
    """Cria (ou troca) a assinatura mensal ou anual e devolve o link para pagar."""
    if plano not in planos.CODIGOS:
        raise ErroNegocio("Escolha um plano válido.")
    if ciclo not in ("mensal", "anual"):
        raise ErroNegocio("Escolha o pagamento mensal ou anual.")
    regra = planos.regras(s, plano)
    if regra.valor_mensal <= 0:
        raise ErroNegocio(f"O valor do plano {regra.nome} ainda não foi definido pela administração.")
    if ciclo == "anual" and not regra.valor_anual:
        raise ErroNegocio(f"O plano {regra.nome} ainda não tem valor anual definido.")
    valor = regra.valor_anual if ciclo == "anual" else regra.valor_mensal
    doc = _cpf_cnpj(cpf_cnpj)
    a = planos.assinatura_de(s, u)
    if a is None:
        a = Assinatura(usuario_id=u.id, plano=plano, status="teste", origem="asaas", testes_usados=[])
        s.add(a)
        s.flush()
    hoje = agora().date()
    sit = planos.situacao(s, u)
    if a.asaas_subscription_id and a.status != "cancelada":
        _chamar("DELETE", f"/subscriptions/{a.asaas_subscription_id}")  # troca de plano: encerra a anterior
        a.asaas_subscription_id = None
    if not a.asaas_customer_id:
        cliente = _chamar("POST", "/customers", {"name": u.nome, "email": u.email, "cpfCnpj": doc, "externalReference": f"usuario:{u.id}", "notificationDisabled": False})
        a.asaas_customer_id = cliente["id"]
    primeira = max(hoje + timedelta(days=1), sit["fim"] + timedelta(days=1) if sit["fim"] and sit["status"] in ("teste", "ativa") and sit["plano"] == plano else hoje + timedelta(days=1))
    sub = _chamar("POST", "/subscriptions", {
        "customer": a.asaas_customer_id, "billingType": "UNDEFINED", "value": float(valor), "nextDueDate": primeira.isoformat(),
        "cycle": "YEARLY" if ciclo == "anual" else "MONTHLY", "description": f"PlayGo — plano {regra.nome} ({ciclo})", "externalReference": f"assinatura:{a.id}",
    })
    a.asaas_subscription_id, a.plano, a.origem, a.ciclo = sub["id"], plano, "asaas", ciclo
    if a.status == "cancelada":
        a.status = "ativa" if a.vigente_ate and a.vigente_ate >= hoje else "teste"
    link = None
    try:
        cobrancas = _chamar("GET", f"/subscriptions/{sub['id']}/payments").get("data") or []
        if cobrancas:
            c0 = cobrancas[0]
            link = c0.get("invoiceUrl")
            _registrar_pagamento(s, a, c0)
    except ErroNegocio:
        pass  # o link também chega por e-mail do Asaas
    auditoria.registrar(s, u.id, "assinatura_criar", "assinatura", a.id, plano=plano, ciclo=ciclo, valor=str(valor))
    s.commit()
    return {"link_pagamento": link, "primeira_cobranca": primeira.isoformat(), "valor": float(valor), "plano": plano, "ciclo": ciclo}


def comprar_eventos(s: SessaoORM, u: Usuario, quantidade: int, cpf_cnpj: str) -> dict:
    """Venda avulsa de eventos (campeonatos): cobrança única no Asaas; os créditos só valem depois da confirmação do pagamento."""
    regra = planos.regras(s, "organizador")
    if not regra.valor_evento or regra.valor_evento <= 0:
        raise ErroNegocio("A venda de eventos avulsos ainda não foi ativada pela administração.")
    if not 1 <= quantidade <= 50:
        raise ErroNegocio("Escolha de 1 a 50 eventos.")
    doc = _cpf_cnpj(cpf_cnpj)
    a = planos.assinatura_de(s, u)
    cliente = a.asaas_customer_id if a is not None else None
    if not cliente:
        cliente = s.scalar(select(CreditoEvento.asaas_customer_id).where(CreditoEvento.usuario_id == u.id, CreditoEvento.asaas_customer_id.is_not(None)).limit(1))
    if not cliente:
        cliente = _chamar("POST", "/customers", {"name": u.nome, "email": u.email, "cpfCnpj": doc, "externalReference": f"usuario:{u.id}", "notificationDisabled": False})["id"]
    total = regra.valor_evento * quantidade
    c = CreditoEvento(usuario_id=u.id, quantidade=quantidade, valor=total, asaas_customer_id=cliente)
    s.add(c)
    s.flush()
    plural = "s" if quantidade > 1 else ""
    pg = _chamar("POST", "/payments", {
        "customer": cliente, "billingType": "UNDEFINED", "value": float(total), "dueDate": (agora().date() + timedelta(days=3)).isoformat(),
        "description": f"PlayGo — {quantidade} evento{plural} (campeonato{plural})", "externalReference": f"evento:{c.id}",
    })
    c.asaas_payment_id, c.invoice_url = pg["id"], pg.get("invoiceUrl")
    auditoria.registrar(s, u.id, "evento_comprar", "credito_evento", c.id, quantidade=quantidade, valor=str(total))
    s.commit()
    return {"link_pagamento": c.invoice_url, "valor": float(total), "quantidade": quantidade, "credito_id": c.id}


def _webhook_evento(s: SessaoORM, evento: str, pagamento: dict, referencia: str) -> str:
    """Pagamento avulso de eventos: confirma, libera os créditos por 12 meses e avisa."""
    try:
        c = s.get(CreditoEvento, int(referencia.split(":", 1)[1]))
    except ValueError:
        c = None
    if c is None:
        return "ignorado"
    hoje = agora().date()
    c.invoice_url = pagamento.get("invoiceUrl") or c.invoice_url
    if evento in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED") and c.status == "pendente":
        c.status, c.pago_em, c.valida_ate = "pago", hoje, hoje + timedelta(days=planos.CREDITO_VALIDADE_DIAS)
        plural = "s" if c.quantidade > 1 else ""
        notificacoes.avisar(s, c.usuario_id, "plano", "✅ Pagamento confirmado", f"{c.quantidade} evento{plural} liberado{plural} para criar campeonatos até {c.valida_ate.strftime('%d/%m/%Y')}.", "/planos", None, f"evento:pgto:{c.id}")
        auditoria.registrar(s, c.usuario_id, "evento_pagamento_confirmado", "credito_evento", c.id, quantidade=c.quantidade, valida_ate=c.valida_ate)
    elif evento in ("PAYMENT_DELETED", "PAYMENT_REFUNDED") and c.status != "cancelado":
        if c.status == "pago":
            c.usados = c.quantidade  # estornado: o saldo some
        c.status = "cancelado"
        auditoria.registrar(s, c.usuario_id, "evento_pagamento_" + evento[8:].lower(), "credito_evento", c.id)
    s.commit()
    return "ok"


def cancelar(s: SessaoORM, u: Usuario) -> dict:
    """Para a cobrança recorrente. O acesso continua até o fim do período já pago."""
    a = planos.assinatura_de(s, u)
    if a is None or not a.asaas_subscription_id or a.status == "cancelada":
        raise ErroNegocio("Você não tem uma assinatura ativa para cancelar.")
    _chamar("DELETE", f"/subscriptions/{a.asaas_subscription_id}")
    a.status = "cancelada" if a.vigente_ate else a.status
    a.asaas_subscription_id, a.cancelada_em = None, agora()
    auditoria.registrar(s, u.id, "assinatura_cancelar", "assinatura", a.id)
    s.commit()
    return planos.resumo(s, u)


# ---------------------------------------------------------------- webhook


def _data(texto: str | None) -> date | None:
    try:
        return date.fromisoformat(texto[:10]) if texto else None
    except ValueError:
        return None


def _registrar_pagamento(s: SessaoORM, a: Assinatura, p: dict) -> tuple[Pagamento, bool]:
    """Grava/atualiza a cobrança. Devolve (pagamento, acabou_de_ser_paga)."""
    pg = s.scalar(select(Pagamento).where(Pagamento.asaas_payment_id == p["id"]))
    ja_paga = pg is not None and pg.status in PAGO  # cobrança nova nunca conta como "já paga"
    if pg is None:
        pg = Pagamento(assinatura_id=a.id, asaas_payment_id=p["id"], valor=Decimal(str(p.get("value", 0))), status=p.get("status", ""), vencimento=_data(p.get("dueDate")) or agora().date())
        s.add(pg)
    pg.status = p.get("status", pg.status)
    pg.invoice_url = p.get("invoiceUrl") or pg.invoice_url
    pg.pago_em = _data(p.get("paymentDate") or p.get("clientPaymentDate")) or pg.pago_em
    s.flush()
    return pg, (pg.status in PAGO and not ja_paga)


def processar_webhook(s: SessaoORM, token: str, corpo: dict) -> str:
    """Eventos do Asaas. Sempre responde 'ok' depois de validar o token (erros nossos não devem gerar reenvios em loop)."""
    esperado = settings.asaas_webhook_token
    if not esperado:
        log.error("Webhook do Asaas recusado: PLAYGO_ASAAS_WEBHOOK_TOKEN não está configurado no servidor.")
        raise TokenWebhookInvalido("O servidor não tem PLAYGO_ASAAS_WEBHOOK_TOKEN configurado.")
    if not secrets.compare_digest(token or "", esperado):
        log.warning("Webhook do Asaas recusado: o token enviado %s (confira o token do webhook no Asaas e PLAYGO_ASAAS_WEBHOOK_TOKEN).", "está vazio" if not token else "é diferente do configurado")
        raise TokenWebhookInvalido("Token do webhook inválido.")
    if not isinstance(corpo, dict):
        return "ignorado"
    evento = str(corpo.get("event") or "")
    # O Asaas manda o evento de TODAS as cobranças da conta (inclusive as que não são do PlayGo) e usa null em vários campos
    pagamento = corpo.get("payment") if isinstance(corpo.get("payment"), dict) else {}
    sub = corpo.get("subscription") if isinstance(corpo.get("subscription"), dict) else {}
    if str(pagamento.get("externalReference") or "").startswith("evento:"):
        return _webhook_evento(s, evento, pagamento, str(pagamento["externalReference"]))
    a = None
    if pagamento.get("subscription"):
        a = s.scalar(select(Assinatura).where(Assinatura.asaas_subscription_id == str(pagamento["subscription"])))
    if a is None and sub.get("id"):
        a = s.scalar(select(Assinatura).where(Assinatura.asaas_subscription_id == str(sub["id"])))
    referencia = str(pagamento.get("externalReference") or "")
    if a is None and referencia.startswith("assinatura:"):
        try:
            a = s.get(Assinatura, int(referencia.split(":", 1)[1]))
        except ValueError:
            a = None
    if a is None:
        return "ignorado"
    hoje = agora().date()
    if evento in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED") and pagamento.get("id"):
        pg, nova = _registrar_pagamento(s, a, pagamento)
        if nova:
            base = max(d for d in (a.vigente_ate, a.teste_ate, hoje, pg.vencimento) if d)
            a.vigente_ate = base + timedelta(days=366 if a.ciclo == "anual" else 31)
            a.status, a.origem = "ativa", "asaas"
            notificacoes.avisar(s, a.usuario_id, "plano", "✅ Pagamento confirmado", f"Plano {planos.DEFAULTS[a.plano].nome} ativo até {a.vigente_ate.strftime('%d/%m/%Y')}.", "/planos", None, f"pgto:{pg.asaas_payment_id}")
            auditoria.registrar(s, a.usuario_id, "pagamento_confirmado", "assinatura", a.id, valor=str(pg.valor), vigente_ate=a.vigente_ate)
    elif evento == "PAYMENT_CREATED" and pagamento.get("id"):
        _registrar_pagamento(s, a, pagamento)
    elif evento in ("PAYMENT_OVERDUE", "PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_DELETED") and pagamento.get("id"):
        _registrar_pagamento(s, a, pagamento)
        if evento == "PAYMENT_OVERDUE":
            notificacoes.avisar(s, a.usuario_id, "plano", "Pagamento do plano em atraso", "Regularize para não perder o acesso a criar e divulgar.", "/planos", None, f"atraso:{pagamento['id']}")
        auditoria.registrar(s, a.usuario_id, "pagamento_" + evento[8:].lower(), "assinatura", a.id, pagamento=pagamento.get("id"))
    elif evento in ("SUBSCRIPTION_DELETED", "SUBSCRIPTION_INACTIVATED"):
        a.asaas_subscription_id, a.cancelada_em = None, agora()
        a.status = "cancelada" if a.vigente_ate else a.status
        auditoria.registrar(s, a.usuario_id, "assinatura_encerrada", "assinatura", a.id, evento=evento)
    s.commit()
    return "ok"
