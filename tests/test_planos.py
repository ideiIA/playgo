"""Planos e mensalidades: limites do gratuito, teste grátis, tolerância, cortesia do admin, Asaas (simulado) e HTTP 402."""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from playgo import arenas, atividades, campeonatos, cobranca, planos
from playgo.atividades import NovaAtividade
from playgo.campeonatos import NovoCampeonato
from playgo.config import settings
from playgo.db import Session, agora
from playgo.erros import ErroNegocio, NaoEncontrado, PlanoNecessario, SemPermissao
from playgo.models import Assinatura, Notificacao, Pagamento, Usuario

PERTO = (-20.4535, -54.6201)


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    """O primeiro cadastro do banco vira administrador; aqui ele não pode ser o 'atleta' do teste."""
    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            from playgo import contas

            a = contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)
            assert a.admin


def _jogo(s, fabrica, org, max_p=10, **kw):
    return atividades.criar(
        s, org,
        NovaAtividade(modalidade_id=fabrica.mod("futebol").id, nome="Racha", inicio=agora() + timedelta(days=1), max_participantes=max_p, local_nome="Q", latitude=PERTO[0], longitude=PERTO[1], **kw),
    )


def _camp(s, fabrica, org):
    hoje = agora().date()
    return campeonatos.criar(
        s, org,
        NovoCampeonato(modalidade_id=fabrica.mod("futsal").id, nome="Copa", data_inicio=hoje + timedelta(days=20), inscricao_ate=hoje + timedelta(days=10), max_equipes=8, local_nome="G", latitude=PERTO[0], longitude=PERTO[1]),
    )


def _queimar_testes(s, u, plano_atual="gratuito"):
    """Pessoa que já usou os dois testes e está fora da tolerância."""
    s.add(Assinatura(usuario_id=u.id, plano="arena", status="cancelada", origem="teste", testes_usados=["pro", "organizador", "arena"], vigente_ate=agora().date() - timedelta(days=60)))
    s.commit()


def _admin(fabrica, s):
    a = fabrica.atleta("Admin")
    a.admin = True
    s.commit()
    return a


# ---------------------------------------------------------------- usuário (gratuito) e Pro


def _dar_pro(s, fabrica, u):
    planos.conceder(s, _admin(fabrica, s), u.id, "pro", agora().date() + timedelta(days=30))


def test_usuario_gratuito_nao_organiza_e_o_primeiro_pedido_abre_o_teste_do_pro(s, fabrica):
    u = fabrica.atleta("Usuario")
    sit = planos.situacao(s, u)
    assert (sit["plano"], sit["status"]) == ("gratuito", "gratuito") and planos.assinatura_de(s, u) is None
    assert sit["teste_disponivel"] == ["pro", "organizador", "arena"]
    assert planos.regras(s, "gratuito").pode_atividade is False and planos.regras(s, "pro").pode_atividade is True
    _jogo(s, fabrica, u, max_p=30)  # organizar atividade é do Pro: abre o teste de 30 dias
    sit = planos.situacao(s, u)
    assert (sit["plano"], sit["status"]) == ("pro", "teste") and planos.assinatura_de(s, u).testes_usados == ["pro"]
    assert sit["teste_disponivel"] == ["organizador", "arena"]


def test_passou_do_limite_do_pro_comeca_o_teste_gratis_do_organizador(s, fabrica):
    u = fabrica.atleta("Organizador em potencial")
    _dar_pro(s, fabrica, u)
    _jogo(s, fabrica, u, max_p=30)  # dentro do limite do Pro
    assert planos.situacao(s, u)["plano"] == "pro"
    _jogo(s, fabrica, u, max_p=40)  # > 30 participantes
    sit = planos.situacao(s, u)
    a = planos.assinatura_de(s, u)
    assert (sit["plano"], sit["status"]) == ("organizador", "teste")
    assert a.teste_ate == agora().date() + timedelta(days=settings.teste_dias) and a.testes_usados == ["organizador"]
    assert s.query(Notificacao).filter(Notificacao.usuario_id == u.id, Notificacao.titulo.like("%Teste do plano%")).count() == 1
    assert sit["teste_disponivel"] == ["arena"]  # o do Arena ainda não foi usado; o do Pro já está incluído


