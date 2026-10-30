"""Fluxos HTTP ponta a ponta: a API do app (token Bearer) e as páginas do site (cookie de sessão)."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from playgo.db import agora

PERTO = (-20.4535, -54.6201)


@pytest.fixture(scope="module")
def cliente(banco):
    from playgo.web.app import app

    with TestClient(app) as c:  # roda o lifespan (tabelas + modalidades)
        yield c


def _cadastrar(cliente, nome="Teste"):
    email = f"{uuid.uuid4().hex[:10]}@teste.local"
    r = cliente.post("/api/v1/auth/cadastro", json={"nome": nome, "email": email, "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True, "maior_de_idade": True, "consent_localizacao": True})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, email


@pytest.fixture()
def auth(cliente):
    return _cadastrar(cliente)[0]


def _ids_modalidades(cliente, auth):
    return {m["codigo"]: m["id"] for m in cliente.get("/api/v1/modalidades", headers=auth).json()}


def test_api_exige_autenticacao(cliente):
    assert cliente.get("/api/v1/feed").status_code == 401
    assert cliente.get("/api/v1/me", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_cadastro_login_e_perfil(cliente):
    auth, email = _cadastrar(cliente, "Maria Souza")
    assert cliente.post("/api/v1/auth/cadastro", json={"nome": "X", "email": email, "senha": "senha-de-teste-1", "usuario": "outro.nome", "aceito_termos": True, "maior_de_idade": True}).status_code == 400  # e-mail repetido
    assert cliente.post("/api/v1/auth/cadastro", json={"nome": "X", "email": "novo@t.local", "senha": "curta", "usuario": "novo.nome", "aceito_termos": True, "maior_de_idade": True}).status_code == 400
    assert cliente.post("/api/v1/auth/entrar", json={"email": email, "senha": "errada-errada"}).status_code == 401
    ok = cliente.post("/api/v1/auth/entrar", json={"email": email.upper(), "senha": "senha-de-teste-1"})
    assert ok.status_code == 200 and ok.json()["usuario"]["iniciais"] == "MS"

    mods = _ids_modalidades(cliente, auth)
    assert {"futebol", "volei", "beach_tennis", "corrida", "padel"} <= set(mods)
    r = cliente.patch("/api/v1/me", headers=auth, json={"latitude": PERTO[0], "longitude": PERTO[1], "raio_km": 8, "disponibilidade": ["noite", "invalida"], "cidade": "Campo Grande"})
    assert r.json()["raio_km"] == 8 and r.json()["disponibilidade"] == ["noite"]
    r = cliente.put("/api/v1/me/esportes", headers=auth, json=[{"modalidade_id": mods["volei"], "nivel": "avancado"}, {"modalidade_id": mods["corrida"], "nivel": "x"}])
    assert [(e["codigo"], e["nivel"]) for e in r.json()["esportes"]] == [("volei", "avancado"), ("corrida", "intermediario")]


def test_fluxo_completo_da_atividade(cliente):
    org, _ = _cadastrar(cliente, "Organizador")
    atleta, _ = _cadastrar(cliente, "Atleta Um")
    outro, _ = _cadastrar(cliente, "Atleta Dois")
    mods = _ids_modalidades(cliente, org)
    for h in (org, atleta, outro):
        cliente.patch("/api/v1/me", headers=h, json={"latitude": PERTO[0], "longitude": PERTO[1]})
    cliente.put("/api/v1/me/esportes", headers=atleta, json=[{"modalidade_id": mods["futebol"], "nivel": "intermediario"}])

    corpo = {"modalidade_id": mods["futebol"], "nome": "Futebol de quarta", "inicio": (agora() + timedelta(hours=1)).isoformat(timespec="minutes"), "max_participantes": 2,
             "local_nome": "Quadra X", "latitude": PERTO[0], "longitude": PERTO[1], "valor": "25", "falta_gente": True}
    r = cliente.post("/api/v1/atividades", headers=org, json=corpo)
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["confirmados"], a["vagas"], a["falta_gente"], a["rotulo_falta"]) == (1, 1, True, "FALTA 1 JOGADOR") and a["valor_texto"] == "R$ 25"

    # aparece no feed e no explorar de quem está perto, com destaque
    feed = cliente.get("/api/v1/feed", headers=atleta).json()
    assert a["id"] in [x["id"] for x in feed["urgentes"]]
    ex = cliente.get("/api/v1/explorar?raio=5&tipos=atividade&modalidades=futebol", headers=atleta).json()
    assert a["id"] in [x["id"] for x in ex["atividades"]] and any(p["id"] == a["id"] and p["urgente"] for p in ex["pontos"])

    # o atleta compatível foi avisado (RF-016)
    avisos = cliente.get("/api/v1/notificacoes", headers=atleta).json()
    assert avisos["nao_lidas"] >= 1 and "Falta 1 jogador" in avisos["itens"][0]["titulo"]

    # EU VOU → confirmado, lotou → o próximo vai para a espera e o destaque some
    r = cliente.post(f"/api/v1/atividades/{a['id']}/entrar", headers=atleta).json()
    assert (r["minha_participacao"], r["vagas"], r["falta_gente"]) == ("confirmado", 0, False)
    r = cliente.post(f"/api/v1/atividades/{a['id']}/entrar", headers=outro).json()
    assert (r["minha_participacao"], r["posicao_espera"]) == ("espera", 1)

    # só o organizador vê/gerencia
    assert cliente.post(f"/api/v1/atividades/{a['id']}/cancelar", headers=atleta).status_code == 403
    assert cliente.post(f"/api/v1/atividades/{a['id']}/sair", headers=atleta).json()["minha_participacao"] is None
    assert cliente.get(f"/api/v1/atividades/{a['id']}", headers=outro).json()["minha_participacao"] == "confirmado"  # subiu da espera

    mine = cliente.get("/api/v1/minhas-atividades", headers=outro).json()
    assert [x["id"] for x in mine["proximas"]] == [a["id"]]
    assert cliente.post(f"/api/v1/atividades/{a['id']}/cancelar", headers=org).json()["status"] == "cancelada"
    assert cliente.post(f"/api/v1/atividades/{a['id']}/entrar", headers=atleta).status_code == 400


def test_validacoes_da_atividade(cliente, auth):
    mods = _ids_modalidades(cliente, auth)
    base = {"modalidade_id": mods["futebol"], "nome": "X", "inicio": (agora() + timedelta(hours=2)).isoformat(timespec="minutes"), "max_participantes": 10, "local_nome": "Q", "latitude": PERTO[0], "longitude": PERTO[1]}
    assert cliente.post("/api/v1/atividades", headers=auth, json={**base, "inicio": (agora() - timedelta(days=1)).isoformat()}).status_code == 400
    assert cliente.post("/api/v1/atividades", headers=auth, json={**base, "max_participantes": 1}).status_code == 400
    sem_local = {k: v for k, v in base.items() if k not in ("latitude", "longitude")}
    assert cliente.post("/api/v1/atividades", headers=auth, json=sem_local).status_code == 400
    assert cliente.post("/api/v1/atividades", headers=auth, json={**base, "nivel": "deus"}).status_code == 400


def test_atividade_sem_quadra_ponto_de_encontro(cliente, auth):
    mods = _ids_modalidades(cliente, auth)
    corpo = {"modalidade_id": mods["corrida"], "nome": "Corre CG 7 km", "inicio": (agora() + timedelta(days=1)).isoformat(timespec="minutes"), "max_participantes": 30,
             "local_nome": "Parque das Nações", "latitude": PERTO[0], "longitude": PERTO[1], "percurso": "2 voltas", "duracao_min": 60}
    a = cliente.post("/api/v1/atividades", headers=auth, json=corpo).json()
    assert a["sem_quadra"] and a["percurso"] == "2 voltas" and a["arena_id"] is None


def test_gestao_arena_pela_api(cliente):
    gestor, email_gestor = _cadastrar(cliente, "Gestor")
    atleta, email_atleta = _cadastrar(cliente, "Atleta")
    mods = _ids_modalidades(cliente, gestor)
    cliente.patch("/api/v1/me", headers=atleta, json={"latitude": PERTO[0], "longitude": PERTO[1]})
    cliente.put("/api/v1/me/esportes", headers=atleta, json=[{"modalidade_id": mods["beach_tennis"], "nivel": "intermediario"}])

    assert cliente.get("/api/v1/gestao/arenas", headers=gestor).json() == []
    ar = cliente.post("/api/v1/gestao/arenas", headers=gestor, json={"nome": "Beach Teste", "latitude": PERTO[0], "longitude": PERTO[1], "abre": "00:00:00", "fecha": "23:59:00"})
    assert ar.status_code == 201
    arena_id = ar.json()["id"]
    q = cliente.post(f"/api/v1/gestao/arenas/{arena_id}/quadras", headers=gestor, json={"nome": "Areia 1", "modalidades": ["beach_tennis"], "valor_hora": "80"})
    quadra_id = q.json()["quadras_lista"][0]["id"]

    # outra pessoa não gerencia
    assert cliente.get(f"/api/v1/gestao/arenas/{arena_id}/painel", headers=atleta).status_code == 403
    assert cliente.post(f"/api/v1/gestao/quadras/{quadra_id}/reservas", headers=atleta, json={"inicio": "2030-01-01T10:00", "fim": "2030-01-01T11:00"}).status_code == 403

    inicio = (agora() + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    r = cliente.post(f"/api/v1/gestao/quadras/{quadra_id}/divulgar", headers=gestor, json={"inicio": inicio.isoformat(), "fim": (inicio + timedelta(hours=1)).isoformat()})
    assert r.status_code == 201, r.text
    assert any(i["titulo"].endswith(("hoje", "amanhã")) or "Quadra disponível" in i["titulo"] for i in cliente.get("/api/v1/notificacoes", headers=atleta).json()["itens"])
    pagina = cliente.get(f"/api/v1/arenas/{arena_id}", headers=atleta).json()
    assert pagina["horarios"][0]["valor_texto"] == "R$ 80/hora" and not pagina["sou_gestor"]

    painel = cliente.get(f"/api/v1/gestao/arenas/{arena_id}/painel", headers=gestor).json()
    assert painel["horarios_divulgados"] >= 0 and "ocupacao_pct" in painel
    ag = cliente.get(f"/api/v1/gestao/arenas/{arena_id}/agenda", headers=gestor).json()
    assert ag["quadras"][0]["nome"] == "Areia 1" and len(ag["linhas"]) == 24

    # reservar o horário divulgado tira da vitrine; reserva em cima de reserva é recusada
    assert cliente.post(f"/api/v1/gestao/quadras/{quadra_id}/reservas", headers=gestor, json={"inicio": inicio.isoformat(), "fim": (inicio + timedelta(hours=1)).isoformat(), "rotulo": "Do João"}).status_code == 201
    assert cliente.post(f"/api/v1/gestao/quadras/{quadra_id}/reservas", headers=gestor, json={"inicio": inicio.isoformat(), "fim": (inicio + timedelta(hours=1)).isoformat()}).status_code == 400
    assert cliente.get(f"/api/v1/arenas/{arena_id}", headers=atleta).json()["horarios"] == []

    # "Criar jogo aqui": o formulário do site só oferece as modalidades da quadra e mostra qual quadra será reservada
    site = TestClient(cliente.app)
    assert site.post("/entrar", data={"email": email_gestor, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303
    pagina_nova = site.get(f"/atividades/nova?arena_id={arena_id}&quadra_id={quadra_id}&inicio={inicio.isoformat(timespec='minutes')}")
    assert pagina_nova.status_code == 200, pagina_nova.text[:300]
    # reserva pelo atleta: política da arena (na arena), reservar, listar e cancelar
    ar_atual = cliente.get(f"/api/v1/arenas/{arena_id}", headers=gestor).json()
    assert ar_atual["reserva"]["pagamento"] == "local" and ar_atual["reserva"]["formas"] == ["local"]
    put = cliente.put(f"/api/v1/gestao/arenas/{arena_id}", headers=gestor, json={"nome": "Beach Teste", "latitude": PERTO[0], "longitude": PERTO[1], "abre": "00:00:00", "fecha": "23:59:00", "pagamento_reserva": "ambos", "cancel_horas": 12, "reembolso_pct": 100, "reembolso_pct_fora": 20})
    assert put.status_code == 200 and put.json()["reserva"]["cancel_horas"] == 12 and put.json()["reserva"]["formas"] == ["local"]  # online só com recebimento ativo
    outro = inicio + timedelta(hours=2)
    h2 = cliente.post(f"/api/v1/gestao/quadras/{quadra_id}/divulgar", headers=gestor, json={"inicio": outro.isoformat(), "fim": (outro + timedelta(hours=1)).isoformat()}).json()["id"]
    r = cliente.post(f"/api/v1/horarios/{h2}/reservar", headers=atleta, json={"forma": "local"})
    assert r.status_code == 201, r.text
    assert r.json()["forma_pagamento"] == "local" and r.json()["valor"] == 80.0
    assert [x["id"] for x in cliente.get("/api/v1/me/reservas", headers=atleta).json()] == [r.json()["id"]]
    site_atleta = TestClient(cliente.app)
    assert site_atleta.post("/entrar", data={"email": email_atleta, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303
    assert "Minhas reservas" in site_atleta.get("/reservas").text and "Beach Teste" in site_atleta.get("/reservas").text
    terceiro = inicio + timedelta(hours=4)
    h3 = cliente.post(f"/api/v1/gestao/quadras/{quadra_id}/divulgar", headers=gestor, json={"inicio": terceiro.isoformat(), "fim": (terceiro + timedelta(hours=1)).isoformat()}).json()["id"]
    tela = site_atleta.get(f"/horarios/{h3}/reservar")
    assert tela.status_code == 200 and "Na arena" in tela.text and "Cancelamento" in tela.text
    assert site_atleta.get(f"/arenas/{arena_id}").text.count("/reservar") >= 1
    assert cliente.post(f"/api/v1/reservas/{r.json()['id']}/cancelar", headers=gestor).status_code == 404  # só quem reservou cancela
    assert cliente.post(f"/api/v1/reservas/{r.json()['id']}/cancelar", headers=atleta).json()["cancelada"] is True
    assert "Beach Teste — Areia 1" in pagina_nova.text and "Beach Tennis" in pagina_nova.text and "Futebol" not in pagina_nova.text.split('id="modalidade_id"')[1].split("</select>")[0]


def test_grupos_e_campeonatos_pela_api(cliente):
    a, _ = _cadastrar(cliente, "Dono")
    b, _ = _cadastrar(cliente, "Membro")
    mods = _ids_modalidades(cliente, a)
    g = cliente.post("/api/v1/grupos", headers=a, json={"nome": "Corre CG", "modalidade_id": mods["corrida"], "entrada": "livre"}).json()
    assert g["sou_admin"] and g["membros"] == 1
    assert cliente.post(f"/api/v1/grupos/{g['id']}/entrar", headers=b).json()["sou_membro"]
    assert cliente.post(f"/api/v1/grupos/{g['id']}/mensagens", headers=b, json={"texto": "Oi!"}).status_code == 201
    assert cliente.post(f"/api/v1/grupos/{g['id']}/mensagens", headers=b, json={"texto": "Aviso", "aviso": True}).status_code == 403
    assert [m["texto"] for m in cliente.get(f"/api/v1/grupos/{g['id']}/mensagens", headers=a).json()] == ["Oi!"]
    lista = cliente.get("/api/v1/grupos", headers=b).json()
    assert g["id"] in [x["id"] for x in lista["meus"]]

    hoje = agora().date()
    camp = {"modalidade_id": mods["beach_tennis"], "nome": "Open Beach", "data_inicio": str(hoje + timedelta(days=20)), "inscricao_ate": str(hoje + timedelta(days=10)), "max_equipes": 8,
            "atletas_por_equipe": 2, "local_nome": "Beach Club", "latitude": PERTO[0], "longitude": PERTO[1], "valor_inscricao": "160"}
    c = cliente.post("/api/v1/campeonatos", headers=a, json=camp)
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    r = cliente.post(f"/api/v1/campeonatos/{cid}/equipes", headers=b, json={"nome": "Dupla Dinâmica"}).json()
    assert r["equipes"] == 1 and r["minha_situacao"]["estado"] == "capitao" and r["vagas"] == 7
    eid = r["equipes_lista"][0]["id"]
    assert cliente.post(f"/api/v1/equipes/{eid}/decidir", headers=b, json={"confirmar": True}).status_code == 403
    assert cliente.post(f"/api/v1/equipes/{eid}/decidir", headers=a, json={"confirmar": True}).json()["equipes_lista"][0]["status"] == "confirmada"
    usuario_a = cliente.get("/api/v1/me", headers=a).json()["usuario"]
    achados = cliente.get(f"/api/v1/atletas?q=@{usuario_a[:6].upper()}", headers=b).json()
    assert [x["usuario"] for x in achados] == [usuario_a] and set(achados[0]) == {"id", "usuario", "arroba", "iniciais"}  # nada de nome real nem e-mail
    assert cliente.get("/api/v1/atletas?q=D", headers=b).status_code == 422


# ---------------------------------------------------------------- site


def _entrar_no_site(cliente):
    email = f"{uuid.uuid4().hex[:10]}@teste.local"
    r = cliente.post("/cadastro", data={"nome": "Carlos Almeida", "email": email, "senha": "senha-de-teste-1", "usuario": f"c{uuid.uuid4().hex[:12]}", "aceito_termos": "1", "maior_de_idade": "1", "consent_localizacao": "1"}, follow_redirects=False)
    assert r.status_code == 303
    return email


def test_site_exige_login_e_renderiza_as_telas(cliente, banco):
    anonimo = TestClient(cliente.app)
    pagina_inicial = anonimo.get("/", follow_redirects=False)  # sem login: página de divulgação, com os caminhos de entrada
    assert pagina_inicial.status_code == 200 and 'href="/cadastro"' in pagina_inicial.text and 'href="/entrar"' in pagina_inicial.text and 'href="/app/"' in pagina_inicial.text
    assert "Encontre onde jogar" in pagina_inicial.text and "Organizador" in pagina_inicial.text
    assert anonimo.get("/entrar").status_code == 200
    assert anonimo.get("/app/").status_code == 200  # PWA
    assert anonimo.get("/app/manifest.json").json()["short_name"] == "PlayGo"
    assert anonimo.get("/app/sw.js").status_code == 200

    site = TestClient(cliente.app)
    _entrar_no_site(site)
    for caminho in ("/", "/explorar", "/explorar?raio=5&quando=hoje&com_vagas=1&q=futebol", "/atividades/nova", "/grupos", "/grupos/novo", "/campeonatos", "/campeonatos/novo", "/gestao", "/gestao/arenas/nova", "/perfil", "/notificacoes"):
        r = site.get(caminho)
        assert r.status_code == 200, f"{caminho}: {r.status_code} {r.text[:300]}"
        assert "PlayGo" in r.text


def test_site_fluxo_criar_e_participar(cliente, banco):
    dono, atleta = TestClient(cliente.app), TestClient(cliente.app)
    _entrar_no_site(dono)
    _entrar_no_site(atleta)
    from playgo.db import Session
    from playgo.models import Modalidade
    from sqlalchemy import select

    with Session() as s:
        futebol = s.scalar(select(Modalidade.id).where(Modalidade.codigo == "futebol"))
    amanha = (agora() + timedelta(days=1)).date().isoformat()
    r = dono.post("/atividades", data={"modalidade_id": futebol, "nome": "Futebol no site", "data": amanha, "hora": "20:00", "max_participantes": 6, "local_nome": "Quadra", "latitude": PERTO[0], "longitude": PERTO[1], "valor": "25,50", "falta_gente": "1"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    pagina = dono.get(r.headers["location"])
    assert pagina.status_code == 200 and "Futebol no site" in pagina.text and "R$ 25,50" in pagina.text and "Cancelar atividade" in pagina.text

    detalhe = atleta.get(r.headers["location"])
    assert "EU VOU" in detalhe.text and "Cancelar atividade" not in detalhe.text
    ir = atleta.post(r.headers["location"] + "/entrar", follow_redirects=True)
    assert ir.status_code == 200 and "Você está confirmado" in ir.text

    # erro de regra volta para a página com a mensagem
    ruim = dono.post("/atividades", data={"modalidade_id": futebol, "nome": "Passado", "data": "2020-01-01", "hora": "20:00", "max_participantes": 6, "local_nome": "Q", "latitude": PERTO[0], "longitude": PERTO[1]}, headers={"referer": "http://testserver/atividades/nova"}, follow_redirects=True)
    assert "precisam estar no futuro" in ruim.text


def test_site_login_errado_e_sair(cliente, banco):
    c = TestClient(cliente.app)
    r = c.post("/entrar", data={"email": "ninguem@teste.local", "senha": "qualquer-coisa"})
    assert r.status_code == 400 and "incorretos" in r.text
    email = _entrar_no_site(c)
    assert c.post("/sair", follow_redirects=False).status_code == 303
    assert "Criar minha conta" in c.get("/").text  # deslogado volta para a página de divulgação
    assert c.post("/entrar", data={"email": email, "senha": "senha-de-teste-1"}, follow_redirects=False).status_code == 303


def test_pagina_de_notificacoes_mostra_o_texto(cliente, banco):
    """Regressão: o menu usava a variável `itens` e escondia a lista, deixando as notificações em branco."""
    from playgo import notificacoes
    from playgo.db import Session
    from playgo.models import Usuario
    from sqlalchemy import select

    site = TestClient(cliente.app)
    email = _entrar_no_site(site)
    with Session() as s:
        u = s.scalar(select(Usuario).where(Usuario.email == email))
        notificacoes.avisar(s, u.id, "vaga", "⚽ Falta 1 jogador perto de você!", "Futebol hoje às 20h, a 1,7 km.", "/atividades/1", None, "teste-pagina")
        s.commit()
    r = site.get("/notificacoes")
    assert r.status_code == 200 and "Falta 1 jogador perto de você!" in r.text and "Futebol hoje às 20h" in r.text and 'href="/atividades/1"' in r.text
