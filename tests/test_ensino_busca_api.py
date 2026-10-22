"""Ensino, fase 1: busca por proximidade, Central "Aulas e Treinamentos", API do profissional e visões pública/privada."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from playgo import contas, descoberta, ensino_busca, ofertas, planos, profissionais
from playgo.db import Session, agora
from playgo.descoberta import Filtros
from playgo.models import Usuario
from playgo.web.app import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64
PERTO = (-20.4535, -54.6201)


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


def _admin(s, fabrica):
    a = fabrica.atleta("Admin Busca")
    a.admin = True
    s.commit()
    return a


def _aprovado(s, fabrica, admin, nome):
    u = fabrica.atleta(nome)
    p = profissionais.salvar(s, u, {"nome_profissional": nome, "cidade": "Campo Grande", "telefone": "67999990000", "latitude": PERTO[0], "longitude": PERTO[1], "modalidades": ["futebol", "volei"]})
    profissionais.aceitar_termo(s, u)
    profissionais.enviar_documento(s, u, "identidade", PNG, "rg.png")
    profissionais.enviar_para_analise(s, u)
    for d in profissionais.documentos_de(p):
        profissionais.decidir_documento(s, admin, d.id, True)
    profissionais.decidir(s, admin, p.id, True)
    return u, p


def _publicada(s, u, titulo, **extra):
    base = {"tipo": "aula", "titulo": titulo, "modalidade": "futebol", "valor_aula": "80", "local_texto": "Arena Teste"} | extra
    o = ofertas.criar(s, u, base)
    ofertas.publicar(s, u, o.id)
    return o


def test_busca_so_mostra_aprovado_com_plano_e_respeita_filtros(s, fabrica):
    admin = _admin(s, fabrica)
    u1, p1 = _aprovado(s, fabrica, admin, "Prof Perto")
    u2, p2 = _aprovado(s, fabrica, admin, "Prof Sem Plano")
    o1 = _publicada(s, u1, "Futebol para crianças", faixa_etaria_min=6, faixa_etaria_max=10, aula_experimental=True)
    turma = ofertas.criar(s, u1, {"tipo": "turma", "titulo": "Turma de vôlei", "modalidade": "volei", "valor_mensal": "150", "local_texto": "Ginásio", "horarios": [{"dia_semana": 1, "hora_inicio": "19:00"}]})
    ofertas.publicar(s, u1, turma.id)
    _publicada(s, u2, "Aula do sem plano")
    a2 = planos.assinatura_de(s, u2, "profissional")
    a2.teste_ate = agora().date() - timedelta(days=30)  # o plano venceu: sai da busca sem apagar nada
    s.commit()

    f = Filtros(raio_km=5, tipos=("profissional", "oferta"))
    titulos = {o["titulo"] for o in ensino_busca.buscar_ofertas(s, PERTO[0], PERTO[1], f)}
    assert {"Futebol para crianças", "Turma de vôlei"} <= titulos and "Aula do sem plano" not in titulos
    profs = ensino_busca.buscar_profissionais(s, PERTO[0], PERTO[1], f)
    assert p1.nome_profissional in {p["nome"] for p in profs} and all(p["id"] != p2.id for p in profs)

    def busca(**kw):
        return {o["titulo"] for o in ensino_busca.buscar_ofertas(s, PERTO[0], PERTO[1], Filtros(raio_km=5, **kw))}

    assert busca(modalidades=["volei"]) == {"Turma de vôlei"}
    assert busca(tipo_oferta="turma") == {"Turma de vôlei"}
    assert busca(dia_semana=1) == {"Turma de vôlei"} and busca(dia_semana=4) == set()
    assert busca(periodo="noite") == {"Turma de vôlei"} and busca(periodo="manha") == set()
    assert busca(experimental=True) == {"Futebol para crianças"}
    assert "Futebol para crianças" in busca(idade=8) and "Futebol para crianças" not in busca(idade=14)
    assert busca(preco_max=100) == {"Futebol para crianças"}
    assert busca(q="crianças") == {"Futebol para crianças"}
    assert ensino_busca.buscar_ofertas(s, -20.9, -54.0, Filtros(raio_km=5)) == []  # longe: nada
    res = descoberta.aulas(s, u1, PERTO[0], PERTO[1], Filtros(raio_km=5, tipos=("profissional", "oferta")))
    assert [o["titulo"] for o in res["secoes"]["turmas"]] == ["Turma de vôlei"] and [o["titulo"] for o in res["secoes"]["experimentais"]] == ["Futebol para crianças"]
    assert {p["tipo"] for p in res["pontos"]} == {"profissional", "oferta"} and all(p["icone"] and p["cor"] for p in res["pontos"])  # pinos próprios
    ofertas.pausar(s, u1, o1.id)
    assert "Futebol para crianças" not in busca()


def _conta(cli, nome):
    r = cli.post("/api/v1/auth/cadastro", json={"nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]


def test_api_do_profissional_documentos_privados_aprovacao_e_central(banco):
    with TestClient(app) as cli:
        prof, _ = _conta(cli, "Instrutora")
        aluno, _ = _conta(cli, "Aluno")
        adm, adm_id = _conta(cli, "Adm")
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.commit()
        assert cli.get("/api/v1/ensino/meu", headers=prof).json()["perfil"] is None
        r = cli.put("/api/v1/ensino/meu/perfil", headers=prof, json={"nome_profissional": "Prof. Carla", "cidade": "Campo Grande", "telefone": "67999990000", "latitude": PERTO[0], "longitude": PERTO[1], "modalidades": ["futebol"]})
        assert r.status_code == 200 and r.json()["status"] == "rascunho"
        assert cli.post("/api/v1/ensino/meu/aceite", headers=prof).status_code == 200
        ruim = cli.post("/api/v1/ensino/meu/documentos", headers=prof, data={"tipo": "identidade"}, files={"arquivo": ("x.txt", b"nada", "text/plain")})
        assert ruim.status_code == 400
        ok = cli.post("/api/v1/ensino/meu/documentos", headers=prof, data={"tipo": "identidade"}, files={"arquivo": ("rg.png", PNG, "image/png")})
        assert ok.status_code == 201 and "documentos/" not in ok.text  # o caminho do arquivo nunca sai na resposta
        doc_id = ok.json()["documentos"][0]["id"]
        assert cli.get(f"/api/v1/ensino/documentos/{doc_id}/arquivo", headers=aluno).status_code == 403
        r = cli.get(f"/api/v1/ensino/documentos/{doc_id}/arquivo", headers=prof)
        assert r.status_code == 200 and r.content == PNG and r.headers["cache-control"] == "no-store"
        assert cli.get(f"/api/v1/ensino/documentos/{doc_id}/arquivo", headers=adm).status_code == 200
        assert cli.post("/api/v1/ensino/meu/analise", headers=prof).status_code == 200
        assert cli.get("/api/v1/admin/profissionais", headers=aluno).status_code == 403
        fila = cli.get("/api/v1/admin/profissionais", headers=adm).json()
        perfil_id = next(x["id"] for x in fila if x["nome"] == "Prof. Carla")
        assert cli.post(f"/api/v1/admin/profissionais/{perfil_id}/decidir", headers=adm, json={"aprovar": True}).status_code == 400  # documento ainda pendente
        assert cli.post(f"/api/v1/admin/profissionais/documentos/{doc_id}/decidir", headers=adm, json={"aprovar": True}).status_code == 200
        assert cli.post(f"/api/v1/admin/profissionais/{perfil_id}/decidir", headers=adm, json={"aprovar": True}).json()["status"] == "aprovado"

        o = cli.post("/api/v1/ensino/ofertas", headers=prof, json={"tipo": "turma", "titulo": "Turma de futebol", "modalidade": "futebol", "valor_mensal": 120, "local_texto": "Campo X", "horarios": [{"dia_semana": 2, "hora_inicio": "18:30"}]})
        assert o.status_code == 201, o.text
        oid = o.json()["id"]
        assert cli.get(f"/api/v1/ofertas/{oid}", headers=aluno).status_code == 404  # rascunho: só o dono vê
        assert cli.post(f"/api/v1/ensino/ofertas/{oid}/publicar", headers=aluno).status_code == 403
        assert cli.post(f"/api/v1/ensino/ofertas/{oid}/publicar", headers=prof).json()["status"] == "publicada"
        central = cli.get("/api/v1/aulas", headers=aluno, params={"lat": PERTO[0], "lng": PERTO[1], "raio": 10}).json()
        assert "Turma de futebol" in [x["titulo"] for x in central["ofertas"]] and "Prof. Carla" in [x["nome"] for x in central["profissionais"]]  # o banco de teste é compartilhado: pode haver outros
        pub = cli.get(f"/api/v1/profissionais/{perfil_id}", headers=aluno).json()
        assert pub["verificado"] is True and "documentos" not in pub and "status" not in pub and pub["ofertas"][0]["preco_texto"] == "R$ 120,00/mês"
        assert cli.get(f"/api/v1/ofertas/{oid}", headers=aluno).json()["horarios_texto"] == "Quarta 18:30"
        assert cli.get("/api/v1/aulas", headers=aluno, params={"dia_semana": 9}).status_code == 422
        meu = cli.get("/api/v1/ensino/meu", headers=prof).json()
        assert meu["ensino"] is True and meu["plano"]["status"] == "teste" and len(meu["ofertas"]) == 1
