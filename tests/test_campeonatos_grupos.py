"""Campeonatos (RF-007/008/021/022), grupos (RF-004/020) e notificações."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from playgo import arenas, campeonatos, descoberta, grupos, notificacoes
from playgo.campeonatos import NovoCampeonato
from playgo.db import agora
from playgo.erros import ErroNegocio, SemPermissao
from playgo.models import Notificacao

PERTO = (-20.4535, -54.6201)


def _camp(s, fabrica, org, **kw):
    hoje = agora().date()
    base = dict(
        modalidade_id=fabrica.mod("futsal").id, nome="Copa", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10),
        max_equipes=2, atletas_por_equipe=3, local_nome="Ginásio", latitude=PERTO[0], longitude=PERTO[1], valor_inscricao=Decimal(500),
    )
    return campeonatos.criar(s, org, NovoCampeonato(**(base | kw)))


def test_criar_valida_prazos(s, fabrica):
    org = fabrica.atleta("Org")
    hoje = date.today()
    # o prazo de inscrição pode passar do início (inscrições abertas com o campeonato em andamento)
    assert _camp(s, fabrica, org, inscricao_ate=hoje + timedelta(days=30)).id
    with pytest.raises(ErroNegocio):
        _camp(s, fabrica, org, max_equipes=1)


def test_campeonato_avisa_atletas_proximos_do_esporte(s, fabrica):
    org = fabrica.atleta("Org")
    interessado = fabrica.atleta("Joga", esportes=(("futsal", "intermediario"),), notif_raio_km=5)
    desinteressado = fabrica.atleta("Corre", esportes=(("corrida", "intermediario"),))
    c = _camp(s, fabrica, org)
    avisos = lambda u: s.query(Notificacao).filter_by(usuario_id=u.id, chave=f"camp:{c.id}").count()  # noqa: E731
    assert avisos(interessado) == 1 and avisos(desinteressado) == 0


def test_inscricao_de_equipe_convites_e_vagas(s, fabrica):
    org = fabrica.atleta("Org")
    c = _camp(s, fabrica, org, max_equipes=3)
    cap1, cap2, cap3, jogador = (fabrica.atleta(n, esportes=(("futsal", "intermediario"),)) for n in ("Cap1", "Cap2", "Cap3", "Jog"))

    e1 = campeonatos.inscrever_equipe(s, c.id, cap1, "Os Craques")
    campeonatos.inscrever_equipe(s, c.id, cap1, "Outra")  # a mesma pessoa pode inscrever mais de uma equipe
    ms = campeonatos.minha_situacao(s, c, cap1)
    assert [e["nome"] for e in ms["equipes"]] == ["Os Craques", "Outra"] and ms["estado"] == "capitao"
    with pytest.raises(ErroNegocio):
        campeonatos.inscrever_equipe(s, c.id, cap1, "OUTRA")  # nome repetido continua barrado
    with pytest.raises(ErroNegocio):
        campeonatos.inscrever_equipe(s, c.id, cap2, "os craques")  # nome repetido
    campeonatos.inscrever_equipe(s, c.id, cap2, "Rivais")
    with pytest.raises(ErroNegocio):
        campeonatos.inscrever_equipe(s, c.id, cap3, "Terceira")  # sem vagas

    # convite → aceite
    campeonatos.convidar(s, e1.id, cap1, jogador.id)
    assert notificacoes.nao_lidas(s, jogador) == 1  # o convite (o atleta nasceu depois do campeonato)
    with pytest.raises(SemPermissao):
        campeonatos.convidar(s, e1.id, jogador, cap3.id)  # só o capitão convida
    campeonatos.responder_convite(s, e1.id, jogador, True)
    s.refresh(e1)
    assert sum(1 for m in e1.membros if m.status == "confirmado") == 2
    assert campeonatos.minha_situacao(s, c, jogador)["estado"] == "jogador"

    # organizador confirma; capitão não decide
    with pytest.raises(SemPermissao):
        campeonatos.decidir_equipe(s, e1.id, cap1, True)
    assert campeonatos.decidir_equipe(s, e1.id, org, True).status == "confirmada"

    # equipe cancelada libera a vaga
    campeonatos.cancelar_equipe(s, e1.id, cap1)
    assert campeonatos.inscrever_equipe(s, c.id, cap3, "Terceira").status == "pendente"


def test_atleta_pode_estar_em_mais_de_uma_equipe_do_campeonato(s, fabrica):
    org = fabrica.atleta("Org")
    c = _camp(s, fabrica, org, max_equipes=4)
    cap1, cap2, jogador = fabrica.atleta("A"), fabrica.atleta("B"), fabrica.atleta("J")
    e1 = campeonatos.inscrever_equipe(s, c.id, cap1, "E1")
    e2 = campeonatos.inscrever_equipe(s, c.id, cap2, "E2")
    campeonatos.convidar(s, e1.id, cap1, jogador.id)
    campeonatos.convidar(s, e2.id, cap2, jogador.id)
    ms = campeonatos.minha_situacao(s, c, jogador)
    assert [cv["equipe"] for cv in ms["convites"]] == ["E1", "E2"] and ms["estado"] == "convidado"
    campeonatos.responder_convite(s, e1.id, jogador, True)
    campeonatos.responder_convite(s, e2.id, jogador, True)  # a mesma pessoa joga por duas equipes
    ms = campeonatos.minha_situacao(s, c, jogador)
    assert [e["nome"] for e in ms["equipes"]] == ["E1", "E2"] and ms["convites"] == []
    with pytest.raises(ErroNegocio):  # mas na mesma equipe só entra uma vez: o limite de atletas continua valendo
        for i in range(c.atletas_por_equipe):
            campeonatos.convidar(s, e1.id, cap1, fabrica.atleta(f"X{i}").id)


def test_inscricao_encerrada(s, fabrica):
    org = fabrica.atleta("Org")
    c = _camp(s, fabrica, org)
    campeonatos.definir_status(s, c.id, org, "em_andamento")
    with pytest.raises(ErroNegocio):
        campeonatos.inscrever_equipe(s, c.id, fabrica.atleta("Cap"), "Tarde")


def test_gestor_cria_campeonato_na_arena_e_so_ele(s, fabrica):
    gestor, outro = fabrica.atleta("Gestor"), fabrica.atleta("Outro")
    a = arenas.criar(s, gestor, "Arena C", *PERTO)
    c = _camp(s, fabrica, gestor, arena_id=a.id, local_nome="", latitude=None, longitude=None)
    assert c.arena_id == a.id and c.local_nome == "Arena C" and c.latitude == PERTO[0]
    with pytest.raises(SemPermissao):
        _camp(s, fabrica, outro, arena_id=a.id)


def test_descoberta_de_campeonatos(s, fabrica):
    org = fabrica.atleta("Org")
    c = _camp(s, fabrica, org)
    fabrica.atleta("Ver")
    achou = descoberta.buscar_campeonatos(s, *PERTO, descoberta.Filtros(raio_km=5))
    item = next(x for x in achou if x["id"] == c.id)
    assert item["situacao"] == "Inscrições abertas" and item["vagas"] == 2 and item["valor_inscricao_texto"] == "R$ 500/equipe"
    assert not [x for x in descoberta.buscar_campeonatos(s, *PERTO, descoberta.Filtros(raio_km=5, modalidades=["corrida"])) if x["id"] == c.id]


# ---------------------------------------------------------------- grupos


def test_grupo_admin_membros_chat_e_avisos(s, fabrica):
    dono, membro, de_fora = fabrica.atleta("Dono"), fabrica.atleta("Membro"), fabrica.atleta("Fora")
    g = grupos.criar(s, dono, "Corre CG", fabrica.mod("corrida").id)
    assert grupos.membro(s, g.id, dono).papel == "admin"
    grupos.entrar(s, g.id, membro)
    grupos.entrar(s, g.id, membro)  # idempotente
    s.refresh(g)
    assert len(g.membros) == 2

    grupos.postar(s, g.id, membro, "Bom dia!")
    with pytest.raises(SemPermissao):
        grupos.postar(s, g.id, membro, "Aviso falso", aviso=True)  # só admin publica aviso
    with pytest.raises(SemPermissao):
        grupos.postar(s, g.id, de_fora, "Oi")  # só membro conversa
    grupos.postar(s, g.id, dono, "Terça 6h!", aviso=True)
    assert [m.texto for m in grupos.mensagens(s, g.id)] == ["Bom dia!", "Terça 6h!"]
    primeiro = grupos.mensagens(s, g.id)[0].id
    assert [m.texto for m in grupos.mensagens(s, g.id, depois_de=primeiro)] == ["Terça 6h!"]  # polling do app
    assert [a.texto for a in grupos.avisos(s, g.id)] == ["Terça 6h!"]

    with pytest.raises(ErroNegocio):
        grupos.sair(s, g.id, dono)  # único admin
    grupos.promover(s, g.id, membro.id, dono)
    grupos.sair(s, g.id, dono)


def test_atividade_do_grupo_avisa_os_membros(s, fabrica):
    from playgo import atividades
    from playgo.atividades import NovaAtividade

    dono, membro = fabrica.atleta("Dono", esportes=(("corrida", "intermediario"),)), fabrica.atleta("Membro", esportes=(("corrida", "intermediario"),))
    g = grupos.criar(s, dono, "Pedal", fabrica.mod("corrida").id)
    grupos.entrar(s, g.id, membro)
    a = atividades.criar(s, dono, NovaAtividade(modalidade_id=fabrica.mod("corrida").id, nome="Corrida de sábado", inicio=agora() + timedelta(days=2), max_participantes=20, grupo_id=g.id, local_nome="Parque", latitude=PERTO[0], longitude=PERTO[1]))
    assert s.query(Notificacao).filter_by(usuario_id=membro.id, chave=f"grp:{a.id}").count() == 1
    with pytest.raises(SemPermissao):  # quem não é do grupo não cria nele
        atividades.criar(s, fabrica.atleta("Intruso"), NovaAtividade(modalidade_id=fabrica.mod("corrida").id, nome="X", inicio=agora() + timedelta(days=2), max_participantes=5, grupo_id=g.id, local_nome="P", latitude=PERTO[0], longitude=PERTO[1]))
