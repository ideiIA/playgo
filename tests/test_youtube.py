"""Transmissão no YouTube do PlayGo: conexão do canal, regras de quem transmite, chave por jogo e ciclo (preparar → no ar → encerrar)."""

import uuid
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from playgo import youtube
from playgo.config import settings
from playgo.db import Session
from playgo.models import ConfigExterna, Jogo, Usuario
from playgo.web.app import app


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    from sqlalchemy import select

    from playgo import contas

    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


class FalsoYouTube:
    """Simula token, canal, liveBroadcasts e liveStreams; guarda o que foi chamado."""

    def __init__(self):
        self.chamadas: list[tuple[str, str]] = []
        self.ciclo = "ready"
        self.sinal = "inactive"
        self.proximo = 0
        self.erro_stream: str | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        caminho = req.url.path
        self.chamadas.append((req.method, caminho.replace("/youtube/v3/", "")))
        if caminho == "/token":
            corpo = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            if corpo.get("grant_type") == "authorization_code":
                return httpx.Response(200, json={"access_token": "acesso-1", "expires_in": 3599, "refresh_token": "renovacao-secreta"})
            return httpx.Response(200, json={"access_token": "acesso-2", "expires_in": 3599})
        if caminho.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": "UCcanal", "snippet": {"title": "PlayGo Sports"}}]})
        if caminho.endswith("/liveBroadcasts/bind"):
            return httpx.Response(200, json={"id": req.url.params["id"]})
        if caminho.endswith("/liveBroadcasts/transition"):
            self.ciclo = {"live": "live", "complete": "complete"}[req.url.params["broadcastStatus"]]
            return httpx.Response(200, json={"id": req.url.params["id"], "status": {"lifeCycleStatus": self.ciclo}})
        if caminho.endswith("/liveBroadcasts"):
            if req.method == "POST":
                self.proximo += 1
                return httpx.Response(200, json={"id": f"video{self.proximo}"})
            if req.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"items": [{"id": req.url.params["id"], "status": {"lifeCycleStatus": self.ciclo}}]})
        if caminho.endswith("/liveStreams"):
            if req.method == "POST":
                if self.erro_stream:
                    return httpx.Response(403, json={"error": {"message": "x", "errors": [{"reason": self.erro_stream}]}})
                self.proximo += 1
                return httpx.Response(200, json={"id": f"stream{self.proximo}", "cdn": {"ingestionInfo": {"ingestionAddress": "rtmp://a.rtmp.youtube.com/live2", "streamName": f"chave-{self.proximo}"}}})
            if req.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"items": [{"id": req.url.params["id"], "status": {"streamStatus": self.sinal, "healthStatus": {"status": "good"}},
                                                          "cdn": {"ingestionInfo": {"ingestionAddress": "rtmp://a.rtmp.youtube.com/live2", "streamName": "chave-atual"}}}]})
        return httpx.Response(404, json={"error": {"message": "rota inesperada"}})


@pytest.fixture
def yt(monkeypatch):
    f = FalsoYouTube()
    monkeypatch.setattr(youtube, "_http", lambda: httpx.Client(transport=httpx.MockTransport(f)))
    monkeypatch.setattr(settings, "youtube_client_id", "cliente-id")
    monkeypatch.setattr(settings, "youtube_client_secret", "cliente-segredo")
    monkeypatch.setattr(settings, "url_publica", "https://playgo.teste")
    return f


def _conta(cli, nome):
    r = cli.post("/api/v1/auth/cadastro", json={"nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]


def _plano(uid, plano):
    from playgo import planos

    with Session() as s:
        planos.conceder(s, s.query(Usuario).filter(Usuario.admin).first(), uid, plano, date.today() + timedelta(days=30))


def _campeonato(cli, org, fas):
    mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=org).json()}
    hoje = date.today()
    cid = cli.post("/api/v1/campeonatos", headers=org, json={
        "modalidade_id": mods["beach_tennis"], "nome": "Open Transmitido", "data_inicio": str(hoje + timedelta(days=10)), "inscricao_ate": str(hoje + timedelta(days=5)),
        "max_equipes": 8, "atletas_por_equipe": 2, "local_nome": "Arena", "latitude": -20.4, "longitude": -54.6,
    }).json()["id"]
    for i, h in enumerate(fas):
        eid = cli.post(f"/api/v1/campeonatos/{cid}/equipes", headers=h, json={"nome": f"Dupla {i + 1}"}).json()["equipes_lista"][-1]["id"]
        cli.post(f"/api/v1/equipes/{eid}/decidir", headers=org, json={"confirmar": True})
    cli.post(f"/api/v1/campeonatos/{cid}/sorteio", headers=org, json={"formato": "eliminatoria"})
    jogos = [j for r in cli.get(f"/api/v1/campeonatos/{cid}/chaves", headers=org).json()["rodadas"] for j in r["jogos"] if j["a"] and j["b"]]
    return cid, jogos


