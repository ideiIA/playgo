"""Taxas da subconta: cópia do Asaas, simulação do que a arena recebe e taxas realmente cobradas (relatório). Asaas simulado."""

from datetime import timedelta
from decimal import Decimal

import pytest

from playgo import recebimentos, relatorios, reservas, taxas
from playgo.config import settings
from playgo.db import agora
from playgo.erros import ErroNegocio, SemPermissao
from playgo.models import Reserva

from test_recebimentos import _ativada, asaas  # noqa: F401 (fixture)
from test_reservas import PAGADOR, _config, _horario, _pagar


def test_normalizar_guarda_so_o_que_a_tela_usa(asaas):
    t = taxas.normalizar(asaas.taxas)
    assert t["pix"]["fixa"] == 1.99 and t["cartao"]["pct_1x"] == 2.99 and t["cartao"]["dias_para_receber"] == 32
    assert t["saque"]["pix"] == 3.5 and t["antecipacao"]["cartao_avista_pct_mes"] == 1.7
    assert taxas.normalizar({})["pix"]["fixa"] is None  # Asaas sem dados: nada quebra


def test_simulacao_pix_cartao_e_antecipacao(asaas, monkeypatch):
    monkeypatch.setattr(settings, "recebimentos_comissao_percentual", Decimal("5"))
    monkeypatch.setattr(settings, "recebimentos_comissao_fixa", Decimal(0))
    t = taxas.normalizar(asaas.taxas)
    sim = {l["forma"]: l for l in taxas.simular(t, Decimal("100"), Decimal("0"), "arena")}
    pix = sim["Pix"]
    assert pix["cliente_paga"] == 100.0 and pix["taxa_asaas"] == 1.99
    assert pix["comissao_playgo"] == 4.9 and pix["voce_recebe"] == 93.11  # 100 - 1,99 - 5% de 98,01
    cartao = sim["Cartão de crédito (à vista)"]
    assert cartao["taxa_asaas"] == 3.48 and cartao["prazo"] == "32 dias"  # 2,99% + 0,49
    assert any(k.startswith("Cartão com antecipação") for k in sim)
    com_acrescimo = {l["forma"]: l for l in taxas.simular(t, Decimal("100"), Decimal("5"), "arena")}
    assert com_acrescimo["Cartão de crédito (à vista)"]["cliente_paga"] == 105.0
    plataforma = {l["forma"]: l for l in taxas.simular(t, Decimal("100"), Decimal("0"), "plataforma")}
    assert plataforma["Pix"]["voce_recebe"] == 95.1  # taxa por conta do PlayGo: a arena só paga a comissão (5% de 98,01 = 4,90)


def test_ver_taxas_guarda_copia_e_so_o_gestor_ve(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    r = taxas.ver(s, a.id, g, Decimal("50"))
    assert r["taxas"]["pix"]["fixa"] == 1.99 and r["valor_simulado"] == 50.0 and r["simulacao"]
    s.refresh(rec)
    assert rec.taxas and rec.taxas_em
    chamadas = len([c for c in asaas.chamadas if c[1] == "/myAccount/fees"])
    taxas.ver(s, a.id, g)  # cópia ainda fresca: não consulta de novo
    assert len([c for c in asaas.chamadas if c[1] == "/myAccount/fees"]) == chamadas
    asaas.falhar.add("GET /myAccount/fees")
    r = taxas.ver(s, a.id, g, forcar=True)  # Asaas falhou: devolve a cópia antiga, avisando
    assert r["aviso"] and r["taxas"]["pix"]["fixa"] == 1.99
    with pytest.raises(SemPermissao):
        taxas.ver(s, a.id, fabrica.atleta("Intruso"))


def test_taxas_exigem_cadastro_aprovado(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas, aprovar=False)
    with pytest.raises(ErroNegocio):
        taxas.ver(s, a.id, g)


def test_agendador_renova_copia_velha(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    assert taxas.atualizar_todas(s, limite=1000) >= 1
    s.refresh(rec)
    assert rec.taxas
    rec.taxas_em = agora() - timedelta(hours=30)
    s.commit()
    assert taxas.atualizar_todas(s, limite=1000) >= 1


def test_relatorio_traz_taxas_realmente_cobradas(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "online")
    q.valor_hora = Decimal("100")
    s.commit()
    atleta = fabrica.atleta("Cliente")
    h = _horario(s, q, g, 72)
    res = reservas.reservar_horario(s, atleta, h.id, "online", PAGADOR, "pix")
    r = s.get(Reserva, res["id"])
    pag = {"id": r.asaas_payment_id, "value": 100.0, "netValue": 98.01, "status": "CONFIRMED"}
    recebimentos.processar_webhook(s, __import__("test_recebimentos").TOKEN, __import__("test_recebimentos")._evento("PAYMENT_CONFIRMED", pag, conta=rec.asaas_account_id))
    taxas.ver(s, a.id, g)
    rel = relatorios.arena(s, g, a.id, agora().date(), agora().date() + timedelta(days=10))
    x = rel["resumo"]
    assert x["taxa_asaas"] == 1.99 and x["taxa_efetiva_pct"] == 1.99
    assert x["liquido_estimado"] <= x["recebido_bruto"] - x["taxa_asaas_paga_pela_arena"]
    assert rel["taxas"]["tem_conta"] and rel["taxas"]["vigentes"]["pix"]["fixa"] == 1.99
