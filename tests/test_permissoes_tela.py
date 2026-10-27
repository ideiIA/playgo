"""O plano Usuário só vê e participa: as telas de criar atividade, campeonato e arena não aparecem nem abrem."""

import uuid

import pytest

from fastapi.testclient import TestClient

from playgo.web.app import app


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    """O primeiro cadastro do banco vira administrador: garante que já existe um para o usuário de teste ser comum."""
    from sqlalchemy import select

    from playgo import contas
    from playgo.db import Session
    from playgo.models import Usuario

    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


def test_usuario_comum_nao_ve_nem_abre_telas_de_criacao(banco):
    with TestClient(app) as cli:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        r = cli.post("/cadastro", data={"nome": "Comum", "email": email, "senha": "senha-de-teste-1", "usuario": f"c{uuid.uuid4().hex[:10]}", "aceito_termos": "1"}, follow_redirects=False)
        assert r.status_code == 303

        # a API informa as permissões que o app usa para esconder as opções
        me = cli.get("/api/v1/me").json()
        assert me["permissoes"] == {"atividade": False, "campeonato": False, "arena": False, "grupo": False, "comunidade": False, "ensino": False, "perfil_prof": False, "ensino_menu": False}

        # menu do site: sem "Criar atividade" e sem "Minha Arena"
        home = cli.get("/").text
        assert "Criar atividade" not in home and "Minha Arena" not in home
        assert "+ Criar campeonato" not in cli.get("/campeonatos").text

        # as telas de criação levam aos planos
        for url in ("/atividades/nova", "/campeonatos/novo", "/gestao", "/gestao/arenas/nova"):
            r = cli.get(url, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == "/planos", url


def test_tutorial_de_campeonatos_so_para_quem_pode_criar(banco):
    from playgo.db import Session
    from playgo.models import Usuario

    with TestClient(app) as cli:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        cli.post("/cadastro", data={"nome": "Comum", "email": email, "senha": "senha-de-teste-1", "usuario": f"c{uuid.uuid4().hex[:10]}", "aceito_termos": "1"}, follow_redirects=False)
        r = cli.get("/tutorial/campeonatos", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/planos"
        assert "Tutorial de campeonatos" not in cli.get("/campeonatos").text

        with Session() as s:  # quem pode criar campeonato (aqui, administrador) vê o tutorial e o menu
            s.query(Usuario).filter(Usuario.email == email).update({Usuario.admin: True})
            s.commit()
        pagina = cli.get("/tutorial/campeonatos")
        assert pagina.status_code == 200 and "Regras de cada modalidade" in pagina.text and "Super 8" in pagina.text and "Beach Tennis" in pagina.text
        assert "/tutorial/campeonatos" in cli.get("/campeonatos").text


def test_menu_tutoriais_com_submenus_e_tutorial_de_transmissao_aberto_a_todos(banco):
    from playgo.db import Session
    from playgo.models import Usuario

    with TestClient(app) as cli:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        r = cli.post("/cadastro", data={"nome": "Comum", "email": email, "senha": "senha-de-teste-1", "usuario": f"c{uuid.uuid4().hex[:10]}", "aceito_termos": "1"}, follow_redirects=False)
        # qualquer usuário logado vê o índice e o tutorial de transmissão (mesários precisam dele)
        indice = cli.get("/tutorial")
        assert indice.status_code == 200 and "/tutorial/transmissao" in indice.text and "/tutorial/campeonatos" not in indice.text.split("Tutoriais do PlayGo", 1)[1]
        pagina = cli.get("/tutorial/transmissao")
        assert pagina.status_code == 200 and "Larix" in pagina.text and "OBS" in pagina.text and "Câmera do navegador" in pagina.text and "Entrar no ar" in pagina.text
        assert "Tutoriais" in pagina.text and 'class="submenu' in pagina.text  # o menu lateral tem o submenu
        # a API entrega o mesmo conteúdo ao app; a versão embutida não repete o cabeçalho
        token = cli.post("/api/v1/auth/entrar", json={"email": email, "senha": "senha-de-teste-1"}).json()["token"]
        h = {"Authorization": "Bearer " + token}
        assert [t["codigo"] for t in cli.get("/api/v1/tutoriais", headers=h).json()] == ["transmissao"]
        cheio = cli.get("/api/v1/tutoriais/transmissao", headers=h).json()["html"]
        embutido = cli.get("/api/v1/tutoriais/transmissao?embutido=true", headers=h).json()["html"]
        assert "tut-hero" in cheio and "tut-hero" not in embutido and "Larix" in embutido
        assert cli.get("/api/v1/tutoriais/campeonatos", headers=h).status_code == 402  # só quem cria campeonatos
        assert cli.get("/api/v1/tutoriais/inexistente", headers=h).status_code == 404
        with Session() as s:
            s.query(Usuario).filter(Usuario.email == email).update({Usuario.admin: True})
            s.commit()
        assert [t["codigo"] for t in cli.get("/api/v1/tutoriais", headers=h).json()] == ["campeonatos", "transmissao"]
        assert "Super 8" in cli.get("/api/v1/tutoriais/campeonatos", headers=h).json()["html"]
        assert "/tutorial/campeonatos" in cli.get("/tutorial").text