def test_conexao_do_canal_so_admin_e_token_guardado(banco, yt):
    with TestClient(app) as cli:
        comum, comum_id = _conta(cli, "Comum")
        adm, adm_id = _conta(cli, "Adm")
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.query(ConfigExterna).filter(ConfigExterna.chave == "youtube").delete()
            s.commit()
        assert cli.get("/api/v1/youtube/estado", headers=comum).json() == {"conectado": False}
        with Session() as s:
            with pytest.raises(Exception):
                youtube.conectar(s, s.get(Usuario, comum_id), "codigo")
            e = youtube.conectar(s, s.get(Usuario, adm_id), "codigo-do-google")
        assert e["conectado"] is True and e["canal"] == "PlayGo Sports" and e["privacidade"] == "unlisted"
        # a autorização permanente fica no banco; a API nunca a devolve
        assert "renovacao-secreta" not in str(cli.get("/api/v1/youtube/estado", headers=adm).json())
        assert cli.post("/api/v1/youtube/privacidade", headers=adm, json={"privacidade": "public"}).json()["privacidade"] == "public"
        assert cli.post("/api/v1/youtube/privacidade", headers=adm, json={"privacidade": "nada"}).status_code == 400
        assert cli.post("/api/v1/youtube/privacidade", headers=comum, json={"privacidade": "public"}).status_code == 403
        cli.post("/api/v1/youtube/privacidade", headers=adm, json={"privacidade": "unlisted"})  # volta ao padrão seguro


