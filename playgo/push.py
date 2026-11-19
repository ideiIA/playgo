"""Avisos no celular (Web Push, RFC 8030 + 8291 + VAPID RFC 8292), sem biblioteca de terceiros: só `cryptography` e `httpx`.

Como funciona: no app (PWA) a pessoa toca em "Ativar avisos neste aparelho"; o navegador pede permissão e devolve uma *inscrição* (endereço do serviço de
push do Google/Apple/Mozilla + chaves), que guardamos em `PushInscricao`. Cada `Notificacao` nova vira um push: `enviar_pendentes` (agendador, a cada ciclo)
pega as notificações recentes de quem tem inscrição, cifra a mensagem para aquele aparelho e entrega ao serviço de push. O service worker do app
(`app/sw.js`) mostra o aviso com a logo do PlayGo.

Segurança: o endereço da inscrição vem do cliente, então só aceitamos serviços de push conhecidos (nada de `localhost` nem rede interna: seria SSRF);
a mensagem só leva título, texto curto e o link do app (nada sensível); a chave privada VAPID fica só no servidor (`PLAYGO_VAPID_PRIVADA`)."""

import base64
import json
import logging
import os
import time
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria
from .config import settings
from .db import agora
from .erros import ErroNegocio
from .models import Notificacao, PushInscricao, Usuario

log = logging.getLogger(__name__)

# Serviços de push que os navegadores usam (Chrome/Edge/Android, Firefox, Safari/iOS, Windows). Qualquer outro endereço é recusado.
HOSTS_PUSH = {"fcm.googleapis.com"}  # Chrome/Android/Edge
SUFIXOS_PUSH = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")  # Firefox, Windows, Safari/iOS
MAX_INSCRICOES_POR_PESSOA = 8
JANELA_ENVIO_MIN = 20  # só vira push a notificação criada nesta janela (evita enxurrada de avisos antigos ao ligar o recurso)
MAX_PUSHES_POR_PESSOA_POR_CICLO = 3  # o que passar disso vira um resumo ("você tem N novos avisos")
TTL_S = 3600  # se o aparelho estiver desligado, o serviço de push guarda o aviso por 1 h


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _desb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


# ---------------------------------------------------------------- chaves VAPID


def gerar_chaves_vapid() -> dict:
    """Par de chaves VAPID para colocar no `.env` (`python -m playgo vapid`). A pública vai ao navegador; a privada fica só no servidor."""
    privada = ec.generate_private_key(ec.SECP256R1())
    numero = privada.private_numbers().private_value.to_bytes(32, "big")
    publica = privada.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"publica": _b64(publica), "privada": _b64(numero)}


def _privada() -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(_desb64(settings.vapid_privada), "big"), ec.SECP256R1())


def configurado() -> bool:
    return bool(settings.vapid_publica and settings.vapid_privada)


def _assunto() -> str:
    return settings.vapid_assunto or (f"mailto:{settings.contato_privacidade}" if "@" in settings.contato_privacidade and not settings.contato_privacidade.endswith(".invalid") else "mailto:contato@playgo.app")


