"""Avisos no celular (Web Push): cifra conferida com o exemplo do RFC 8291, assinatura VAPID, inscrições, envio com serviço de push simulado."""

import json
import os
from datetime import timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from playgo import notificacoes, push
from playgo.config import settings
from playgo.db import agora
from playgo.erros import ErroNegocio

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _aparelho():
    """Um 'navegador': par de chaves e segredo de autenticação, como a inscrição que ele entregaria."""
    chave = ec.generate_private_key(ec.SECP256R1())
    publica = chave.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return chave, publica, push._b64(publica), push._b64(auth), auth


def _decifrar(corpo: bytes, chave_ua, publica_ua: bytes, auth: bytes) -> bytes:
    """O que o navegador faz ao receber (RFC 8291 §4): prova que só o aparelho lê a mensagem."""
    sal, idlen = corpo[:16], corpo[20]
    publica_as, cifrado = corpo[21 : 21 + idlen], corpo[21 + idlen :]
    segredo = chave_ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), publica_as))
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth, info=b"WebPush: info\x00" + publica_ua + publica_as).derive(segredo)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=sal, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=sal, info=b"Content-Encoding: nonce\x00").derive(ikm)
    claro = AESGCM(cek).decrypt(nonce, cifrado, None)
    assert claro.endswith(b"\x02")
    return claro[:-1]


@pytest.fixture
def vapid(monkeypatch):
    chaves = push.gerar_chaves_vapid()
    monkeypatch.setattr(settings, "vapid_publica", chaves["publica"])
    monkeypatch.setattr(settings, "vapid_privada", chaves["privada"])
    monkeypatch.setattr(settings, "vapid_assunto", "mailto:teste@playgo.app")
    return chaves


@pytest.fixture
def servico_de_push(monkeypatch):
    """Serviço de push simulado: guarda cada requisição e responde com o status configurado."""

    class Servico:
        def __init__(self):
            self.pedidos: list[httpx.Request] = []
            self.status = 201

        def __call__(self, req: httpx.Request) -> httpx.Response:
            self.pedidos.append(req)
            return httpx.Response(self.status)

    f = Servico()
    monkeypatch.setattr(push, "_http", lambda: httpx.Client(transport=httpx.MockTransport(f)))
    return f