def test_ciclo_da_transmissao_de_um_jogo_com_todas_as_regras(banco, yt):
    with TestClient(app) as cli:
        adm, adm_id = _conta(cli, "Adm")
        org, org_id = _conta(cli, "Org")
        mesario, mesario_id = _conta(cli, "Mesario")
        curioso, _ = _conta(cli, "Curioso")
        _plano(org_id, "organizador")
        fas = [_conta(cli, f"Cap{i}")[0] for i in range(4)]
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.query(ConfigExterna).filter(ConfigExterna.chave == "youtube").delete()
            s.commit()
        cid, jogos = _campeonato(cli, org, fas)
        j1, j2 = jogos[0]["id"], jogos[1]["id"]

        def url(j, suf=""):
            return f"/api/v1/campeonatos/{cid}/jogos/{j}/transmissao{suf}"

        # 1) canal ainda não conectado
        assert "não foi conectado" in cli.post(url(j1), headers=org).json()["detail"] or "ainda não liberou" in cli.post(url(j1), headers=org).json()["detail"]
        with Session() as s:
            youtube.conectar(s, s.get(Usuario, adm_id), "codigo")
        assert "ainda não liberou" in cli.post(url(j1), headers=org).json()["detail"]

        # 2) só quem conduz libera/prepara; a liberação exige a declaração
        assert cli.post(f"/api/v1/campeonatos/{cid}/transmissao/liberar", headers=curioso, json={"permitir": True, "declaro": True}).status_code == 403
        assert cli.post(f"/api/v1/campeonatos/{cid}/transmissao/liberar", headers=org, json={"permitir": True, "declaro": False}).status_code == 400
        assert cli.post(f"/api/v1/campeonatos/{cid}/transmissao/liberar", headers=org, json={"permitir": True, "declaro": True}).json() == {"transmissao_permitida": True}
        assert cli.get(f"/api/v1/campeonatos/{cid}", headers=curioso).json()["transmissao_permitida"] is True  # o público vê o aviso
        assert cli.post(url(j1), headers=curioso).status_code == 403
        assert cli.post(url(j1), headers=mesario).status_code == 403  # ainda não é mesário
        assert cli.post(f"/api/v1/campeonatos/{cid}/mesarios", headers=org, json={"usuario_id": mesario_id}).status_code in (200, 201)

        # 3) o mesário prepara: recebe endereço e chave (só ele, sem cache); o servidor não guarda a chave
        r = cli.post(url(j1), headers=mesario)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        d = r.json()
        assert d["estado"] == "preparada" and d["envio"]["endereco"].startswith("rtmp://") and d["envio"]["chave"] == "chave-atual" and d["video_id"] is None
        with Session() as s:
            j = s.get(Jogo, j1)
            assert (j.yt_estado, j.yt_broadcast_id) == ("preparada", "video1") and "chave" not in (j.yt_stream_id or "")
        n = len(yt.chamadas)
        assert cli.post(url(j1), headers=mesario).json()["estado"] == "preparada"  # idempotente: não cria outra transmissão
        assert not any(m == "POST" and c in ("liveBroadcasts", "liveStreams") for m, c in yt.chamadas[n:])

        # 4) só entra no ar com sinal ativo; nova chave só antes disso
        assert "ainda não está recebendo" in cli.post(url(j1, "/no-ar"), headers=mesario).json()["detail"]
        assert cli.post(url(j1, "/nova-chave"), headers=mesario).status_code == 200
        yt.sinal = "active"
        d = cli.post(url(j1, "/no-ar"), headers=mesario).json()
        assert d["estado"] == "ao_vivo" and d["video_id"] == "video1"
        assert "antes de entrar no ar" in cli.post(url(j1, "/nova-chave"), headers=mesario).json()["detail"]

        # 5) o público vê o vídeo no telão e na faixa do início
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/iniciar", headers=org).status_code == 200
        token = cli.get(f"/api/v1/campeonatos/{cid}/telao", headers=org).json()["link"].split("/")[-1]
        telao = TestClient(app).get(f"/telao/{token}/dados").json()
        assert telao["jogos"][0]["video"] == {"id": "video1", "estado": "ao_vivo"}
        assert any(x["video"] is True for x in cli.get("/api/v1/feed", headers=curioso).json()["ao_vivo"])

        # 6) limite de transmissões simultâneas
        settings.youtube_max_simultaneas = 1
        try:
            assert "Encerre uma antes" in cli.post(url(j2), headers=org).json()["detail"]
        finally:
            settings.youtube_max_simultaneas = 4

        # 7) encerrar o jogo no PlayGo finaliza a transmissão no YouTube; a gravação entra no book
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/sets", headers=org, json={"sets": [[6, 3], [6, 4]]}).status_code == 200
        yt.chamadas.clear()
        assert cli.post(f"/api/v1/campeonatos/{cid}/jogos/{j1}/encerrar", headers=org, json={}).status_code == 200
        with Session() as s:
            j = s.get(Jogo, j1)
            assert j.yt_estado == "encerrada" and j.yt_broadcast_id == "video1" and j.yt_stream_id is None
        assert ("POST", "liveBroadcasts/transition") in yt.chamadas
        cli.post(f"/api/v1/campeonatos/{cid}/book/publicar", headers=org)
        book = cli.get(f"/api/v1/campeonatos/{cid}/book", headers=curioso).json()
        assert [t["video_id"] for t in book["transmissoes"]] == ["video1"]

        # 8) erro do YouTube vira mensagem clara e não deixa transmissão pendurada
        yt.erro_stream = "liveStreamingNotEnabled"
        r = cli.post(url(j2), headers=org)
        assert r.status_code == 400 and "24 horas" in r.json()["detail"]
        with Session() as s:
            assert s.get(Jogo, j2).yt_estado is None


def test_tela_de_controle_mostra_a_secao_de_transmissao_conforme_o_estado(banco, yt):
    with TestClient(app) as cli:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        r = cli.post("/api/v1/auth/cadastro", json={"nome": "Org", "email": email, "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
        org, org_id = {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]
        _plano(org_id, "organizador")
        adm, adm_id = _conta(cli, "Adm")
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.query(ConfigExterna).filter(ConfigExterna.chave == "youtube").delete()
            s.commit()
        cid, _ = _campeonato(cli, org, [_conta(cli, f"C{i}")[0] for i in range(4)])
        site = TestClient(app)
        assert site.post("/entrar", data={"email": email, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303
        assert "ainda não conectou o canal" in site.get(f"/campeonatos/{cid}/telao").text
        with Session() as s:
            youtube.conectar(s, s.get(Usuario, adm_id), "codigo")
        pagina = site.get(f"/campeonatos/{cid}/telao").text
        assert "Declaro que os participantes" in pagina and "Liberar transmissão deste campeonato" in pagina
        cli.post(f"/api/v1/campeonatos/{cid}/transmissao/liberar", headers=org, json={"permitir": True, "declaro": True})
        pagina = site.get(f"/campeonatos/{cid}/telao").text
        assert "Preparar transmissão deste jogo" in pagina and "Bloquear transmissões" in pagina and "PODE TER TRANSMISSÃO" in site.get(f"/campeonatos/{cid}").text