def _jwt_vapid(audiencia: str, validade_s: int = 12 * 3600) -> str:
    cab = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corpo = _b64(json.dumps({"aud": audiencia, "exp": int(time.time()) + validade_s, "sub": _assunto()}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(_privada().sign(f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{cab}.{corpo}." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


# ---------------------------------------------------------------- cifra da mensagem (RFC 8291, aes128gcm)


def cifrar(mensagem: bytes, p256dh: str, auth: str, *, privada_efemera: ec.EllipticCurvePrivateKey | None = None, sal: bytes | None = None) -> bytes:
    """Corpo da requisição de push, cifrado para o aparelho (só ele consegue ler; nem o serviço de push vê o texto)."""
    chave_ua = _desb64(p256dh)
    segredo_auth = _desb64(auth)
    efemera = privada_efemera or ec.generate_private_key(ec.SECP256R1())
    publica_as = efemera.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    publica_ua = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), chave_ua)
    segredo_ecdh = efemera.exchange(ec.ECDH(), publica_ua)
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=segredo_auth, info=b"WebPush: info\x00" + chave_ua + publica_as).derive(segredo_ecdh)
    sal = sal or os.urandom(16)
    chave = HKDF(algorithm=hashes.SHA256(), length=16, salt=sal, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=sal, info=b"Content-Encoding: nonce\x00").derive(ikm)
    cifrado = AESGCM(chave).encrypt(nonce, mensagem + b"\x02", None)  # 0x02 = último (e único) bloco
    return sal + (4096).to_bytes(4, "big") + bytes([len(publica_as)]) + publica_as + cifrado


# ---------------------------------------------------------------- inscrições


def endpoint_valido(endpoint: str) -> bool:
    """Só https, porta padrão e um serviço de push conhecido (o servidor vai fazer POST nesse endereço, que vem do cliente: SSRF se fosse livre)."""
    try:
        partes = urlsplit(endpoint or "")
        host, porta = (partes.hostname or "").lower(), partes.port
    except ValueError:
        return False
    return partes.scheme == "https" and porta is None and not partes.username and (host in HOSTS_PUSH or host.endswith(SUFIXOS_PUSH)) and len(endpoint) <= 700


def inscrever(s: SessaoORM, u: Usuario, endpoint: str, p256dh: str, auth: str, user_agent: str | None = None) -> PushInscricao:
    if not endpoint_valido(endpoint):
        raise ErroNegocio("Este aparelho usa um serviço de avisos que o PlayGo não aceita.")
    try:
        if len(_desb64(p256dh)) != 65 or len(_desb64(auth)) < 16:
            raise ValueError
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _desb64(p256dh))
    except (ValueError, TypeError):
        raise ErroNegocio("As chaves de aviso deste aparelho são inválidas. Tente ativar de novo.") from None
    existente = s.scalar(select(PushInscricao).where(PushInscricao.endpoint == endpoint))
    if existente is not None:  # o mesmo aparelho (ou um que trocou de dono): passa a valer para quem ativou agora
        existente.usuario_id, existente.p256dh, existente.auth, existente.user_agent = u.id, p256dh, auth, (user_agent or "")[:200]
        s.commit()
        return existente
    atuais = list(s.scalars(select(PushInscricao).where(PushInscricao.usuario_id == u.id).order_by(PushInscricao.id)))
    for antiga in atuais[: max(0, len(atuais) - MAX_INSCRICOES_POR_PESSOA + 1)]:
        s.delete(antiga)
    nova = PushInscricao(usuario_id=u.id, endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent=(user_agent or "")[:200])
    s.add(nova)
    auditoria.registrar(s, u.id, "push_ativar", "usuario", u.id)
    s.commit()
    return nova


def cancelar(s: SessaoORM, u: Usuario, endpoint: str) -> bool:
    n = s.execute(delete(PushInscricao).where(PushInscricao.usuario_id == u.id, PushInscricao.endpoint == endpoint)).rowcount or 0
    if n:
        auditoria.registrar(s, u.id, "push_desativar", "usuario", u.id)
    s.commit()
    return bool(n)


def estado(s: SessaoORM, u: Usuario, endpoint: str | None = None) -> dict:
    mine = list(s.scalars(select(PushInscricao).where(PushInscricao.usuario_id == u.id)))
    return {"disponivel": configurado(), "chave_publica": settings.vapid_publica if configurado() else None, "aparelhos": len(mine), "este_aparelho": bool(endpoint and any(i.endpoint == endpoint for i in mine))}


# ---------------------------------------------------------------- envio


def _http() -> httpx.Client:
    return httpx.Client(timeout=10, follow_redirects=False)


