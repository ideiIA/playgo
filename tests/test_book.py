"""Book do campeonato/atividade: capa no cadastro, curtidas, campeões e publicações mais curtidas/comentadas, rascunho × publicado."""

import io
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

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


def _png():
    buf = io.BytesIO()
    Image.new("RGBA", (300, 120), (200, 30, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


def _plano(uid, plano):
    from playgo import planos
    from playgo.db import Session
    from playgo.models import Usuario

    with Session() as s:
        planos.conceder(s, s.query(Usuario).filter(Usuario.admin).first(), uid, plano, date.today() + timedelta(days=30))


def test_curtir_publicacao_liga_desliga_e_conta(banco):
    with TestClient(app) as cli:
        a, _ = _conta(cli, "Autor")
        b, _ = _conta(cli, "Fa")
        # post no feed geral (precisa de local)
        r = cli.post("/api/v1/publicacoes", headers=a, data={"escopo": "geral", "texto": "Boa tarde!", "latitude": -20.4, "longitude": -54.6, "local_nome": "Praça"})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        from playgo.db import Session
        from playgo.models import Publicacao

        with Session() as s:
            s.query(Publicacao).filter(Publicacao.id == pid).update({Publicacao.status: "publicada"})
            s.commit()
        assert cli.post(f"/api/v1/publicacoes/{pid}/curtir", headers=b).json() == {"curtiu": True, "n_curtidas": 1}
        assert cli.post(f"/api/v1/publicacoes/{pid}/curtir", headers=a).json()["n_curtidas"] == 2
        det = cli.get(f"/api/v1/publicacoes/{pid}", headers=b).json()
        assert det["n_curtidas"] == 2 and det["curtiu"] is True
        assert cli.post(f"/api/v1/publicacoes/{pid}/curtir", headers=b).json() == {"curtiu": False, "n_curtidas": 1}


def test_capa_no_cadastro_e_book_rascunho_publicado(banco):
    with TestClient(app) as cli:
        org, org_id = _conta(cli, "Org")
        fa, _ = _conta(cli, "Fa")
        _plano(org_id, "organizador")
        mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=org).json()}
        hoje = date.today()
        c = cli.post("/api/v1/campeonatos", headers=org, json={
            "modalidade_id": mods["beach_tennis"], "nome": "Open Book", "data_inicio": str(hoje + timedelta(days=10)), "inscricao_ate": str(hoje + timedelta(days=5)),
            "max_equipes": 8, "atletas_por_equipe": 2, "local_nome": "Arena X", "latitude": -20.4, "longitude": -54.6,
        })
        assert c.status_code == 201, c.text
        cid = c.json()["id"]

        # capa: só a organização envia; vira /capa/<32 hex>.jpg 16:9 e é servida
        assert cli.post(f"/api/v1/campeonatos/{cid}/capa", headers=fa, files={"arquivo": ("logo.png", _png(), "image/png")}).status_code == 403
        assert cli.post(f"/api/v1/campeonatos/{cid}/capa", headers=org, files={"arquivo": ("x.txt", b"nao e imagem", "text/plain")}).status_code == 400
        r = cli.post(f"/api/v1/campeonatos/{cid}/capa", headers=org, files={"arquivo": ("logo.png", _png(), "image/png")})
        assert r.status_code == 200
        capa = r.json()["capa_url"]
        img = cli.get(capa)
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(img.content)).size == (1600, 900)

        # rascunho: a organização vê; os demais não
        b = cli.get(f"/api/v1/campeonatos/{cid}/book", headers=org).json()
        assert b["nome"] == "Open Book" and b["capa_url"] == capa and b["publicado"] is False and b["sou_organizacao"] is True
        assert b["numeros"]["equipes"] == 0 and b["publicacoes"]["totais"]["publicacoes"] == 0
        assert cli.get(f"/api/v1/campeonatos/{cid}/book", headers=fa).status_code == 404
        assert cli.post(f"/api/v1/campeonatos/{cid}/book/publicar", headers=fa).status_code == 403

        # publicado: todos veem
        assert cli.post(f"/api/v1/campeonatos/{cid}/book/publicar", headers=org).json() == {"publicado": True}
        v = cli.get(f"/api/v1/campeonatos/{cid}/book", headers=fa).json()
        assert v["publicado"] is True and v["sou_organizacao"] is False
        assert cli.get(f"/api/v1/campeonatos/{cid}", headers=fa).json()["capa_url"] == capa


def test_book_do_campeonato_lista_publicacoes_mais_curtidas_e_comentadas(banco):
    from playgo.db import Session
    from playgo.models import Publicacao

    with TestClient(app) as cli:
        org, org_id = _conta(cli, "Org")
        fa1, _ = _conta(cli, "Fa1")
        fa2, _ = _conta(cli, "Fa2")
        _plano(org_id, "organizador")
        mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=org).json()}
        hoje = date.today()
        cid = cli.post("/api/v1/campeonatos", headers=org, json={
            "modalidade_id": mods["futsal"], "nome": "Copa Histórias", "data_inicio": str(hoje + timedelta(days=10)), "inscricao_ate": str(hoje + timedelta(days=5)),
            "max_equipes": 8, "local_nome": "Ginásio", "latitude": -20.4, "longitude": -54.6,
        }).json()["id"]
        for h, nome in ((fa1, "Time Um"), (fa2, "Time Dois")):  # quem se inscreve passa a ler e participar do mural do campeonato
            assert cli.post(f"/api/v1/campeonatos/{cid}/equipes", headers=h, json={"nome": nome}).status_code == 201
        posts = []
        for texto in ("Primeiro post", "Segundo post", "Terceiro post"):
            r = cli.post("/api/v1/publicacoes", headers=org, data={"escopo": "campeonato", "escopo_id": cid, "texto": texto, "latitude": -20.4, "longitude": -54.6, "local_nome": "Ginásio"})
            assert r.status_code == 201, r.text
            posts.append(r.json()["id"])
        with Session() as s:
            s.query(Publicacao).filter(Publicacao.id.in_(posts)).update({Publicacao.status: "publicada"})
            s.commit()
        # 2º post: 2 curtidas; 3º post: 3 comentários e 1 curtida
        for h in (fa1, fa2):
            assert cli.post(f"/api/v1/publicacoes/{posts[1]}/curtir", headers=h).status_code == 200
        assert cli.post(f"/api/v1/publicacoes/{posts[2]}/curtir", headers=fa1).status_code == 200
        for i in range(3):
            assert cli.post(f"/api/v1/publicacoes/{posts[2]}/comentarios", headers=fa1, json={"texto": f"comentário {i}"}).status_code == 201
        from playgo.models import Comentario

        with Session() as s:
            s.query(Comentario).filter(Comentario.publicacao_id == posts[2]).update({Comentario.status: "publicada"})
            s.commit()

        b = cli.get(f"/api/v1/campeonatos/{cid}/book", headers=org).json()["publicacoes"]
        assert [x["texto"] for x in b["mais_curtidas"]] == ["Segundo post", "Terceiro post"]
        assert b["mais_curtidas"][0]["n_curtidas"] == 2
        assert [x["texto"] for x in b["mais_comentadas"]] == ["Terceiro post"] and b["mais_comentadas"][0]["n_comentarios"] == 3
        assert b["totais"] == {"publicacoes": 3, "curtidas": 3, "comentarios": 3}

        # página do site (HTML) para a organização
        site = TestClient(app)
        assert site.get(f"/campeonatos/{cid}/book", follow_redirects=False).status_code in (303, 401)
