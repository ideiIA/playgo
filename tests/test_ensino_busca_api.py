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
from playgo.api import ensino as api_ensino
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
    suf = " #" + uuid.uuid4().hex[:6]  # o banco de teste é compartilhado: só conta o que este teste criou
    admin = _admin(s, fabrica)
    u1, p1 = _aprovado(s, fabrica, admin, "Prof Perto" + suf)
    u2, p2 = _aprovado(s, fabrica, admin, "Prof Sem Plano" + suf)
    o1 = _publicada(s, u1, "Futebol para crianças" + suf, faixa_etaria_min=6, faixa_etaria_max=10, aula_experimental=True)
    turma = ofertas.criar(s, u1, {"tipo": "turma", "titulo": "Turma de vôlei" + suf, "modalidade": "volei", "valor_mensal": "150", "local_texto": "Ginásio", "horarios": [{"dia_semana": 1, "hora_inicio": "19:00"}]})
    ofertas.publicar(s, u1, turma.id)
    _publicada(s, u2, "Aula do sem plano" + suf)
    a2 = planos.assinatura_de(s, u2, "profissional")
    a2.teste_ate = agora().date() - timedelta(days=30)  # o plano venceu: sai da busca sem apagar nada
    s.commit()
    fut, vol = "Futebol para crianças" + suf, "Turma de vôlei" + suf

    def busca(**kw):
        kw.setdefault("raio_km", 5)
        return {o["titulo"] for o in ensino_busca.buscar_ofertas(s, PERTO[0], PERTO[1], Filtros(**kw), limite=500) if o["titulo"].endswith(suf)}

    f = Filtros(raio_km=5, tipos=("profissional", "oferta"))
    assert busca() == {fut, vol}  # a oferta de quem está sem plano não aparece
    profs = ensino_busca.buscar_profissionais(s, PERTO[0], PERTO[1], f, limite=500)
    assert p1.nome_profissional in {p["nome"] for p in profs} and all(p["id"] != p2.id for p in profs)
    assert busca(modalidades=["volei"]) == {vol}
    assert busca(tipo_oferta="turma") == {vol}
    assert busca(dia_semana=1) == {vol} and busca(dia_semana=4) == set()
    assert busca(periodo="noite") == {vol} and busca(periodo="manha") == set()
    assert busca(experimental=True) == {fut}
    assert fut in busca(idade=8) and fut not in busca(idade=14)
    assert busca(preco_max=100) == {fut}
    assert busca(q="crianças") == {fut}
    assert ensino_busca.buscar_ofertas(s, -20.9, -54.0, Filtros(raio_km=5)) == []  # longe: nada
    res = descoberta.aulas(s, admin, PERTO[0], PERTO[1], Filtros(raio_km=5, tipos=("profissional", "oferta"), q=suf.strip()))
    assert [o["titulo"] for o in res["secoes"]["turmas"]] == [vol] and [o["titulo"] for o in res["secoes"]["experimentais"]] == [fut]
    assert {p["tipo"] for p in res["pontos"]} == {"profissional", "oferta"} and all(p["icone"] and p["cor"] for p in res["pontos"])  # pinos próprios
    ofertas.pausar(s, u1, o1.id)
    assert fut not in busca()


def _conta(cli, nome):
    r = cli.post("/api/v1/auth/cadastro", json={"nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]


def test_ensino_fechado_para_quem_nao_e_admin(banco):
    with TestClient(app) as cli:
        h, _ = _conta(cli, "Comum")
        assert cli.get("/api/v1/aulas", headers=h).status_code == 404
        assert cli.get("/api/v1/ensino/meu", headers=h).status_code == 404
        me = cli.get("/api/v1/me", headers=h).json()
        assert me["permissoes"]["ensino_menu"] is False
        assert cli.get("/api/v1/planos", headers=h).json()["profissional"] is None
        explorar = cli.get("/api/v1/explorar", headers=h, params={"lat": PERTO[0], "lng": PERTO[1], "tipos": "profissional,oferta"})
        assert explorar.status_code == 200 and explorar.json()["profissionais"] == [] and explorar.json()["ofertas"] == []


def test_api_do_profissional_documentos_privados_aprovacao_e_central(banco):
    app.dependency_overrides[api_ensino._so_admin] = lambda: None  # o fluxo abaixo testa as regras do módulo; a trava de admin tem teste próprio
    try:
        _fluxo_api_do_profissional()
    finally:
        app.dependency_overrides.pop(api_ensino._so_admin, None)


def _fluxo_api_do_profissional():
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
        central = cli.get("/api/v1/aulas", headers=adm, params={"lat": PERTO[0], "lng": PERTO[1], "raio": 10}).json()
        assert "Turma de futebol" in [x["titulo"] for x in central["ofertas"]] and "Prof. Carla" in [x["nome"] for x in central["profissionais"]]  # o banco de teste é compartilhado: pode haver outros
        pub = cli.get(f"/api/v1/profissionais/{perfil_id}", headers=aluno).json()
        assert pub["verificado"] is True and "documentos" not in pub and "status" not in pub and pub["ofertas"][0]["preco_texto"] == "R$ 120,00/mês"
        assert cli.get(f"/api/v1/ofertas/{oid}", headers=aluno).json()["horarios_texto"] == "Quarta 18:30"
        assert cli.get("/api/v1/aulas", headers=aluno, params={"dia_semana": 9}).status_code == 422
        meu = cli.get("/api/v1/ensino/meu", headers=prof).json()
        assert meu["ensino"] is True and meu["plano"]["status"] == "teste" and len(meu["ofertas"]) == 1
