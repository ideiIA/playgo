"""Recebimentos de parceiros pela subconta Asaas (BaaS): fases, ativação, KYC, cobrança, webhooks, saque e validação de saque. Transporte do Asaas simulado."""

import json as jsonlib
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text

from playgo import arenas, cobranca, recebimentos
from playgo.config import settings
from playgo.db import Session, agora
from playgo.erros import ErroNegocio, SemPermissao
from playgo.models import (
    Arena,
    EventoRecebimento,
    LancamentoRecebedor,
    Recebedor,
    SaqueRecebedor,
)

PERTO = (-20.4535, -54.6201)
TOKEN = "t" * 40
U = uuid.uuid4().hex[:6]  # o banco de testes é compartilhado: ids de transferência únicos por execução
CHAVE_SUB = "$aact_sub_chave_secreta_da_subconta"


class FakeAsaas:
    """Estado e roteador do Asaas simulado. Guarda cada requisição (com a apiKey usada) para os testes conferirem."""

    def __init__(self):
        self.chamadas: list[tuple[str, str, str, dict | None]] = []  # método, caminho, apiKey, corpo
        self.balance = 0
        self.general = "PENDING"
        self.docs: list[dict] = []
        self.transfer_status = "PENDING"
        self.falhar: set[str] = set()  # "METODO caminho-final" que devolve 400
        self.contas = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        caminho = req.url.path.removeprefix("/v3")
        corpo = None
        if req.content and req.headers.get("content-type", "").startswith("application/json"):
            corpo = jsonlib.loads(req.content)
        self.chamadas.append((req.method, caminho, req.headers.get("access_token", ""), corpo))
        if f"{req.method} {caminho}" in self.falhar:
            return httpx.Response(400, json={"errors": [{"description": "recusado pelo Asaas"}]})
        if req.method == "POST" and caminho == "/accounts":
            self.contas += 1
            n = uuid.uuid4().hex[:8]
            return httpx.Response(200, json={"id": f"acc_{n}", "walletId": f"wal_{n}", "accessToken": {"id": f"tok_{n}", "apiKey": CHAVE_SUB + n}})
        if req.method == "GET" and caminho == "/myAccount/status":
            return httpx.Response(200, json={"general": self.general})
        if req.method == "GET" and caminho == "/myAccount/documents":
            return httpx.Response(200, json={"rejectReasons": None, "data": self.docs})
        if req.method == "POST" and caminho.startswith("/myAccount/documents/"):
            return httpx.Response(200, json={"id": "doc_1", "status": "PENDING"})
        if req.method == "POST" and caminho == "/myAccount/commercialInfo":
            return httpx.Response(200, json={})
        if req.method == "GET" and caminho == "/finance/balance":
            return httpx.Response(200, json={"balance": self.balance})
        if req.method == "POST" and caminho == "/customers":
            return httpx.Response(200, json={"id": "cus_" + uuid.uuid4().hex[:6]})
        if req.method == "POST" and caminho == "/payments":
            return httpx.Response(200, json={"id": "pay_" + uuid.uuid4().hex[:8], "invoiceUrl": "https://sandbox.asaas.com/i/reserva"})
        if req.method == "DELETE" and caminho.startswith("/payments/"):
            return httpx.Response(200, json={"deleted": True})
        if req.method == "POST" and caminho == "/transfers":
            return httpx.Response(200, json={"id": "tr_" + uuid.uuid4().hex[:8], "status": self.transfer_status})
        if req.method == "POST" and caminho.endswith("/accessTokens"):
            return httpx.Response(200, json={"id": "tok_novo", "apiKey": CHAVE_SUB + "NOVA"})
        if req.method == "PUT" and "/accessTokens/" in caminho:
            return httpx.Response(200, json={"enabled": False})
        return httpx.Response(404, json={"errors": [{"description": "rota desconhecida " + caminho}]})

    def chamou(self, metodo, caminho_parte):
        return [c for c in self.chamadas if c[0] == metodo and caminho_parte in c[1]]


@pytest.fixture()
def asaas(monkeypatch):
    fake = FakeAsaas()
    monkeypatch.setattr(cobranca, "_http", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(settings, "asaas_api_key", "$aact_hmlg_mae")
    monkeypatch.setattr(settings, "asaas_ambiente", "sandbox")
    monkeypatch.setattr(settings, "recebimentos_fase", "baas")
    monkeypatch.setattr(settings, "asaas_webhook_token_parceiros", TOKEN)
    monkeypatch.setattr(settings, "url_publica", "https://playgo.teste")
    monkeypatch.setattr(settings, "asaas_wallet_id", "wal_mae")
    monkeypatch.setattr(settings, "recebimentos_comissao_percentual", Decimal(10))
    monkeypatch.setattr(settings, "recebimentos_comissao_fixa", Decimal(0))
    monkeypatch.setattr(settings, "recebimentos_taxa_por", "arena")
    monkeypatch.setattr(settings, "recebimentos_saque_minimo", Decimal(10))
    return fake


@pytest.fixture(autouse=True)
def _sem_saques_orfaos(banco):
    """O banco de testes é compartilhado: saques sem id de testes anteriores entrariam no casamento por valor/destino."""
    with Session() as limpa:
        limpa.execute(text("UPDATE saques_recebedor SET status = 'falhou' WHERE asaas_transfer_id IS NULL AND status IN ('solicitado', 'enviado')"))
        limpa.commit()


def _admin(s):
    from playgo import contas

    a = contas.cadastrar(s, "Admin R", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"adm{uuid.uuid4().hex[:10]}", True, False)
    a.admin = True
    s.commit()
    return a


def _arena(s, fabrica, nome="Arena Gol"):
    gestor = fabrica.atleta("Gestor " + nome)
    from playgo import planos

    planos.conceder(s, _admin(s), gestor.id, "arena", agora().date() + timedelta(days=90))
    a = arenas.criar(s, gestor, nome, *PERTO)
    q = arenas.criar_quadra(s, a, gestor, "Quadra 1", [], 10, Decimal(100))
    return a, q, gestor


DADOS_PF = {"nome": "Maria Souza", "email": "maria@arena.test", "cpf_cnpj": "529.982.247-25", "data_nascimento": "1985-05-20", "telefone": "(67) 99999-1111", "cep": "79002-000",
            "endereco": "Rua das Flores", "numero": "10", "bairro": "Centro", "renda_mensal": 8000}
DADOS_PJ = {"nome": "Arena Gol LTDA", "email": "contato@arena.test", "cpf_cnpj": "11.222.333/0001-81", "tipo_empresa": "LIMITED", "telefone": "6733334444", "cep": "79002000",
            "endereco": "Av. Brasil", "numero": "100", "bairro": "Centro", "renda_mensal": 30000}


def _ativada(s, fabrica, asaas, aprovar=True, nome="Arena Gol"):
    a, q, g = _arena(s, fabrica, nome)
    recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    rec = s.scalar(select(Recebedor).where(Recebedor.arena_id == a.id))
    if aprovar:
        rec.status_kyc = "aprovado"
        s.commit()
    return a, q, g, rec


def _reserva(s, q, hora=10):
    inicio = (agora() + timedelta(days=2)).replace(hour=hora, minute=0, second=0, microsecond=0)
    return arenas.reservar(s, q.id, inicio, inicio + timedelta(hours=1), rotulo="Cliente")


def _evento(evento, pagamento=None, conta=None, **extra):
    return {"id": "evt_" + uuid.uuid4().hex[:10], "event": evento, "payment": pagamento, "account": {"id": conta} if conta else None, **extra}


# ---------------------------------------------------------------- fases


def test_fase_desligada_nao_chama_o_asaas_e_responde_indisponivel(s, fabrica, asaas, monkeypatch):
    monkeypatch.setattr(settings, "recebimentos_fase", "desligado")
    a, _q, g = _arena(s, fabrica)
    for chamada in (lambda: recebimentos.ver(s, a.id, g), lambda: recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF), lambda: recebimentos.saldo(s, a.id, g),
                    lambda: recebimentos.solicitar_saque(s, a.id, g, Decimal(50)), lambda: recebimentos.extrato(s, a.id, g)):
        with pytest.raises(ErroNegocio, match="ainda não está disponível"):
            chamada()
    assert asaas.chamadas == []
    assert recebimentos.validar_saque(s, TOKEN, {"type": "TRANSFER", "transfer": {"id": "tr_x", "value": 10}})["status"] == "REFUSED"