def test_limite_de_atividades_abertas(s, fabrica):
    u = fabrica.atleta("Muitos jogos")
    _dar_pro(s, fabrica, u)
    for _ in range(5):
        _jogo(s, fabrica, u)
    assert planos.situacao(s, u)["plano"] == "pro"
    _jogo(s, fabrica, u)  # a 6ª abre o teste do Organizador
    sit = planos.situacao(s, u)
    assert (sit["plano"], sit["status"]) == ("organizador", "teste")


def test_campeonato_e_arena_pedem_plano_com_teste_automatico(s, fabrica):
    u = fabrica.atleta("Gestor")
    _camp(s, fabrica, u)
    assert planos.situacao(s, u)["plano"] == "organizador"
    arenas.criar(s, u, "Arena Teste", *PERTO)  # precisa do Arena: troca o teste, que cobre também o Organizador
    sit = planos.situacao(s, u)
    assert sit["plano"] == "arena" and sit["teste_disponivel"] == []
    _camp(s, fabrica, u)  # plano Arena inclui tudo do Organizador


def test_sem_teste_disponivel_vira_plano_necessario(s, fabrica):
    u = fabrica.atleta("Já testou")
    _queimar_testes(s, u)
    assert planos.situacao(s, u)["status"] == "vencida"
    with pytest.raises(PlanoNecessario) as e:
        _camp(s, fabrica, u)
    assert e.value.plano == "organizador" and "Organizador" in str(e.value)
    with pytest.raises(PlanoNecessario) as e2:
        arenas.criar(s, u, "Arena", *PERTO)
    assert e2.value.plano == "arena"
    with pytest.raises(PlanoNecessario) as e3:
        _jogo(s, fabrica, u, max_p=20)  # o plano Usuário não organiza atividades
    assert e3.value.plano == "pro" and "Pro" in str(e3.value)


def test_participar_continua_gratis_mesmo_vencido(s, fabrica):
    from playgo import vagas

    org, vencido = fabrica.atleta("Org"), fabrica.atleta("Vencido")
    _queimar_testes(s, vencido)
    a = _jogo(s, fabrica, org)
    assert vagas.entrar(s, a.id, vencido).status == "confirmado"


# ---------------------------------------------------------------- vencimento e tolerância


def test_tolerancia_e_bloqueio(s, fabrica):
    u = fabrica.atleta("Vai vencer")
    _camp(s, fabrica, u)  # abre o teste
    a = planos.assinatura_de(s, u)
    hoje = agora().date()
    # último dia do teste: ainda 'teste'
    a.teste_ate = hoje
    s.commit()
    assert planos.situacao(s, u)["status"] == "teste"
    # venceu ontem: tolerância (segue funcionando)
    a.teste_ate = hoje - timedelta(days=1)
    s.commit()
    sit = planos.situacao(s, u)
    assert sit["status"] == "tolerancia" and sit["plano"] == "organizador" and sit["tolerancia_ate"] == hoje - timedelta(days=1) + timedelta(days=7)
    _camp(s, fabrica, u)
    # passou a tolerância: volta ao gratuito e não cria mais campeonato
    a.teste_ate = hoje - timedelta(days=8)
    s.commit()
    assert planos.situacao(s, u)["status"] == "vencida"
    with pytest.raises(PlanoNecessario, match="terminou"):
        _camp(s, fabrica, u)


def test_avisos_de_vencimento_sem_repetir(s, fabrica):
    u = fabrica.atleta("Aviso")
    _camp(s, fabrica, u)
    a = planos.assinatura_de(s, u)
    a.teste_ate = agora().date() + timedelta(days=3)
    s.commit()
    assert planos.avisar_vencimentos(s) >= 1
    assert planos.avisar_vencimentos(s) == 0  # o mesmo marco não repete
    assert s.query(Notificacao).filter(Notificacao.usuario_id == u.id, Notificacao.titulo.like("%vence em 3 dias%")).count() == 1


# ---------------------------------------------------------------- administrador


