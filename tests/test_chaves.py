"""Sorteio, chaves, classificação e jogo ao vivo dos campeonatos."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from playgo import campeonatos, chaves
from playgo.campeonatos import NovoCampeonato
from playgo.db import Session, agora
from playgo.erros import ErroNegocio, SemPermissao
from playgo.models import Equipe, Jogo, Notificacao, Usuario

PERTO = (-20.4535, -54.6201)


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            from playgo import contas

            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


def _torneio(s, fabrica, n_equipes, confirmar=True):
    org = fabrica.atleta("Organizador")
    hoje = agora().date()
    c = campeonatos.criar(
        s, org,
        NovoCampeonato(modalidade_id=fabrica.mod("futsal").id, nome="Copa", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10), max_equipes=32, local_nome="G", latitude=PERTO[0], longitude=PERTO[1]),
    )
    donos = []
    for i in range(n_equipes):
        cap = fabrica.atleta(f"Capitao{i}")
        e = campeonatos.inscrever_equipe(s, c.id, cap, f"Equipe {i + 1}")
        if confirmar:
            campeonatos.decidir_equipe(s, e.id, org, True)
        donos.append(cap)
    return c, org, donos


def _jogos(s, c):
    return list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id).order_by(Jogo.rodada, Jogo.posicao)))


def _disputar(s, c, org, j, a, b, vencedor_id=None):
    chaves.iniciar(s, c.id, j.id, org)
    chaves.definir_placar(s, c.id, j.id, org, a, b)
    return chaves.encerrar(s, c.id, j.id, org, vencedor_id)


def test_sorteio_so_da_organizacao_e_so_com_equipes_confirmadas(s, fabrica):
    c, org, donos = _torneio(s, fabrica, 1)
    with pytest.raises(ErroNegocio, match="pelo menos 2"):
        chaves.sortear(s, c.id, org, "eliminatoria")
    with pytest.raises(SemPermissao):
        chaves.sortear(s, c.id, donos[0], "eliminatoria")
    e2 = campeonatos.inscrever_equipe(s, c.id, fabrica.atleta("Outro"), "Pendente")
    with pytest.raises(ErroNegocio, match="pelo menos 2"):
        chaves.sortear(s, c.id, org, "eliminatoria")  # pendente não entra no sorteio
    campeonatos.decidir_equipe(s, e2.id, org, True)
    with pytest.raises(ErroNegocio, match="formato"):
        chaves.sortear(s, c.id, org, "xadrez")
    r = chaves.sortear(s, c.id, org, "eliminatoria")
    assert r["equipes"] == 2 and r["semente"]


def test_eliminatoria_com_folgas_avanca_o_vencedor_ate_o_campeao(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 6)
    chaves.sortear(s, c.id, org, "eliminatoria")
    jogos = _jogos(s, c)
    assert [(j.rodada_nome, sum(1 for x in jogos if x.rodada == j.rodada)) for j in jogos if j.posicao == 0] == [("Quartas de final", 4), ("Semifinal", 2), ("Final", 1)]
    folgas = [j for j in jogos if j.folga]
    assert len(folgas) == 2 and all(j.status == "encerrado" and j.vencedor_id for j in folgas)
    assert {j.vencedor_id for j in folgas} <= {j.equipe_a_id or j.equipe_b_id for j in jogos if j.rodada == 2 for _ in (0,)} | {x for j in jogos if j.rodada == 2 for x in (j.equipe_a_id, j.equipe_b_id)}  # já estão nas semifinais
    assert s.get(type(org), org.id)  # sanidade
    equipes_r1 = [e for j in jogos if j.rodada == 1 for e in (j.equipe_a_id, j.equipe_b_id) if e]
    assert len(equipes_r1) == len(set(equipes_r1)) == 6  # todas as equipes, cada uma uma vez

    # joga a chave inteira dando a vitória ao lado A
    for rodada in (1, 2, 3):
        for j in [x for x in _jogos(s, c) if x.rodada == rodada and x.status == "agendado"]:
            _disputar(s, c, org, j, 2, 1)
    final = _jogos(s, c)[-1]
    assert final.status == "encerrado" and final.vencedor_id == final.equipe_a_id
    s.refresh(c)
    assert c.status == "encerrado"
    d = chaves.chaveamento(s, c, org)
    assert d["campeao"]["id"] == final.vencedor_id and d["formato"] == "eliminatoria" and len(d["rodadas"]) == 3


def test_empate_no_mata_mata_exige_desempate_e_pontos_corridos_aceita_empate(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 2)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    chaves.marcar(s, c.id, j.id, org, "a", 1)
    chaves.marcar(s, c.id, j.id, org, "b", 1)
    with pytest.raises(ErroNegocio, match="desempate"):
        chaves.encerrar(s, c.id, j.id, org)
    with pytest.raises(ErroNegocio, match="desempate"):
        chaves.encerrar(s, c.id, j.id, org, vencedor_id=987654)
    j = chaves.encerrar(s, c.id, j.id, org, vencedor_id=j.equipe_b_id)
    assert j.vencedor_id == j.equipe_b_id and j.desempate is True

    c2, org2, _ = _torneio(s, fabrica, 2)
    chaves.sortear(s, c2.id, org2, "pontos_corridos")
    j2 = _disputar(s, c2, org2, _jogos(s, c2)[0], 1, 1)
    assert j2.vencedor_id is None


def test_pontos_corridos_todos_contra_todos_e_classificacao(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "pontos_corridos")
    jogos = _jogos(s, c)
    assert len(jogos) == 6 and {j.rodada for j in jogos} == {1, 2, 3}
    pares = {frozenset((j.equipe_a_id, j.equipe_b_id)) for j in jogos}
    assert len(pares) == 6  # cada par uma vez
    for j in jogos:
        _disputar(s, c, org, j, 3, 0)  # o lado A sempre vence por 3 a 0
    s.refresh(c)
    assert c.status == "encerrado"
    tab = chaves.chaveamento(s, c, org)["classificacao"]
    assert [l["posicao"] for l in tab] == [1, 2, 3, 4]
    assert sum(l["pontos"] for l in tab) == 6 * 3 and sum(l["jogos"] for l in tab) == 12
    assert tab[0]["pontos"] >= tab[1]["pontos"] >= tab[2]["pontos"] >= tab[3]["pontos"]
    assert chaves.chaveamento(s, c, org)["campeao"]["id"] == tab[0]["equipe"]["id"]


def test_numero_impar_em_pontos_corridos_tem_folga_por_rodada(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 5)
    chaves.sortear(s, c.id, org, "pontos_corridos")
    assert len(_jogos(s, c)) == 10  # 5 equipes: 10 jogos


def test_so_mesario_ou_organizacao_conduz_e_o_placar_nao_fica_negativo(s, fabrica):
    c, org, donos = _torneio(s, fabrica, 2)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    intruso, mesario = fabrica.atleta("Intruso"), fabrica.atleta("Mesario")
    for acao in (lambda: chaves.iniciar(s, c.id, j.id, intruso), lambda: chaves.adicionar_mesario(s, c.id, mesario, mesario.id)):
        with pytest.raises(SemPermissao):
            acao()
    assert chaves.adicionar_mesario(s, c.id, org, mesario.id)[0]["usuario_id"] == mesario.id
    chaves.iniciar(s, c.id, j.id, mesario)
    chaves.marcar(s, c.id, j.id, mesario, "a", -1)  # não desce de zero
    assert (j.placar_a, j.placar_b) == (0, 0)
    chaves.marcar(s, c.id, j.id, mesario, "a", 1)
    chaves.lance(s, c.id, j.id, mesario, "Cartão amarelo")
    with pytest.raises(ErroNegocio, match="já começou"):
        chaves.iniciar(s, c.id, j.id, mesario)
    with pytest.raises(SemPermissao):
        chaves.reabrir(s, c.id, j.id, mesario)  # reabrir é da organização
    chaves.encerrar(s, c.id, j.id, mesario)
    with pytest.raises(ErroNegocio, match="ao vivo"):
        chaves.marcar(s, c.id, j.id, mesario, "b", 1)
    s.refresh(j)
    assert [e.tipo for e in j.eventos] == ["inicio", "placar", "lance", "fim"]
    # avisos: o capitão do outro time foi avisado do início e do fim, sem duplicar
    assert s.query(Notificacao).filter(Notificacao.usuario_id.in_([d.id for d in donos]), Notificacao.titulo.like("%Ao vivo%")).count() >= 1


def test_reabrir_desfaz_o_avanco_se_a_proxima_rodada_nao_comecou(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j1 = _jogos(s, c)[0]
    _disputar(s, c, org, j1, 5, 2)
    final = [x for x in _jogos(s, c) if x.rodada == 2][0]
    campeao_provisorio = j1.vencedor_id
    assert campeao_provisorio in (final.equipe_a_id, final.equipe_b_id)
    chaves.reabrir(s, c.id, j1.id, org)
    s.refresh(final)
    assert j1.status == "ao_vivo" and j1.vencedor_id is None and campeao_provisorio not in (final.equipe_a_id, final.equipe_b_id)
    # com a final já em andamento, o jogo de origem não reabre
    _disputar_de_novo = chaves.encerrar(s, c.id, j1.id, org)
    j2 = _jogos(s, c)[1]
    _disputar(s, c, org, j2, 1, 0)
    chaves.iniciar(s, c.id, final.id, org)
    with pytest.raises(ErroNegocio, match="já começou"):
        chaves.reabrir(s, c.id, j1.id, org)
    assert _disputar_de_novo.status == "encerrado"


def test_desfazer_inicio_volta_o_jogo_para_a_fila(s, fabrica):
    c, org, donos = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    with pytest.raises(ErroNegocio, match="ao vivo"):
        chaves.desfazer_inicio(s, c.id, j.id, org)  # ainda agendado
    chaves.iniciar(s, c.id, j.id, org)
    chaves.marcar(s, c.id, j.id, org, "a", 1)
    chaves.lance(s, c.id, j.id, org, "cartão amarelo")
    with pytest.raises(SemPermissao):
        chaves.desfazer_inicio(s, c.id, j.id, donos[0])
    chaves.desfazer_inicio(s, c.id, j.id, org)
    s.refresh(j)
    assert j.status == "agendado" and (j.placar_a, j.placar_b) == (0, 0) and j.iniciado_em is None and not j.eventos
    chaves.iniciar(s, c.id, j.id, org)  # dá para iniciar de novo
    assert j.status == "ao_vivo"


def test_nao_sorteia_de_novo_depois_que_um_jogo_comecou(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "eliminatoria")
    chaves.sortear(s, c.id, org, "pontos_corridos")  # ainda dá: nada começou
    assert {j.rodada_nome[:6] for j in _jogos(s, c)} == {"Rodada"}
    chaves.iniciar(s, c.id, _jogos(s, c)[0].id, org)
    with pytest.raises(ErroNegocio, match="Já há jogos"):
        chaves.sortear(s, c.id, org, "eliminatoria")


def _equipes_confirmadas(s, c):
    from playgo.models import Equipe

    return list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == "confirmada").order_by(Equipe.id)))


def test_cabecas_de_chave_na_eliminatoria_ficam_nas_melhores_posicoes_e_com_as_folgas(s, fabrica):
    from playgo.models import ChaveEquipe

    c, org, _ = _torneio(s, fabrica, 6)
    eq = _equipes_confirmadas(s, c)
    cabecas = [eq[4].id, eq[1].id]  # a equipe 5 é a cabeça nº 1 e a 2 é a nº 2
    for _ in range(5):  # o sorteio das demais varia, as posições dos cabeças não
        chaves.sortear(s, c.id, org, "eliminatoria", cabecas=cabecas)
        r1 = [j for j in _jogos(s, c) if j.rodada == 1]
        folgas = {j.vencedor_id for j in r1 if j.folga}
        assert folgas == set(cabecas)  # 6 equipes numa chave de 8: as duas folgas são dos cabeças
        semis = [j for j in _jogos(s, c) if j.rodada == 2]
        assert semis[0].equipe_a_id == cabecas[0] and semis[1].equipe_a_id == cabecas[1]  # nº 1 e nº 2 só se cruzam na final
    marcas = {k.equipe_id: k.cabeca for k in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id))}
    assert marcas == {cabecas[0]: 1, cabecas[1]: 2}
    d = chaves.chaveamento(s, c, org)
    assert [x["ordem"] for x in d["cabecas"]] == [1, 2] and d["cabecas"][0]["equipe"]["id"] == cabecas[0]
    with pytest.raises(ErroNegocio, match="cabeças de chave"):
        chaves.sortear(s, c.id, org, "eliminatoria", cabecas=[cabecas[0], cabecas[0]])  # repetido
    with pytest.raises(ErroNegocio, match="cabeças de chave"):
        chaves.sortear(s, c.id, org, "eliminatoria", cabecas=[999999])  # não é equipe do campeonato


def test_fase_de_grupos_cabecas_em_grupos_diferentes_e_mata_mata_automatico(s, fabrica):
    from playgo.models import ChaveEquipe

    c, org, _ = _torneio(s, fabrica, 9)
    eq = _equipes_confirmadas(s, c)
    cabecas = [eq[0].id, eq[3].id, eq[6].id]
    with pytest.raises(ErroNegocio, match="grupos"):
        chaves.sortear(s, c.id, org, "grupos", grupos=1)
    with pytest.raises(ErroNegocio, match="cabeças de chave do que grupos"):
        chaves.sortear(s, c.id, org, "grupos", grupos=2, cabecas=cabecas)
    with pytest.raises(ErroNegocio, match="Classificam"):
        chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam=4)
    chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam=2, cabecas=cabecas)
    marcas = list(s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id)))
    por_grupo = {}
    for m in marcas:
        por_grupo.setdefault(m.grupo, []).append(m)
    assert sorted(por_grupo) == ["A", "B", "C"] and all(len(v) == 3 for v in por_grupo.values())  # 9 equipes em 3 grupos de 3
    assert [next(m.grupo for m in marcas if m.equipe_id == i) for i in cabecas] == ["A", "B", "C"]  # um cabeça por grupo, na ordem
    jogos = _jogos(s, c)
    assert len(jogos) == 9 and all(j.fase == "grupos" and j.grupo for j in jogos)  # 3 grupos × 3 jogos

    # joga os grupos dando a vitória ao lado A; o mata-mata aparece sozinho no último jogo
    pendentes = [j for j in jogos]
    for j in pendentes[:-1]:
        _disputar(s, c, org, j, 2, 0)
    assert not [x for x in _jogos(s, c) if x.fase == "mata_mata"]
    d = chaves.chaveamento(s, c, org)
    assert d["formato"] == "grupos" and len(d["grupos"]) == 3 and d["rodadas"] == [] and d["jogos_grupos_restantes"] == 1
    _disputar(s, c, org, pendentes[-1], 2, 0)
    mata = [x for x in _jogos(s, c) if x.fase == "mata_mata"]
    assert mata, "o mata-mata deve nascer quando o último jogo de grupo termina"
    classificados = {e for x in mata if x.rodada == 1 for e in (x.equipe_a_id, x.equipe_b_id) if e}
    assert len(classificados) == 6  # 3 grupos × 2 classificados
    d = chaves.chaveamento(s, c, org)
    assert d["classificam"] == 2 and d["jogos_grupos_restantes"] == 0 and len(d["rodadas"]) == 3
    for g in d["grupos"]:
        assert [L["classifica"] for L in g["classificacao"]] == [True, True, False]
    # primeira rodada do mata-mata: ninguém enfrenta quem era do próprio grupo
    grupo_de = {m.equipe_id: m.grupo for m in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id))}
    for x in mata:
        if x.rodada == 1 and x.equipe_a_id and x.equipe_b_id:
            assert grupo_de[x.equipe_a_id] != grupo_de[x.equipe_b_id]
    # empate no jogo do mata-mata pede desempate; no grupo, não
    jm = next(x for x in _jogos(s, c) if x.fase == "mata_mata" and x.equipe_a_id and x.equipe_b_id and x.status == "agendado")
    chaves.iniciar(s, c.id, jm.id, org)
    with pytest.raises(ErroNegocio, match="desempate"):
        chaves.encerrar(s, c.id, jm.id, org)
    chaves.encerrar(s, c.id, jm.id, org, vencedor_id=jm.equipe_a_id)
    # terminar todo o mata-mata fecha o campeonato com campeão
    for rodada in (1, 2, 3):
        for x in [y for y in _jogos(s, c) if y.fase == "mata_mata" and y.rodada == rodada and y.status == "agendado"]:
            _disputar(s, c, org, x, 3, 1)
    s.refresh(c)
    final = max((x for x in _jogos(s, c) if x.fase == "mata_mata"), key=lambda x: x.rodada)
    assert c.status == "encerrado" and chaves.chaveamento(s, c, org)["campeao"]["id"] == final.vencedor_id


def test_grupos_reabrir_apaga_o_mata_mata_se_ainda_nao_comecou(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "grupos", grupos=2, classificam=1)
    jogos = _jogos(s, c)
    assert len(jogos) == 2
    for j in jogos:
        _disputar(s, c, org, j, 1, 0)
    assert [x for x in _jogos(s, c) if x.fase == "mata_mata"]
    chaves.reabrir(s, c.id, jogos[0].id, org)  # mata-mata ainda não começou: é desfeito
    assert not [x for x in _jogos(s, c) if x.fase == "mata_mata"]
    chaves.encerrar(s, c.id, jogos[0].id, org)
    final = [x for x in _jogos(s, c) if x.fase == "mata_mata"][0]
    chaves.iniciar(s, c.id, final.id, org)
    with pytest.raises(ErroNegocio, match="mata-mata já começou"):
        chaves.reabrir(s, c.id, jogos[0].id, org)


def test_sortear_de_novo_funciona_mesmo_com_folgas(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 6)
    chaves.sortear(s, c.id, org, "eliminatoria")  # tem 2 folgas, que já nascem encerradas
    assert chaves.chaveamento(s, c, org)["pode_sortear_de_novo"] is True
    chaves.sortear(s, c.id, org, "eliminatoria")  # não pode travar por causa das folgas
    chaves.sortear(s, c.id, org, "grupos", grupos=2, classificam=2)


def test_agenda_em_lote_marca_um_jogo_depois_do_outro_por_rodada(s, fabrica):
    from datetime import datetime, time

    c, org, donos = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "eliminatoria")
    h = lambda j: j.inicio_previsto.strftime("%d %H:%M") if j.inicio_previsto else None  # noqa: E731
    inicio = datetime(2026, 11, 7, 9, 0)

    r = chaves.agendar_lote(s, c.id, org, "todos", inicio, 40, 10)
    semis = [j for j in _jogos(s, c) if j.rodada == 1]
    final = [j for j in _jogos(s, c) if j.rodada == 2][0]
    assert r["agendados"] == 3 and [h(j) for j in semis] == ["07 09:00", "07 09:50"] and h(final) == "07 10:40"  # cada rodada em horário novo
    assert r["primeiro"] == "2026-11-07T09:00:00" and r["ultimo_fim"] == "2026-11-07T11:20:00"
    s.refresh(c)
    assert c.duracao_jogo_min == 40 and chaves.chaveamento(s, c, org)["duracao_jogo_min"] == 40
    # a capitã de uma equipe é avisada
    assert s.query(Notificacao).filter(Notificacao.usuario_id.in_([d.id for d in donos]), Notificacao.titulo.like("%Horários%")).count() >= 1

    # duas quadras: os jogos da mesma rodada acontecem juntos, uma quadra cada
    chaves.agendar_lote(s, c.id, org, "todos", inicio, 40, 10, locais=["Quadra 01", "Quadra 02"])
    semis = [j for j in _jogos(s, c) if j.rodada == 1]
    assert [(h(j), j.local) for j in semis] == [("07 09:00", "Quadra 01"), ("07 09:00", "Quadra 02")]
    assert h([j for j in _jogos(s, c) if j.rodada == 2][0]) == "07 09:50"

    # horário-limite: o que não cabe no dia segue no dia seguinte, na hora do início
    chaves.agendar_lote(s, c.id, org, "todos", inicio, 40, 10, ate=time(10, 0))
    assert [h(j) for j in _jogos(s, c)] == ["07 09:00", "08 09:00", "09 09:00"]
    with pytest.raises(ErroNegocio, match="horário-limite"):
        chaves.agendar_lote(s, c.id, org, "todos", inicio, 40, 10, ate=time(9, 30))

    # uma rodada só; sem sobrescrever, quem já tem horário fica como está
    chaves.agendar_lote(s, c.id, org, "rodada:2", datetime(2026, 11, 14, 15, 0), 30)
    assert h([j for j in _jogos(s, c) if j.rodada == 2][0]) == "14 15:00" and h(_jogos(s, c)[0]) == "07 09:00"
    with pytest.raises(ErroNegocio, match="Não há jogos"):
        chaves.agendar_lote(s, c.id, org, "todos", inicio, 40, 10, sobrescrever=False)

    # regras: duração, escopo e permissão; jogo que já começou não é remarcado
    for args in (dict(duracao_min=2), dict(duracao_min=40, intervalo_min=-1), dict(duracao_min=40, escopo="xyz"), dict(duracao_min=40, escopo="rodada:abc")):
        with pytest.raises(ErroNegocio):
            chaves.agendar_lote(s, c.id, org, args.pop("escopo", "todos"), inicio, **args)
    with pytest.raises(SemPermissao):
        chaves.agendar_lote(s, c.id, donos[0], "todos", inicio, 40)
    primeiro = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, primeiro.id, org)
    chaves.agendar_lote(s, c.id, org, "todos", datetime(2026, 12, 1, 8, 0), 40)
    assert h(primeiro) == "07 09:00"  # o jogo ao vivo não foi tocado


def test_agenda_em_lote_dos_grupos_e_depois_do_mata_mata(s, fabrica):
    from datetime import datetime

    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "grupos", grupos=2, classificam=1)
    with pytest.raises(ErroNegocio, match="Não há jogos"):
        chaves.agendar_lote(s, c.id, org, "mata_mata", datetime(2026, 11, 7, 9, 0), 40)  # o mata-mata ainda não existe
    chaves.agendar_lote(s, c.id, org, "grupos", datetime(2026, 11, 7, 9, 0), 30, 0, locais=["Q1", "Q2"])
    jogos = _jogos(s, c)
    assert len(jogos) == 2 and {j.local for j in jogos} == {"Q1", "Q2"} and len({j.inicio_previsto for j in jogos}) == 1  # 2 grupos, 1 jogo cada, juntos
    for j in jogos:
        _disputar(s, c, org, j, 1, 0)
    r = chaves.agendar_lote(s, c.id, org, "mata_mata", datetime(2026, 11, 7, 11, 0), 40)
    assert r["agendados"] == 1


def test_volei_nasce_com_pontuacao_por_sets_e_o_resto_com_placar_simples(s, fabrica):
    org = fabrica.atleta("Org")
    hoje = agora().date()
    campo = lambda mod: campeonatos.criar(s, org, NovoCampeonato(modalidade_id=fabrica.mod(mod).id, nome=f"Copa {mod}", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10), max_equipes=8, local_nome="G", latitude=PERTO[0], longitude=PERTO[1]))  # noqa: E731
    v, f = campo("volei"), campo("futsal")
    assert (v.placar_modo, v.sets_melhor_de, v.pontos_set, v.pontos_tiebreak, v.diferenca_set) == ("sets", 3, 25, 15, 2)
    assert chaves.regras_placar(v)["sets_para_vencer"] == 2 and chaves.regras_placar(f)["modo"] == "simples"
    areia = campo("volei_de_areia")
    assert (areia.pontos_set, areia.pontos_tiebreak) == (21, 15)


def test_jogo_por_sets_fecha_cada_set_e_o_jogo_so_encerra_decidido(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 2)
    with pytest.raises(SemPermissao):
        chaves.configurar_placar(s, c.id, fabrica.atleta("Intruso"), "sets")
    with pytest.raises(ErroNegocio, match="1, 3, 5 ou 7"):
        chaves.configurar_placar(s, c.id, org, "sets", melhor_de=4)
    r = chaves.configurar_placar(s, c.id, org, "sets", melhor_de=3, pontos_set=3, pontos_tiebreak=2, diferenca=1)
    assert r["sets_para_vencer"] == 2 and r["pontos_set"] == 3
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    with pytest.raises(ErroNegocio, match="por sets"):
        chaves.definir_placar(s, c.id, j.id, org, 1, 0)
    with pytest.raises(ErroNegocio, match="ainda não terminou"):
        chaves.encerrar(s, c.id, j.id, org)
    for _ in range(3):
        chaves.marcar(s, c.id, j.id, org, "a", 1)  # set 1 para A (3 pontos)
    assert (j.placar_a, j.placar_b) == (1, 0)
    for _ in range(3):
        chaves.marcar(s, c.id, j.id, org, "b", 1)  # set 2 para B
    assert (j.placar_a, j.placar_b) == (1, 1)
    chaves.marcar(s, c.id, j.id, org, "a", 1)
    chaves.marcar(s, c.id, j.id, org, "a", 1)  # set 3 é o decisivo: vai só a 2
    assert (j.placar_a, j.placar_b) == (2, 1)
    with pytest.raises(ErroNegocio, match="já está decidido"):
        chaves.marcar(s, c.id, j.id, org, "b", 1)
    # corrigir com −1 reabre o set decisivo
    chaves.marcar(s, c.id, j.id, org, "a", -1)
    assert (j.placar_a, j.placar_b) == (1, 1)
    with pytest.raises(ErroNegocio, match="ainda não terminou"):
        chaves.encerrar(s, c.id, j.id, org)
    chaves.marcar(s, c.id, j.id, org, "a", 1)
    j = chaves.encerrar(s, c.id, j.id, org)
    assert j.vencedor_id == j.equipe_a_id and j.desempate is False
    d = chaves.detalhe_jogo(s, c, j.id, org)
    assert [(x["a"], x["b"], x["encerrado"]) for x in d["sets"]] == [(3, 0, True), (0, 3, True), (2, 0, True)]
    assert d["regras_placar"]["modo"] == "sets" and "(3-0, 0-3, 2-0)" in d["eventos"][-1]["texto"]
    with pytest.raises(ErroNegocio, match="já há jogos|Já há jogos"):
        chaves.configurar_placar(s, c.id, org, "simples")  # depois de começar não troca


def test_lancar_sets_de_uma_vez_valida_cada_set(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 2)
    chaves.configurar_placar(s, c.id, org, "sets", melhor_de=3, pontos_set=25, pontos_tiebreak=15, diferenca=2)
    chaves.sortear(s, c.id, org, "pontos_corridos")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    for ruim, msg in (([[25, 24]], "não termina assim"), ([[25, 20]], "não decide"), ([[25, 20], [25, 10], [25, 5]], "já estava decidido"), ([[25, 20], [20, 25], [14, 10]], "não termina assim")):
        with pytest.raises(ErroNegocio, match=msg):
            chaves.definir_sets(s, c.id, j.id, org, ruim)
    chaves.definir_sets(s, c.id, j.id, org, [[25, 20], [20, 25], [16, 14]])  # decisivo vai a 15 com 2 de diferença
    assert (j.placar_a, j.placar_b) == (2, 1)
    chaves.encerrar(s, c.id, j.id, org)
    assert j.vencedor_id == j.equipe_a_id


def test_classificacao_por_sets_pontua_3_2_1_0(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 3)
    chaves.configurar_placar(s, c.id, org, "sets", melhor_de=3, pontos_set=25, pontos_tiebreak=15, diferenca=2)
    chaves.sortear(s, c.id, org, "pontos_corridos")
    j1, j2, j3 = _jogos(s, c)
    for j, sets in ((j1, [[25, 10], [25, 12]]), (j2, [[25, 20], [20, 25], [15, 10]]), (j3, [[10, 25], [10, 25]])):
        chaves.iniciar(s, c.id, j.id, org)
        chaves.definir_sets(s, c.id, j.id, org, sets)
        chaves.encerrar(s, c.id, j.id, org)
    tab = chaves.chaveamento(s, c, org)["classificacao"]
    # 2×0 = 3 a 0; 2×1 = 2 para quem ganha e 1 para quem perde no set decisivo
    esperado: dict[int, int] = {}
    for j, (pa, pb) in ((j1, (3, 0)), (j2, (2, 1)), (j3, (0, 3))):
        esperado[j.equipe_a_id] = esperado.get(j.equipe_a_id, 0) + pa
        esperado[j.equipe_b_id] = esperado.get(j.equipe_b_id, 0) + pb
    assert {L["equipe"]["id"]: L["pontos"] for L in tab} == esperado
    assert all("pts_saldo" in L for L in tab) and [L["posicao"] for L in tab] == [1, 2, 3]


def test_api_todos_acompanham_e_so_a_organizacao_conduz(banco):
    """Só pela API/site (sem a sessão de teste aberta): o startup do app altera tabelas e travaria com ela."""
    from playgo.web.app import app

    with TestClient(app) as cli:
        def conta(nome):
            email = f"{uuid.uuid4().hex[:10]}@teste.local"
            r = cli.post("/api/v1/auth/cadastro", json={"nome": nome, "email": email, "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
            return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"], email

        h_org, org_id, org_email = conta("Organizadora")
        h_x, _, x_email = conta("Espectador")
        mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=h_org).json()}
        hoje = agora().date()
        camp = cli.post("/api/v1/campeonatos", headers=h_org, json={"modalidade_id": mods["futsal"], "nome": "Copa", "data_inicio": str(hoje + timedelta(days=20)), "inscricao_ate": str(hoje + timedelta(days=10)), "max_equipes": 8, "local_nome": "G", "latitude": PERTO[0], "longitude": PERTO[1]})
        assert camp.status_code == 201, camp.text
        cid = camp.json()["id"]
        for i in range(4):
            h_cap, _, _ = conta(f"Capitao{i}")
            cli.post(f"/api/v1/campeonatos/{cid}/equipes", headers=h_cap, json={"nome": f"Equipe {i + 1}"})
        for e in cli.get(f"/api/v1/campeonatos/{cid}", headers=h_org).json()["equipes_lista"]:
            assert cli.post(f"/api/v1/equipes/{e['id']}/decidir", headers=h_org, json={"confirmar": True}).status_code == 200

        assert cli.get(f"/api/v1/campeonatos/{cid}/chaves", headers=h_x).json()["rodadas"] == []
        assert cli.post(f"/api/v1/campeonatos/{cid}/sorteio", headers=h_x, json={"formato": "eliminatoria"}).status_code == 403
        d = cli.post(f"/api/v1/campeonatos/{cid}/sorteio", headers=h_org, json={"formato": "eliminatoria"}).json()
        assert len(d["rodadas"]) == 2 and d["pode_gerir"] is True
        d_x = cli.get(f"/api/v1/campeonatos/{cid}/chaves", headers=h_x).json()  # qualquer pessoa logada acompanha
        assert len(d_x["rodadas"]) == 2 and d_x["pode_gerir"] is False and d_x["pode_pontuar"] is False and d_x["mesarios"] == []
        jid = d_x["rodadas"][0]["jogos"][0]["id"]
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{jid}/iniciar", headers=h_x).status_code == 403
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{jid}/iniciar", headers=h_org).json()["status"] == "ao_vivo"
        v = cli.post(f"/api/v1/campeonatos/{cid}/jogos/{jid}/marcar", headers=h_org, json={"lado": "a", "delta": 1}).json()
        assert v["placar_a"] == 1 and v["eventos"][-1]["tipo"] == "placar" and v["pode_pontuar"] is True
        ao_vivo = cli.get(f"/api/v1/campeonatos/{cid}/jogos/{jid}", headers=h_x).json()
        assert ao_vivo["placar_a"] == 1 and ao_vivo["status"] == "ao_vivo" and ao_vivo["pode_pontuar"] is False
        assert cli.get(f"/api/v1/campeonatos/{cid}/chaves", headers=h_x).json()["ao_vivo"][0]["id"] == jid
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{jid}/encerrar", headers=h_org, json={}).json()["status"] == "encerrado"

        # páginas do site: logada acessa todas; o sorteio é só da organização
        assert cli.get(f"/campeonatos/{cid}/chaves", follow_redirects=False).status_code == 303  # sem login
        site = TestClient(app)
        assert site.post("/entrar", data={"email": x_email, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303
        for caminho in ("chaves", "ao-vivo", f"jogos/{jid}"):
            r = site.get(f"/campeonatos/{cid}/{caminho}")
            assert r.status_code == 200 and "chaves.js" in r.text, caminho
        assert site.get(f"/campeonatos/{cid}/sorteio", follow_redirects=False).headers["location"] == f"/campeonatos/{cid}/chaves"
        org_site = TestClient(app)
        org_site.post("/entrar", data={"email": org_email, "senha": "senha-de-teste-1"}, follow_redirects=False)
        assert org_site.get(f"/campeonatos/{cid}/sorteio").status_code == 200 and "Sorteio" in org_site.get(f"/campeonatos/{cid}").text


# ---------------------------------------------------------------- beach tennis em games e Super 8


def _campeonato_de(s, fabrica, org, codigo, n, atletas_por_equipe=5, confirmar=True):
    hoje = agora().date()
    c = campeonatos.criar(
        s, org,
        NovoCampeonato(modalidade_id=fabrica.mod(codigo).id, nome=f"Copa {codigo}", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10),
                       max_equipes=16, atletas_por_equipe=atletas_por_equipe, local_nome="G", latitude=PERTO[0], longitude=PERTO[1]),
    )
    for i in range(n):
        e = campeonatos.inscrever_equipe(s, c.id, fabrica.atleta(f"Atleta{i}"), f"Atleta {i + 1}")
        if confirmar:
            campeonatos.decidir_equipe(s, e.id, org, True)
    return c


def test_beach_tennis_nasce_em_games_e_o_set_fecha_em_6x4_7x5_ou_7x6(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 2)
    assert c.placar_modo == "games"
    r = chaves.regras_placar(c)
    assert r["modo"] == "sets" and r["estilo"] == "games" and r["unidade"] == "games"
    with pytest.raises(ErroNegocio, match="2 a 12"):
        chaves.configurar_placar(s, c.id, org, "games", pontos_set=30)
    chaves.configurar_placar(s, c.id, org, "games", melhor_de=1, pontos_set=6, pontos_tiebreak=10)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    with pytest.raises(ErroNegocio, match="tie-break"):
        chaves.definir_sets(s, c.id, j.id, org, [[8, 6]])  # em games não existe 8×6: 6×6 vai a 7×6
    for _ in range(5):
        chaves.marcar(s, c.id, j.id, org, "a", 1)
    for _ in range(5):
        chaves.marcar(s, c.id, j.id, org, "b", 1)
    chaves.marcar(s, c.id, j.id, org, "a", 1)  # 6×5: ainda não fecha (precisa de 2 de vantagem ou tie-break)
    assert max(s.get(Jogo, j.id).placar_a, s.get(Jogo, j.id).placar_b) == 0
    chaves.marcar(s, c.id, j.id, org, "b", 1)  # 6×6: tie-break
    assert (s.get(Jogo, j.id).placar_a, s.get(Jogo, j.id).placar_b) == (0, 0)
    chaves.marcar(s, c.id, j.id, org, "a", 1)  # 7×6 fecha o set
    assert s.get(Jogo, j.id).placar_a == 1
    chaves.encerrar(s, c.id, j.id, org)


def test_super8_exige_8_atletas_individuais_e_gera_7_rodadas_com_duplas_que_giram(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 7, atletas_por_equipe=1)
    with pytest.raises(ErroNegocio, match="8, 16, 24"):
        chaves.sortear(s, c.id, org, "super8")
    campeonatos.inscrever_equipe(s, c.id, fabrica.atleta("Oitavo"), "Atleta 8")
    for e in s.query(Equipe).filter(Equipe.campeonato_id == c.id, Equipe.status != "confirmada").all():
        campeonatos.decidir_equipe(s, e.id, org, True)
    chaves.sortear(s, c.id, org, "super8")
    jogos = _jogos(s, c)
    assert len(jogos) == 14 and {j.rodada for j in jogos} == set(range(1, 8))
    assert all(j.equipe_a2_id and j.equipe_b2_id for j in jogos)
    # cada atleta joga em todas as rodadas, com cada outro como parceiro 1 vez e como adversário 2 vezes
    from collections import Counter

    parceiros, adversarios = Counter(), Counter()
    for j in jogos:
        parceiros[frozenset((j.equipe_a_id, j.equipe_a2_id))] += 1
        parceiros[frozenset((j.equipe_b_id, j.equipe_b2_id))] += 1
        for x in (j.equipe_a_id, j.equipe_a2_id):
            for y in (j.equipe_b_id, j.equipe_b2_id):
                adversarios[frozenset((x, y))] += 1
    assert len(parceiros) == 28 and set(parceiros.values()) == {1}
    assert len(adversarios) == 28 and set(adversarios.values()) == {2}
    for r in range(1, 8):
        assert len({i for j in jogos if j.rodada == r for i in (j.equipe_a_id, j.equipe_a2_id, j.equipe_b_id, j.equipe_b2_id)}) == 8
    assert (c.placar_modo, c.sets_melhor_de, c.pontos_set) == ("games", 1, 6)
    with pytest.raises(ErroNegocio, match="set único"):
        chaves.configurar_placar(s, c.id, org, "simples")


def test_super8_ranking_individual_por_vitorias_e_saldo_de_games(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 8, atletas_por_equipe=1)
    chaves.sortear(s, c.id, org, "super8")
    jogos = _jogos(s, c)
    # a dupla A vence todos os jogos por 6×3
    for j in jogos:
        chaves.iniciar(s, c.id, j.id, org)
        chaves.definir_sets(s, c.id, j.id, org, [[6, 3]])
        chaves.encerrar(s, c.id, j.id, org)
    tab = chaves.classificacao_super8(_jogos(s, c), list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id))), c)
    assert len(tab) == 8 and sum(L["vitorias"] for L in tab) == 28 and all(L["jogos"] == 7 for L in tab)
    assert sum(L["pro"] for L in tab) == 14 * (6 + 3) * 2 and sum(L["saldo"] for L in tab) == 0  # cada jogo conta 9 games para os 4 atletas
    assert tab[0]["vitorias"] >= tab[-1]["vitorias"]
    d = chaves.chaveamento(s, c, org)
    assert d["formato"] == "super8" and d["campeao"]["id"] == tab[0]["equipe"]["id"] and "+" in d["rodadas"][0]["jogos"][0]["a"]["nome"]
    assert s.get(type(c), c.id).status == "encerrado"


def test_super8_com_duplas_1o_jogo_e_dupla_contra_dupla_depois_mistura(s, fabrica):
    org = fabrica.atleta("Org")
    hoje = agora().date()
    c = campeonatos.criar(
        s, org,
        NovoCampeonato(modalidade_id=fabrica.mod("beach_tennis").id, nome="Super 8 duplas", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10),
                       max_equipes=8, atletas_por_equipe=2, local_nome="G", latitude=PERTO[0], longitude=PERTO[1]),
    )
    duplas = {}
    for i in range(4):
        cap, par = fabrica.atleta(f"Cap{i}"), fabrica.atleta(f"Par{i}")
        e = campeonatos.inscrever_equipe(s, c.id, cap, f"Dupla {i + 1}", convidados=[par.id])
        campeonatos.responder_convite(s, e.id, par, True)
        campeonatos.decidir_equipe(s, e.id, org, True)
        duplas[e.id] = {cap.id, par.id}
    chaves.sortear(s, c.id, org, "super8")
    jogos = _jogos(s, c)
    assert len(jogos) == 14
    atletas = {a.id: a.capitao_id for a in s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == "atleta"))}
    assert len(atletas) == 8
    # rodada 1: cada lado é exatamente uma dupla inscrita
    for j in (x for x in jogos if x.rodada == 1):
        for a, b in ((j.equipe_a_id, j.equipe_a2_id), (j.equipe_b_id, j.equipe_b2_id)):
            assert {atletas[a], atletas[b]} in duplas.values()
    # nas demais rodadas nenhuma dupla inscrita se repete como parceria
    for j in (x for x in jogos if x.rodada > 1):
        for a, b in ((j.equipe_a_id, j.equipe_a2_id), (j.equipe_b_id, j.equipe_b2_id)):
            assert {atletas[a], atletas[b]} not in duplas.values()
    # as duplas inscritas continuam as únicas inscrições; os atletas não aparecem como inscrição
    assert campeonatos.equipes_ativas(s, c) == 4
    # sortear de novo troca os atletas, sem acumular
    chaves.sortear(s, c.id, org, "super8")
    assert s.query(Equipe).filter(Equipe.campeonato_id == c.id, Equipe.status == "atleta").count() == 8
    d = chaves.chaveamento(s, c, org)
    tab = d["ranking_super8"][0]["classificacao"]
    assert len(tab) == 8 and tab[0]["equipe"]["nome"].startswith("@")


def test_super8_com_8_duplas_do_elenco_faz_2_grupos_e_a_rodada_1_e_a_dupla_inscrita(s, fabrica):
    from playgo import elenco

    org = fabrica.atleta("Org")
    hoje = agora().date()
    c = campeonatos.criar(
        s, org,
        NovoCampeonato(modalidade_id=fabrica.mod("beach_tennis").id, nome="Super 8 elenco", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10),
                       max_equipes=16, atletas_por_equipe=2, local_nome="G", latitude=PERTO[0], longitude=PERTO[1]),
    )
    campeonatos.editar(s, c.id, org, cadastro_elenco=True)
    inscritas = {}
    for i in range(8):
        cap = fabrica.atleta(f"Cap{i}")
        e = campeonatos.inscrever_equipe(s, c.id, cap, f"{i + 1}")
        elenco.adicionar(s, e.id, cap, f"Atleta {i + 1}A", f"RG-{i}-1")
        elenco.adicionar(s, e.id, cap, f"Atleta {i + 1}B", f"RG-{i}-2")
        if i < 7:
            campeonatos.decidir_equipe(s, e.id, org, True)
        inscritas[e.id] = {f"Atleta {i + 1}A", f"Atleta {i + 1}B"}
    ultima = max(inscritas)
    with pytest.raises(ErroNegocio, match="4, 8, 12"):
        chaves.sortear(s, c.id, org, "super8")  # 7 confirmadas
    campeonatos.decidir_equipe(s, ultima, org, True)
    chaves.sortear(s, c.id, org, "super8")
    jogos = _jogos(s, c)
    assert len(jogos) == 28 and {j.grupo for j in jogos} == {"A", "B"}
    nomes = {a.id: a.nome for a in s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == "atleta"))}
    assert len(nomes) == 16
    for j in (x for x in jogos if x.rodada == 1):
        for a, b in ((j.equipe_a_id, j.equipe_a2_id), (j.equipe_b_id, j.equipe_b2_id)):
            assert {nomes[a], nomes[b]} in inscritas.values()  # a dupla inscrita joga junta no 1º jogo
    d = chaves.chaveamento(s, c, org)
    assert [g["grupo"] for g in d["ranking_super8"]] == ["A", "B"] and all(len(g["classificacao"]) == 8 for g in d["ranking_super8"])
    assert d["campeao"] is None


def test_super8_total_fixo_de_5_games_todos_sao_jogados_e_cada_game_vale_ponto(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 8, atletas_por_equipe=1)
    with pytest.raises(ErroNegocio, match="1 a 20"):
        chaves.configurar_placar(s, c.id, org, "total", melhor_de=1, pontos_set=30)
    r = chaves.configurar_placar(s, c.id, org, "total", melhor_de=1, pontos_set=5)
    assert r["estilo"] == "total" and r["total_games"] == 5 and r["modo"] == "simples"
    chaves.sortear(s, c.id, org, "super8")
    assert c.placar_modo == "total" and c.pontos_set == 5  # o sorteio respeita a escolha
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    for _ in range(3):
        chaves.marcar(s, c.id, j.id, org, "a", 1)
    chaves.marcar(s, c.id, j.id, org, "b", 1)
    with pytest.raises(ErroNegocio, match="faltam 1"):
        chaves.encerrar(s, c.id, j.id, org)
    chaves.marcar(s, c.id, j.id, org, "b", 1)  # 3×2: os 5 games foram jogados
    with pytest.raises(ErroNegocio, match="já foram disputados"):
        chaves.marcar(s, c.id, j.id, org, "a", 1)
    with pytest.raises(ErroNegocio, match="passa disso"):
        chaves.definir_placar(s, c.id, j.id, org, 4, 3)
    chaves.encerrar(s, c.id, j.id, org)
    tab = chaves.classificacao_super8(_jogos(s, c), list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id))), c)
    por_nome = {L["equipe"]["id"]: L for L in tab}
    j = s.get(Jogo, j.id)
    for eid in (j.equipe_a_id, j.equipe_a2_id):
        assert por_nome[eid]["pontos"] == 3 and por_nome[eid]["vitorias"] == 1
    for eid in (j.equipe_b_id, j.equipe_b2_id):
        assert por_nome[eid]["pontos"] == 2 and por_nome[eid]["derrotas"] == 1
    assert tab[0]["pontos"] == 3  # o ranking é por games feitos


def _pontos(s, c, org, j, lado, n):
    for _ in range(n):
        chaves.marcar_ponto(s, c.id, j.id, org, lado, 1)
    return s.get(Jogo, j.id)


def test_ponto_a_ponto_15_30_40_e_ponto_de_ouro(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 2)
    chaves.configurar_placar(s, c.id, org, "games", melhor_de=1, pontos_set=6, pontos_tiebreak=10)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    with pytest.raises(ErroNegocio, match="ao vivo"):
        chaves.marcar_ponto(s, c.id, j.id, org, "a", 1)
    chaves.iniciar(s, c.id, j.id, org)
    jj = _pontos(s, c, org, j, "a", 3)  # 40–0
    assert (jj.ponto_a, jj.ponto_b) == (3, 0) and chaves.texto_do_ponto(jj.ponto_a) == "40"
    jj = _pontos(s, c, org, j, "b", 3)  # 40–40: ponto de ouro, ninguém tem vantagem
    assert (jj.ponto_a, jj.ponto_b) == (3, 3)
    chaves.marcar_ponto(s, c.id, j.id, org, "b", -1)  # corrige o último ponto
    assert s.get(Jogo, j.id).ponto_b == 2
    chaves.marcar_ponto(s, c.id, j.id, org, "b", 1)
    jj = _pontos(s, c, org, j, "b", 1)  # o ponto de ouro fecha o game para B
    sets = _sets_deste(s, j)
    assert (jj.ponto_a, jj.ponto_b) == (0, 0) and (sets[0].pontos_a, sets[0].pontos_b) == (0, 1)
    # game fechado por 4 pontos seguidos
    _pontos(s, c, org, j, "a", 4)
    assert (_sets_deste(s, j)[0].pontos_a, _sets_deste(s, j)[0].pontos_b) == (1, 1)


def _sets_deste(s, j):
    from playgo.models import JogoSet

    return list(s.scalars(select(JogoSet).where(JogoSet.jogo_id == j.id).order_by(JogoSet.numero).execution_options(populate_existing=True)))


def test_ponto_a_ponto_tie_break_no_6x6(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 2)
    chaves.configurar_placar(s, c.id, org, "games", melhor_de=1, pontos_set=2, pontos_tiebreak=10)  # set curto de 2 games para o teste
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    for lado in ("a", "b", "a", "b"):  # 1×1 e depois 2×2 não, 2 games por set: vai a 2×2
        _pontos(s, c, org, j, lado, 4)
    st = _sets_deste(s, j)[0]
    assert (st.pontos_a, st.pontos_b) == (2, 2)  # N×N: tie-break
    _pontos(s, c, org, j, "a", 6)
    assert not _sets_deste(s, j)[0].encerrado and s.get(Jogo, j.id).ponto_a == 6
    _pontos(s, c, org, j, "b", 6)  # 6–6 no tie-break: continua
    _pontos(s, c, org, j, "a", 1)  # 7–6: ainda sem 2 de vantagem
    assert not _sets_deste(s, j)[0].encerrado
    _pontos(s, c, org, j, "a", 1)  # 8–6 fecha o tie-break: 3×2 e o set termina
    st = _sets_deste(s, j)[0]
    assert (st.pontos_a, st.pontos_b) == (3, 2) and st.encerrado and s.get(Jogo, j.id).placar_a == 1


def test_ponto_a_ponto_no_total_fixo_e_so_em_games(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "futsal", 2)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    with pytest.raises(ErroNegocio, match="não conta os pontos"):
        chaves.marcar_ponto(s, c.id, j.id, org, "a", 1)
    c2 = _campeonato_de(s, fabrica, org, "beach_tennis", 2)
    chaves.configurar_placar(s, c2.id, org, "total", melhor_de=1, pontos_set=2)
    chaves.sortear(s, c2.id, org, "eliminatoria")
    j2 = _jogos(s, c2)[0]
    chaves.iniciar(s, c2.id, j2.id, org)
    _pontos(s, c2, org, j2, "a", 4)
    _pontos(s, c2, org, j2, "b", 4)
    jj = s.get(Jogo, j2.id)
    assert (jj.placar_a, jj.placar_b) == (1, 1)
    with pytest.raises(ErroNegocio, match="já foram disputados"):
        chaves.marcar_ponto(s, c2.id, j2.id, org, "a", 1)
    chaves.encerrar(s, c2.id, j2.id, org, vencedor_id=jj.equipe_a_id)  # 1×1 num mata-mata: desempate pela organização


def test_ao_vivo_guarda_o_resultado_de_cada_game_e_a_correcao_desfaz(s, fabrica):
    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 2)
    chaves.configurar_placar(s, c.id, org, "games", melhor_de=1, pontos_set=6, pontos_tiebreak=10)
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    chaves.iniciar(s, c.id, j.id, org)
    _pontos(s, c, org, j, "a", 4)  # A fecha 4×0
    _pontos(s, c, org, j, "a", 2)
    _pontos(s, c, org, j, "b", 3)
    _pontos(s, c, org, j, "a", 1)  # 3×3: ponto de ouro
    _pontos(s, c, org, j, "a", 1)  # A fecha 4×3
    chaves.marcar(s, c.id, j.id, org, "b", 1)  # game lançado à mão para B
    jogo = s.get(Jogo, j.id)
    s.refresh(jogo)
    d = chaves.jogo_dict(jogo)
    assert [(g["numero"], g["lado"], g["a"], g["b"], g["manual"]) for g in d["games"]] == [(1, "a", 4, 0, False), (2, "a", 4, 3, False), (3, "b", None, None, True)]
    assert d["ponto_a"] == 0 and d["ponto_b"] == 0
    chaves.marcar(s, c.id, j.id, org, "b", -1)  # tira o game lançado à mão: some da lista
    s.refresh(s.get(Jogo, j.id))
    assert [g["numero"] for g in chaves.jogo_dict(s.get(Jogo, j.id))["games"]] == [1, 2]
    chaves.marcar_ponto(s, c.id, j.id, org, "a", -1)  # sem pontos no game: corrige o último game do A
    s.refresh(s.get(Jogo, j.id))
    assert [g["numero"] for g in chaves.jogo_dict(s.get(Jogo, j.id))["games"]] == [1]
    sets = _sets_deste(s, j)
    assert (sets[0].pontos_a, sets[0].pontos_b) == (1, 0)


def test_grupos_com_os_8_melhores_no_geral_cruzam_1x8_2x7_3x6_4x5(s, fabrica):
    from playgo.models import ChaveEquipe

    c, org, _ = _torneio(s, fabrica, 9)
    with pytest.raises(ErroNegocio, match="no geral"):
        chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam_geral=10)
    chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam_geral=8)
    assert c.classificam_geral == 8 and c.classificam is None
    jogos = _jogos(s, c)
    # vitórias diferentes por jogo para a tabela geral ter ordem própria: lado A ganha por 2x0 (3 pts) em todos
    for j in jogos:
        _disputar(s, c, org, j, 2, 0)
    d = chaves.chaveamento(s, c, org)
    geral = d["classificacao_geral"]
    assert len(geral) == 9 and [L["classifica"] for L in geral] == [True] * 8 + [False]
    ordem = [L["equipe"]["id"] for L in geral[:8]]
    mata = [x for x in _jogos(s, c) if x.fase == "mata_mata" and x.rodada == 1]
    assert len(mata) == 4
    pares = {frozenset((x.equipe_a_id, x.equipe_b_id)) for x in mata}
    assert pares == {frozenset((ordem[0], ordem[7])), frozenset((ordem[1], ordem[6])), frozenset((ordem[2], ordem[5])), frozenset((ordem[3], ordem[4]))}
    # jogos na ordem do regulamento: 1x8, 2x7, 3x6, 4x5 (jogos 13 a 16)
    por_pos = {x.posicao: x for x in mata}
    assert [{por_pos[i].equipe_a_id, por_pos[i].equipe_b_id} for i in range(4)] == [{ordem[0], ordem[7]}, {ordem[1], ordem[6]}, {ordem[2], ordem[5]}, {ordem[3], ordem[4]}]
    # semifinais: vencedor do jogo 13 (1x8) x vencedor do 16 (4x5); vencedor do 14 (2x7) x vencedor do 15 (3x6)
    assert por_pos[0].proximo_id == por_pos[3].proximo_id and por_pos[1].proximo_id == por_pos[2].proximo_id and por_pos[0].proximo_id != por_pos[1].proximo_id
    assert (por_pos[0].proximo_lado, por_pos[3].proximo_lado) == ("a", "b")
    for i, vencedor in ((0, ordem[0]), (1, ordem[1]), (2, ordem[2]), (3, ordem[3])):
        _disputar(s, c, org, por_pos[i], 2, 0)
    semis = [x for x in _jogos(s, c) if x.fase == "mata_mata" and x.rodada == 2]
    assert [{x.equipe_a_id, x.equipe_b_id} for x in semis] == [{ordem[0], ordem[3]}, {ordem[1], ordem[2]}]
    # a equipe de fora não joga o mata-mata, e as linhas dos grupos marcam só as 8 do geral
    fora = geral[8]["equipe"]["id"]
    assert fora not in {e for x in mata for e in (x.equipe_a_id, x.equipe_b_id)}
    assert sum(1 for g in d["grupos"] for L in g["classificacao"] if L["classifica"]) == 8
    assert s.scalar(select(ChaveEquipe.grupo).where(ChaveEquipe.equipe_id == fora)) in ("A", "B", "C")


def test_trocar_equipes_de_grupo_antes_dos_jogos_e_travar_depois(s, fabrica):
    from playgo.models import ChaveEquipe

    c, org, donos = _torneio(s, fabrica, 6)
    chaves.sortear(s, c.id, org, "grupos", grupos=2, classificam=2)
    grupo_de = lambda i: s.scalar(select(ChaveEquipe.grupo).where(ChaveEquipe.equipe_id == i))  # noqa: E731
    a_id = next(m.equipe_id for m in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id)) if m.grupo == "A")
    b_id = next(m.equipe_id for m in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id)) if m.grupo == "B")
    with pytest.raises(SemPermissao):
        chaves.trocar_equipes(s, c.id, donos[0], a_id, b_id)
    with pytest.raises(ErroNegocio, match="diferentes"):
        chaves.trocar_equipes(s, c.id, org, a_id, a_id)
    chaves.trocar_equipes(s, c.id, org, a_id, b_id)
    assert grupo_de(a_id) == "B" and grupo_de(b_id) == "A"
    for j in _jogos(s, c):
        assert j.grupo == ("A" if b_id in (j.equipe_a_id, j.equipe_b_id) else "B") or a_id not in (j.equipe_a_id, j.equipe_b_id) and b_id not in (j.equipe_a_id, j.equipe_b_id)
    d = chaves.chaveamento(s, c, org)
    assert {L["equipe"]["id"] for g in d["grupos"] if g["grupo"] == "B" for L in g["classificacao"]} >= {a_id}
    # depois que um jogo da equipe começa, não dá mais para trocar
    jogo = next(j for j in _jogos(s, c) if a_id in (j.equipe_a_id, j.equipe_b_id))
    chaves.iniciar(s, c.id, jogo.id, org)
    with pytest.raises(ErroNegocio, match="já tem jogo"):
        chaves.trocar_equipes(s, c.id, org, a_id, b_id)


def test_trocar_equipe_do_sorteio_por_outra_confirmada_que_ficou_de_fora(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    novo = campeonatos.inscrever_equipe(s, c.id, fabrica.atleta("CapNovo"), "Equipe Nova")  # ainda pendente na hora do sorteio
    chaves.sortear(s, c.id, org, "eliminatoria")
    campeonatos.decidir_equipe(s, novo.id, org, True)
    dentro = _jogos(s, c)[0].equipe_a_id
    chaves.trocar_equipes(s, c.id, org, novo.id, dentro)
    ids = {e for j in _jogos(s, c) for e in (j.equipe_a_id, j.equipe_b_id)}
    assert novo.id in ids and dentro not in ids
    assert {e["id"]: e["no_sorteio"] for e in chaves.chaveamento(s, c, org)["equipes_lugar"]}[novo.id] is True


def test_gestao_lista_campeonatos_em_qualquer_situacao_e_encerrados_continua_o_padrao(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 2)  # criado e nunca encerrado
    adm = fabrica.atleta("AdminListagem")
    adm.admin = True
    s.flush()
    todos = campeonatos.encerrados(s, adm, situacao="todos", limite=500)
    assert c.id in {x["id"] for x in todos["itens"]} and all("status" in x for x in todos["itens"])
    assert c.id not in {x["id"] for x in campeonatos.encerrados(s, adm, limite=500)["itens"]}  # o padrão segue só encerrados
    assert c.id in {x["id"] for x in campeonatos.encerrados(s, adm, situacao=c.status, limite=500)["itens"]}


def _grupos_de_nove(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 9)
    chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam=3)  # por engano: as 9 avançam
    return c, org


def test_classificacao_geral_aparece_sempre_e_da_para_trocar_quem_avanca(s, fabrica):
    c, org = _grupos_de_nove(s, fabrica)
    d = chaves.chaveamento(s, c, org)
    assert len(d["classificacao_geral"]) == 9 and d["pode_mudar_classificacao"]
    for j in [j for j in _jogos(s, c) if j.fase == "grupos"]:
        _disputar(s, c, org, j, 2, 0)
    assert {j.rodada_nome for j in _jogos(s, c) if j.fase == "mata_mata"} >= {"Oitavas de final"} or any(j.fase == "mata_mata" for j in _jogos(s, c))
    chaves.definir_classificacao(s, c.id, org, None, 8)
    mata = [j for j in _jogos(s, c) if j.fase == "mata_mata"]
    assert {j.rodada_nome for j in mata if j.rodada == min(x.rodada for x in mata)} == {"Quartas de final"}
    d = chaves.chaveamento(s, c, org)
    assert sum(1 for L in d["classificacao_geral"] if L["classifica"]) == 8
    with pytest.raises(SemPermissao):
        chaves.definir_classificacao(s, c.id, fabrica.atleta("Intruso"), None, 8)
    chaves.iniciar(s, c.id, next(j for j in mata if j.equipe_a_id and j.equipe_b_id).id, org)
    with pytest.raises(ErroNegocio, match="já começou"):
        chaves.definir_classificacao(s, c.id, org, None, 6)


def test_telao_mostra_classificacao_so_quando_ligado(s, fabrica):
    from playgo import telao

    c, org = _grupos_de_nove(s, fabrica)
    assert telao.painel(s, c, org)["tem_classificacao"] and telao._montar(s, c)["classificacao"] is None
    telao.mostrar_classificacao(s, c.id, org, True)
    cl = telao._montar(s, c)["classificacao"]
    assert cl["titulo"] == "Classificação geral" and len(cl["linhas"]) == 9


def test_telao_classificacao_traz_pontos_pro_contra_e_saldo(s, fabrica):
    from playgo import telao

    c, org = _grupos_de_nove(s, fabrica)
    telao.mostrar_classificacao(s, c.id, org, True)
    L = telao._montar(s, c)["classificacao"]["linhas"][0]
    assert {"derrotas", "pts_pro", "pts_contra", "pts_saldo", "saldo"} <= set(L)


def test_espectador_recebe_a_classificacao_mesmo_desligada_no_telao(s, fabrica):
    from playgo import telao

    c, org = _grupos_de_nove(s, fabrica)
    assert telao._montar(s, c)["classificacao"] is None  # link da TV: segue a organização
    d = telao.dados_espectador(s, c)
    assert d["classificacao"] and d["classificacao_org"] is False


def test_telao_classificacao_em_qualquer_formato(s, fabrica):
    from playgo import telao

    c, org, _ = _torneio(s, fabrica, 4)
    assert telao.painel(s, c, org)["tem_classificacao"] and telao.mostrar_classificacao(s, c.id, org, True)["tem_classificacao"]
    assert telao._montar(s, c)["classificacao"] is None  # sem sorteio ainda
    chaves.sortear(s, c.id, org, "eliminatoria")
    j = _jogos(s, c)[0]
    _disputar(s, c, org, j, 3, 1)
    cl = telao._montar(s, c)["classificacao"]
    assert cl["titulo"] == "Campanha das equipes" and cl["linhas"][0]["vitorias"] == 1 and cl["linhas"][0]["pro"] == 3


def test_telao_classificacao_do_super8(s, fabrica):
    from playgo import telao

    org = fabrica.atleta("Org")
    c = _campeonato_de(s, fabrica, org, "beach_tennis", 8, atletas_por_equipe=1)
    chaves.sortear(s, c.id, org, "super8")
    cl = chaves.classificacao_resumo(s, c)
    assert cl["titulo"] == "Ranking" and len(cl["linhas"]) == 8


def test_grupos_montados_a_mao(s, fabrica):
    from playgo.models import ChaveEquipe

    c, org, _ = _torneio(s, fabrica, 6)
    eq = _equipes_confirmadas(s, c)
    ids = [e.id for e in eq]
    with pytest.raises(ErroNegocio, match="pelo menos 2"):
        chaves.sortear(s, c.id, org, "grupos", grupos_manual=[ids[:4], [ids[4]]])
    with pytest.raises(ErroNegocio, match="único grupo"):
        chaves.sortear(s, c.id, org, "grupos", grupos_manual=[ids[:3], ids[2:5]])
    chaves.sortear(s, c.id, org, "grupos", classificam=2, grupos_manual=[[ids[5], ids[0], ids[3]], [ids[1], ids[2]]])  # a equipe 5 fica de fora
    marcas = {m.equipe_id: m.grupo for m in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id))}
    assert marcas == {ids[5]: "A", ids[0]: "A", ids[3]: "A", ids[1]: "B", ids[2]: "B"} and ids[4] not in marcas
    assert len(_jogos(s, c)) == 3 + 1


def test_chave_manual_define_quem_joga_com_quem_e_preenche_os_colocados(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 9)
    ids = [e.id for e in _equipes_confirmadas(s, c)]
    chaves.sortear(s, c.id, org, "grupos", classificam_geral=8, grupos_manual=[ids[0:3], ids[3:6], ids[6:9]])
    texto = "13: 1 x 8\n14: 2 x 7\n15: 3 x 6\n16: 4 x 5\n17: V13 x V16\n18: V14 x V15\n19: V17 x V18"
    for ruim, msg in [("13: 1 x 8\n13: 2 x 7", "mesmo número"), ("1: 1 x 1\n2: V1 x 2", "mais de uma vez"), ("1: 1 x 2\n2: 3 x 4", "única final"), ("1: V9 x 2", "não existe"), ("1: 1 x 20", "colocado geral"), ("sem formato", "Linha 1"), ("1: Fantasma x 2", "não é uma equipe")]:
        with pytest.raises(ErroNegocio, match=msg):
            chaves.definir_chave_manual(s, c.id, org, ruim)
    with pytest.raises(SemPermissao):
        chaves.definir_chave_manual(s, c.id, fabrica.atleta("Intruso"), texto)
    chaves.definir_chave_manual(s, c.id, org, texto)
    mata = {j.numero: j for j in _jogos(s, c) if j.fase == "mata_mata"}
    assert sorted(mata) == [13, 14, 15, 16, 17, 18, 19]
    assert [mata[n].rodada_nome for n in (13, 17, 19)] == ["Quartas de final", "Semifinal", "Final"]
    assert mata[13].origem_a == "1º colocado geral" and mata[13].origem_b == "8º colocado geral" and mata[17].origem_a == "Vencedor do jogo 13"
    assert mata[13].equipe_a_id is None  # ainda não há colocados
    assert (mata[13].proximo_id, mata[13].proximo_lado) == (mata[17].id, "a") and (mata[16].proximo_id, mata[16].proximo_lado) == (mata[17].id, "b")
    assert (mata[14].proximo_id, mata[15].proximo_id) == (mata[18].id, mata[18].id) and mata[17].proximo_id == mata[19].id
    d = chaves.chaveamento(s, c, org)
    assert d["tem_chave_manual"] and d["chave_manual"] == texto and [r["nome"] for r in d["rodadas"]] == ["Quartas de final", "Semifinal", "Final"]
    assert d["rodadas"][0]["jogos"][0]["numero"] == 13 and d["jogos_grupos_restantes"] == 9

    for j in [x for x in _jogos(s, c) if x.fase == "grupos"]:
        _disputar(s, c, org, j, 2, 0)
    ordem = [L["equipe"]["id"] for L in chaves.chaveamento(s, c, org)["classificacao_geral"]]
    mata = {j.numero: j for j in _jogos(s, c) if j.fase == "mata_mata"}
    assert [(mata[n].equipe_a_id, mata[n].equipe_b_id) for n in (13, 14, 15, 16)] == [(ordem[0], ordem[7]), (ordem[1], ordem[6]), (ordem[2], ordem[5]), (ordem[3], ordem[4])]
    for n in (13, 14, 15, 16, 17, 18, 19):
        j = next(x for x in _jogos(s, c) if x.numero == n)
        _disputar(s, c, org, j, 3, 1)
    s.refresh(c)
    final = next(x for x in _jogos(s, c) if x.numero == 19)
    semi = {x.numero: x for x in _jogos(s, c)}
    assert semi[17].equipe_a_id == semi[13].vencedor_id and semi[17].equipe_b_id == semi[16].vencedor_id and c.status == "encerrado"
    assert chaves.chaveamento(s, c, org)["campeao"]["id"] == final.vencedor_id
    # texto vazio volta ao cruzamento automático (os grupos já acabaram, então o mata-mata automático nasce)
    with pytest.raises(ErroNegocio, match="já começou"):
        chaves.definir_chave_manual(s, c.id, org, "")


def test_chave_manual_na_eliminatoria_por_nome_de_equipe(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "eliminatoria")
    chaves.definir_chave_manual(s, c.id, org, "1: Equipe 1 x Equipe 4\n2: equipe 2 x EQUIPE 3\n3: V1 x V2")
    jogos = _jogos(s, c)
    assert [(j.numero, j.rodada_nome) for j in jogos] == [(1, "Semifinal"), (2, "Semifinal"), (3, "Final")]
    assert {j.equipe_a.nome for j in jogos[:2]} == {"Equipe 1", "Equipe 2"}
    with pytest.raises(ErroNegocio, match="só existe na fase de grupos"):
        chaves.definir_chave_manual(s, c.id, org, "1: 1 x 2")


def test_melhores_terceiros_avancam_e_entram_na_chave_manual(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 12)
    ids = [e.id for e in _equipes_confirmadas(s, c)]
    with pytest.raises(ErroNegocio, match="um por grupo"):
        chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam=2, classificam_melhores=4)
    chaves.sortear(s, c.id, org, "grupos", classificam=2, classificam_melhores=2, grupos_manual=[ids[0:4], ids[4:8], ids[8:12]])
    d = chaves.chaveamento(s, c, org)
    assert d["classificam_melhores"] == 2
    texto = "13: 1A x 3T2\n14: 1B x 3T1\n15: 1C x 2C\n16: 2A x 2B\n17: V13 x V16\n18: V14 x V15\n19: V17 x V18"
    chaves.definir_chave_manual(s, c.id, org, texto)
    c = s.get(type(c), c.id)
    assert c.classificam == 2 and c.classificam_melhores == 2
    for j in [x for x in _jogos(s, c) if x.fase == "grupos"]:
        _disputar(s, c, org, j, 2, 0)
    d = chaves.chaveamento(s, c, org)
    terceiros = [L["equipe"]["id"] for L in d["classificacao_geral"] if next(g for g in d["grupos"] if any(x["equipe"]["id"] == L["equipe"]["id"] for x in g["classificacao"]))["classificacao"][2]["equipe"]["id"] == L["equipe"]["id"]]
    mata = {j.numero: j for j in _jogos(s, c) if j.fase == "mata_mata"}
    assert mata[13].equipe_b_id == terceiros[1] and mata[14].equipe_b_id == terceiros[0]
    assert sum(1 for g in d["grupos"] for L in g["classificacao"] if L["classifica"]) == 8  # 6 diretos + 2 melhores terceiros
    assert sum(1 for L in d["classificacao_geral"] if L["classifica"]) == 8
    with pytest.raises(ErroNegocio, match="não existe"):
        chaves.definir_chave_manual(s, c.id, org, "1: 3T9 x 1A")


def test_melhores_terceiros_no_mata_mata_automatico(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 12)
    chaves.sortear(s, c.id, org, "grupos", grupos=3, classificam=2, classificam_melhores=2)
    for j in [x for x in _jogos(s, c) if x.fase == "grupos"]:
        _disputar(s, c, org, j, 2, 0)
    mata = [j for j in _jogos(s, c) if j.fase == "mata_mata" and j.rodada == 1]
    assert len(mata) == 4 and len({e for j in mata for e in (j.equipe_a_id, j.equipe_b_id)}) == 8


def test_disputa_de_terceiro_lugar_na_chave_manual(s, fabrica):
    c, org, _ = _torneio(s, fabrica, 4)
    chaves.sortear(s, c.id, org, "eliminatoria")
    with pytest.raises(ErroNegocio, match="só pode seguir"):
        chaves.definir_chave_manual(s, c.id, org, "1: Equipe 1 x Equipe 4\n2: Equipe 2 x Equipe 3\n3: V1 x V2\n4: P1 x P1")
    chaves.definir_chave_manual(s, c.id, org, "1: Equipe 1 x Equipe 4\n2: Equipe 2 x Equipe 3\n3: V1 x V2\n4: P1 x P2")
    j = {x.numero: x for x in _jogos(s, c)}
    assert j[4].rodada_nome == "Disputa de 3º lugar" and j[4].rodada == j[3].rodada and j[3].posicao < j[4].posicao
    _disputar(s, c, org, j[1], 3, 1)
    _disputar(s, c, org, j[2], 0, 2)
    s.refresh(j[4])
    assert (j[4].equipe_a_id, j[4].equipe_b_id) == (j[1].equipe_b_id, j[2].equipe_a_id)
    _disputar(s, c, org, j[3], 2, 1)
    s.refresh(c)
    assert c.status == "em_andamento"  # falta o 3º lugar
    _disputar(s, c, org, j[4], 1, 0)
    s.refresh(c)
    assert c.status == "encerrado" and chaves.chaveamento(s, c, org)["campeao"]["id"] == j[3].vencedor_id