def test_avaliacao_so_arenas_liberadas_e_com_limites_locais(s, fabrica, asaas, monkeypatch):
    monkeypatch.setattr(settings, "recebimentos_fase", "avaliacao")
    monkeypatch.setattr(settings, "asaas_ambiente", "producao")
    a, q, g = _arena(s, fabrica, "Arena A")
    with pytest.raises(ErroNegocio, match="não foi liberada"):
        recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    assert asaas.chamadas == []
    admin = _admin(s)
    with pytest.raises(SemPermissao):
        recebimentos.liberar_arena(s, g, a.id)
    recebimentos.liberar_arena(s, admin, a.id)
    recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    rec = s.scalar(select(Recebedor).where(Recebedor.arena_id == a.id))
    assert rec.ambiente == "producao"
    ava = recebimentos.avaliacao(s)
    assert ava["subcontas"] >= 1 and ava["dias_restantes"] == 60 - ava["dias_usados"]

    # bloqueio preventivo de cobrança: a 80% de R$ 2.000
    rec.status_kyc = "aprovado"
    s.commit()
    r1 = _reserva(s, q, 8)
    with pytest.raises(ErroNegocio, match="avaliação do Asaas"):
        recebimentos.criar_cobranca_reserva(s, r1.id, g, Decimal(1700), {"nome": "Cliente Um", "cpf_cnpj": "52998224725"})
    recebimentos.criar_cobranca_reserva(s, r1.id, g, Decimal(1000), {"nome": "Cliente Um", "cpf_cnpj": "52998224725"})
    r2 = _reserva(s, q, 11)
    with pytest.raises(ErroNegocio, match="avaliação do Asaas"):
        recebimentos.criar_cobranca_reserva(s, r2.id, g, Decimal(700), {"nome": "Cliente Dois", "cpf_cnpj": "52998224725"})

    # prazo de 60 dias: a 80% (48 dias) novas subcontas e cobranças esperam
    s.execute(text("update recebedores set criado_em = :d where id = :i"), {"d": agora() - timedelta(days=49), "i": rec.id})
    s.commit()
    s.expire_all()
    b, _qb, gb = _arena(s, fabrica, "Arena B")
    recebimentos.liberar_arena(s, admin, b.id)
    with pytest.raises(ErroNegocio, match="perto do fim"):
        recebimentos.ativar_recebimento(s, b.id, gb, DADOS_PF)
    with pytest.raises(ErroNegocio, match="perto do fim"):
        recebimentos.criar_cobranca_reserva(s, r2.id, g, Decimal(10), {"nome": "Cliente Dois", "cpf_cnpj": "52998224725"})


def test_avaliacao_limite_de_subcontas_e_sandbox_nao_conta(s, fabrica, asaas, monkeypatch):
    monkeypatch.setattr(settings, "recebimentos_fase", "avaliacao")
    admin = _admin(s)
    a, _, g = _arena(s, fabrica, "Arena Sandbox")
    recebimentos.liberar_arena(s, admin, a.id)
    recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)  # sandbox
    antes = recebimentos.avaliacao(s)["subcontas"]
    monkeypatch.setattr(settings, "asaas_ambiente", "producao")
    # simula 8 subcontas de produção já criadas (80% do limite de 10)
    with Session() as outra:
        base = len(list(outra.scalars(select(Recebedor).where(Recebedor.ambiente == "producao"))))
        for i in range(8 - base):
            ar = Arena(nome=f"X{i}", latitude=0, longitude=0, recebimento_liberado=True)
            outra.add(ar)
            outra.flush()
            outra.add(Recebedor(arena_id=ar.id, asaas_account_id=f"acc_x{uuid.uuid4().hex[:6]}", wallet_id="w", api_key_cripto=recebimentos.cifrar("k"), ambiente="producao"))
        outra.commit()
    assert recebimentos.avaliacao(s)["subcontas"] >= 8 > antes
    b, _, gb = _arena(s, fabrica, "Arena Limite")
    recebimentos.liberar_arena(s, admin, b.id)
    with pytest.raises(ErroNegocio, match="até 10 subcontas"):
        recebimentos.ativar_recebimento(s, b.id, gb, DADOS_PF)


def test_fase_baas_nao_aplica_os_bloqueios_da_avaliacao(s, fabrica, asaas, monkeypatch):
    monkeypatch.setattr(settings, "asaas_ambiente", "producao")
    _a, q, g, _rec = _ativada(s, fabrica, asaas)
    r = _reserva(s, q)
    r_ = recebimentos.criar_cobranca_reserva(s, r.id, g, Decimal(5000), {"nome": "Cliente Grande", "cpf_cnpj": "52998224725"})
    assert r_["valor"] == Decimal("5000.00")


# ---------------------------------------------------------------- ativação e KYC


def test_ativar_cria_subconta_com_webhooks_e_guarda_a_chave_cifrada(s, fabrica, asaas):
    a, _q, g = _arena(s, fabrica)
    with pytest.raises(SemPermissao):
        recebimentos.ativar_recebimento(s, a.id, fabrica.atleta("Intruso"), DADOS_PF)
    with pytest.raises(ErroNegocio, match="tipo da empresa"):
        recebimentos.ativar_recebimento(s, a.id, g, DADOS_PJ | {"tipo_empresa": ""})
    with pytest.raises(ErroNegocio, match="nascimento"):
        recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF | {"data_nascimento": ""})
    est = recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    assert est["recebedor"]["status_kyc"] == "criando" and est["recebedor"]["documento_final"] == "4725" and est["asaas"]["texto"]
    post = asaas.chamou("POST", "/accounts")[0]
    assert post[2] == "$aact_hmlg_mae" and post[3]["cpfCnpj"] == "52998224725" and post[3]["birthDate"] == "1985-05-20"
    wh = post[3]["webhooks"][0]
    assert wh["url"] == "https://playgo.teste/api/v1/recebimentos/asaas" and wh["authToken"] == TOKEN and "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED" in wh["events"] and "TRANSFER_DONE" in wh["events"]
    rec = s.scalar(select(Recebedor).where(Recebedor.arena_id == a.id))
    assert rec.api_key_cripto != CHAVE_SUB and CHAVE_SUB not in rec.api_key_cripto and recebimentos.decifrar(rec.api_key_cripto).startswith(CHAVE_SUB)
    assert "529" not in str(est) and CHAVE_SUB not in str(est)  # nem CPF inteiro nem apiKey na resposta
    with pytest.raises(ErroNegocio, match="já ativou"):
        recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    # PJ
    b, _, gb = _arena(s, fabrica, "Arena PJ")
    recebimentos.ativar_recebimento(s, b.id, gb, DADOS_PJ)
    assert asaas.chamou("POST", "/accounts")[-1][3]["companyType"] == "LIMITED"


