"""Taxas da subconta de cada arena: o que o Asaas cobra (Pix, cartão, prazo, antecipação, saque), uma cópia guardada para abrir rápido,
a simulação "reserva de R$ X: quanto cai na minha conta" e o que já foi realmente cobrado nos pagamentos.

A consulta é `GET /v3/myAccount/fees` com a apiKey da própria subconta (a Arena nunca vê o painel do Asaas). A cópia fica em `Recebedor.taxas`
e é renovada por `atualizar_todas` (agendador, 1 vez por dia) ou quando a Arena pede. Só leitura: nada aqui cobra, estorna ou altera taxa."""

import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import recebimentos
from .config import settings
from .db import agora
from .erros import ErroNegocio
from .models import LancamentoRecebedor, Recebedor, Reserva, Usuario

log = logging.getLogger(__name__)

VALIDADE_H = 24  # idade máxima da cópia antes de renovar sozinha ao abrir a tela
DIAS_ANTECIPAR = (7, 14, 30)
CENT = Decimal("0.01")


def _n(v) -> float | None:
    """Número do Asaas (ou nada)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalizar(resp: dict) -> dict:
    """Guarda só o que a tela usa, com nomes em português. Campos que o Asaas não mandar ficam vazios (a tela não os mostra)."""
    pag = resp.get("payment") or {}
    pix, cc, cd = pag.get("pix") or {}, pag.get("creditCard") or {}, pag.get("debitCard") or {}
    tr = resp.get("transfer") or {}
    ant = (resp.get("anticipation") or {}).get("creditCard") or {}
    return {
        "pix": {"fixa": _n(pix.get("fixedFeeValue")), "fixa_promocional": _n(pix.get("fixedFeeValueWithDiscount")), "promocao_ate": pix.get("discountExpiration"),
                "percentual": _n(pix.get("percentageFee")), "minima": _n(pix.get("minimumFeeValue")), "maxima": _n(pix.get("maximumFeeValue")),
                "gratis_por_mes": _n(pix.get("monthlyCreditsWithoutFee")), "gratis_usados_no_mes": _n(pix.get("creditsReceivedOfCurrentMonth"))},
        "cartao": {"fixa": _n(cc.get("operationValue")), "pct_1x": _n(cc.get("oneInstallmentPercentage")), "pct_ate_6x": _n(cc.get("upToSixInstallmentsPercentage")),
                   "pct_ate_12x": _n(cc.get("upToTwelveInstallmentsPercentage")), "pct_ate_21x": _n(cc.get("upToTwentyOneInstallmentsPercentage")),
                   "pct_1x_promocional": _n(cc.get("discountOneInstallmentPercentage")), "promocao_ate": cc.get("discountExpiration"), "dias_para_receber": _n(cc.get("daysToReceive"))},
        "debito": {"fixa": _n(cd.get("operationValue")), "percentual": _n(cd.get("defaultPercentage")), "dias_para_receber": _n(cd.get("daysToReceive"))},
        "saque": {"pix": _n((tr.get("pix") or {}).get("feeValue")), "ted": _n((tr.get("ted") or {}).get("feeValue")), "gratis_por_mes": _n(tr.get("monthlyTransfersWithoutFee"))},
        "antecipacao": {"cartao_avista_pct_mes": _n(ant.get("detachedMonthlyFeeValue")), "cartao_parcelado_pct_mes": _n(ant.get("installmentMonthlyFeeValue"))},
    }


def _vigente(valor, promo, ate) -> float | None:
    """Usa o valor promocional enquanto a promoção não venceu."""
    if promo is not None and (not ate or str(ate)[:10] >= agora().date().isoformat()):
        return promo
    return valor


def atualizar(s: SessaoORM, rec: Recebedor) -> dict:
    """Consulta o Asaas com a chave da subconta e guarda a cópia. Quem chama dá o commit."""
    resp = recebimentos._chamar(rec, "GET", "/myAccount/fees")
    rec.taxas, rec.taxas_em = normalizar(resp), agora()
    return rec.taxas


def atualizar_todas(s: SessaoORM, limite: int = 20) -> int:
    """Agendador: renova a cópia das subcontas aprovadas cuja consulta passou de 24 h (poucas por ciclo; falha de uma não trava as outras)."""
    corte = agora() - timedelta(hours=VALIDADE_H)
    q = select(Recebedor).where(Recebedor.ativo, Recebedor.status_kyc == "aprovado", (Recebedor.taxas_em.is_(None)) | (Recebedor.taxas_em < corte)).limit(limite)
    n = 0
    for rec in s.scalars(q):
        try:
            atualizar(s, rec)
            s.commit()
            n += 1
        except ErroNegocio as e:
            s.rollback()
            log.warning("Taxas: não consegui consultar a subconta %s: %s", rec.id, e)
    return n


# ---------------------------------------------------------------- simulação


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(v) -> Decimal:
    return Decimal(str(v or 0))


def simular(taxas: dict, valor: Decimal, acrescimo_cartao_pct, taxa_por: str | None = None) -> list[dict]:
    """Quanto a arena recebe numa reserva de `valor`, no Pix e no cartão à vista. Estimativa: o Asaas calcula o valor exato na cobrança.
    A comissão do PlayGo incide sobre o valor líquido da taxa, como o livro-razão registra."""
    taxa_por = taxa_por or settings.recebimentos_taxa_por
    linhas = []

    def linha(forma, paga, taxa, prazo):
        liquido = paga - taxa
        comissao = recebimentos._comissao(liquido) if (settings.recebimentos_comissao_percentual > 0 or settings.recebimentos_comissao_fixa > 0) else Decimal(0)
        desconto_taxa = taxa if taxa_por == "arena" else Decimal(0)
        recebe = paga - desconto_taxa - comissao
        return {"forma": forma, "cliente_paga": float(_q(paga)), "taxa_asaas": float(_q(taxa)), "taxa_paga_por": taxa_por, "comissao_playgo": float(_q(comissao)),
                "voce_recebe": float(_q(recebe)), "prazo": prazo}

    pix = taxas.get("pix") or {}
    if any(pix.get(k) is not None for k in ("fixa", "percentual", "fixa_promocional")):
        fixa = _vigente(pix.get("fixa"), pix.get("fixa_promocional"), pix.get("promocao_ate"))
        taxa = _dec(fixa)
        if pix.get("percentual"):
            taxa = valor * _dec(pix["percentual"]) / 100
            if pix.get("minima") is not None:
                taxa = max(taxa, _dec(pix["minima"]))
            if pix.get("maxima") is not None:
                taxa = min(taxa, _dec(pix["maxima"]))
        linhas.append(linha("Pix", valor, taxa, "na hora"))

    cc = taxas.get("cartao") or {}
    pct = _vigente(cc.get("pct_1x"), cc.get("pct_1x_promocional"), cc.get("promocao_ate"))
    if pct is not None or cc.get("fixa") is not None:
        paga = valor * (1 + _dec(acrescimo_cartao_pct) / 100)
        taxa = paga * _dec(pct) / 100 + _dec(cc.get("fixa"))
        dias = cc.get("dias_para_receber")
        linhas.append(linha("Cartão de crédito (à vista)", paga, taxa, f"{int(dias)} dias" if dias else None))
        ant = (taxas.get("antecipacao") or {}).get("cartao_avista_pct_mes")
        if ant is not None and dias:
            for d in DIAS_ANTECIPAR:
                if d >= dias:
                    continue
                custo = (paga - taxa) * _dec(ant) / 100 * Decimal(d) / 30
                extra = linha(f"Cartão com antecipação ({int(dias) - d} dias)", paga, taxa + custo, f"{int(dias) - d} dias")
                extra["antecipacao"] = float(_q(custo))
                linhas.append(extra)
    return linhas


def ver(s: SessaoORM, arena_id: int, u: Usuario, valor: Decimal | None = None, forcar: bool = False) -> dict:
    """Taxas da arena + simulação. Usa a cópia (renova se tiver mais de 24 h ou se `forcar`); se o Asaas falhar e houver cópia, devolve a antiga avisando."""
    recebimentos.exigir_ligado()
    arena = recebimentos._arena_do_gestor(s, arena_id, u)
    rec = recebimentos._recebedor_obrigatorio(s, arena)
    if rec.status_kyc != "aprovado":
        raise ErroNegocio("As taxas aparecem depois que o cadastro de recebimento for aprovado.")
    aviso = None
    velha = rec.taxas_em is None or rec.taxas_em < agora() - timedelta(hours=VALIDADE_H)
    if forcar or velha or not rec.taxas:
        try:
            atualizar(s, rec)
            s.commit()
        except ErroNegocio as e:
            s.rollback()
            if not rec.taxas:
                raise
            aviso = f"Não consegui atualizar agora ({e}). Estas são as últimas taxas consultadas."
    base = _q(valor) if valor and valor > 0 else Decimal(100)
    taxas = rec.taxas or {}
    return recebimentos.com_rodape({
        "taxas": taxas, "atualizado_em": rec.taxas_em, "aviso": aviso, "valor_simulado": float(base),
        "comissao_playgo": {"percentual": float(settings.recebimentos_comissao_percentual), "fixa": float(settings.recebimentos_comissao_fixa)},
        "taxa_paga_por": settings.recebimentos_taxa_por, "acrescimo_cartao_pct": float(arena.acrescimo_cartao_pct or 0),
        "simulacao": simular(taxas, base, arena.acrescimo_cartao_pct, settings.recebimentos_taxa_por),
    })


# ---------------------------------------------------------------- o que foi cobrado de verdade


def cobradas(s: SessaoORM, arena_id: int, reservas: list[Reserva]) -> dict:
    """Taxas do Asaas e comissão efetivamente lançadas (livro-razão) para as reservas pagas informadas. Vazio se a arena não tem subconta."""
    rec = s.scalar(select(Recebedor).where(Recebedor.arena_id == arena_id))
    pids = [r.asaas_payment_id for r in reservas if r.asaas_payment_id and r.status_pagamento in ("pago", "estornado")]
    vazio = {"tem_conta": rec is not None, "taxa_asaas": 0.0, "taxa_asaas_paga_pela_arena": 0.0, "comissao_playgo": 0.0, "taxa_efetiva_pct": 0.0, "pagamentos": len(pids), "atualizado_em": rec.taxas_em if rec else None, "vigentes": None}
    if rec is None:
        return vazio
    vazio["vigentes"] = rec.taxas or None
    if not pids:
        return vazio
    taxa = comissao = Decimal(0)
    descontada = Decimal(0)
    bruto = Decimal(0)
    for x in s.scalars(select(LancamentoRecebedor).where(LancamentoRecebedor.recebedor_id == rec.id, LancamentoRecebedor.referencia.in_(pids))):
        if x.tipo == "taxa_asaas":
            taxa += x.valor_bruto
            descontada += -x.valor_liquido
        elif x.tipo == "comissao":
            comissao += -x.valor_liquido
        elif x.tipo == "recebimento":
            bruto += x.valor_bruto
    vazio.update({"taxa_asaas": float(_q(taxa)), "taxa_asaas_paga_pela_arena": float(_q(descontada)), "comissao_playgo": float(_q(comissao)), "taxa_efetiva_pct": round(float(100 * taxa / bruto), 2) if bruto else 0.0})
    return vazio
