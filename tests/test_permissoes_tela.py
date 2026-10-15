"""O plano Usuário só vê e participa: as telas de criar atividade, campeonato e arena não aparecem nem abrem."""

import uuid

from fastapi.testclient import TestClient

from playgo.web.app import app


def test_usuario_comum_nao_ve_nem_abre_telas_de_criacao(banco):
    with TestClient(app) as cli:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        r = cli.post("/cadastro", data={"nome": "Comum", "email": email, "senha": "senha-de-teste-1", "usuario": f"c{uuid.uuid4().hex[:10]}", "aceito_termos": "1"}, follow_redirects=False)
        assert r.status_code == 303

        # a API informa as permissões que o app usa para esconder as opções
        me = cli.get("/api/v1/me").json()
        assert me["permissoes"] == {"atividade": False, "campeonato": False, "arena": False}

        # menu do site: sem "Criar atividade" e sem "Minha Arena"
        home = cli.get("/").text
        assert "Criar atividade" not in home and "Minha Arena" not in home
        assert "+ Criar campeonato" not in cli.get("/campeonatos").text

        # as telas de criação levam aos planos
        for url in ("/atividades/nova", "/campeonatos/novo", "/gestao", "/gestao/arenas/nova"):
            r = cli.get(url, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/planos", url