def test_ativar_exige_token_e_url_do_webhook(s, fabrica, asaas, monkeypatch):
    a, _q, g = _arena(s, fabrica)
    monkeypatch.setattr(settings, "asaas_webhook_token_parceiros", "curto")
    with pytest.raises(ErroNegocio, match="TOKEN_PARCEIROS"):
        recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    monkeypatch.setattr(settings, "asaas_webhook_token_parceiros", TOKEN)
    monkeypatch.setattr(settings, "url_publica", "")
    with pytest.raises(ErroNegocio, match="URL_PUBLICA"):
        recebimentos.ativar_recebimento(s, a.id, g, DADOS_PF)
    assert asaas.chamou("POST", "/accounts") == []


def test_pendencias_com_e_sem_onboarding_url_e_espera_de_15s(s, fabrica, asaas):
    a, _q, g, rec = _ativada(s, fabrica, asaas, aprovar=False)
    est = recebimentos.atualizar_situacao(s, a.id, g)
    assert est["recebedor"]["aguardar_s"] > 0 and asaas.chamou("GET", "/myAccount") == []  # ainda não passou de 15 s
    rec.consultar_apos = agora() - timedelta(seconds=1)
    s.commit()
    asaas.docs = [
        {"id": "g1", "status": "NOT_SENT", "type": "IDENTIFICATION", "title": "Documento de identificação", "onboardingUrl": "https://asaas.com/onboarding/xyz"},
        {"id": "g2", "status": "NOT_SENT", "type": "MINUTES_OF_ELECTION", "title": "Ata de eleição"},
        {"id": "g3", "status": "APPROVED", "type": "INVOICE"},
    ]
    est = recebimentos.atualizar_situacao(s, a.id, g)
    assert est["recebedor"]["status_kyc"] == "documentos_pendentes"
    pend = {p["id"]: p for p in est["recebedor"]["documentos_pendentes"]}
    assert set(pend) == {"g1", "g2"} and pend["g1"]["onboarding_url"] and pend["g2"]["onboarding_url"] is None
    assert all(c[2].startswith(CHAVE_SUB) for c in asaas.chamou("GET", "/myAccount"))  # sempre com a apiKey da subconta
    # grupo com onboardingUrl não aceita envio pela API
    pdf = b"%PDF-1.4 conteudo"
    with pytest.raises(ErroNegocio, match="link"):
        recebimentos.enviar_documento(s, a.id, g, "g1", "IDENTIFICATION", "a.pdf", pdf, "application/pdf")
    with pytest.raises(ErroNegocio, match="PDF, JPG ou PNG"):
        recebimentos.enviar_documento(s, a.id, g, "g2", "MINUTES_OF_ELECTION", "a.exe", b"MZ....", "application/octet-stream")
    with pytest.raises(ErroNegocio, match="não está pendente"):
        recebimentos.enviar_documento(s, a.id, g, "gX", "INVOICE", "a.pdf", pdf, "application/pdf")
    asaas.docs = [{"id": "g2", "status": "PENDING"}]
    recebimentos.enviar_documento(s, a.id, g, "g2", "MINUTES_OF_ELECTION", "ata.pdf", pdf, "application/pdf")
    assert asaas.chamou("POST", "/myAccount/documents/g2")
    from playgo.models import Registro

    reg = s.scalars(select(Registro).where(Registro.acao == "recebimento_documento")).all()
    assert reg and all("ata.pdf" not in str(r.detalhes) for r in reg)  # nem o nome do arquivo vai para a trilha
    assert s.scalar(select(Recebedor).where(Recebedor.id == rec.id)).status_kyc == "em_analise"


def test_kyc_aprovado_e_recusado_por_consulta_e_por_webhook(s, fabrica, asaas):
    a, _q, g, rec = _ativada(s, fabrica, asaas, aprovar=False)
    rec.consultar_apos = None
    s.commit()
    asaas.general = "APPROVED"
    assert recebimentos.atualizar_situacao(s, a.id, g)["recebedor"]["status_kyc"] == "aprovado"
    _b, _qb, _gb, rb = _ativada(s, fabrica, asaas, aprovar=False, nome="Arena Recusada")
    assert recebimentos.processar_webhook(s, TOKEN, _evento("ACCOUNT_STATUS_GENERAL_APPROVAL_REJECTED", conta=rb.asaas_account_id)) == "ok"
    s.refresh(rb)
    assert rb.status_kyc == "recusado"
    _c, _qc, _gc, rc = _ativada(s, fabrica, asaas, aprovar=False, nome="Arena Webhook")
    recebimentos.processar_webhook(s, TOKEN, _evento("ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED", conta=rc.asaas_account_id))
    s.refresh(rc)
    assert rc.status_kyc == "aprovado" and rc.documentos_pendentes == []


# ---------------------------------------------------------------- cobrança


def test_cobranca_so_com_kyc_aprovado_e_com_split_da_comissao(s, fabrica, asaas):
    _a, q, g, rec = _ativada(s, fabrica, asaas, aprovar=False)
    r = _reserva(s, q)
    pagador = {"nome": "Cliente Teste", "email": "c@t.test", "cpf_cnpj": "529.982.247-25"}
    with pytest.raises(ErroNegocio, match="não foi aprovado"):
        recebimentos.criar_cobranca_reserva(s, r.id, g, Decimal(100), pagador)
    assert asaas.chamou("POST", "/payments") == []
    rec.status_kyc = "aprovado"
    s.commit()
    with pytest.raises(SemPermissao):
        recebimentos.criar_cobranca_reserva(s, r.id, fabrica.atleta("Outro"), Decimal(100), pagador)
    res = recebimentos.criar_cobranca_reserva(s, r.id, g, Decimal(100), pagador)
    assert res["link_pagamento"].startswith("https://") and res["comissao"] == Decimal("10.00") and res["asaas"]["texto"]
    pg = asaas.chamou("POST", "/payments")[0]
    assert pg[2].startswith(CHAVE_SUB) and pg[3]["billingType"] == "UNDEFINED" and pg[3]["externalReference"] == f"reserva:{r.id}"
    assert pg[3]["split"] == [{"walletId": "wal_mae", "percentualValue": 10.0}]
    s.refresh(r)
    assert r.status_pagamento == "pendente" and r.asaas_payment_id and r.valor == Decimal("100.00")
    with pytest.raises(ErroNegocio, match="já tem cobrança"):
        recebimentos.criar_cobranca_reserva(s, r.id, g, Decimal(100), pagador)
    recebimentos.cancelar_cobranca_reserva(s, r.id, g)
    s.refresh(r)
    assert r.status_pagamento == "cancelado"


def test_cobranca_sem_comissao_nao_envia_split(s, fabrica, asaas, monkeypatch):
    monkeypatch.setattr(settings, "recebimentos_comissao_percentual", Decimal(0))
    _a, q, g, _rec = _ativada(s, fabrica, asaas)
    r = _reserva(s, q)
    recebimentos.criar_cobranca_reserva(s, r.id, g, Decimal(80), {"nome": "Cliente Teste", "cpf_cnpj": "52998224725"})
    assert "split" not in asaas.chamou("POST", "/payments")[0][3]


# ---------------------------------------------------------------- webhooks e livro-razão


def _cobrar(s, q, g, valor=100):
    r = _reserva(s, q, hora=9 + uuid.uuid4().int % 8)
    recebimentos.criar_cobranca_reserva(s, r.id, g, Decimal(valor), {"nome": "Cliente Teste", "cpf_cnpj": "52998224725"})
    s.refresh(r)
    return r


def _lancamentos(s, rec):
    s.expire_all()
    return {(x.tipo): x for x in s.scalars(select(LancamentoRecebedor).where(LancamentoRecebedor.recebedor_id == rec.id))}


