"""Busca de atletas (RF-015/016), descoberta (RF-003/010/011), agenda e divulgação de horários (RF-006/024)."""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from playgo import arenas, atividades, descoberta, match
from playgo.atividades import NovaAtividade
from playgo.db import agora
from playgo.erros import ErroNegocio, SemPermissao
from playgo.models import Notificacao

# ~1,8 km ao norte do centro; ~40 km ao sul (fora de qualquer raio)
PERTO = (-20.4535, -54.6201)
LONGE = (-20.83, -54.62)


def _jogo(s, fabrica, org, codigo="volei", em_horas=2, lat=PERTO[0], lng=PERTO[1], **kw):
    return atividades.criar(
        s, org,
        NovaAtividade(modalidade_id=fabrica.mod(codigo).id, nome="Jogo", inicio=agora() + timedelta(hours=em_horas), max_participantes=6, local_nome="Quadra", latitude=lat, longitude=lng, **kw),
    )


def test_compativel_exige_esporte_nivel_e_distancia(s, fabrica):
    org = fabrica.atleta("Org", esportes=(("volei", "intermediario"),))
    a = _jogo(s, fabrica, org, nivel="avancado")
    mesmo_nivel = fabrica.atleta("Avancado", esportes=(("volei", "avancado"),))
    um_degrau = fabrica.atleta("Inter", esportes=(("volei", "intermediario"),))
    dois_degraus = fabrica.atleta("Iniciante", esportes=(("volei", "iniciante"),))
    outro_esporte = fabrica.atleta("Nadador", esportes=(("futebol", "avancado"),))
    longe = fabrica.atleta("Longe", esportes=(("volei", "avancado"),), lat=LONGE[0], lng=LONGE[1])

    achados = {u.id: nota for u, _, nota in match.candidatos(s, a)}
    assert mesmo_nivel.id in achados and um_degrau.id in achados
    assert achados[mesmo_nivel.id] > achados[um_degrau.id]  # nível exato vale mais
    assert dois_degraus.id not in achados
    assert outro_esporte.id not in achados
    assert longe.id not in achados


def test_disponibilidade_filtra_o_periodo(s, fabrica):
    org = fabrica.atleta("Org", esportes=(("volei", "intermediario"),))
    a = _jogo(s, fabrica, org, em_horas=2)
    periodo = next(iter(match.periodos_do(a.inicio - timedelta(days=0))))
    outro = "manha" if periodo != "manha" else "noite"
    so_do_periodo = fabrica.atleta("Livre", esportes=(("volei", "intermediario"),), disponibilidade=[periodo])
    so_de_outro = fabrica.atleta("Ocupado", esportes=(("volei", "intermediario"),), disponibilidade=[outro])
    achados = {u.id for u, _, _ in match.candidatos(s, a)}
    assert so_do_periodo.id in achados
    assert so_de_outro.id not in achados


def test_falta_gente_avisa_quem_combina_e_respeita_preferencias(s, fabrica):
    org = fabrica.atleta("Org", esportes=(("volei", "intermediario"),))
    quer = fabrica.atleta("Quer", esportes=(("volei", "intermediario"),), notif_raio_km=5)
    nao_quer = fabrica.atleta("NaoQuer", esportes=(("volei", "intermediario"),), notif_vagas=False)
    raio_curto = fabrica.atleta("Curto", esportes=(("volei", "intermediario"),), notif_raio_km=1)  # a ~1,8 km
    a = _jogo(s, fabrica, org, falta_gente=True)

    def avisos(u):
        return s.scalars(select(Notificacao).where(Notificacao.usuario_id == u.id, Notificacao.atividade_id == a.id)).all()

    assert len(avisos(quer)) == 1 and "Falta" in avisos(quer)[0].titulo
    assert not avisos(nao_quer)
    assert not avisos(raio_curto)
    # chamar de novo não repete o aviso
    match.avisar_compativeis(s, a, "falta_gente")
    assert len(avisos(quer)) == 1


def test_explorar_filtra_por_distancia_esporte_e_vagas(s, fabrica):
    org = fabrica.atleta("Org", esportes=(("volei", "intermediario"), ("futebol", "intermediario")))
    perto = _jogo(s, fabrica, org, "volei")
    longe = _jogo(s, fabrica, org, "volei", lat=LONGE[0], lng=LONGE[1])
    fut = _jogo(s, fabrica, org, "futebol")
    ver = fabrica.atleta("Ver", esportes=(("volei", "intermediario"),))

    def ids(**f):
        r = descoberta.explorar(s, ver, f=descoberta.Filtros(tipos=("atividade",), **f))
        return {a["id"] for a in r["atividades"]}

    assert {perto.id, fut.id} <= ids(raio_km=5)
    assert longe.id not in ids(raio_km=5)
    assert longe.id in ids(raio_km=None)  # "toda a cidade"
    assert fut.id not in ids(raio_km=5, modalidades=["volei"])
    # lotada some de "com vagas"
    perto.max_participantes = perto.confirmados
    s.commit()
    assert perto.id not in ids(raio_km=5, com_vagas=True)


def test_explorar_ordena_por_urgencia(s, fabrica):
    org = fabrica.atleta("Org", esportes=(("volei", "intermediario"),))
    proximo = _jogo(s, fabrica, org, em_horas=1, falta_gente=True)
    distante_no_tempo = _jogo(s, fabrica, org, em_horas=24 * 6)
    ver = fabrica.atleta("Ver", esportes=(("volei", "intermediario"),))
    r = descoberta.explorar(s, ver, f=descoberta.Filtros(tipos=("atividade",), raio_km=5))
    ordem = [a["id"] for a in r["atividades"]]
    assert ordem.index(proximo.id) < ordem.index(distante_no_tempo.id)


