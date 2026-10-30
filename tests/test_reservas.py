"""Reserva feita pelo atleta: forma de pagamento da arena (online, na arena ou as duas) e cancelamento com devolução. Asaas simulado."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from playgo import arenas, recebimentos, reservas
from playgo.db import agora
from playgo.erros import ErroNegocio, NaoEncontrado, SemPermissao
from playgo.models import HorarioDivulgado, Reserva

from test_recebimentos import TOKEN, _ativada, _evento, asaas  # noqa: F401 (fixture)

PAGADOR = {"nome": "Cliente Teste", "email": "c@t.test", "cpf_cnpj": "529.982.247-25"}


def _horario(s, q, g, em_horas=72):
    inicio = (agora() + timedelta(hours=em_horas)).replace(minute=0, second=0, microsecond=0)
    return arenas.divulgar(s, q.id, inicio, inicio + timedelta(hours=1), None, None, g, avisar=False)


def _config(s, a, g, pagamento="ambos", horas=24, dentro=100, fora=50):
    arenas.editar(s, a, g, a.nome, a.latitude, a.longitude, pagamento_reserva=pagamento, cancel_horas=horas, reembolso_pct=dentro, reembolso_pct_fora=fora)


def _pagar(s, r, rec):
    pag = {"id": r.asaas_payment_id, "value": float(r.valor), "netValue": float(r.valor), "status": "CONFIRMED"}
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", pag, conta=rec.asaas_account_id))
    s.refresh(r)


def test_politica_da_arena_e_validada_so_pelo_gestor(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "online", 12, 100, 30)
    s.refresh(a)
    assert (a.pagamento_reserva, a.cancel_horas, a.reembolso_pct, a.reembolso_pct_fora) == ("online", 12, 100, 30)
    for ruim in ({"pagamento_reserva": "pix"}, {"reembolso_pct": 120}, {"reembolso_pct_fora": 90, "reembolso_pct": 50}, {"cancel_horas": -1}):
        args = {"pagamento_reserva": "online", "cancel_horas": 24, "reembolso_pct": 100, "reembolso_pct_fora": 0} | ruim
        with pytest.raises(ErroNegocio):
            arenas.editar(s, a, g, a.nome, a.latitude, a.longitude, **args)
    with pytest.raises(SemPermissao):
        arenas.editar(s, a, fabrica.atleta("Intruso"), a.nome, a.latitude, a.longitude, pagamento_reserva="local")
    arenas.editar(s, a, g, a.nome, a.latitude, a.longitude)  # tela antiga, sem política: não mexe
    s.refresh(a)
    assert a.pagamento_reserva == "online"


def test_formas_dependem_do_recebimento_da_arena(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas, aprovar=False)
    _config(s, a, g, "ambos")
    assert reservas.formas(s, a) == ["local"]  # sem KYC aprovado, só na arena
    _config(s, a, g, "online")
    assert reservas.formas(s, a) == []
    rec.status_kyc = "aprovado"
    s.commit()
    assert reservas.formas(s, a) == ["online"]
    _config(s, a, g, "ambos")
    assert reservas.formas(s, a) == ["online", "local"]
    assert "devolvemos 100%" in reservas.politica(s, a)["texto"]


def test_reserva_na_arena_nao_cobra_e_cancela_sem_devolucao(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "local")
    atleta = fabrica.atleta("Reservista")
    h = _horario(s, q, g)
    r = reservas.reservar_horario(s, atleta, h.id)
    assert r["forma_pagamento"] == "local" and r["status_pagamento"] == "sem_cobranca" and r["valor"] == 100.0
    assert asaas.chamou("POST", "/payments") == []
    assert s.scalar(select(HorarioDivulgado).where(HorarioDivulgado.id == h.id)) is None  # saiu da vitrine
    assert [x["id"] for x in reservas.minhas(s, atleta)] == [r["id"]]
    with pytest.raises(NaoEncontrado):
        reservas.cancelar(s, fabrica.atleta("Outro"), r["id"])
    out = reservas.cancelar(s, atleta, r["id"])
    assert out["cancelada"] and out["devolvido"] == 0 and reservas.minhas(s, atleta) == []


def test_reserva_online_cria_cobranca_e_segura_o_horario(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "online")
    atleta = fabrica.atleta("Pagador")
    h = _horario(s, q, g)
    with pytest.raises(ErroNegocio, match="Informe o nome"):
        reservas.reservar_horario(s, atleta, h.id, "online", {"nome": "", "cpf_cnpj": "529.982.247-25"})
    assert s.scalar(select(HorarioDivulgado).where(HorarioDivulgado.id == h.id)) is not None  # falhou: nada ficou pela metade
    with pytest.raises(ErroNegocio, match="Escolha como pagar"):
        reservas.reservar_horario(s, atleta, h.id, "local", PAGADOR)  # a arena só aceita pelo app
    r = reservas.reservar_horario(s, atleta, h.id, "online", PAGADOR)
    assert r["status_pagamento"] == "pendente" and r["link_pagamento"].startswith("https://") and r["paga_ate"]
    pg = asaas.chamou("POST", "/payments")[0][3]
    assert pg["value"] == 100.0 and pg["split"][0]["walletId"] == "wal_mae" and "529" not in str(r)
    with pytest.raises(ErroNegocio, match="não está mais disponível"):
        reservas.reservar_horario(s, fabrica.atleta("Atrasado"), h.id, "online", PAGADOR)


def test_cancelar_dentro_do_prazo_devolve_tudo_e_fora_devolve_a_parte(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "online", horas=24, dentro=100, fora=50)
    atleta = fabrica.atleta("Cancela Cedo")
    r = reservas.reservar_horario(s, atleta, _horario(s, q, g, 72).id, "online", PAGADOR)
    reserva = s.get(Reserva, r["id"])
    _pagar(s, reserva, rec)
    assert reservas.serializar(s, reserva)["devolucao_pct"] == 100
    saldo = recebimentos.saldo_interno(s, rec.id)
    out = reservas.cancelar(s, atleta, r["id"])
    assert out["devolvido"] == 100.0 and out["devolucao_pct"] == 100
    assert asaas.chamou("POST", "/refund")[-1][3]["value"] == 100.0
    assert recebimentos.saldo_interno(s, rec.id) == saldo - Decimal("90.00")  # a Arena devolve a parte dela; a comissão volta junto

    atleta2 = fabrica.atleta("Cancela Tarde")
    r2 = reservas.reservar_horario(s, atleta2, _horario(s, q, g, 10).id, "online", PAGADOR)
    _pagar(s, s.get(Reserva, r2["id"]), rec)
    out2 = reservas.cancelar(s, atleta2, r2["id"])
    assert out2["devolvido"] == 50.0 and out2["devolucao_pct"] == 50 and out2["mantido_pela_arena"] == 50.0
    assert asaas.chamou("POST", "/refund")[-1][3]["value"] == 50.0

    _config(s, a, g, "online", horas=24, dentro=100, fora=0)
    atleta3 = fabrica.atleta("Cancela Muito Tarde")
    r3 = reservas.reservar_horario(s, atleta3, _horario(s, q, g, 5).id, "online", PAGADOR)
    _pagar(s, s.get(Reserva, r3["id"]), rec)
    n_refunds = len(asaas.chamou("POST", "/refund"))
    out3 = reservas.cancelar(s, atleta3, r3["id"])
    assert out3["devolvido"] == 0 and len(asaas.chamou("POST", "/refund")) == n_refunds  # nada devolvido, mas o horário é liberado


def test_cancelar_antes_de_pagar_apaga_a_cobranca_e_reserva_que_comecou_nao_cancela(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "online")
    atleta = fabrica.atleta("Desiste")
    r = reservas.reservar_horario(s, atleta, _horario(s, q, g).id, "online", PAGADOR)
    out = reservas.cancelar(s, atleta, r["id"])
    assert out["cancelada"] and asaas.chamou("DELETE", "/payments/")
    comecou = arenas.reservar(s, q.id, agora() - timedelta(minutes=10), agora() + timedelta(minutes=50))
    comecou.usuario_id, comecou.forma_pagamento = atleta.id, "local"
    s.commit()
    with pytest.raises(ErroNegocio, match="já começou"):
        reservas.cancelar(s, atleta, comecou.id)


def test_reserva_online_nao_paga_expira_e_libera_o_horario(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "online")
    atleta = fabrica.atleta("Esquece")
    r = reservas.reservar_horario(s, atleta, _horario(s, q, g).id, "online", PAGADOR)
    s.get(Reserva, r["id"]).criado_em = agora() - timedelta(minutes=reservas.PRAZO_PAGAMENTO_MIN + 1)
    s.commit()
    assert reservas.expirar_pendentes(s) >= 1
    assert s.get(Reserva, r["id"]) is None and asaas.chamou("DELETE", "/payments/")