def test_webhook_confirma_o_pagamento_uma_unica_vez_e_lanca_no_livro_razao(s, fabrica, asaas):
    _a, q, g, rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g, 100)
    pag = {"id": r.asaas_payment_id, "value": 100, "netValue": 98.01, "externalReference": f"reserva:{r.id}", "status": "CONFIRMED"}
    with pytest.raises(cobranca.TokenWebhookInvalido):
        recebimentos.processar_webhook(s, "errado", _evento("PAYMENT_CONFIRMED", pag))
    evento = _evento("PAYMENT_CONFIRMED", pag, conta=rec.asaas_account_id)
    assert recebimentos.processar_webhook(s, TOKEN, evento) == "ok"
    assert recebimentos.processar_webhook(s, TOKEN, evento) == "duplicado"  # mesmo evento duas vezes
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_RECEIVED", pag | {"status": "RECEIVED"}, conta=rec.asaas_account_id))  # outro evento, mesmo pagamento
    s.refresh(r)
    assert r.status_pagamento == "pago"
    lanc = _lancamentos(s, rec)
    assert lanc["recebimento"].valor_liquido == Decimal("100.00") and lanc["taxa_asaas"].valor_liquido == Decimal("-1.99") and lanc["comissao"].valor_liquido == Decimal("-9.80")
    assert len(lanc) == 3 and recebimentos.saldo_interno(s, rec.id) == Decimal("88.21")


def test_taxa_por_plataforma_nao_desconta_da_arena(s, fabrica, asaas, monkeypatch):
    monkeypatch.setattr(settings, "recebimentos_taxa_por", "plataforma")
    _a, q, g, rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g, 100)
    pag = {"id": r.asaas_payment_id, "value": 100, "netValue": 98, "status": "CONFIRMED"}
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", pag, conta=rec.asaas_account_id))
    lanc = _lancamentos(s, rec)
    assert lanc["taxa_asaas"].valor_bruto == Decimal("2.00") and lanc["taxa_asaas"].valor_liquido == 0


def test_estorno_devolve_o_valor_e_recupera_a_comissao(s, fabrica, asaas):
    _a, q, g, rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g, 100)
    pag = {"id": r.asaas_payment_id, "value": 100, "netValue": 100, "status": "CONFIRMED"}
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", pag, conta=rec.asaas_account_id))
    assert recebimentos.saldo_interno(s, rec.id) == Decimal("90.00")
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_REFUNDED", pag | {"status": "REFUNDED"}, conta=rec.asaas_account_id))
    s.refresh(r)
    assert r.status_pagamento == "estornado" and recebimentos.saldo_interno(s, rec.id) == Decimal("0.00")
    assert _lancamentos(s, rec)["estorno"].valor_bruto == Decimal("100.00")


def test_webhook_ignora_cobranca_de_outra_conta_e_eventos_desconhecidos(s, fabrica, asaas):
    _a, q, g, rec = _ativada(s, fabrica, asaas)
    _b, _qb, _gb, rb = _ativada(s, fabrica, asaas, nome="Arena B")
    r = _cobrar(s, q, g, 100)
    pag = {"id": r.asaas_payment_id, "value": 100, "netValue": 100, "status": "CONFIRMED"}
    assert recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", pag, conta=rb.asaas_account_id)) == "ignorado"  # conta de outra Arena
    s.refresh(r)
    assert r.status_pagamento == "pendente" and recebimentos.saldo_interno(s, rec.id) == 0
    assert recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", {"id": "pay_de_outro_sistema", "externalReference": "assinatura:9"})) == "ignorado"
    assert recebimentos.processar_webhook(s, TOKEN, _evento("COISA_NOVA")) == "ignorado"
    assert recebimentos.processar_webhook(s, TOKEN, {"payment": None}) == "ignorado"


def test_cobranca_apagada_cancela_a_reserva(s, fabrica, asaas):
    _a, q, g, rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g)
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_DELETED", {"id": r.asaas_payment_id}, conta=rec.asaas_account_id))
    s.refresh(r)
    assert r.status_pagamento == "cancelado"


def test_livro_razao_so_aceita_insercao(s, fabrica, asaas):
    _a, q, g, rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g)
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", {"id": r.asaas_payment_id, "value": 100, "netValue": 100}, conta=rec.asaas_account_id))
    with pytest.raises(Exception, match="somente de inserção"):
        s.execute(text("update lancamentos_recebedor set valor_liquido = 1000"))
    s.rollback()
    with pytest.raises(Exception, match="somente de inserção"):
        s.execute(text("delete from lancamentos_recebedor"))
    s.rollback()


# ---------------------------------------------------------------- saldo, destino e saque


def _com_saldo(s, fabrica, asaas, valor=100):
    a, q, g, rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g, valor)
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", {"id": r.asaas_payment_id, "value": valor, "netValue": valor}, conta=rec.asaas_account_id))
    return a, q, g, rec


def test_saldo_e_extrato_e_divergencia_com_o_asaas(s, fabrica, asaas):
    a, _q, g, _rec = _com_saldo(s, fabrica, asaas, 100)
    asaas.balance = 90
    sd = recebimentos.saldo(s, a.id, g)
    assert sd["saldo"] == Decimal("90.00") and sd["saldo_asaas"] == Decimal("90.00") and sd["divergencia"] is False
    asaas.balance = 70
    sd = recebimentos.saldo(s, a.id, g)
    assert sd["divergencia"] is True and sd["diferenca"] == Decimal("-20.00")
    ext = recebimentos.extrato(s, a.id, g, agora().date() - timedelta(days=1), agora().date())
    assert {x["tipo"] for x in ext["lancamentos"]} == {"recebimento", "comissao"} and ext["saldo"] == Decimal("90.00")
    assert all(c[2].startswith(CHAVE_SUB) for c in asaas.chamou("GET", "/finance/balance"))


def test_destino_do_saque_confere_titularidade(s, fabrica, asaas):
    a, _q, g, rec = _ativada(s, fabrica, asaas)
    with pytest.raises(ErroNegocio, match="mesma titularidade"):
        recebimentos.definir_destino(s, a.id, g, "EMAIL", "maria@arena.test", False)
    with pytest.raises(ErroNegocio, match="mesmo titular"):
        recebimentos.definir_destino(s, a.id, g, "CPF", "111.444.777-35", True)  # outro CPF
    est = recebimentos.definir_destino(s, a.id, g, "CPF", "529.982.247-25", True)
    assert est["recebedor"]["destino"] == {"tipo": "CPF", "chave": "•••4725", "titularidade_confirmada": True}
    s.refresh(rec)
    assert "52998224725" not in rec.destino_cripto and recebimentos.decifrar(rec.destino_cripto) == "52998224725"


