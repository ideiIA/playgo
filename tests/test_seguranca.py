"""Segurança: hash de senha com versão, freio de tentativas, foto de perfil sem caminho livre, CSV sem fórmula, imagem enorme, cabeçalhos e comandos de dev."""

import io
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from playgo import arenas, auditoria, contas, midia, relatorios, seguranca
from playgo.db import agora
from playgo.erros import ErroNegocio, TentativasDemais
from playgo.models import Registro

from test_planos import PERTO, _admin


def _com_ip(ip):
    return auditoria._contexto.set({"ip": ip, "porta": 1, "user_agent": "teste"})


def test_hash_antigo_continua_valendo_e_sobe_de_versao(s, fabrica):
    import base64, hashlib, os

    sal = os.urandom(16)
    antigo = "scrypt$" + base64.b64encode(sal).decode() + "$" + base64.b64encode(hashlib.scrypt(b"senha-antiga-1", salt=sal, n=2**14, r=8, p=1, dklen=32)).decode()
    assert seguranca.conferir("senha-antiga-1", antigo) and not seguranca.conferir("outra", antigo)
    assert seguranca.precisa_atualizar(antigo) and not seguranca.precisa_atualizar(seguranca.gerar_hash("x"))
    assert not seguranca.conferir("x", "scryptX$abc$def") and not seguranca.conferir("x", "lixo")
    u = fabrica.atleta("Antigo")
    u.senha_hash = antigo
    s.commit()
    assert contas.autenticar(s, u.email, "senha-antiga-1") is not None
    s.refresh(u)
    assert u.senha_hash.startswith(seguranca.VERSAO_ATUAL + "$") and seguranca.conferir("senha-antiga-1", u.senha_hash)


def test_freio_de_tentativas_de_login(s, fabrica, monkeypatch):
    from playgo.config import settings

    monkeypatch.setattr(settings, "max_falhas_login_conta", 5)
    monkeypatch.setattr(settings, "max_falhas_login_ip", 30)
    u = fabrica.atleta("Alvo")
    ip_a, ip_b = f"203.0.113.{int(agora().timestamp() * 1000) % 250 + 1}", f"198.51.100.{int(agora().timestamp() * 1000) % 250 + 1}"  # o banco de teste guarda a trilha entre execuções
    token = _com_ip(ip_a)
    try:
        for _ in range(5):
            assert contas.autenticar(s, u.email, "errada-errada") is None
        with pytest.raises(TentativasDemais):
            contas.autenticar(s, u.email, "errada-errada")
        with pytest.raises(TentativasDemais):
            contas.autenticar(s, u.email, "a-senha-certa-nao-vale-durante-o-freio")
        # outro endereço não sofre o bloqueio, e a trilha não guardou o e-mail digitado
        auditoria._contexto.reset(token)
        token = _com_ip(ip_b)
        assert contas.autenticar(s, u.email, "errada-errada") is None
        falhas = s.scalars(select(Registro).where(Registro.acao == "login_falha", Registro.ip == ip_a)).all()
        assert falhas and all(u.email not in str(f.detalhes) for f in falhas)
    finally:
        auditoria._contexto.reset(token)


def test_freio_de_cadastros_em_massa(s, monkeypatch):
    from playgo.config import settings

    monkeypatch.setattr(settings, "max_cadastros_ip_hora", 10)
    token = _com_ip(f"203.0.113.{int(agora().timestamp()) % 200 + 1}")
    try:
        for i in range(10):
            contas.cadastrar(s, f"Pessoa {i}", f"massa{i}-{agora().timestamp()}@teste.com", "senha-boa-123", f"massa{i}x{int(agora().timestamp()) % 1000}", True)
        with pytest.raises(TentativasDemais):
            contas.cadastrar(s, "Mais Uma", f"extra-{agora().timestamp()}@teste.com", "senha-boa-123", "maisumaxx", True)
    finally:
        auditoria._contexto.reset(token)


def test_foto_de_perfil_nao_aceita_endereco_livre(s, fabrica):
    u = fabrica.atleta("Foto")
    for ruim in ("https://rastreador.exemplo/pixel.png", "/foto/../capas/" + "a" * 32 + ".jpg", "/foto/abc.jpg", "javascript:alert(1)"):
        with pytest.raises(ErroNegocio):
            contas.atualizar_perfil(s, u, foto_url=ruim)
    u.foto_url = "/foto/" + "b" * 32 + ".jpg"
    s.commit()
    contas.atualizar_perfil(s, u, foto_url=u.foto_url)  # o mesmo endereço (tela que reenvia o perfil) segue valendo
    assert contas._arquivo_da_foto(u) == "perfil/" + "b" * 32 + ".jpg"
    u.foto_url = "/foto/../capas/" + "a" * 32 + ".jpg"
    assert contas._arquivo_da_foto(u) is None  # nunca monta caminho com ".."
    contas.atualizar_perfil(s, u, foto_url="")
    assert u.foto_url is None