def test_admin_nao_paga_e_concede_plano(s, fabrica):
    admin, alvo = _admin(fabrica, s), fabrica.atleta("Cortesia")
    assert planos.situacao(s, admin) == planos.situacao(s, admin) and planos.situacao(s, admin)["plano"] == "arena"
    arenas.criar(s, admin, "Arena do admin", *PERTO)  # sem teste, sem assinatura
    assert planos.assinatura_de(s, admin) is None

    ate = agora().date() + timedelta(days=90)
    r = planos.conceder(s, admin, alvo.id, "organizador", ate)
    assert (r["plano"], r["status"], r["fim"]) == ("organizador", "ativa", ate.isoformat())
    _camp(s, fabrica, alvo)
    with pytest.raises(SemPermissao):
        planos.conceder(s, alvo, alvo.id, "arena", ate)
    with pytest.raises(ErroNegocio):
        planos.conceder(s, admin, alvo.id, "arena", agora().date() - timedelta(days=1))
    planos.conceder(s, admin, alvo.id, "gratuito", None)
    assert planos.situacao(s, alvo)["plano"] == "gratuito"


def test_admin_concede_plano_com_data_de_inicio_e_o_acesso_so_vale_a_partir_dela(s, fabrica):
    admin, alvo = _admin(fabrica, s), fabrica.atleta("Agendado")
    hoje = agora().date()
    inicio, ate = hoje + timedelta(days=10), hoje + timedelta(days=40)

    # concedido para começar daqui a 10 dias: até lá continua no plano Usuário
    r = planos.conceder(s, admin, alvo.id, "pro", ate, inicio)
    assert (r["plano"], r["status"], r["inicio"], r["fim"]) == ("gratuito", "agendada", inicio.isoformat(), ate.isoformat())
    assert planos.permissoes(s, alvo)["atividade"] is False
    # no dia do início o plano passa a valer
    sit = planos._situacao(s, alvo, inicio)
    assert (sit["plano"], sit["status"]) == ("pro", "ativa")
    assert planos._situacao(s, alvo, ate)["plano"] == "pro" and planos._situacao(s, alvo, ate + timedelta(days=1))["status"] == "tolerancia"
    # o item da administração mostra o início
    from playgo import administracao

    item = next(i for i in administracao.listar(s, admin, "Agendado") if i["id"] == alvo.id)
    assert item["plano_inicio"] == inicio.isoformat() and item["plano_fim"] == ate.isoformat()

    # sem início informado vale desde hoje; a validade não pode ser anterior ao início
    r = planos.conceder(s, admin, alvo.id, "organizador", ate)
    assert (r["plano"], r["status"], r["inicio"]) == ("organizador", "ativa", hoje.isoformat())
    with pytest.raises(ErroNegocio, match="anterior ao início"):
        planos.conceder(s, admin, alvo.id, "pro", hoje + timedelta(days=5), hoje + timedelta(days=9))


def test_admin_define_precos_e_limites(s, fabrica):
    admin, comum = _admin(fabrica, s), fabrica.atleta("Comum")
    with pytest.raises(SemPermissao):
        planos.salvar_regras(s, comum, "organizador", valor_mensal=Decimal(10))
    r = planos.salvar_regras(s, admin, "organizador", valor_mensal=Decimal("39.90"), nome="Organizador Pro")
    assert r["valor_mensal"] == 39.9 and r["assinavel"] and r["nome"] == "Organizador Pro"
    planos.salvar_regras(s, admin, "pro", max_participantes=12, max_atividades_abertas=2)
    u = fabrica.atleta("Limitado")
    _dar_pro(s, fabrica, u)
    _jogo(s, fabrica, u, max_p=12)
    assert planos.situacao(s, u)["plano"] == "pro"  # dentro do novo limite
    _jogo(s, fabrica, u, max_p=13)  # passou do limite que o admin definiu: abre o teste do Organizador
    assert planos.situacao(s, u)["plano"] == "organizador"
    planos.salvar_regras(s, admin, "pro", max_participantes=30, max_atividades_abertas=5)
    # o administrador também pode liberar atividades para o plano Usuário
    outro = fabrica.atleta("Sem Pro")
    assert planos.salvar_regras(s, admin, "gratuito", pode_atividade=True)["pode_atividade"] is True
    _jogo(s, fabrica, outro)
    assert planos.assinatura_de(s, outro) is None
    planos.salvar_regras(s, admin, "gratuito", pode_atividade=False)
    planos.salvar_regras(s, admin, "organizador", valor_mensal=Decimal(0), nome="Organizador")


# ---------------------------------------------------------------- Asaas (transporte simulado)