# ---------------------------------------------------------------- arenas


@pytest.fixture()
def arena(s, fabrica):
    gestor = fabrica.atleta("Gestor", esportes=(("beach_tennis", "intermediario"),))
    a = arenas.criar(s, gestor, "Arena Teste", *PERTO, abre=time(0, 0), fecha=time(23, 59))
    q = arenas.criar_quadra(s, a, gestor, "Quadra 1", ["beach_tennis"], 4, Decimal(80))
    return gestor, a, q


def test_gestor_virou_gestor_e_cadastrou_quadra(s, arena):
    gestor, a, q = arena
    assert gestor.gestor and q.modalidades == ["beach_tennis"]


def test_editar_arena_so_gestor_e_valida(s, arena, fabrica):
    gestor, a, _ = arena
    arenas.editar(s, a, gestor, "  Arena Nova ", -20.5, -54.7, endereco="Rua A, 10", cidade="Campo Grande", descricao="Boa", abre=time(8, 0), fecha=time(22, 0), tipo_estabelecimento="clube")
    s.refresh(a)
    assert (a.nome, a.latitude, a.endereco, a.cidade, a.tipo_estabelecimento, a.abre) == ("Arena Nova", -20.5, "Rua A, 10", "Campo Grande", "clube", time(8, 0))
    with pytest.raises(ErroNegocio):
        arenas.editar(s, a, gestor, " ", -20.5, -54.7)
    with pytest.raises(ErroNegocio):
        arenas.editar(s, a, gestor, "X", -20.5, -54.7, abre=time(10, 0), fecha=time(9, 0))
    with pytest.raises(SemPermissao):
        arenas.editar(s, a, fabrica.atleta("Intruso"), "Hack", 0, 0)


def test_reserva_nao_sobrepoe(s, arena):
    _, _, q = arena
    ini = (agora() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    arenas.reservar(s, q.id, ini, ini + timedelta(hours=1), rotulo="Futebol")
    s.commit()
    with pytest.raises(ErroNegocio):
        arenas.reservar(s, q.id, ini + timedelta(minutes=30), ini + timedelta(hours=2))
    s.rollback()
    arenas.reservar(s, q.id, ini + timedelta(hours=1), ini + timedelta(hours=2))  # encostado, sem sobrepor


def test_so_gestor_mexe_na_quadra(s, arena, fabrica):
    _, _, q = arena
    intruso = fabrica.atleta("Intruso")
    with pytest.raises(SemPermissao):
        arenas.quadra_do_gestor(s, q.id, intruso)


def test_divulgar_horario_avisa_atletas_e_vira_jogo(s, arena, fabrica):
    gestor, a, q = arena
    jogador = fabrica.atleta("Joga", esportes=(("beach_tennis", "intermediario"),))
    inicio = (agora() + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    h = arenas.divulgar(s, q.id, inicio, inicio + timedelta(hours=1), Decimal(80), None, gestor)

    aviso = s.scalar(select(Notificacao).where(Notificacao.usuario_id == jogador.id, Notificacao.chave == f"hd:{h.id}"))
    assert aviso and "Quadra disponível" in aviso.titulo and "R$ 80" in aviso.corpo
    # aparece na descoberta
    lista = descoberta.buscar_horarios(s, *PERTO, descoberta.Filtros(raio_km=5, quando="hoje" if inicio.date() == agora().date() else None))
    assert any(x["id"] == h.id for x in lista) or inicio.date() != agora().date()

    # o atleta usa o horário divulgado para criar um jogo: a quadra é reservada e a divulgação some
    jogo = atividades.criar(s, jogador, NovaAtividade(modalidade_id=fabrica.mod("beach_tennis").id, nome="Beach 21h", inicio=inicio, max_participantes=4, quadra_id=q.id, duracao_min=60))
    assert jogo.arena_id == a.id and jogo.local_nome.startswith("Arena Teste")
    assert arenas._conflito(s, q.id, inicio, inicio + timedelta(hours=1)) is not None
    assert s.get(type(h), h.id) is None
    # horário que a arena não divulgou não é de qualquer um
    outro = (inicio + timedelta(hours=5))
    with pytest.raises(SemPermissao):
        atividades.criar(s, jogador, NovaAtividade(modalidade_id=fabrica.mod("beach_tennis").id, nome="X", inicio=outro, max_participantes=4, quadra_id=q.id))


def test_agenda_e_indicadores(s, arena):
    gestor, a, q = arena
    hoje = agora().date()
    ini = datetime.combine(hoje, time(10, 0))
    arenas.reservar(s, q.id, ini, ini + timedelta(hours=2), rotulo="Beach Tennis")
    s.commit()
    ag = arenas.agenda_do_dia(s, a, hoje)
    celulas = {l["hora"]: l["celulas"][0] for l in ag["linhas"]}
    assert celulas["10:00"]["estado"] == "ocupado" and celulas["10:00"]["rotulo"] == "Beach Tennis"
    assert celulas["11:00"]["estado"] == "ocupado"
    assert celulas["12:00"]["estado"] == "livre"
    ind = arenas.indicadores(s, a, hoje)
    assert ind["reservas_hoje"] == 1
    assert ind["ocupacao_pct"] == round(100 * 2 / 24)  # 2 h de 24 h
    assert ind["ocupacao_por_hora"][10]["pct"] == 100


def test_divulgar_ociosos_publica_o_que_resta_do_dia(s, arena):
    gestor, a, q = arena
    a.abre, a.fecha = time(0, 0), time(23, 59)
    s.commit()
    n = arenas.divulgar_ociosos(s, a, gestor)
    restante = 23 - (agora() + timedelta(hours=1)).hour + 1
    assert 0 < n <= restante + 1
    assert arenas.divulgar_ociosos(s, a, gestor) == 0  # idempotente
