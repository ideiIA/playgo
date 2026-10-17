"""Câmera do navegador → YouTube, pelo LiveKit Cloud.

A pessoa autorizada (organização/mesário) abre a câmera no próprio navegador do celular ou do computador; o vídeo vai ao LiveKit Cloud
(WebRTC) e o "Egress" do LiveKit o repassa por RTMP à chave do jogo no YouTube. **O servidor do PlayGo continua sem receber vídeo**: aqui
só se emite o token curto da sala e se liga/desliga a saída. A chave RTMP é entregue ao LiveKit no pedido e nunca é gravada nem logada.

Sem `PLAYGO_LIVEKIT_*` no ambiente o recurso some e fica só o envio por Larix/OBS. Os testes trocam `_http`."""

import base64
import hashlib
import hmac
import json
import time

import httpx

from .config import settings
from .erros import ErroNegocio

TOKEN_TTL_S = 3 * 3600  # um jogo; a pessoa pede outro token se precisar voltar depois


def _http() -> httpx.Client:
    return httpx.Client(timeout=30)


def configurado() -> bool:
    return bool(settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret)


def sala_do_jogo(jogo_id: int) -> str:
    return f"playgo-jogo-{jogo_id}"


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _jwt(identidade: str, video: dict, ttl: int) -> str:
    agora = int(time.time())
    cab = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    corpo = _b64(json.dumps({"iss": settings.livekit_api_key, "sub": identidade, "nbf": agora - 10, "exp": agora + ttl, "video": video}, separators=(",", ":")).encode())
    assinatura = hmac.new(settings.livekit_api_secret.encode(), f"{cab}.{corpo}".encode(), hashlib.sha256).digest()
    return f"{cab}.{corpo}.{_b64(assinatura)}"


def token_camera(jogo_id: int, usuario_id: int) -> dict:
    """Token para o navegador entrar na sala do jogo SÓ publicando (não assiste nem recebe dados de ninguém)."""
    if not configurado():
        raise ErroNegocio("A câmera pelo navegador ainda não foi configurada neste servidor. Use o Larix ou o OBS.")
    sala = sala_do_jogo(jogo_id)
    token = _jwt(f"u{usuario_id}", {"room": sala, "roomJoin": True, "canPublish": True, "canSubscribe": False, "canPublishData": False}, TOKEN_TTL_S)
    return {"url": settings.livekit_url, "token": token, "sala": sala}


def _chamar(metodo: str, corpo: dict) -> dict:
    base = settings.livekit_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
    token = _jwt("playgo-servidor", {"roomRecord": True}, 60)
    try:
        with _http() as c:
            r = c.post(f"{base}/twirp/livekit.Egress/{metodo}", json=corpo, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise ErroNegocio("Não consegui falar com o LiveKit agora. Tente de novo em instantes.") from e
    if r.status_code >= 400:
        try:
            msg = str(r.json().get("msg") or r.text)[:200]
        except ValueError:
            msg = r.text[:200]
        raise ErroNegocio(f"LiveKit: {msg}")
    return r.json()


def iniciar_saida(jogo_id: int, rtmp_url: str) -> str:
    """Liga a saída da sala para o RTMP do YouTube (720p). Devolve o id da saída."""
    corpo = {"room_name": sala_do_jogo(jogo_id), "layout": "speaker", "preset": "H264_720P_30", "stream_outputs": [{"protocol": "RTMP", "urls": [rtmp_url]}]}
    try:
        info = _chamar("StartRoomCompositeEgress", corpo)
    except ErroNegocio as e:
        if "stream_outputs" not in str(e):
            raise
        corpo.pop("stream_outputs")  # servidores mais antigos só conhecem o campo "stream"
        corpo["stream"] = {"protocol": "RTMP", "urls": [rtmp_url]}
        info = _chamar("StartRoomCompositeEgress", corpo)
    egress_id = info.get("egress_id") or info.get("egressId")
    if not egress_id:
        raise ErroNegocio("O LiveKit não confirmou o início do envio. Confira se a câmera já está ligada e tente de novo.")
    return str(egress_id)


def estado_saida(egress_id: str) -> dict | None:
    """Estado da saída no LiveKit (ACTIVE, ENDING, COMPLETE, FAILED…) e o motivo se falhou; None se não deu para consultar."""
    try:
        itens = _chamar("ListEgress", {"egress_id": egress_id}).get("items") or []
    except ErroNegocio:
        return None
    if not itens:
        return {"estado": "NAO_ENCONTRADA", "erro": None}
    i = itens[0]
    return {"estado": str(i.get("status") or "").replace("EGRESS_", ""), "erro": (i.get("error") or None)}


def parar_saida(egress_id: str) -> None:
    try:
        _chamar("StopEgress", {"egress_id": egress_id})
    except ErroNegocio as e:
        # já terminou (sala vazia, jogo encerrado): não é erro para quem está encerrando
        if "not found" not in str(e).lower() and "ended" not in str(e).lower() and "complete" not in str(e).lower():
            raise