def test_saque_sucesso_falha_duplicado_e_saldo_insuficiente(s, fabrica, asaas):
    a, _q, g, rec = _com_saldo(s, fabrica, asaas, 100)  # saldo 90
    with pytest.raises(ErroNegocio, match="chave Pix"):
        recebimentos.solicitar_saque(s, a.id, g, Decimal(20))
    recebimentos.definir_destino(s, a.id, g, "EMAIL", "maria@arena.test", True)
    with pytest.raises(ErroNegocio, match="mínimo"):
        recebimentos.solicitar_saque(s, a.id, g, Decimal(5))
    with pytest.raises(ErroNegocio, match="insuficiente"):
        recebimentos.solicitar_saque(s, a.id, g, Decimal(500))
    with pytest.raises(SemPermissao):
        recebimentos.solicitar_saque(s, a.id, fabrica.atleta("Intruso"), Decimal(20))
    sq = recebimentos.solicitar_saque(s, a.id, g, Decimal(40), "clique-1")
    assert sq["status"] == "enviado" and recebimentos.saldo_interno(s, rec.id) == Decimal("50.00")
    tr = asaas.chamou("POST", "/transfers")[0]
    assert tr[2].startswith(CHAVE_SUB) and tr[3]["pixAddressKey"] == "maria@arena.test" and tr[3]["pixAddressKeyType"] == "EMAIL" and tr[3]["externalReference"] == f"saque:{sq['id']}"
    repetido = recebimentos.solicitar_saque(s, a.id, g, Decimal(40), "clique-1")  # duplo clique
    assert repetido["repetido"] is True and len(asaas.chamou("POST", "/transfers")) == 1 and recebimentos.saldo_interno(s, rec.id) == Decimal("50.00")
    saque = s.get(SaqueRecebedor, sq["id"])
    # concluído por webhook
    recebimentos.processar_webhook(s, TOKEN, {"id": "evt_a", "event": "TRANSFER_DONE", "transfer": {"id": saque.asaas_transfer_id, "status": "DONE"}})
    s.refresh(saque)
    assert saque.status == "concluido"
    # falha do Asaas na criação devolve o valor
    asaas.falhar.add("POST /transfers")
    with pytest.raises(ErroNegocio, match="recusado"):
        recebimentos.solicitar_saque(s, a.id, g, Decimal(30), "clique-2")
    assert recebimentos.saldo_interno(s, rec.id) == Decimal("50.00")
    asaas.falhar.clear()
    # falha depois (TRANSFER_FAILED) devolve uma única vez
    sq3 = recebimentos.solicitar_saque(s, a.id, g, Decimal(25), "clique-3")
    assert recebimentos.saldo_interno(s, rec.id) == Decimal("25.00")
    tid = s.get(SaqueRecebedor, sq3["id"]).asaas_transfer_id
    for i in (1, 2):
        recebimentos.processar_webhook(s, TOKEN, {"id": f"evt_f{i}", "event": "TRANSFER_FAILED", "transfer": {"id": tid, "failReason": "chave inválida"}})
    assert recebimentos.saldo_interno(s, rec.id) == Decimal("50.00") and s.get(SaqueRecebedor, sq3["id"]).status == "falhou"
    assert [x for x in _lancamentos(s, rec) if x == "saque_falho"]


# ---------------------------------------------------------------- validação de saque


def _transf(tid, valor, chave="maria@arena.test", descricao=None, **extra):
    t = {"id": tid, "value": valor, "operationType": "PIX", "bankAccount": {"pixAddressKey": chave}, "description": descricao} | extra
    return {"type": "TRANSFER", "transfer": t}


def _saque_sem_id(s, fabrica, asaas, valor=30, chave="maria@arena.test", nome="Arena Gol", chave_idem=None):
    """Arena com saldo e um saque no estado 'a transferência já foi pedida, mas o POST ainda não voltou' (sem id gravado)."""
    a, _q, g, rec = _com_saldo(s, fabrica, asaas, 100) if nome == "Arena Gol" else _com_saldo_nome(s, fabrica, asaas, nome)
    recebimentos.definir_destino(s, a.id, g, "EMAIL", chave, True)
    sq = recebimentos.solicitar_saque(s, a.id, g, Decimal(valor), f"{chave_idem or 'sem-id'}-{uuid.uuid4().hex[:8]}")
    saque = s.get(SaqueRecebedor, sq["id"])
    saque.asaas_transfer_id, saque.status = None, "solicitado"
    s.commit()
    return saque, rec


def _com_saldo_nome(s, fabrica, asaas, nome, valor=100):
    a, q, g, rec = _ativada(s, fabrica, asaas, nome=nome)
    r = _cobrar(s, q, g, valor)
    recebimentos.processar_webhook(s, TOKEN, _evento("PAYMENT_CONFIRMED", {"id": r.asaas_payment_id, "value": valor, "netValue": valor}, conta=rec.asaas_account_id))
    return a, q, g, rec


def _eventos_validacao(s):
    s.expire_all()
    return list(s.scalars(select(EventoRecebimento).where(EventoRecebimento.tipo == "validacao_saque").order_by(EventoRecebimento.criado_em)))


def test_validacao_casa_pelo_id_da_transferencia_e_registra_o_evento(s, fabrica, asaas):
    a, _q, g, _rec = _com_saldo(s, fabrica, asaas, 100)
    recebimentos.definir_destino(s, a.id, g, "EMAIL", "maria@arena.test", True)
    sq = recebimentos.solicitar_saque(s, a.id, g, Decimal(40), "v-1")
    saque = s.get(SaqueRecebedor, sq["id"])
    tid = saque.asaas_transfer_id
    tr = asaas.chamou("POST", "/transfers")[0][3]
    assert tr["description"] == f"PlayGo saque:{sq['id']}" and saque.status == "enviado" and saque.destino_selo and "maria" not in saque.destino_selo
    with pytest.raises(cobranca.TokenWebhookInvalido):
        recebimentos.validar_saque(s, "errado", _transf(tid, 40))
    with pytest.raises(cobranca.TokenWebhookInvalido):
        recebimentos.validar_saque(s, "", _transf(tid, 40))
    assert recebimentos.validar_saque(s, TOKEN, _transf(tid, 40)) == {"status": "APPROVED"}
    ev = _eventos_validacao(s)
    ev = [e for e in ev if e.saque_id == sq["id"]]
    assert len(ev) == 1 and ev[0].resultado == "aprovado"
    assert "maria" not in repr([(e.chave, e.motivo) for e in ev])