def test_cifra_bate_com_o_exemplo_do_rfc_8291():
    d = push._desb64
    efemera = ec.derive_private_key(int.from_bytes(d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    corpo = push.cifrar(
        b"When I grow up, I want to be a watermelon", "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", "BTBZMqHH6r4Tts7J_aSIgg",
        privada_efemera=efemera, sal=d("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert push._b64(corpo) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_so_o_aparelho_consegue_ler_a_mensagem():
    chave, publica, p256dh, auth, auth_bytes = _aparelho()
    corpo = push.cifrar("Vaga aberta: Vôlei às 19h".encode(), p256dh, auth)
    assert b"Vaga" not in corpo
    assert _decifrar(corpo, chave, publica, auth_bytes) == "Vaga aberta: Vôlei às 19h".encode()


def test_assinatura_vapid_confere_com_a_chave_publica(vapid):
    jwt = push._jwt_vapid("https://fcm.googleapis.com", 600)
    cab, corpo, assinatura = jwt.split(".")
    bruta = push._desb64(assinatura)
    r, s = int.from_bytes(bruta[:32], "big"), int.from_bytes(bruta[32:], "big")
    publica = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push._desb64(vapid["publica"]))
    publica.verify(encode_dss_signature(r, s), f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256()))  # levanta se a assinatura não bate
    dados = json.loads(push._desb64(corpo))
    assert dados["aud"] == "https://fcm.googleapis.com" and dados["sub"] == "mailto:teste@playgo.app" and dados["exp"] > int(agora().timestamp())


@pytest.mark.parametrize("endereco,ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
    ("https://web.push.apple.com/abc", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=abc", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),  # sem https
    ("https://storage.googleapis.com/bucket/x", False),  # outro serviço do Google
    ("https://localhost/x", False), ("https://127.0.0.1/x", False), ("https://169.254.169.254/latest/meta-data", False),
    ("https://fcm.googleapis.com.malicioso.exemplo/x", False), ("https://fcm.googleapis.com:8443/x", False),
    ("https://user@fcm.googleapis.com/x", False), ("", False), ("javascript:alert(1)", False),
])
def test_endereco_do_push_so_de_servico_conhecido(endereco, ok):
    assert push.endpoint_valido(endereco) is ok


def test_inscrever_cancelar_e_limite_por_pessoa(s, fabrica, vapid):
    u, outro = fabrica.atleta("Aparelho"), fabrica.atleta("Outro")
    _, _, p256dh, auth, _ = _aparelho()
    endereco = f"https://fcm.googleapis.com/fcm/send/ins-{u.id}-{agora().timestamp()}"
    push.inscrever(s, u, endereco, p256dh, auth, "Android")
    assert push.estado(s, u, endereco) == {"disponivel": True, "chave_publica": vapid["publica"], "aparelhos": 1, "este_aparelho": True}
    assert push.estado(s, outro, endereco)["este_aparelho"] is False
    push.inscrever(s, u, endereco, p256dh, auth)  # repetir não duplica
    assert push.estado(s, u)["aparelhos"] == 1
    assert push.cancelar(s, outro, endereco) is False  # ninguém cancela o aparelho de outra pessoa
    assert push.cancelar(s, u, endereco) is True and push.estado(s, u)["aparelhos"] == 0
    for i in range(push.MAX_INSCRICOES_POR_PESSOA + 3):
        push.inscrever(s, u, f"https://fcm.googleapis.com/fcm/send/{i}-{agora().timestamp()}", p256dh, auth)
    assert push.estado(s, u)["aparelhos"] == push.MAX_INSCRICOES_POR_PESSOA
    for ruim in (("https://exemplo.com/x", p256dh, auth), (endereco, "chave-curta", auth), (endereco, p256dh, "x")):
        with pytest.raises(ErroNegocio):
            push.inscrever(s, u, *ruim)


def test_envio_das_notificacoes_novas(s, fabrica, vapid, servico_de_push):
    u = fabrica.atleta("Recebe")
    chave, publica, p256dh, auth, auth_bytes = _aparelho()
    endereco = f"https://fcm.googleapis.com/fcm/send/env-{u.id}-{agora().timestamp()}"
    push.inscrever(s, u, endereco, p256dh, auth)
    n = notificacoes.avisar(s, u.id, "vaga", "🏐 Vaga aberta", "Vôlei hoje às 19h", "/atividades/7", None, f"push-novo:{u.id}:{agora().timestamp()}")
    antiga = notificacoes.avisar(s, u.id, "vaga", "Aviso velho", "", None, None, f"push-velho:{u.id}:{agora().timestamp()}")
    antiga.criado_em = agora() - timedelta(hours=3)  # fora da janela: nunca vira push (evita enxurrada ao ligar o recurso)
    s.commit()

    enviados = push.enviar_pendentes(s)
    meus = [p for p in servico_de_push.pedidos if str(p.url) == endereco]
    assert enviados >= 1 and len(meus) == 1
    pedido = meus[0]
    assert pedido.headers["content-encoding"] == "aes128gcm" and pedido.headers["authorization"].startswith("vapid t=")
    assert pedido.headers["authorization"].endswith(f"k={vapid['publica']}") and pedido.headers["ttl"] == str(push.TTL_S)
    carga = json.loads(_decifrar(pedido.content, chave, publica, auth_bytes))
    assert carga == {"titulo": "🏐 Vaga aberta", "corpo": "Vôlei hoje às 19h", "link": "/atividades/7", "tag": f"n{n.id}"}
    s.refresh(n)
    s.refresh(antiga)
    assert n.push_em is not None and antiga.push_em is None
    push.enviar_pendentes(s)
    assert len([p for p in servico_de_push.pedidos if str(p.url) == endereco]) == 1  # já enviado: não repete


def test_muitos_avisos_viram_um_resumo_e_inscricao_morta_e_apagada(s, fabrica, vapid, servico_de_push):
    u = fabrica.atleta("Muitos")
    chave, publica, p256dh, auth, auth_bytes = _aparelho()
    endereco = f"https://fcm.googleapis.com/fcm/send/mu-{u.id}-{agora().timestamp()}"
    push.inscrever(s, u, endereco, p256dh, auth)
    for i in range(push.MAX_PUSHES_POR_PESSOA_POR_CICLO + 2):
        notificacoes.avisar(s, u.id, "vaga", f"Aviso {i}", "", None, None, f"muitos:{u.id}:{i}:{agora().timestamp()}")
    s.commit()
    push.enviar_pendentes(s)
    meus = [p for p in servico_de_push.pedidos if str(p.url) == endereco]
    cargas = [json.loads(_decifrar(p.content, chave, publica, auth_bytes)) for p in meus]
    assert len(cargas) == push.MAX_PUSHES_POR_PESSOA_POR_CICLO + 1
    assert cargas[-1]["corpo"] == "Você tem mais 2 novos avisos."

    servico_de_push.status = 410  # o navegador cancelou a inscrição: o serviço de push avisa e nós esquecemos o aparelho
    assert push.enviar_para(s, u.id, push._carga("x", "y", None, "t")) == 0
    assert push.estado(s, u)["aparelhos"] == 0


def test_sem_chaves_vapid_nada_e_enviado(s, fabrica, servico_de_push, monkeypatch):
    monkeypatch.setattr(settings, "vapid_publica", "")
    monkeypatch.setattr(settings, "vapid_privada", "")
    u = fabrica.atleta("Sem chave")
    assert push.estado(s, u)["disponivel"] is False and push.enviar_pendentes(s) == 0 and not servico_de_push.pedidos
    with pytest.raises(ErroNegocio):
        push.enviar_teste(s, u)


def test_link_de_fora_nao_vai_na_mensagem():
    assert push._carga("t", "c", "https://malicioso.exemplo", "x")["link"] is None
    assert push._carga("t", "c", "/atividades/1", "x")["link"] == "/atividades/1"
    assert len(push._carga("t" * 500, "c" * 500, None, "x")["corpo"]) == 160


# ---------------------------------------------------------------- API (sem a sessão `s` aberta junto: o startup do app altera tabelas)


def test_api_de_push_exige_login_e_recusa_endereco_estranho(banco, monkeypatch):
    import uuid

    from fastapi.testclient import TestClient

    from playgo.web.app import app

    chaves = push.gerar_chaves_vapid()
    monkeypatch.setattr(settings, "vapid_publica", chaves["publica"])
    monkeypatch.setattr(settings, "vapid_privada", chaves["privada"])
    _, _, p256dh, auth, _ = _aparelho()
    with TestClient(app) as c:
        for caminho, metodo in (("/api/v1/push/estado", "get"), ("/api/v1/push/inscrever", "post"), ("/api/v1/push/cancelar", "post"), ("/api/v1/push/teste", "post")):
            assert getattr(c, metodo)(caminho).status_code in (401, 422)
        r = c.post("/api/v1/auth/cadastro", json={"nome": "Push", "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True, "maior_de_idade": True})
        cab = {"Authorization": "Bearer " + r.json()["token"]}
        assert c.get("/api/v1/push/estado", headers=cab).json()["chave_publica"] == chaves["publica"]
        ruim = c.post("/api/v1/push/inscrever", headers=cab, json={"endpoint": "https://rede-interna.exemplo/x", "keys": {"p256dh": p256dh, "auth": auth}})
        assert ruim.status_code == 400
        bom = c.post("/api/v1/push/inscrever", headers=cab, json={"endpoint": "https://fcm.googleapis.com/fcm/send/api-" + uuid.uuid4().hex, "keys": {"p256dh": p256dh, "auth": auth}})
        assert bom.status_code == 201 and bom.json()["aparelhos"] == 1
