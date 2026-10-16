"""Telão do campeonato: link secreto sem login, jogo fixo × automático, ponto do game e faixa 'ao vivo agora'."""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from playgo.web.app import app


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    from sqlalchemy import select

    from playgo import contas
    from playgo.db import Session
    from playgo.models import Usuario

    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


def _conta(cli, nome):
    r = cli.post("/api/v1/auth/cadastro", json={"nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]


def _plano(uid, plano):
    from playgo import planos
    from playgo.db import Session
    from playgo.models import Usuario

    with Session() as s:
        planos.conceder(s, s.query(Usuario).filter(Usuario.admin).first(), uid, plano, date.today() + timedelta(days=30))


def _campeonato_com_jogos(cli, org, fas):
    """Beach tennis por games, 4 equipes, eliminatória: devolve (campeonato_id, jogos)."""
    mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=org).json()}
    hoje = date.today()
    cid = cli.post("/api/v1/campeonatos", headers=org, json={
        "modalidade_id": mods["beach_tennis"], "nome": "Open Telão", "data_inicio": str(hoje + timedelta(days=10)), "inscricao_ate": str(hoje + timedelta(days=5)),
        "max_equipes": 8, "atletas_por_equipe": 2, "local_nome": "Arena", "latitude": -20.4, "longitude": -54.6,
    }).json()["id"]
    for i, h in enumerate(fas):
        eid = cli.post(f"/api/v1/campeonatos/{cid}/equipes", headers=h, json={"nome": f"Dupla {i + 1}"}).json()["equipes_lista"][-1]["id"]
        assert cli.post(f"/api/v1/equipes/{eid}/decidir", headers=org, json={"confirmar": True}).status_code == 200
    assert cli.post(f"/api/v1/campeonatos/{cid}/sorteio", headers=org, json={"formato": "eliminatoria"}).status_code in (200, 201)
    jogos = [j for r in cli.get(f"/api/v1/campeonatos/{cid}/chaves", headers=org).json()["rodadas"] for j in r["jogos"]]
    return cid, [j for j in jogos if j["a"] and j["b"]]


def test_telao_link_secreto_automatico_fixo_e_ponto_do_game(banco):
    with TestClient(app) as cli:
        org, org_id = _conta(cli, "Org")
        _plano(org_id, "organizador")
        fas = [_conta(cli, f"Cap{i}")[0] for i in range(4)]
        outro, _ = _conta(cli, "Curioso")
        cid, jogos = _campeonato_com_jogos(cli, org, fas)
        assert len(jogos) == 2

        # só quem conduz abre o painel; o link nasce na primeira visita
        assert cli.get(f"/api/v1/campeonatos/{cid}/telao", headers=outro).status_code == 403
        p = cli.get(f"/api/v1/campeonatos/{cid}/telao", headers=org).json()
        assert p["link"].startswith("/telao/") and p["escolhido"] is None and len(p["proximos"]) == 2
        token = p["link"].split("/")[-1]

        # a tela pública abre sem login; token errado = 404 em JSON e na página
        anonimo = TestClient(app)
        assert anonimo.get(p["link"]).status_code == 200
        assert anonimo.get("/telao/token-que-nao-existe-123456/dados").status_code == 404
        assert anonimo.get("/telao/token-que-nao-existe-123456").status_code == 404
        d = anonimo.get(f"/telao/{token}/dados").json()
        assert d["modo"] == "auto" and d["jogos"] == [] and len(d["proximos"]) == 2 and d["campeonato"]["nome"] == "Open Telão"

        # jogo ao vivo aparece sozinho no automático, com o ponto do game (15, 30, 40)
        j1 = jogos[0]["id"]
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/iniciar", headers=org).status_code == 200
        for _ in range(2):
            assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/ponto", headers=org, json={"lado": "a", "delta": 1}).status_code == 200
        d = anonimo.get(f"/telao/{token}/dados").json()
        assert [j["id"] for j in d["jogos"]] == [j1] and d["jogos"][0]["status"] == "ao_vivo"
        assert d["jogos"][0]["ponto"] == {"a": "30", "b": "0", "rotulo": "Game"}

        # fixar outro jogo (ainda não começou) e voltar ao automático
        j2 = jogos[1]["id"]
        assert cli.post(f"/api/v1/campeonatos/{cid}/telao/jogo", headers=outro, json={"jogo_id": j2}).status_code == 403
        assert cli.post(f"/api/v1/campeonatos/{cid}/telao/jogo", headers=org, json={"jogo_id": 99999999}).status_code == 400
        assert cli.post(f"/api/v1/campeonatos/{cid}/telao/jogo", headers=org, json={"jogo_id": j2}).json()["escolhido"] == j2
        d = anonimo.get(f"/telao/{token}/dados").json()
        assert d["modo"] == "fixo" and [j["id"] for j in d["jogos"]] == [j2]
        cli.post(f"/api/v1/campeonatos/{cid}/telao/jogo", headers=org, json={"jogo_id": None})
        assert anonimo.get(f"/telao/{token}/dados").json()["modo"] == "auto"

        # "ao vivo agora" no início
        feed = cli.get("/api/v1/feed", headers=outro).json()
        assert any(x["jogo_id"] == j1 and x["campeonato"] == "Open Telão" for x in feed["ao_vivo"])

        # renovar derruba o link antigo
        novo = cli.post(f"/api/v1/campeonatos/{cid}/telao/renovar", headers=org).json()["link"]
        assert novo != p["link"] and anonimo.get(f"/telao/{token}/dados").status_code == 404 and anonimo.get(novo + "/dados").status_code == 200