def test_validacao_antes_de_gravar_o_id_aprova_com_um_unico_candidato_e_grava_o_id(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_novo_{U}", 30)) == {"status": "APPROVED"}
    s.refresh(saque)
    assert saque.asaas_transfer_id == f"tr_novo_{U}" and saque.validacao == "aprovado"


def test_validacao_casa_pela_description_com_o_nosso_id_interno(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    outro = SaqueRecebedor(recebedor_id=saque.recebedor_id, valor=Decimal(30), destino_selo=saque.destino_selo, chave_idempotencia=f"gemeo-{U}{uuid.uuid4().hex[:4]}", status="solicitado")
    s.add(outro)  # idêntico em valor e destino: sem a description seria ambíguo
    s.commit()
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_d_{U}", 30, descricao=f"PlayGo saque:{saque.id}")) == {"status": "APPROVED"}
    s.refresh(saque)
    s.refresh(outro)
    assert saque.asaas_transfer_id == f"tr_d_{U}" and outro.asaas_transfer_id is None
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_e_{U}", 30, descricao=f"PlayGo saque:{saque.id}"))["status"] == "REFUSED"  # o saque já tem outra transferência


def test_duas_arenas_mesmo_valor_sem_id_o_destino_desambigua(s, fabrica, asaas):
    sa, _ = _saque_sem_id(s, fabrica, asaas, 30, "maria@arena.test", "Arena Gol", "ca")
    sb, _ = _saque_sem_id(s, fabrica, asaas, 30, "joao@arena.test", "Arena Dois", "cb")
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_b_{U}", 30, chave="joao@arena.test")) == {"status": "APPROVED"}
    s.refresh(sa)
    s.refresh(sb)
    assert sb.asaas_transfer_id == f"tr_b_{U}" and sa.asaas_transfer_id is None


def test_duas_arenas_mesmo_valor_e_mesmo_destino_sem_id_recusa_os_dois(s, fabrica, asaas):
    sa, _ = _saque_sem_id(s, fabrica, asaas, 30, "maria@arena.test", "Arena Gol", "ca")
    sb, _ = _saque_sem_id(s, fabrica, asaas, 30, "maria@arena.test", "Arena Dois", "cb")
    for tid in (f"tr_1_{U}", f"tr_2_{U}"):
        r = recebimentos.validar_saque(s, TOKEN, _transf(tid, 30))
        assert r["status"] == "REFUSED" and "ambíguo" in r["refuseReason"]
    s.refresh(sa)
    s.refresh(sb)
    assert sa.asaas_transfer_id is None and sb.asaas_transfer_id is None


def test_dois_saques_do_mesmo_recebedor_iguais_sem_id_recusa_por_ambiguidade(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    s.add(SaqueRecebedor(recebedor_id=saque.recebedor_id, valor=Decimal(30), destino_selo=saque.destino_selo, chave_idempotencia=f"gemeo-{U}{uuid.uuid4().hex[:4]}", status="solicitado"))
    s.commit()
    r = recebimentos.validar_saque(s, TOKEN, _transf(f"tr_x_{U}", 30))
    assert r["status"] == "REFUSED" and "ambíguo" in r["refuseReason"]
    s.refresh(saque)
    assert saque.asaas_transfer_id is None


def test_validacao_recusa_valor_destino_titular_e_pedido_antigo(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_v_{U}", 31))["status"] == "REFUSED"  # valor diferente: nenhum candidato
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_v_{U}", 30, chave="outro@x.test"))["status"] == "REFUSED"  # destino diferente
    saque.criado_em = agora() - timedelta(minutes=5)  # fora da janela de 2 min
    s.commit()
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_v_{U}", 30))["status"] == "REFUSED"
    # casado pela description, mas com divergência: recusa e o pedido fica marcado como recusado
    saque.criado_em = agora()
    s.commit()
    d = f"PlayGo saque:{saque.id}"
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_w_{U}", 31, descricao=d))["status"] == "REFUSED"
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_w_{U}", 30, descricao=d, bankAccount={"pixAddressKey": "maria@arena.test", "cpfCnpj": "11144477735"}))["status"] == "REFUSED"  # a primeira recusa já marcou
    s.refresh(saque)
    assert saque.validacao == "recusado" and saque.asaas_transfer_id is None


def test_validacao_recusa_titular_diferente_e_destino_ausente(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    d = f"PlayGo saque:{saque.id}"
    r = recebimentos.validar_saque(s, TOKEN, _transf(f"tr_t_{U}", 30, descricao=d, bankAccount={"pixAddressKey": "maria@arena.test", "cpfCnpj": "11144477735"}))
    assert r["status"] == "REFUSED" and "Titular" in r["refuseReason"]
    assert recebimentos.validar_saque(s, TOKEN, {"type": "TRANSFER", "transfer": {"id": f"tr_s_{U}", "value": 30, "description": d}})["status"] == "REFUSED"  # sem chave Pix no payload


def test_validacao_de_operacao_desconhecida_e_de_outros_tipos_recusa(s, fabrica, asaas):
    _a, _q, _g, _rec = _com_saldo(s, fabrica, asaas, 100)
    antes = len(_eventos_validacao(s))
    desconhecida = recebimentos.validar_saque(s, TOKEN, _transf("tr_nao_registrada", 999))
    assert desconhecida["status"] == "REFUSED" and desconhecida["refuseReason"]
    for corpo in ({"type": "BILL", "bill": {"id": "b"}}, {"type": "PIX_REFUND", "pixRefund": {"id": "r"}}, {}, {"type": "TRANSFER"}, {"type": "TRANSFER", "transfer": "lixo"}):
        assert recebimentos.validar_saque(s, TOKEN, corpo)["status"] == "REFUSED"
    assert recebimentos.validar_saque(s, TOKEN, [])["status"] == "REFUSED"
    novos = _eventos_validacao(s)[antes:]
    assert len(novos) == 7 and all(e.resultado == "recusado" for e in novos)


def test_mesma_validacao_duas_vezes_e_idempotente(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    corpo = _transf(f"tr_i_{U}", 30)
    assert recebimentos.validar_saque(s, TOKEN, corpo) == {"status": "APPROVED"}
    assert recebimentos.validar_saque(s, TOKEN, corpo) == {"status": "APPROVED"}
    saque.status = "concluido"  # já liquidado: repetir continua devolvendo APPROVED
    s.commit()
    assert recebimentos.validar_saque(s, TOKEN, corpo) == {"status": "APPROVED"}
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_i_{U}", 99))["status"] == "REFUSED"  # mesmo id, valor diferente
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_outra_{U}", 30))["status"] == "REFUSED"  # não aprova um segundo saque
    s.expire_all()
    aprovados = s.scalars(select(SaqueRecebedor).where(SaqueRecebedor.validacao == "aprovado", SaqueRecebedor.asaas_transfer_id.like(f"tr_%_{U}"), SaqueRecebedor.recebedor_id == saque.recebedor_id)).all()
    assert len(aprovados) == 1 and aprovados[0].asaas_transfer_id == f"tr_i_{U}"


def test_saque_recusado_ou_concluido_sem_aprovacao_previa_nao_e_aprovado(s, fabrica, asaas):
    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    saque.asaas_transfer_id, saque.status = f"tr_c_{U}", "concluido"
    s.commit()
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_c_{U}", 30))["status"] == "REFUSED"
    saque.status, saque.validacao = "enviado", "recusado"
    s.commit()
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_c_{U}", 30))["status"] == "REFUSED"


def test_saque_que_falhou_no_asaas_estorna_e_a_validacao_posterior_e_recusada(s, fabrica, asaas):
    a, _q, g, rec = _com_saldo(s, fabrica, asaas, 100)
    recebimentos.definir_destino(s, a.id, g, "EMAIL", "maria@arena.test", True)
    antes = recebimentos.saldo_interno(s, rec.id)
    asaas.falhar.add("POST /transfers")
    with pytest.raises(ErroNegocio):
        recebimentos.solicitar_saque(s, a.id, g, Decimal(30), "falha-1")
    saque = s.scalar(select(SaqueRecebedor).where(SaqueRecebedor.chave_idempotencia == "falha-1"))
    assert saque.status == "falhou" and saque.asaas_transfer_id is None and recebimentos.saldo_interno(s, rec.id) == antes
    assert {"saque", "saque_falho"} <= set(_lancamentos(s, rec))
    r = recebimentos.validar_saque(s, TOKEN, _transf(f"tr_f_{U}", 30, descricao=f"PlayGo saque:{saque.id}"))
    assert r["status"] == "REFUSED"
    assert recebimentos.validar_saque(s, TOKEN, _transf(f"tr_g_{U}", 30))["status"] == "REFUSED"  # nem por valor e destino


def test_erro_interno_na_validacao_recusa_sem_vazar_dados(s, fabrica, asaas, monkeypatch, caplog):
    def quebra(*_a, **_k):
        raise RuntimeError("segredo-interno maria@arena.test")

    monkeypatch.setattr(recebimentos, "_validar_transferencia", quebra)
    r = recebimentos.validar_saque(s, TOKEN, _transf(f"tr_e_{U}", 30))
    assert r["status"] == "REFUSED" and r["refuseReason"]
    assert "maria@arena.test" not in caplog.text and "segredo-interno" not in caplog.text
    assert _eventos_validacao(s)[-1].resultado == "recusado"


def test_duas_validacoes_simultaneas_nao_aprovam_duas_vezes(s, fabrica, asaas):
    import threading

    saque, _rec = _saque_sem_id(s, fabrica, asaas, 30)
    resultados, barreira = [], threading.Barrier(2)

    def validar(tid):
        with Session() as outra:
            barreira.wait()
            resultados.append(recebimentos.validar_saque(outra, TOKEN, _transf(tid, 30))["status"])

    ts = [threading.Thread(target=validar, args=(t,)) for t in (f"tr_p1_{U}", f"tr_p2_{U}")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(resultados) == ["APPROVED", "REFUSED"]
    s.expire_all()
    assert s.get(SaqueRecebedor, saque.id).asaas_transfer_id in (f"tr_p1_{U}", f"tr_p2_{U}")


def test_validacao_de_split_so_para_cobrancas_nossas_e_carteira_do_playgo(s, fabrica, asaas):
    _a, q, g, _rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g)
    ok = {"type": "PAYMENT_SPLIT", "paymentSplit": {"payment": r.asaas_payment_id, "walletId": "wal_mae", "percentualValue": 10}}
    assert recebimentos.validar_saque(s, TOKEN, ok) == {"status": "APPROVED"}
    assert recebimentos.validar_saque(s, TOKEN, ok | {"paymentSplit": {"payment": "pay_alheia", "walletId": "wal_mae"}})["status"] == "REFUSED"
    assert recebimentos.validar_saque(s, TOKEN, {"type": "PAYMENT_SPLIT", "paymentSplit": {"payment": r.asaas_payment_id, "walletId": "wal_estranha"}})["status"] == "REFUSED"


def test_validacao_de_split_exige_reserva_nossa_referencia_e_comissao_esperada(s, fabrica, asaas):
    _a, q, g, _rec = _ativada(s, fabrica, asaas)
    r = _cobrar(s, q, g)
    base = {"payment": {"id": r.asaas_payment_id, "externalReference": f"reserva:{r.id}"}, "walletId": "wal_mae", "externalReference": f"reserva:{r.id}", "percentualValue": 10}
    assert recebimentos.validar_saque(s, TOKEN, {"type": "PAYMENT_SPLIT", "paymentSplit": base}) == {"status": "APPROVED"}
    errada = base | {"payment": {"id": r.asaas_payment_id, "externalReference": "reserva:999999"}, "externalReference": None}
    assert recebimentos.validar_saque(s, TOKEN, {"type": "PAYMENT_SPLIT", "paymentSplit": errada})["status"] == "REFUSED"
    assert recebimentos.validar_saque(s, TOKEN, {"type": "PAYMENT_SPLIT", "paymentSplit": base | {"percentualValue": 50}})["status"] == "REFUSED"  # percentual fora do combinado
    r.comissao = Decimal(0)  # cobrança sem split de comissão
    s.commit()
    assert recebimentos.validar_saque(s, TOKEN, {"type": "PAYMENT_SPLIT", "paymentSplit": base})["status"] == "REFUSED"


# ---------------------------------------------------------------- chave, dados anuais, isolamento


def test_rotacao_da_chave_troca_confere_e_desativa_a_antiga(s, fabrica, asaas):
    a, _q, g, rec = _ativada(s, fabrica, asaas)
    antiga = recebimentos.decifrar(rec.api_key_cripto)
    with pytest.raises(SemPermissao):
        recebimentos.rotacionar_chave(s, g, a.id)
    recebimentos.rotacionar_chave(s, _admin(s), a.id)
    s.refresh(rec)
    assert recebimentos.decifrar(rec.api_key_cripto) == CHAVE_SUB + "NOVA" != antiga and rec.api_key_id == "tok_novo"
    assert asaas.chamou("PUT", "/accessTokens/")  # a antiga foi desativada
    assert asaas.chamou("GET", "/finance/balance")[-1][2] == CHAVE_SUB + "NOVA"  # a nova foi testada antes de trocar


def test_atualizacao_anual_dos_dados_e_lembrete(s, fabrica, asaas):
    a, _q, g, rec = _ativada(s, fabrica, asaas)
    assert recebimentos.ver(s, a.id, g)["recebedor"]["precisa_atualizar_dados"] is False
    rec.dados_atualizados_em = agora() - timedelta(days=340)
    s.commit()
    assert recebimentos.ver(s, a.id, g)["recebedor"]["precisa_atualizar_dados"] is True
    assert recebimentos.avisar_atualizacao_dados(s) >= 1
    with pytest.raises(ErroNegocio, match="não é o da conta"):
        recebimentos.atualizar_dados_cadastrais(s, a.id, g, DADOS_PF | {"cpf_cnpj": "11144477735"})
    est = recebimentos.atualizar_dados_cadastrais(s, a.id, g, DADOS_PF)
    assert est["recebedor"]["precisa_atualizar_dados"] is False and asaas.chamou("POST", "/myAccount/commercialInfo")[0][2].startswith(CHAVE_SUB)


def test_acesso_cruzado_uma_arena_nunca_ve_nem_mexe_na_outra(s, fabrica, asaas):
    a, qa, _ga, ra = _com_saldo(s, fabrica, asaas, 100)
    _b, _qb, gb, rb = _ativada(s, fabrica, asaas, nome="Arena B")
    for chamada in (
        lambda: recebimentos.ver(s, a.id, gb), lambda: recebimentos.saldo(s, a.id, gb), lambda: recebimentos.extrato(s, a.id, gb),
        lambda: recebimentos.saques(s, a.id, gb), lambda: recebimentos.solicitar_saque(s, a.id, gb, Decimal(20)),
        lambda: recebimentos.definir_destino(s, a.id, gb, "EMAIL", "x@y.test", True), lambda: recebimentos.atualizar_situacao(s, a.id, gb),
    ):
        with pytest.raises(SemPermissao):
            chamada()
    ra_reserva = _reserva(s, qa, 15)
    with pytest.raises(SemPermissao):  # reserva da arena A, gestor da B
        recebimentos.criar_cobranca_reserva(s, ra_reserva.id, gb, Decimal(10), {"nome": "Cliente Teste", "cpf_cnpj": "52998224725"})
    assert recebimentos.saldo_interno(s, rb.id) == 0 and recebimentos.saldo_interno(s, ra.id) > 0


def test_a_apikey_nunca_aparece_em_respostas_nem_em_log(s, fabrica, asaas, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    a, _q, g, _rec = _com_saldo(s, fabrica, asaas, 100)
    recebimentos.definir_destino(s, a.id, g, "EMAIL", "maria@arena.test", True)
    asaas.falhar.add("POST /transfers")
    with pytest.raises(ErroNegocio):
        recebimentos.solicitar_saque(s, a.id, g, Decimal(20), "log-1")
    respostas = [recebimentos.ver(s, a.id, g), recebimentos.saldo(s, a.id, g), recebimentos.extrato(s, a.id, g), recebimentos.saques(s, a.id, g), recebimentos.painel_admin(s, _admin(s))]
    texto = jsonlib.dumps(respostas, default=str) + caplog.text
    assert CHAVE_SUB not in texto and "maria@arena.test" not in texto and "52998224725" not in texto


# ---------------------------------------------------------------- HTTP


def test_api_http_rotas_webhooks_e_isolamento(banco, monkeypatch):
    from fastapi.testclient import TestClient

    from playgo.models import Usuario
    from playgo.web.app import app

    fake = FakeAsaas()
    monkeypatch.setattr(cobranca, "_http", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    for k, v in {"asaas_api_key": "$aact_hmlg_mae", "asaas_ambiente": "sandbox", "recebimentos_fase": "baas", "asaas_webhook_token_parceiros": TOKEN,
                 "url_publica": "https://playgo.teste", "asaas_wallet_id": "wal_mae", "recebimentos_comissao_percentual": Decimal(10)}.items():
        monkeypatch.setattr(settings, k, v)
    with TestClient(app) as c:
        def cadastrar(nome):
            r = c.post("/api/v1/auth/cadastro", json={"nome": nome, "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"x{uuid.uuid4().hex[:10]}", "aceito_termos": True, "maior_de_idade": True})
            return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]

        adm, adm_id = cadastrar("Adm")
        ga, ga_id = cadastrar("Gestor A")
        gb, gb_id = cadastrar("Gestor B")
        with Session() as s:
            s.query(Usuario).filter(Usuario.id == adm_id).update({Usuario.admin: True})
            s.commit()
        ate = str(agora().date() + timedelta(days=30))
        arenas_ids = {}
        for nome, h, uid in (("A", ga, ga_id), ("B", gb, gb_id)):
            c.post(f"/api/v1/admin/usuarios/{uid}/plano", headers=adm, json={"plano": "arena", "ate": ate})
            r = c.post("/api/v1/gestao/arenas", headers=h, json={"nome": "Arena " + nome, "latitude": PERTO[0], "longitude": PERTO[1]})
            assert r.status_code == 201, r.text
            arenas_ids[nome] = r.json()["id"]
        A, B = arenas_ids["A"], arenas_ids["B"]
        corpo = {"nome": "Maria Souza", "email": "maria@arena.test", "cpf_cnpj": "529.982.247-25", "data_nascimento": "1985-05-20", "telefone": "67999991111", "cep": "79002000", "endereco": "Rua X", "numero": "1", "bairro": "Centro", "renda_mensal": 5000}

        # isolamento e login
        assert c.get(f"/api/v1/recebimentos/arenas/{A}").status_code in (401, 403)  # sem login
        assert c.get(f"/api/v1/recebimentos/arenas/{A}", headers=gb).status_code == 403
        assert c.post(f"/api/v1/recebimentos/arenas/{A}/ativar", headers=gb, json=corpo).status_code == 403
        assert c.get("/api/v1/recebimentos/admin/painel", headers=ga).status_code == 403
        r = c.post(f"/api/v1/recebimentos/arenas/{A}/ativar", headers=ga, json=corpo)
        assert r.status_code == 200 and r.json()["recebedor"]["status_kyc"] == "criando" and r.json()["asaas"]["texto"] and r.headers["cache-control"] == "no-store"
        assert CHAVE_SUB not in r.text and "52998224725" not in r.text
        assert c.get(f"/api/v1/recebimentos/arenas/{B}", headers=gb).json()["recebedor"] is None  # B não vê a de A
        painel = c.get("/api/v1/recebimentos/admin/painel", headers=adm).json()
        assert painel["fase"] == "baas" and any(x["arena"] == "Arena A" and x["recebedor"] for x in painel["arenas"]) and painel["asaas"]["texto"]
        assert c.post(f"/api/v1/recebimentos/admin/arenas/{B}/liberar", headers=ga, json={"liberada": True}).status_code == 403
        assert c.post(f"/api/v1/recebimentos/admin/arenas/{B}/liberar", headers=adm, json={"liberada": True}).json()["liberada"] is True

        # upload de documento (multipart) só em memória
        with Session() as s:
            rec = s.scalar(select(Recebedor).where(Recebedor.arena_id == A))
            rec.consultar_apos, rec.status_kyc, rec.documentos_pendentes = None, "documentos_pendentes", [{"id": "g9", "tipo": "IDENTIFICATION", "titulo": "Identificação", "onboarding_url": None}]
            conta = rec.asaas_account_id
            s.commit()
        fake.docs = [{"id": "g9", "status": "PENDING"}]
        r = c.post(f"/api/v1/recebimentos/arenas/{A}/documentos", headers=ga, data={"grupo_id": "g9", "tipo": "IDENTIFICATION"}, files={"arquivo": ("rg.pdf", b"%PDF-1.4 x", "application/pdf")})
        assert r.status_code == 200 and fake.chamou("POST", "/myAccount/documents/g9")

        # webhooks por HTTP
        evento = {"id": "evt_http1", "event": "ACCOUNT_STATUS_GENERAL_APPROVAL_APPROVED", "account": {"id": conta}}
        assert c.post("/api/v1/recebimentos/asaas", json=evento).status_code == 401
        assert c.post("/api/v1/recebimentos/asaas", json=evento, headers={"asaas-access-token": "errado"}).status_code == 401
        assert c.post("/api/v1/recebimentos/asaas", json=evento, headers={"asaas-access-token": TOKEN}).json() == {"resultado": "ok"}
        assert c.post("/api/v1/recebimentos/asaas", json=evento, headers={"asaas-access-token": TOKEN}).json() == {"resultado": "duplicado"}
        assert c.post("/api/v1/recebimentos/asaas", content=b"", headers={"asaas-access-token": TOKEN}).json() == {"resultado": "ignorado"}  # teste de conexão
        assert c.get(f"/api/v1/recebimentos/arenas/{A}", headers=ga).json()["recebedor"]["pode_cobrar"] is True

        # validação de saque por HTTP: sem token 401; operação desconhecida recusada com 200
        assert c.post("/api/v1/recebimentos/asaas/validar-saque", json={"type": "TRANSFER"}).status_code == 401
        r = c.post("/api/v1/recebimentos/asaas/validar-saque", json={"type": "TRANSFER", "transfer": {"id": "tr_x", "value": 5}}, headers={"asaas-access-token": TOKEN})
        assert r.status_code == 200 and r.json()["status"] == "REFUSED"

        # o webhook antigo dos planos continua valendo e separado
        monkeypatch.setattr(settings, "asaas_webhook_token", "tok-planos")
        assert c.post("/api/v1/cobranca/asaas", json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "p", "externalReference": "reserva:1"}}, headers={"asaas-access-token": "tok-planos"}).json() == {"resultado": "ignorado"}


def test_http_fase_desligada_responde_recurso_indisponivel(banco, monkeypatch):
    from fastapi.testclient import TestClient

    from playgo.web.app import app

    monkeypatch.setattr(settings, "recebimentos_fase", "desligado")
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/cadastro", json={"nome": "X", "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"x{uuid.uuid4().hex[:10]}", "aceito_termos": True, "maior_de_idade": True})
        h = {"Authorization": "Bearer " + r.json()["token"]}
        resp = c.get("/api/v1/recebimentos/arenas/1", headers=h)
        assert resp.status_code == 400 and "não está disponível" in resp.text


def test_paginas_do_site_renderizam_e_respeitam_a_fase(banco, monkeypatch):
    from fastapi.testclient import TestClient

    from playgo.models import Usuario
    from playgo.web.app import app

    for k, v in {"recebimentos_fase": "desligado", "recebimentos_texto_asaas": "Pagamentos processados por Asaas (teste)"}.items():
        monkeypatch.setattr(settings, k, v)
    with TestClient(app) as c:
        email = f"{uuid.uuid4().hex[:10]}@teste.local"
        assert c.post("/cadastro", data={"nome": "Gestor Pagina", "email": email, "senha": "senha-de-teste-1", "usuario": f"gp{uuid.uuid4().hex[:10]}", "aceito_termos": "1", "maior_de_idade": "1"}, follow_redirects=False).status_code == 303
        with Session() as s:
            u = s.scalar(select(Usuario).where(Usuario.email == email))
            u.admin = True  # admin não paga plano e vê o painel
            s.commit()
        assert c.post("/gestao/arenas", data={"nome": "Arena Pagina", "tipo_estabelecimento": "arena", "endereco": "Rua", "latitude": str(PERTO[0]), "longitude": str(PERTO[1])}, follow_redirects=False).status_code in (303, 200)
        g = c.get("/gestao")
        assert g.status_code == 200 and "Receber pelo app" not in g.text  # desligado: nada de botão
        r = c.get("/gestao/recebimentos")
        assert r.status_code == 200 and "ainda não está disponível" in r.text and "Pagamentos processados por Asaas (teste)" in r.text
        monkeypatch.setattr(settings, "recebimentos_fase", "baas")
        g = c.get("/gestao")
        assert "💰 Receber pelo app" in g.text and "recebimentos.js" in g.text
        r = c.get("/gestao/recebimentos")
        assert r.status_code == 200 and "Recebimentos.montarArena" in r.text and "recebimentos.css" in r.text
        a = c.get("/administracao/recebimentos")
        assert a.status_code == 200 and "Recebimentos.montarAdmin" in a.text
        assert "Recebimentos de parceiros" in c.get("/administracao").text
