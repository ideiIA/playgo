"""Relatórios: números da arena (reservas, valores, ocupação), acesso só do gestor ou admin e visão geral só do admin."""

from datetime import timedelta
from decimal import Decimal

import pytest

from playgo import relatorios, reservas
from playgo.db import agora
from playgo.erros import ErroNegocio, SemPermissao

from test_recebimentos import asaas, _ativada  # noqa: F401 (fixture)
from test_reservas import PAGADOR, _config, _horario, _pagar


def test_relatorio_da_arena_soma_reservas_valores_e_ocupacao(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    _config(s, a, g, "ambos")
    q.valor_hora = Decimal("100")
    s.commit()
    atleta = fabrica.atleta("Cliente")
    h1, h2 = _horario(s, q, g, 72), _horario(s, q, g, 96)
    r1 = reservas.reservar_horario(s, atleta, h1.id, "online", PAGADOR, "pix")
    reservas.reservar_horario(s, atleta, h2.id, "local")
    from playgo.models import Reserva

    _pagar(s, s.get(Reserva, r1["id"]), rec)

    rel = relatorios.arena(s, g, a.id, agora().date(), agora().date() + timedelta(days=10))
    x = rel["resumo"]
    assert x["reservas"] == 2 and x["reservas_atletas"] == 2
    assert x["pagas_pelo_app"] == 1 and x["combinadas_na_arena"] == 1
    assert x["recebido_bruto"] == 100.0 and x["previsto_na_arena"] == 100.0
    assert x["horas_reservadas"] == 2.0 and x["ocupacao_pct"] > 0
    assert rel["melhores_clientes"][0]["reservas"] == 2 and rel["melhores_clientes"][0]["usuario"].startswith("@")
    assert sum(d["reservas"] for d in rel["por_dia"]) == 2
    linhas = relatorios.reservas_csv(s, g, a.id, agora().date(), agora().date() + timedelta(days=10))
    assert len(linhas) == 3 and "@" in linhas[1][5]  # cliente só pelo @usuario


def test_acesso_ao_relatorio(s, fabrica, asaas):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    intruso = fabrica.atleta("Intruso")
    with pytest.raises(SemPermissao):
        relatorios.arena(s, intruso, a.id)
    with pytest.raises(SemPermissao):
        relatorios.plataforma(s, g)  # gestor de arena não vê o geral
    with pytest.raises(ErroNegocio):
        relatorios.arena(s, g, a.id, agora().date(), agora().date() - timedelta(days=1))
    with pytest.raises(ErroNegocio):
        relatorios.arena(s, g, a.id, agora().date() - timedelta(days=400), agora().date())
    assert [x["id"] for x in relatorios.arenas_do_usuario(s, g)] == [a.id]
    assert relatorios.arenas_do_usuario(s, intruso) == []