def test_qualquer_usuario_logado_acompanha_o_telao_no_proprio_aparelho_sem_poder_de_organizar(banco):
    with TestClient(app) as cli:
        org, org_id = _conta(cli, "Org")
        _plano(org_id, "organizador")
        fas = [_conta(cli, f"Cap{i}")[0] for i in range(4)]
        cid, jogos = _campeonato_com_jogos(cli, org, fas)
        j1, j2 = jogos[0]["id"], jogos[1]["id"]
        cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/iniciar", headers=org)

        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        cli.post("/api/v1/auth/cadastro", json={"nome": "Espectador", "email": email, "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
        site = TestClient(app)
        # sem login não entra
        assert site.get(f"/campeonatos/{cid}/acompanhar", follow_redirects=False).status_code in (303, 401)
        assert site.post("/entrar", data={"email": email, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303

        # a página do espectador abre; o painel de controle do telão leva para ela
        assert "sel-jogo" in site.get(f"/campeonatos/{cid}/acompanhar").text
        r = site.get(f"/campeonatos/{cid}/telao", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/campeonatos/{cid}/acompanhar"
        assert "📺 Telão" in site.get(f"/campeonatos/{cid}/chaves").text

        # dados: segue o jogo ao vivo; a pessoa pode fixar outro só para si, sem mexer no telão da organização
        d = site.get(f"/campeonatos/{cid}/acompanhar/dados").json()
        assert [j["id"] for j in d["jogos"]] == [j1] and d["modo"] == "auto" and len(d["opcoes"]) >= 2
        d = site.get(f"/campeonatos/{cid}/acompanhar/dados", params={"jogo": j2}).json()
        assert [j["id"] for j in d["jogos"]] == [j2]
        assert cli.get(f"/api/v1/campeonatos/{cid}/telao", headers=org).json()["escolhido"] is None

        # ver não dá poder de transmitir nem de controlar o telão
        espectador = {"Authorization": "Bearer " + cli.post("/api/v1/auth/entrar", json={"email": email, "senha": "senha-de-teste-1"}).json()["token"]}
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/transmissao", headers=espectador).status_code == 403
        assert cli.post(f"/api/v1/campeonatos/{cid}/telao/jogo", headers=espectador, json={"jogo_id": j2}).status_code == 403
        assert cli.post(f"/api/v1/campeonatos/{cid}/telao/renovar", headers=espectador).status_code == 403