def _enviar_um(inscricao: PushInscricao, carga: dict, urgencia: str = "normal") -> int:
    """Entrega uma mensagem a um aparelho. Devolve o status HTTP (201 = aceito; 404/410 = inscrição morta)."""
    corpo = cifrar(json.dumps(carga, ensure_ascii=False, separators=(",", ":")).encode(), inscricao.p256dh, inscricao.auth)
    partes = urlsplit(inscricao.endpoint)
    cab = {
        "Authorization": f"vapid t={_jwt_vapid(f'{partes.scheme}://{partes.netloc}')}, k={settings.vapid_publica}",
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL_S), "Urgency": urgencia,
    }
    try:
        with _http() as c:
            return c.post(inscricao.endpoint, content=corpo, headers=cab).status_code
    except httpx.HTTPError as e:
        log.warning("Push: falha de rede com %s: %s", partes.netloc, e)
        return 0


def _carga(titulo: str, corpo: str, link: str | None, tag: str) -> dict:
    return {"titulo": (titulo or "PlayGo")[:90], "corpo": (corpo or "")[:160], "link": link if (link or "").startswith("/") else None, "tag": tag}


def enviar_para(s: SessaoORM, usuario_id: int, carga: dict, urgencia: str = "normal") -> int:
    """Manda a mesma mensagem a todos os aparelhos da pessoa e apaga as inscrições que o serviço de push disse estarem mortas. Devolve quantos aceitaram."""
    aceitos = 0
    for i in list(s.scalars(select(PushInscricao).where(PushInscricao.usuario_id == usuario_id))):
        status = _enviar_um(i, carga, urgencia)
        if 200 <= status < 300:
            aceitos += 1
        elif status in (404, 410):
            s.delete(i)
        elif status in (400, 401, 403):
            log.warning("Push: %s recusou a mensagem (HTTP %s); confira as chaves VAPID.", urlsplit(i.endpoint).netloc, status)
    s.commit()
    return aceitos


def enviar_teste(s: SessaoORM, u: Usuario) -> int:
    if not configurado():
        raise ErroNegocio("Os avisos no celular ainda não estão ligados neste servidor.")
    n = enviar_para(s, u.id, _carga("PlayGo", "Pronto! Os avisos estão funcionando neste aparelho.", "/notificacoes", f"teste-{u.id}"))
    if not n:
        raise ErroNegocio("Não consegui entregar o aviso de teste. Ative os avisos de novo neste aparelho.")
    return n


def enviar_pendentes(s: SessaoORM) -> int:
    """Roda no ciclo do agendador: transforma em push as notificações recentes de quem tem aparelho inscrito. No máximo 3 por pessoa por ciclo; o resto vira um resumo."""
    if not configurado():
        return 0
    desde = agora() - timedelta(minutes=JANELA_ENVIO_MIN)
    com_aparelho = select(PushInscricao.usuario_id).distinct()
    pendentes = list(s.scalars(select(Notificacao).where(Notificacao.push_em.is_(None), Notificacao.criado_em >= desde, Notificacao.usuario_id.in_(com_aparelho)).order_by(Notificacao.id)))
    por_pessoa: dict[int, list[Notificacao]] = {}
    for n in pendentes:
        por_pessoa.setdefault(n.usuario_id, []).append(n)
    enviados = 0
    for uid, itens in por_pessoa.items():
        for n in itens[:MAX_PUSHES_POR_PESSOA_POR_CICLO]:
            if enviar_para(s, uid, _carga(n.titulo, n.corpo, n.link, f"n{n.id}"), "high" if n.tipo in ("urgente", "lembrete") else "normal"):
                enviados += 1
        resto = itens[MAX_PUSHES_POR_PESSOA_POR_CICLO:]
        if resto:
            enviar_para(s, uid, _carga("PlayGo", f"Você tem mais {len(resto)} novos avisos.", "/notificacoes", f"resumo-{uid}"))
        agora_ = agora()
        for n in itens:
            n.push_em = agora_
    s.commit()
    return enviados
