"""Regulamento em PDF do campeonato: envio pela organização, download por quem está logado."""

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from playgo.config import settings
from playgo.db import agora

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PERTO = (-20.4535, -54.6201)


def test_enviar_trocar_baixar_e_remover_o_pdf_do_regulamento(banco, monkeypatch):
    from playgo.web.app import app

    with TestClient(app) as cli:
        def conta(nome):
            email = f"{uuid.uuid4().hex[:10]}@teste.local"
            r = cli.post("/api/v1/auth/cadastro", json={"nome": nome, "email": email, "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
            return {"Authorization": "Bearer " + r.json()["token"]}

        org, outro, cap = conta("Org"), conta("Outro"), conta("Capitao")
        mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=org).json()}
        hoje = agora().date()
        c = cli.post("/api/v1/campeonatos", headers=org, json={"modalidade_id": mods["futsal"], "nome": "Copa", "data_inicio": str(hoje + timedelta(days=20)), "inscricao_ate": str(hoje + timedelta(days=10)), "max_equipes": 8, "local_nome": "G", "latitude": PERTO[0], "longitude": PERTO[1]})
        cid = c.json()["id"]
        cli.post(f"/api/v1/campeonatos/{cid}/equipes", headers=cap, json={"nome": "Time A"})
        url = f"/api/v1/campeonatos/{cid}/regulamento"

        assert cli.get(f"/api/v1/campeonatos/{cid}", headers=org).json()["regulamento_pdf"] is None
        # só a organização envia
        assert cli.post(url, headers=outro, files={"arquivo": ("r.pdf", PDF, "application/pdf")}).status_code == 403
        # só PDF, nada vazio, nada enorme
        assert cli.post(url, headers=org, files={"arquivo": ("r.pdf", b"nao e pdf", "application/pdf")}).status_code == 400
        assert cli.post(url, headers=org, files={"arquivo": ("r.pdf", b"", "application/pdf")}).status_code == 400
        monkeypatch.setattr(settings, "max_pdf_mb", 0)  # teto zero: qualquer arquivo passa do limite
        assert "MB" in cli.post(url, headers=org, files={"arquivo": ("r.pdf", PDF, "application/pdf")}).json()["detail"]
        monkeypatch.setattr(settings, "max_pdf_mb", 15)

        k = cli.post(url, headers=org, files={"arquivo": ("Regulamento 2026.pdf", PDF, "application/pdf")}).json()
        assert k["regulamento_pdf"]["nome"] == "Regulamento 2026.pdf" and k["regulamento_pdf"]["url"].startswith(f"/campeonatos/{cid}/regulamento.pdf?t=")
        # a capitã foi avisada e qualquer pessoa logada baixa pelo link já autorizado
        visto = cli.get(f"/api/v1/campeonatos/{cid}", headers=outro).json()["regulamento_pdf"]
        r = cli.get(visto["url"])
        assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"
        # sem login e sem token válido: não baixa
        assert TestClient(app).get(f"/campeonatos/{cid}/regulamento.pdf").status_code == 404
        assert TestClient(app).get(f"/campeonatos/{cid}/regulamento.pdf?t=invalido").status_code == 404
        # o link de outro campeonato não vale aqui
        assert cli.get(visto["url"].replace(f"/campeonatos/{cid}/", f"/campeonatos/{cid + 999}/")).status_code == 404

        # trocar substitui o arquivo; remover limpa
        k2 = cli.post(url, headers=org, files={"arquivo": ("novo.pdf", PDF + b"x", "application/pdf")}).json()
        assert k2["regulamento_pdf"]["nome"] == "novo.pdf"
        assert cli.get(cli.get(f"/api/v1/campeonatos/{cid}", headers=outro).json()["regulamento_pdf"]["url"]).content == PDF + b"x"
        assert cli.delete(url, headers=outro).status_code == 403
        assert cli.delete(url, headers=org).json()["regulamento_pdf"] is None
        assert cli.get(visto["url"]).status_code == 404


def test_enviar_o_pdf_ja_na_criacao_do_campeonato_pelo_site(banco):
    from playgo.web.app import app

    with TestClient(app) as cli:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        assert cli.post("/cadastro", data={"nome": "Org Site", "email": email, "senha": "senha-de-teste-1", "usuario": f"o{uuid.uuid4().hex[:10]}", "aceito_termos": "1"}, follow_redirects=False).status_code == 303
        from playgo.db import Session
        from playgo.models import Usuario

        with Session() as sx:  # criar campeonato exige plano; aqui não é o foco do teste
            sx.query(Usuario).filter(Usuario.email == email).update({Usuario.admin: True})
            sx.commit()
        mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades").json()}
        hoje = agora().date()
        dados = {"nome": "Copa com PDF", "modalidade_id": mods["futsal"], "data_inicio": str(hoje + timedelta(days=20)), "inscricao_ate": str(hoje + timedelta(days=10)), "max_equipes": 8, "atletas_por_equipe": 5,
                 "local_nome": "Ginásio", "latitude": str(PERTO[0]), "longitude": str(PERTO[1])}
        assert "enctype=\"multipart/form-data\"" in cli.get("/campeonatos/novo").text and 'name="regulamento_pdf"' in cli.get("/campeonatos/novo").text

        # PDF inválido: nada é criado
        antes = len(cli.get("/api/v1/explorar?tipos=campeonato&raio=0").json().get("campeonatos", []))
        r = cli.post("/campeonatos", data=dados, files={"regulamento_pdf": ("r.pdf", b"nao e pdf", "application/pdf")}, follow_redirects=False)
        assert r.status_code == 303 and "/campeonatos/" not in r.headers["location"].replace("/campeonatos/novo", "")
        assert len(cli.get("/api/v1/explorar?tipos=campeonato&raio=0").json().get("campeonatos", [])) == antes

        # PDF válido: o campeonato nasce já com o regulamento
        r = cli.post("/campeonatos", data=dados, files={"regulamento_pdf": ("Regulamento Final.pdf", PDF, "application/pdf")}, follow_redirects=False)
        assert r.status_code == 303
        cid = int(r.headers["location"].rsplit("/", 1)[1])
        k = cli.get(f"/api/v1/campeonatos/{cid}").json()
        assert k["regulamento_pdf"]["nome"] == "Regulamento Final.pdf"
        assert cli.get(k["regulamento_pdf"]["url"]).content == PDF

        # sem arquivo continua funcionando
        r = cli.post("/campeonatos", data=dados | {"nome": "Copa sem PDF"}, follow_redirects=False)
        sem = cli.get(f"/api/v1/campeonatos/{int(r.headers['location'].rsplit('/', 1)[1])}").json()
        assert sem["regulamento_pdf"] is None