def test_csv_do_relatorio_neutraliza_formulas(s, fabrica):
    gestor = _admin(fabrica, s)
    a = arenas.criar(s, gestor, "Arena CSV", *PERTO)
    q = arenas.criar_quadra(s, a, gestor, "=HYPERLINK(\"http://x\")", [], 4, __import__("decimal").Decimal(10))
    inicio = (agora() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    arenas.reservar(s, q.id, inicio, inicio + timedelta(hours=1), "reserva", "+cmd|calc")
    s.commit()
    linhas = relatorios.reservas_csv(s, gestor, a.id, agora().date(), agora().date() + timedelta(days=3))
    corpo = linhas[1:]
    assert corpo and all(not str(c).startswith(("=", "+", "-", "@")) for l in corpo for c in l)
    assert any(str(c).startswith("'=") for l in corpo for c in l) and any(str(c).startswith("'+") for l in corpo for c in l)


def test_imagem_com_pixels_demais_e_recusada():
    buf = io.BytesIO()
    Image.new("L", (7000, 7000), 0).save(buf, "PNG")  # 49 MP: pequeno em disco, grande na memória
    with pytest.raises(ErroNegocio):
        midia.processar_capa(buf.getvalue())
    with pytest.raises(ErroNegocio):
        midia.processar_avatar(buf.getvalue())


def test_comando_demo_so_em_desenvolvimento(monkeypatch):
    from playgo import __main__ as cli
    from playgo.config import settings

    cli._exigir_desenvolvimento()  # banco local do ambiente de testes: passa
    monkeypatch.setattr(settings, "cookie_seguro", True)
    with pytest.raises(SystemExit):
        cli._exigir_desenvolvimento()
    monkeypatch.setattr(settings, "cookie_seguro", False)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u:p@db.interno.exemplo:5432/playgo")
    with pytest.raises(SystemExit):
        cli._exigir_desenvolvimento()


def test_cabecalhos_de_seguranca_em_toda_resposta():
    from fastapi.testclient import TestClient

    from playgo.web.app import app

    with TestClient(app) as c:
        h = c.get("/entrar").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in h["content-security-policy"] and "object-src 'none'" in h["content-security-policy"]
    assert "geolocation=(self)" in h["permissions-policy"] and h["referrer-policy"] == "strict-origin-when-cross-origin"


def test_pedido_que_muda_dados_de_outro_site_e_recusado():
    from fastapi.testclient import TestClient

    from playgo.web.app import app

    with TestClient(app) as c:
        dados = {"email": "ninguem@teste.com", "senha": "qualquer-coisa"}
        de_fora = c.post("/entrar", data=dados, headers={"Origin": "https://site-malicioso.exemplo"})
        nulo = c.post("/entrar", data=dados, headers={"Origin": "null"})
        proprio = c.post("/entrar", data=dados, headers={"Origin": "http://testserver"})
        sem_origem = c.post("/entrar", data=dados)
        leitura = c.get("/entrar", headers={"Origin": "https://site-malicioso.exemplo"})
    assert de_fora.status_code == 403 and nulo.status_code == 403
    assert proprio.status_code == 400 and sem_origem.status_code == 400  # chegam até a tela de login (e-mail ou senha incorretos)
    assert leitura.status_code == 200


def test_referer_de_outro_site_nao_vira_redirecionamento():
    from starlette.requests import Request

    from playgo.web.app import _de_volta_ao_site

    def pedido(ref):
        cab = [(b"host", b"playgo.exemplo")] + ([(b"referer", ref.encode())] if ref else [])
        return Request({"type": "http", "method": "POST", "path": "/x", "headers": cab, "scheme": "https", "server": ("playgo.exemplo", 443), "query_string": b""})

    assert _de_volta_ao_site(pedido("https://playgo.exemplo/arenas/1")) == "https://playgo.exemplo/arenas/1"
    assert _de_volta_ao_site(pedido("https://malicioso.exemplo/phishing")) == "/"
    assert _de_volta_ao_site(pedido("javascript:alert(1)")) == "/"
    assert _de_volta_ao_site(pedido("")) == "/"
