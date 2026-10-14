"""Edição do campeonato e elenco das equipes (nome, RG, técnico e capitão)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from playgo import campeonatos, chaves, elenco
from playgo.campeonatos import NovoCampeonato
from playgo.db import Session, agora
from playgo.erros import ErroNegocio, SemPermissao
from playgo.models import Notificacao, Usuario

PERTO = (-20.4535, -54.6201)


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            from playgo import contas

            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


def _camp(s, fabrica, org, **kw):
    hoje = agora().date()
    return campeonatos.criar(
        s, org,
        NovoCampeonato(modalidade_id=fabrica.mod("futsal").id, nome="Copa", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10), max_equipes=8, atletas_por_equipe=5, local_nome="G", latitude=PERTO[0], longitude=PERTO[1], **kw),
    )


def test_editar_campeonato_so_a_organizacao_e_com_regras(s, fabrica):
    org, outro, admin = fabrica.atleta("Org"), fabrica.atleta("Outro"), fabrica.atleta("Adm")
    admin.admin = True
    s.commit()
    c = _camp(s, fabrica, org)
    cap = fabrica.atleta("Capitao")
    e = campeonatos.inscrever_equipe(s, c.id, cap, "Time A")
    hoje = agora().date()

    with pytest.raises(SemPermissao):
        campeonatos.editar(s, c.id, outro, nome="Invasão")
    campeonatos.editar(s, c.id, org, nome="  Copa Nova  ", data_inicio=hoje + timedelta(days=30), inscricao_ate=hoje + timedelta(days=25), regulamento="  Novo regulamento ", cadastro_elenco=True)
    assert c.nome == "Copa Nova" and c.data_inicio == hoje + timedelta(days=30) and c.regulamento == "Novo regulamento" and c.cadastro_elenco is True
    # o administrador também edita
    campeonatos.editar(s, c.id, admin, valor_inscricao=50, premiacao="Troféu")
    assert float(c.valor_inscricao) == 50 and c.premiacao == "Troféu"
    # a capitã foi avisada da mudança de data
    assert s.query(Notificacao).filter(Notificacao.usuario_id == cap.id, Notificacao.titulo.like("%datas ou local%")).count() == 1

    for ruim, msg in (
        (dict(nome="   "), "nome"),
        (dict(data_fim=hoje), "data final"),
        (dict(max_equipes=1), "pelo menos 2"),
        (dict(valor_inscricao=-1), "negativo"),
        (dict(local_nome=" "), "local"),
    ):
        with pytest.raises(ErroNegocio, match=msg):
            campeonatos.editar(s, c.id, org, **ruim)
    campeonatos.convidar(s, e.id, cap, fabrica.atleta("Jog").id)
    with pytest.raises(ErroNegocio, match="Atletas por equipe"):
        campeonatos.editar(s, c.id, org, atletas_por_equipe=1)  # a equipe já tem 2 pessoas
    # só muda o que foi enviado
    campeonatos.editar(s, c.id, org, descricao="Sobre")
    assert c.nome == "Copa Nova"


def test_elenco_so_com_cadastro_liberado_e_por_quem_pode(s, fabrica):
    org, cap, intruso = fabrica.atleta("Org"), fabrica.atleta("Capitao"), fabrica.atleta("Intruso")
    c = _camp(s, fabrica, org)
    e = campeonatos.inscrever_equipe(s, c.id, cap, "Time A")
    with pytest.raises(ErroNegocio, match="não liberou"):
        elenco.adicionar(s, e.id, cap, "Fulano da Silva", "1234567", "atleta")
    campeonatos.editar(s, c.id, org, cadastro_elenco=True)
    with pytest.raises(SemPermissao):
        elenco.listar(s, e.id, intruso)
    with pytest.raises(SemPermissao):
        elenco.adicionar(s, e.id, intruso, "Fulano da Silva", "1234567", "atleta")
    r = elenco.adicionar(s, e.id, cap, "Fulano da Silva", "12.345.678-9", "atleta", capitao=True)
    assert r["atletas"] == 1 and r["componentes"][0]["capitao"] is True and r["pode_editar"] is True
    # a organização também cadastra, vê o RG
    r = elenco.adicionar(s, e.id, org, "Beltrano Souza", "9876543", "atleta")
    assert [x["rg"] for x in r["componentes"]] == ["12.345.678-9", "9876543"]


def test_elenco_regras_de_limite_rg_tecnico_e_capitao(s, fabrica):
    org, cap = fabrica.atleta("Org"), fabrica.atleta("Capitao")
    c = _camp(s, fabrica, org)
    campeonatos.editar(s, c.id, org, cadastro_elenco=True, atletas_por_equipe=3)
    e = campeonatos.inscrever_equipe(s, c.id, cap, "Time A")
    a = elenco.adicionar(s, e.id, cap, "Atleta Um", "11111", "atleta")["componentes"][0]["id"]
    b = elenco.adicionar(s, e.id, cap, "Atleta Dois", "22222", "atleta", capitao=True)["componentes"][1]["id"]
    with pytest.raises(ErroNegocio, match="mesmo RG|esse RG"):
        elenco.adicionar(s, e.id, cap, "Outro Nome", "22.222", "atleta")  # RG repetido (ignora pontuação)
    with pytest.raises(ErroNegocio, match="RG"):
        elenco.adicionar(s, e.id, cap, "Sem RG Valido", "12", "atleta")
    with pytest.raises(ErroNegocio, match="nome completo"):
        elenco.adicionar(s, e.id, cap, "Jo", "3333333", "atleta")
    elenco.adicionar(s, e.id, cap, "Atleta Tres", "33333", "atleta")
    with pytest.raises(ErroNegocio, match="já tem os 3 atletas"):
        elenco.adicionar(s, e.id, cap, "Atleta Quatro", "44444", "atleta")
    r = elenco.adicionar(s, e.id, cap, "Treinador Silva", "55555", "tecnico")  # o técnico não ocupa vaga de atleta
    assert r["tem_tecnico"] and r["atletas"] == 3
    with pytest.raises(ErroNegocio, match="já tem um técnico"):
        elenco.adicionar(s, e.id, cap, "Treinador Dois", "66666", "tecnico")
    with pytest.raises(ErroNegocio, match="técnico não pode ser o capitão"):
        elenco.definir_capitao(s, e.id, next(x["id"] for x in r["componentes"] if x["funcao"] == "tecnico"), cap)
    # trocar o capitão: só um por vez
    r = elenco.definir_capitao(s, e.id, a, cap)
    assert [x["id"] for x in r["componentes"] if x["capitao"]] == [a]
    assert b in [x["id"] for x in r["componentes"] if not x["capitao"]]
    # editar e remover
    r = elenco.atualizar(s, e.id, a, cap, "Atleta Um Silva", "11.111", "atleta")
    assert next(x for x in r["componentes"] if x["id"] == a)["nome"] == "Atleta Um Silva"
    r = elenco.remover(s, e.id, b, cap)
    assert r["atletas"] == 2


def test_elenco_fecha_para_o_capitao_quando_a_equipe_comeca_a_jogar(s, fabrica):
    org = fabrica.atleta("Org")
    c = _camp(s, fabrica, org)
    campeonatos.editar(s, c.id, org, cadastro_elenco=True)
    caps = [fabrica.atleta(f"Cap{i}") for i in range(2)]
    eqs = []
    for i, cap in enumerate(caps):
        e = campeonatos.inscrever_equipe(s, c.id, cap, f"Time {i}")
        campeonatos.decidir_equipe(s, e.id, org, True)
        eqs.append(e)
    chaves.sortear(s, c.id, org, "eliminatoria")
    from playgo.models import Jogo

    j = s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id)).first()
    elenco.adicionar(s, eqs[0].id, caps[0], "Antes do Jogo", "12345", "atleta")
    chaves.iniciar(s, c.id, j.id, org)
    with pytest.raises(ErroNegocio, match="já começou a jogar"):
        elenco.adicionar(s, eqs[0].id, caps[0], "Depois do Jogo", "54321", "atleta")
    assert elenco.listar(s, eqs[0].id, caps[0])["pode_editar"] is False
    elenco.adicionar(s, eqs[0].id, org, "Pela Organização", "54321", "atleta")  # a organização sempre pode
    # contagem pública: só números, sem nomes
    assert elenco.contagens(s, [eqs[0].id, eqs[1].id]) == {eqs[0].id: 2}
