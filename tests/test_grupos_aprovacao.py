"""Grupos: criar/gerir é do plano Pro em diante, com aceite de pedidos e remoção; comunidade só Organizador/Arena."""

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


def test_pedido_aceite_recusa_e_remocao_no_grupo_com_aprovacao(banco):
    with TestClient(app) as cli:
        dono, dono_id = _conta(cli, "Dono")
        _plano(dono_id, "pro")
        a, a_id = _conta(cli, "Aceito")
        b, b_id = _conta(cli, "Recusado")
        mods = {m["codigo"]: m["id"] for m in cli.get("/api/v1/modalidades", headers=dono).json()}
        g = cli.post("/api/v1/grupos", headers=dono, json={"nome": "Corre CG", "modalidade_id": mods["corrida"]}).json()
        assert g["entrada"] == "aprovacao" and g["sou_admin"]
        gid = g["id"]

        # pedido: ainda não é membro, não vê a conversa e conta como pendente
        d = cli.post(f"/api/v1/grupos/{gid}/entrar", headers=a).json()
        assert d["sou_membro"] is False and d["pedido_pendente"] is True and d["membros"] == 1
        assert cli.get(f"/api/v1/grupos/{gid}/mensagens", headers=a).status_code == 403
        cli.post(f"/api/v1/grupos/{gid}/entrar", headers=b)

        # só o admin do grupo vê e decide os pedidos
        assert cli.get(f"/api/v1/grupos/{gid}", headers=a).json()["pedidos"] == []
        assert cli.post(f"/api/v1/grupos/{gid}/pedidos/{b_id}/aceitar", headers=a).status_code == 403
        assert {p["usuario_id"] for p in cli.get(f"/api/v1/grupos/{gid}", headers=dono).json()["pedidos"]} == {a_id, b_id}
        d = cli.post(f"/api/v1/grupos/{gid}/pedidos/{a_id}/aceitar", headers=dono).json()
        assert d["membros"] == 2 and a_id in {m["usuario_id"] for m in d["membros_lista"]}
        assert cli.get(f"/api/v1/grupos/{gid}", headers=a).json()["sou_membro"] is True
        d = cli.post(f"/api/v1/grupos/{gid}/pedidos/{b_id}/recusar", headers=dono).json()
        assert d["pedidos"] == [] and d["membros"] == 2

        # remoção: só admin; não remove admin nem a si mesmo
        assert cli.post(f"/api/v1/grupos/{gid}/membros/{a_id}/remover", headers=a).status_code == 403
        assert cli.post(f"/api/v1/grupos/{gid}/membros/{dono_id}/remover", headers=dono).status_code == 400
        d = cli.post(f"/api/v1/grupos/{gid}/membros/{a_id}/remover", headers=dono).json()
        assert d["membros"] == 1
        assert cli.get(f"/api/v1/grupos/{gid}", headers=a).json()["sou_membro"] is False


def test_comunidade_so_organizador_e_arena_e_grupo_so_pro_em_diante(banco):
    with TestClient(app) as cli:
        pro, pro_id = _conta(cli, "ProUser")
        org, org_id = _conta(cli, "OrgUser")
        _plano(pro_id, "pro")
        _plano(org_id, "organizador")
        assert cli.get("/api/v1/me", headers=pro).json()["permissoes"]["grupo"] is True
        assert cli.get("/api/v1/me", headers=pro).json()["permissoes"]["comunidade"] is False
        assert cli.get("/api/v1/me", headers=org).json()["permissoes"]["comunidade"] is True
        r = cli.post("/api/v1/comunidades", headers=org, json={"nome": "Comunidade do Organizador"})
        assert r.status_code == 201
        # qualquer usuário participa
        cid = r.json()["id"]
        assert cli.post(f"/api/v1/comunidades/{cid}/entrar", headers=pro).status_code == 200


def test_campeonatos_encerrados_admin_ve_todos_e_organizador_so_os_seus(banco):
    from datetime import timedelta

    from playgo.db import Session, agora
    from playgo.models import Campeonato, Modalidade, Usuario

    with TestClient(app) as cli:
        org_a, a_id = _conta(cli, "OrgA")
        org_b, b_id = _conta(cli, "OrgB")
        comum, _ = _conta(cli, "Comum")
        _plano(a_id, "organizador")
        _plano(b_id, "organizador")
        hoje = agora().date()
        with Session() as s:
            mod = s.query(Modalidade).first()
            adm = s.query(Usuario).filter(Usuario.admin).first()
            for dono, nome, dias in ((a_id, "Copa Alfa", 40), (a_id, "Copa Beta", 10), (b_id, "Copa Gama", 25)):
                s.add(Campeonato(
                    organizador_id=dono, modalidade_id=mod.id, nome=f"{nome} {uuid.uuid4().hex[:4]}", local_nome="G", latitude=-20.4, longitude=-54.6,
                    data_inicio=hoje - timedelta(days=dias + 2), data_fim=hoje - timedelta(days=dias), inscricao_ate=hoje - timedelta(days=dias + 5),
                    max_equipes=8, status="encerrado",
                ))
            s.commit()
            admin_email = adm.email

        # organizador vê só os seus; não vê o filtro de organizador
        r = cli.get("/api/v1/campeonatos/encerrados", headers=org_a).json()
        nomes_a = [x["nome"] for x in r["itens"]]
        assert r["todos"] is False and any(n.startswith("Copa Alfa") for n in nomes_a) and any(n.startswith("Copa Beta") for n in nomes_a)
        assert not any(n.startswith("Copa Gama") for n in nomes_a)
        # busca por nome e por data
        assert [x["nome"][:9] for x in cli.get("/api/v1/campeonatos/encerrados", params={"q": "alfa"}, headers=org_a).json()["itens"]] == ["Copa Alfa"]
        recentes = cli.get("/api/v1/campeonatos/encerrados", params={"de": str(hoje - timedelta(days=20))}, headers=org_a).json()["itens"]
        assert recentes and all(x["data_fim"] >= str(hoje - timedelta(days=20)) for x in recentes)
        # usuário comum: sem acesso
        assert cli.get("/api/v1/campeonatos/encerrados", headers=comum).status_code == 403
        # administrador: todos, filtrando por organizador
        h_adm, adm_id = _conta(cli, "AdmNovo")
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.commit()
        todos = cli.get("/api/v1/campeonatos/encerrados", headers=h_adm).json()
        assert todos["todos"] is True and any(x["nome"].startswith("Copa Gama") for x in todos["itens"]) and any(x["nome"].startswith("Copa Alfa") for x in todos["itens"])
        so_b = cli.get("/api/v1/campeonatos/encerrados", params={"organizador": "orgb"}, headers=h_adm).json()["itens"]
        assert so_b and all(x["nome"].startswith("Copa Gama") for x in so_b)