@pytest.fixture()
def asaas(monkeypatch, s):
    chamadas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        caminho = req.url.path
        if req.method == "POST" and caminho.endswith("/customers"):
            return httpx.Response(200, json={"id": "cus_001"})
        if req.method == "POST" and caminho.endswith("/subscriptions"):
            return httpx.Response(200, json={"id": "sub_" + uuid.uuid4().hex[:8]})
        if req.method == "GET" and "/payments" in caminho:
            return httpx.Response(200, json={"data": [{"id": "pay_" + uuid.uuid4().hex[:8], "value": 39.9, "status": "PENDING", "dueDate": (agora().date() + timedelta(days=1)).isoformat(), "invoiceUrl": "https://sandbox.asaas.com/i/abc"}]})
        if req.method == "DELETE":
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404, json={"errors": [{"description": "rota desconhecida"}]})

    monkeypatch.setattr(settings, "asaas_api_key", "$aact_hmlg_teste")
    monkeypatch.setattr(settings, "asaas_webhook_token", "token-do-webhook")
    monkeypatch.setattr(cobranca, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    admin = _admin_local(s)
    planos.salvar_regras(s, admin, "organizador", valor_mensal=Decimal("39.90"))
    yield chamadas
    planos.salvar_regras(s, admin, "organizador", valor_mensal=Decimal(0))


def _admin_local(s):
    from playgo import contas

    a = contas.cadastrar(s, "Admin Local", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"adm{uuid.uuid4().hex[:10]}", True, False)
    a.admin = True
    s.commit()
    return a


def test_assinar_cria_cliente_e_assinatura_no_asaas(s, fabrica, asaas):
    u = fabrica.atleta("Assinante")
    r = cobranca.assinar(s, u, "organizador", "529.982.247-25")
    assert r["link_pagamento"] == "https://sandbox.asaas.com/i/abc" and r["valor"] == 39.9
    cliente, assinatura = [c for c in asaas if c.method == "POST"]
    assert cliente.headers["access_token"] == "$aact_hmlg_teste" and cliente.url.host == "api-sandbox.asaas.com"
    assert json.loads(cliente.content)["cpfCnpj"] == "52998224725" and json.loads(cliente.content)["email"] == u.email
    corpo = json.loads(assinatura.content)
    assert corpo["cycle"] == "MONTHLY" and corpo["billingType"] == "UNDEFINED" and corpo["value"] == 39.9 and corpo["customer"] == "cus_001"
    a = planos.assinatura_de(s, u)
    assert a.asaas_customer_id == "cus_001" and a.asaas_subscription_id and a.plano == "organizador"
    # o CPF não é guardado em lugar nenhum do banco
    assert "52998224725" not in str([getattr(a, c.name) for c in Assinatura.__table__.columns]) and "52998224725" not in (u.email + u.nome)
    assert s.scalar(select(Pagamento).where(Pagamento.assinatura_id == a.id)).invoice_url == "https://sandbox.asaas.com/i/abc"
    # a pessoa reencontra a fatura (2ª via) em Meu plano
    fatura = planos.resumo(s, u)["cobrancas"]
    assert len(fatura) == 1 and fatura[0]["link"] == "https://sandbox.asaas.com/i/abc" and fatura[0]["status"] == "PENDING"


def test_plano_usuario_com_valor_passa_a_cobrar(s, fabrica, asaas):
    from playgo import vagas

    admin = _admin(fabrica, s)
    org, u = fabrica.atleta("Org"), fabrica.atleta("Usuario")
    _dar_pro(s, fabrica, org)
    jogo = _jogo(s, fabrica, org)
    assert planos.resumo(s, u)["acesso_basico"] is True  # em R$ 0 nada é cobrado
    r = planos.salvar_regras(s, admin, "gratuito", valor_mensal=Decimal("5"))
    try:
        assert r["assinavel"] is True and planos.resumo(s, u)["acesso_basico"] is False
        with pytest.raises(PlanoNecessario) as e:
            vagas.entrar(s, jogo.id, u)  # participar também exige o plano Usuário em dia
        assert e.value.plano == "gratuito" and "Usuário" in str(e.value)
        assert planos.resumo(s, org)["acesso_basico"] is True  # qualquer plano em dia cobre o básico
        out = cobranca.assinar(s, u, "gratuito", "529.982.247-25")
        assert out["plano"] == "gratuito" and out["valor"] == 5.0
        planos.conceder(s, admin, u.id, "gratuito", agora().date() + timedelta(days=30))  # cortesia do administrador
        assert planos.resumo(s, u)["acesso_basico"] is True and vagas.entrar(s, jogo.id, u).status == "confirmado"
    finally:
        planos.salvar_regras(s, admin, "gratuito", valor_mensal=Decimal(0))


def test_assinar_o_plano_pro(s, fabrica, asaas):
    u = fabrica.atleta("Assina Pro")
    admin = _admin(fabrica, s)
    with pytest.raises(ErroNegocio, match="ainda não foi definido"):
        cobranca.assinar(s, u, "pro", "529.982.247-25")
    planos.salvar_regras(s, admin, "pro", valor_mensal=Decimal("19.90"))
    try:
        r = cobranca.assinar(s, u, "pro", "529.982.247-25")
        assert r["plano"] == "pro" and r["valor"] == 19.9 and planos.assinatura_de(s, u).plano == "pro"
    finally:
        planos.salvar_regras(s, admin, "pro", valor_mensal=Decimal(0))


def test_assinar_valida_dados(s, fabrica, asaas, monkeypatch):
    u = fabrica.atleta("Dados")
    with pytest.raises(ErroNegocio, match="CPF"):
        cobranca.assinar(s, u, "organizador", "123")
    with pytest.raises(ErroNegocio, match="plano válido"):
        cobranca.assinar(s, u, "inexistente", "52998224725")
    with pytest.raises(ErroNegocio, match="ainda não foi definido"):
        cobranca.assinar(s, u, "gratuito", "52998224725")  # o Usuário em R$ 0 não tem o que cobrar
    with pytest.raises(ErroNegocio, match="ainda não foi definido"):
        cobranca.assinar(s, u, "arena", "52998224725")  # preço do Arena segue em R$ 0
    monkeypatch.setattr(settings, "asaas_api_key", "")
    with pytest.raises(ErroNegocio, match="cobrança ainda não está ativa"):
        cobranca.assinar(s, u, "organizador", "52998224725")


def test_webhook_confirma_pagamento_uma_unica_vez(s, fabrica, asaas):
    u = fabrica.atleta("Pagador")
    cobranca.assinar(s, u, "organizador", "52998224725")
    a = planos.assinatura_de(s, u)
    evento = {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_confirmado", "subscription": a.asaas_subscription_id, "value": 39.9, "status": "CONFIRMED", "dueDate": agora().date().isoformat(), "paymentDate": agora().date().isoformat()}}
    with pytest.raises(cobranca.TokenWebhookInvalido):
        cobranca.processar_webhook(s, "token-errado", evento)
    assert cobranca.processar_webhook(s, "token-do-webhook", evento) == "ok"
    s.refresh(a)
    primeiro = a.vigente_ate
    assert a.status == "ativa" and primeiro >= agora().date() + timedelta(days=31)
    assert s.query(Notificacao).filter(Notificacao.usuario_id == u.id, Notificacao.titulo.like("%Pagamento confirmado%")).count() == 1
    # o Asaas manda CONFIRMED e depois RECEIVED para a mesma cobrança: só estende uma vez
    cobranca.processar_webhook(s, "token-do-webhook", {**evento, "event": "PAYMENT_RECEIVED", "payment": {**evento["payment"], "status": "RECEIVED"}})
    s.refresh(a)
    assert a.vigente_ate == primeiro
    # próxima mensalidade estende a partir do fim atual
    prox = {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_2", "subscription": a.asaas_subscription_id, "value": 39.9, "status": "CONFIRMED", "dueDate": primeiro.isoformat()}}
    cobranca.processar_webhook(s, "token-do-webhook", prox)
    s.refresh(a)
    assert a.vigente_ate == primeiro + timedelta(days=31)
    assert planos.situacao(s, u)["plano"] == "organizador" and planos.situacao(s, u)["status"] == "ativa"


def test_webhook_atraso_cancelamento_e_ignorados(s, fabrica, asaas):
    u = fabrica.atleta("Atrasado")
    cobranca.assinar(s, u, "organizador", "52998224725")
    a = planos.assinatura_de(s, u)
    sub = a.asaas_subscription_id
    cobranca.processar_webhook(s, "token-do-webhook", {"event": "PAYMENT_OVERDUE", "payment": {"id": "pay_x", "subscription": sub, "value": 39.9, "status": "OVERDUE", "dueDate": agora().date().isoformat()}})
    assert s.query(Notificacao).filter(Notificacao.usuario_id == u.id, Notificacao.titulo.like("%em atraso%")).count() == 1
    assert cobranca.processar_webhook(s, "token-do-webhook", {"event": "PAYMENT_CONFIRMED", "payment": {"id": "p", "subscription": "sub_desconhecida"}}) == "ignorado"
    cobranca.processar_webhook(s, "token-do-webhook", {"event": "SUBSCRIPTION_DELETED", "subscription": {"id": sub}})
    s.refresh(a)
    assert a.asaas_subscription_id is None and a.cancelada_em


def test_webhook_de_cobranca_de_outro_sistema_com_campos_nulos_nao_da_erro(s, fabrica, asaas):
    """O Asaas manda eventos de todas as cobranças da conta, com externalReference/subscription nulos: responde 'ignorado', nunca 500."""
    token = "token-do-webhook"
    outras = [
        {"event": "PAYMENT_CREATED", "payment": {"id": "pay_outra", "externalReference": None, "subscription": None, "value": 120, "status": "PENDING", "dueDate": None}},
        {"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_o2", "externalReference": "pedido:77", "subscription": None}},
        {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_o3", "externalReference": "assinatura:nao-e-numero"}},
        {"event": "PAYMENT_CONFIRMED", "payment": None, "subscription": None},
        {"event": None},
        {},
    ]
    for corpo in outras:
        assert cobranca.processar_webhook(s, token, corpo) == "ignorado", corpo
    assert cobranca.processar_webhook(s, token, ["lista", "inesperada"]) == "ignorado"


def test_cancelar_mantem_o_acesso_ate_o_fim_do_periodo_pago(s, fabrica, asaas):
    u = fabrica.atleta("Cancela")
    cobranca.assinar(s, u, "organizador", "52998224725")
    a = planos.assinatura_de(s, u)
    cobranca.processar_webhook(s, "token-do-webhook", {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_c", "subscription": a.asaas_subscription_id, "value": 39.9, "status": "CONFIRMED", "dueDate": agora().date().isoformat()}})
    r = cobranca.cancelar(s, u)
    assert r["status"] == "cancelada" and r["plano"] == "organizador" and not r["assinatura_asaas"]
    assert any(c.method == "DELETE" for c in asaas)
    with pytest.raises(ErroNegocio):
        cobranca.cancelar(s, u)


# ---------------------------------------------------------------- HTTP


def test_api_devolve_402_com_o_plano_necessario(banco):
    from playgo.web.app import app

    with TestClient(app) as c:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        r = c.post("/api/v1/auth/cadastro", json={"nome": "Sem Plano", "email": email, "senha": "senha-de-teste-1", "usuario": f"sp{uuid.uuid4().hex[:10]}", "aceito_termos": True, "maior_de_idade": True})
        h = {"Authorization": "Bearer " + r.json()["token"]}
        uid = r.json()["usuario"]["id"]
        with Session() as s:
            s.add(Assinatura(usuario_id=uid, plano="arena", status="cancelada", origem="teste", testes_usados=["organizador", "arena"], vigente_ate=agora().date() - timedelta(days=60)))
            s.commit()
        meu = c.get("/api/v1/planos", headers=h).json()
        assert meu["plano"] == "gratuito" and meu["status"] == "vencida" and len(meu["planos"]) == 4 and meu["regras"]["max_participantes"] == 30
        mods = {m["codigo"]: m["id"] for m in c.get("/api/v1/modalidades", headers=h).json()}
        hoje = agora().date()
        camp = {"modalidade_id": mods["futsal"], "nome": "Copa", "data_inicio": str(hoje + timedelta(days=20)), "inscricao_ate": str(hoje + timedelta(days=10)), "max_equipes": 8, "local_nome": "G", "latitude": PERTO[0], "longitude": PERTO[1]}
        resp = c.post("/api/v1/campeonatos", headers=h, json=camp)
        assert resp.status_code == 402 and resp.json()["detail"]["codigo"] == "plano_necessario" and resp.json()["detail"]["plano"] == "organizador"
        assert c.post("/api/v1/planos/teste", headers=h, json={"plano": "organizador"}).status_code == 400  # teste já usado
        assert c.post("/api/v1/planos/assinar", headers=h, json={"plano": "organizador", "cpf_cnpj": "52998224725"}).status_code == 400  # sem chave do Asaas configurada


def test_api_webhook_e_painel_admin(banco, monkeypatch):
    from playgo.web.app import app

    monkeypatch.setattr(settings, "asaas_webhook_token", "tok")
    with TestClient(app) as c:
        assert c.post("/api/v1/cobranca/asaas", json={"event": "PAYMENT_CONFIRMED"}, headers={"asaas-access-token": "errado"}).status_code == 401
        assert c.post("/api/v1/cobranca/asaas", json={"event": "PAYMENT_CONFIRMED"}, headers={"asaas-access-token": "tok"}).json() == {"resultado": "ignorado"}

        def cadastrar(nome):
            r = c.post("/api/v1/auth/cadastro", json={"nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"x{uuid.uuid4().hex[:10]}", "aceito_termos": True, "maior_de_idade": True})
            return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]

        adm, adm_id = cadastrar("Adm")
        comum, comum_id = cadastrar("Comum")
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.commit()
        assert c.get("/api/v1/admin/planos", headers=comum).status_code == 403
        assert [p["codigo"] for p in c.get("/api/v1/admin/planos", headers=adm).json()] == ["gratuito", "pro", "organizador", "arena"]
        r = c.put("/api/v1/admin/planos/arena", headers=adm, json={"valor_mensal": "99.90"}).json()
        assert r["valor_mensal"] == 99.9 and r["pode_arena"] and r["assinavel"]
        assert c.put("/api/v1/admin/planos/arena", headers=comum, json={"valor_mensal": "1"}).status_code == 403
        ate = str(agora().date() + timedelta(days=30))
        assert c.post(f"/api/v1/admin/usuarios/{comum_id}/plano", headers=adm, json={"plano": "arena", "ate": ate}).json()["plano"] == "arena"
        arroba = c.get("/api/v1/me", headers=comum).json()["usuario"]
        lista = c.get(f"/api/v1/admin/usuarios?perfil=usuario&q={arroba}", headers=adm).json()["itens"]
        assert next(i for i in lista if i["id"] == comum_id)["plano"] == "arena"
        c.put("/api/v1/admin/planos/arena", headers=adm, json={"valor_mensal": "0"})


def test_paginas_de_planos_renderizam(banco):
    from playgo.web.app import app

    c = TestClient(app)
    email = f"{uuid.uuid4().hex[:10]}@teste.local"
    assert c.post("/cadastro", data={"nome": "Pagina Plano", "email": email, "senha": "senha-de-teste-1", "usuario": f"pg{uuid.uuid4().hex[:10]}", "aceito_termos": "1", "maior_de_idade": "1"}, follow_redirects=False).status_code == 303
    r = c.get("/planos")
    assert r.status_code == 200 and "Meu plano" in r.text and "sempre gratuito" not in r.text
    assert 'href="/planos"' in c.get("/mural").text  # item no menu
    assert c.get("/administracao", follow_redirects=False).status_code == 303  # só admin


def test_webhook_http_responde_401_com_token_errado_e_200_com_o_certo(banco, monkeypatch):
    """O Asaas mostra o código HTTP: token errado precisa ser 401 (claro), nunca 404 (parece URL errada)."""
    from fastapi.testclient import TestClient

    from playgo.web.app import app

    monkeypatch.setattr(settings, "asaas_webhook_token", "token-do-webhook")
    corpo = {"event": "PAYMENT_CREATED", "payment": {"id": "pay_outro", "externalReference": None, "subscription": None}}
    with TestClient(app) as cli:
        r = cli.post("/api/v1/cobranca/asaas", json=corpo, headers={"asaas-access-token": "errado"})
        assert r.status_code == 401 and "inválido" in r.json()["detail"]
        assert cli.post("/api/v1/cobranca/asaas", json=corpo).status_code == 401  # sem o cabeçalho
        r = cli.post("/api/v1/cobranca/asaas", json=corpo, headers={"asaas-access-token": "token-do-webhook"})
        assert r.status_code == 200 and r.json() == {"resultado": "ignorado"}
        monkeypatch.setattr(settings, "asaas_webhook_token", "")
        assert cli.post("/api/v1/cobranca/asaas", json=corpo, headers={"asaas-access-token": ""}).status_code == 401
