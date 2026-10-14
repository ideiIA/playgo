"""Guia de locais (Explorar): só a administração cadastra; todos leem; campos obrigatórios e filtros."""

import uuid

from fastapi.testclient import TestClient

from playgo.db import Session
from playgo.models import Local, Usuario
from playgo.web.app import app

CORPO = {
    "tipo": "publico", "nome": "Praça da Liberdade", "endereco": "Av. Afonso Pena, s/n", "cidade": "Campo Grande",
    "latitude": -20.4697, "longitude": -54.6201, "horario_funcionamento": "Todos os dias: 6h às 22h", "telefone": "(67) 3000-0000",
    "regras": "Proibido som alto após as 21h.",
}


def test_guia_de_locais(banco):
    """Sem a sessão `s` aberta (o startup do app altera tabelas): monta tudo pela API."""
    with TestClient(app) as cli:
        def conta(nome):
            r = cli.post("/api/v1/auth/cadastro", json={
                "nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1",
                "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True,
            })
            return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]

        h_adm, adm_id = conta("Adm")
        h_x, _ = conta("Comum")
        with Session() as s:
            s.query(Local).delete()
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.commit()

        # só admin cadastra e lista a gestão
        assert cli.post("/api/v1/gestao/locais", json=CORPO, headers=h_x).status_code == 403
        assert cli.get("/api/v1/gestao/locais", headers=h_x).status_code == 403

        # obrigatórios: horário, telefone e endereço
        for campo in ("horario_funcionamento", "telefone", "endereco"):
            r = cli.post("/api/v1/gestao/locais", json={**CORPO, campo: "  "}, headers=h_adm)
            assert r.status_code == 400, campo
        assert cli.post("/api/v1/gestao/locais", json={**CORPO, "tipo": "outro"}, headers=h_adm).status_code == 400
        assert cli.post("/api/v1/gestao/locais", json={**CORPO, "email": "sem-arroba"}, headers=h_adm).status_code == 400

        # e-mail e regras são opcionais; loja sem esporte
        r = cli.post("/api/v1/gestao/locais", json=CORPO, headers=h_adm)
        assert r.status_code == 201
        publico = r.json()
        r = cli.post("/api/v1/gestao/locais", json={
            **{k: v for k, v in CORPO.items() if k != "regras"}, "tipo": "loja", "nome": "Loja do Atleta", "email": "loja@exemplo.com.br",
        }, headers=h_adm)
        assert r.status_code == 201 and r.json()["regras"] is None
        loja = r.json()

        # qualquer usuário logado vê no Explorar e no detalhe
        ex = cli.get("/api/v1/explorar", params={"raio": 0, "tipos": "local"}, headers=h_x).json()
        assert {x["nome"] for x in ex["locais"]} == {"Praça da Liberdade", "Loja do Atleta"}
        assert any(p["tipo"] == "local" for p in ex["pontos"])
        d = cli.get(f"/api/v1/locais/{publico['id']}", headers=h_x).json()
        assert d["regras"] == "Proibido som alto após as 21h." and d["telefone"] == "(67) 3000-0000"
        assert cli.get("/api/v1/explorar", params={"raio": 0, "q": "Loja", "tipos": "local"}, headers=h_x).json()["locais"][0]["id"] == loja["id"]

        # editar e ocultar: oculto some do Explorar e do detalhe, mas a gestão ainda o lista
        r = cli.put(f"/api/v1/gestao/locais/{loja['id']}", json={**CORPO, "tipo": "loja", "nome": "Loja do Atleta 2"}, headers=h_adm)
        assert r.status_code == 200 and r.json()["nome"] == "Loja do Atleta 2"
        assert cli.post(f"/api/v1/gestao/locais/{loja['id']}/ativo", params={"ativo": "false"}, headers=h_adm).json()["ativo"] is False
        ex = cli.get("/api/v1/explorar", params={"raio": 0, "tipos": "local"}, headers=h_x).json()
        assert [x["id"] for x in ex["locais"]] == [publico["id"]]
        assert cli.get(f"/api/v1/locais/{loja['id']}", headers=h_x).status_code == 404
        assert len(cli.get("/api/v1/gestao/locais", headers=h_adm).json()) == 2

        # site: página do local, lista da gestão e seção no Explorar
        email = f"{uuid.uuid4().hex[:8]}@teste.local"
        site = TestClient(app)
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.email: email})
            s.commit()
        # a senha é a da conta de teste (mesmo hash): entra pelo formulário
        # (o e-mail original foi trocado só para termos um valor conhecido)
        h = cli.post("/api/v1/auth/entrar", json={"email": email, "senha": "senha-de-teste-1"})
        assert h.status_code == 200
        assert site.post("/entrar", data={"email": email, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303
        assert "Regras de utilização" in site.get(f"/locais/{publico['id']}").text
        assert "Cadastrar local" in site.get("/gestao/locais").text
        assert "Locais e lojas" in site.get("/explorar?raio=0").text
        assert "Como preencher" in site.get("/gestao/locais/importar").text
        modelo = site.get("/gestao/locais/modelo.csv")
        assert modelo.status_code == 200 and "horario_funcionamento" in modelo.text
        r = site.post("/gestao/locais/importar", data={"tipo": "loja"}, files={"arquivo": ("l.csv", "nome;endereco;telefone;horario_funcionamento;latitude;longitude\nLoja Z;Rua Z;1;9h;-20,4;-54,6\n".encode())})
        assert r.status_code == 200 and "cadastrado" in r.text
