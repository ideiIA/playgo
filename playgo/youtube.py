"""Transmissão ao vivo dos jogos no YouTube OFICIAL do PlayGo (YouTube Live Streaming API).

Como funciona
- Um administrador conecta o canal UMA vez (botão "Conectar YouTube", login do Google do dono do canal). O token de renovação fica em
  `ConfigExterna`; o ID e a chave do cliente OAuth vêm do ambiente (PLAYGO_YOUTUBE_CLIENT_ID / _CLIENT_SECRET).
- Quem conduz o campeonato (organização e mesários, `chaves.pode_pontuar`) prepara a transmissão de UM jogo: o PlayGo cria no YouTube a
  transmissão e a chave de envio desse jogo e mostra o endereço RTMP e a chave só a essa pessoa. O vídeo vai do celular/computador
  direto para o YouTube (OBS, Larix, Streamlabs…): **o servidor do PlayGo não recebe nem processa vídeo**; quem assiste puxa do YouTube.
- Só campeonatos que a organização liberou (`Campeonato.transmissao_permitida`, com a declaração de ciência dos participantes) transmitem.
- O jogo vai ao ar quando o envio chega ao YouTube e a pessoa clica "Entrar no ar"; ao encerrar o jogo (ou pelo botão) a transmissão
  é finalizada e a gravação fica no canal (aparece no book). O agendador finaliza o que ficou esquecido.
A chave de envio é um segredo: nunca é gravada no banco (é lida do YouTube a cada pedido da pessoa autorizada), nunca vai para log
ou auditoria, e "Nova chave" troca a do jogo se vazar."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, campeonatos, chaves
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import J_AGENDADO, J_AO_VIVO, J_ENCERRADO, Campeonato, ConfigExterna, Jogo, Usuario

log = logging.getLogger(__name__)

CHAVE = "youtube"
ESCOPO = "https://www.googleapis.com/auth/youtube"
API = "https://www.googleapis.com/youtube/v3"
PRIVACIDADES = {"unlisted": "Não listada (só quem tem o link do PlayGo assiste)", "public": "Pública (aparece no canal e na busca do YouTube)"}
ATIVAS = ("preparada", "ao_vivo")
ESQUECIDA_APOS_H = 10  # transmissão sem jogo ativo há mais que isso é finalizada pelo agendador

DECLARACAO = (
    "Declaro que os participantes (e os responsáveis pelos menores de idade) foram informados de que os jogos deste campeonato podem ser "
    "filmados, transmitidos ao vivo e gravados no canal do YouTube do PlayGo, e que tenho autorização para isso."
)


def _http() -> httpx.Client:
    """Os testes substituem esta função por um transporte simulado."""
    return httpx.Client(timeout=40)


# ---------------------------------------------------------------- configuração e conexão


def app_configurado() -> bool:
    return bool(settings.youtube_client_id and settings.youtube_client_secret and settings.url_publica)


def redirect_uri() -> str:
    if not settings.url_publica:
        raise ErroNegocio("Defina PLAYGO_URL_PUBLICA (o endereço público do site) para conectar o YouTube.")
    return f"{settings.url_publica.rstrip('/')}/youtube/retorno"


def _cfg(s: SessaoORM) -> ConfigExterna | None:
    c = s.get(ConfigExterna, CHAVE)
    return c if c is not None and c.valor else None


def conectado(s: SessaoORM) -> bool:
    return _cfg(s) is not None


def privacidade_padrao(s: SessaoORM) -> str:
    c = _cfg(s)
    return ((c.meta or {}).get("privacidade") if c else None) or "unlisted"


def estado(s: SessaoORM) -> dict:
    c = _cfg(s)
    meta = (c.meta or {}) if c else {}
    return {
        "app_configurado": app_configurado(), "conectado": c is not None, "canal": meta.get("canal_titulo"), "canal_id": meta.get("canal_id"),
        "privacidade": meta.get("privacidade") or "unlisted", "privacidades": PRIVACIDADES,
        "redirect_uri": f"{settings.url_publica.rstrip('/')}/youtube/retorno" if settings.url_publica else None,
        "max_simultaneas": settings.youtube_max_simultaneas,
    }


def url_autorizacao(state: str) -> str:
    if not app_configurado():
        raise ErroNegocio("Falta configurar PLAYGO_YOUTUBE_CLIENT_ID, PLAYGO_YOUTUBE_CLIENT_SECRET e PLAYGO_URL_PUBLICA.")
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.youtube_client_id, "redirect_uri": redirect_uri(), "response_type": "code", "scope": ESCOPO,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "false", "state": state,
    })


def _erro_google(r: httpx.Response) -> tuple[str, str]:
    """(motivo, mensagem) de uma resposta de erro do Google/YouTube."""
    try:
        e = r.json().get("error")
        if isinstance(e, dict):
            motivo = (e.get("errors") or [{}])[0].get("reason") or e.get("status") or ""
            return str(motivo), str(e.get("message") or r.text)[:240]
        return str(e or ""), str(r.json().get("error_description") or r.text)[:240]
    except ValueError:
        return "", r.text[:240]


_MENSAGENS = {
    "liveStreamingNotEnabled": "O canal ainda não está liberado para transmissões ao vivo. No YouTube Studio verifique o telefone e ative o recurso (pode levar até 24 horas).",
    "quotaExceeded": "O limite diário de uso da API do YouTube acabou. Tente de novo amanhã ou peça aumento de cota no Google Cloud.",
    "rateLimitExceeded": "O YouTube pediu uma pausa. Tente de novo em instantes.",
    "invalid_grant": "A autorização do YouTube expirou ou foi revogada. Um administrador precisa conectar o canal de novo.",
    "insufficientPermissions": "A conta conectada não tem permissão neste canal. Conecte de novo com a conta dona do canal.",
    "forbidden": "O YouTube recusou a operação para este canal (verifique se as transmissões ao vivo estão ativas).",
}


def _falha(r: httpx.Response) -> ErroNegocio:
    motivo, msg = _erro_google(r)
    return ErroNegocio(_MENSAGENS.get(motivo) or f"YouTube: {msg}")


def conectar(s: SessaoORM, admin: Usuario, codigo: str) -> dict:
    """Troca o código da autorização pelo token de renovação do canal e guarda no banco."""
    if not admin.admin:
        raise SemPermissao("Só administradores conectam o YouTube.")
    if not codigo:
        raise ErroNegocio("O Google não devolveu a autorização. Tente conectar de novo.")
    try:
        with _http() as c:
            r = c.post("https://oauth2.googleapis.com/token", data={
                "code": codigo, "client_id": settings.youtube_client_id, "client_secret": settings.youtube_client_secret,
                "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
            })
    except httpx.HTTPError:
        raise ErroNegocio("Não foi possível falar com o Google agora. Tente de novo.") from None
    if r.status_code >= 400:
        raise _falha(r)
    tokens = r.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise ErroNegocio("O Google não enviou a autorização permanente. Remova o acesso do PlayGo em myaccount.google.com/permissions e conecte de novo.")
    meta = {"access_token": tokens.get("access_token"), "access_expira": (agora() + timedelta(seconds=int(tokens.get("expires_in") or 3000))).isoformat(), "privacidade": "unlisted"}
    cfg = s.get(ConfigExterna, CHAVE) or ConfigExterna(chave=CHAVE)
    if cfg.meta and cfg.meta.get("privacidade"):
        meta["privacidade"] = cfg.meta["privacidade"]
    cfg.valor, cfg.meta, cfg.atualizado_em = refresh, meta, agora()
    s.add(cfg)
    s.flush()
    canal = _api(s, "GET", "channels", params={"part": "snippet", "mine": "true"})
    itens = canal.get("items") or []
    if not itens:
        raise ErroNegocio("Essa conta Google não tem um canal do YouTube. Entre com a conta dona do canal PlayGo Sports.")
    meta = dict(cfg.meta)
    meta["canal_id"], meta["canal_titulo"] = itens[0]["id"], itens[0]["snippet"]["title"]
    cfg.meta = meta
    auditoria.registrar(s, admin.id, "youtube_conectar", "youtube", None, canal=meta["canal_titulo"])
    s.commit()
    return estado(s)


def desconectar(s: SessaoORM, admin: Usuario) -> dict:
    if not admin.admin:
        raise SemPermissao("Só administradores desconectam o YouTube.")
    c = s.get(ConfigExterna, CHAVE)
    if c is not None:
        s.delete(c)
        auditoria.registrar(s, admin.id, "youtube_desconectar", "youtube", None)
        s.commit()
    return estado(s)


def definir_privacidade(s: SessaoORM, admin: Usuario, privacidade: str) -> dict:
    if not admin.admin:
        raise SemPermissao("Só administradores mudam a privacidade das transmissões.")
    if privacidade not in PRIVACIDADES:
        raise ErroNegocio("Escolha: não listada ou pública.")
    c = _cfg(s)
    if c is None:
        raise ErroNegocio("Conecte o YouTube primeiro.")
    c.meta = {**(c.meta or {}), "privacidade": privacidade}
    auditoria.registrar(s, admin.id, "youtube_privacidade", "youtube", None, privacidade=privacidade)
    s.commit()
    return estado(s)


# ---------------------------------------------------------------- chamadas à API


def _token_acesso(s: SessaoORM) -> str:
    c = _cfg(s)
    if c is None:
        raise ErroNegocio("O YouTube do PlayGo ainda não foi conectado pela administração.")
    meta = c.meta or {}
    expira = meta.get("access_expira")
    if meta.get("access_token") and expira and datetime.fromisoformat(expira) - agora() > timedelta(minutes=2):
        return meta["access_token"]
    try:
        with _http() as h:
            r = h.post("https://oauth2.googleapis.com/token", data={
                "client_id": settings.youtube_client_id, "client_secret": settings.youtube_client_secret, "refresh_token": c.valor, "grant_type": "refresh_token",
            })
    except httpx.HTTPError:
        raise ErroNegocio("Não foi possível falar com o Google agora. Tente de novo em instantes.") from None
    if r.status_code >= 400:
        raise _falha(r)
    novo = r.json()
    c.meta = {**meta, "access_token": novo["access_token"], "access_expira": (agora() + timedelta(seconds=int(novo.get("expires_in") or 3000))).isoformat()}
    s.flush()
    return novo["access_token"]


def _api(s: SessaoORM, metodo: str, caminho: str, params: dict | None = None, corpo: dict | None = None) -> dict:
    token = _token_acesso(s)
    try:
        with _http() as c:
            r = c.request(metodo, f"{API}/{caminho}", params=params, json=corpo, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        log.error("YouTube indisponível: %s", type(e).__name__)
        raise ErroNegocio("Não foi possível falar com o YouTube agora. Tente de novo em instantes.") from None
    if r.status_code >= 400:
        raise _falha(r)
    return r.json() if r.content else {}


# ---------------------------------------------------------------- regras de quem pode transmitir


def liberar_campeonato(s: SessaoORM, campeonato_id: int, por: Usuario, permitir: bool, declaro: bool) -> Campeonato:
    """A organização libera (ou bloqueia) a transmissão do campeonato, declarando que os participantes sabem que serão filmados."""
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    if permitir and not declaro:
        raise ErroNegocio("Para liberar a transmissão, marque a declaração de ciência dos participantes.")
    c.transmissao_permitida = permitir
    c.transmissao_aceite_por_id, c.transmissao_aceite_em = (por.id, agora()) if permitir else (None, None)
    auditoria.registrar(s, por.id, "campeonato_transmissao_liberar" if permitir else "campeonato_transmissao_bloquear", "campeonato", c.id)
    s.commit()
    return c


def _contexto(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> tuple[Campeonato, Jogo]:
    c = campeonatos.obter(s, campeonato_id)
    chaves._exigir_pontuar(s, c, por)
    if not c.transmissao_permitida:
        raise ErroNegocio("A organização ainda não liberou a transmissão deste campeonato (aba 📺 Telão → 3 · Transmissão).")
    j = s.scalar(select(Jogo).where(Jogo.id == jogo_id, Jogo.campeonato_id == c.id).with_for_update(of=Jogo).execution_options(populate_existing=True))
    if j is None or j.folga or not (j.equipe_a_id and j.equipe_b_id):
        raise NaoEncontrado("Jogo não encontrado.")
    return c, j


def _iso_utc(naive_local: datetime) -> str:
    """Horário do servidor (naïve, no fuso configurado) → ISO 8601 em UTC, como o YouTube exige."""
    return naive_local.replace(tzinfo=ZoneInfo(settings.fuso)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _titulo(c: Campeonato, j: Jogo) -> str:
    return f"{c.nome} · {chaves._nome_lado(j, 'a')} × {chaves._nome_lado(j, 'b')} · {j.rodada_nome}"[:100]


def _ativas(s: SessaoORM) -> int:
    return s.scalar(select(func.count()).select_from(Jogo).where(Jogo.yt_estado.in_(ATIVAS))) or 0


# ---------------------------------------------------------------- transmissão de um jogo


def _criar_chave(s: SessaoORM, j: Jogo, titulo: str) -> dict:
    ls = _api(s, "POST", "liveStreams", {"part": "snippet,cdn,contentDetails"}, {
        "snippet": {"title": titulo[:100]},
        "cdn": {"frameRate": "variable", "ingestionType": "rtmp", "resolution": "variable"},
        "contentDetails": {"isReusable": False},
    })
    _api(s, "POST", "liveBroadcasts/bind", {"id": j.yt_broadcast_id, "part": "id,contentDetails", "streamId": ls["id"]})
    j.yt_stream_id = ls["id"]
    return ls


def _entrega(ls: dict) -> dict:
    info = (ls.get("cdn") or {}).get("ingestionInfo") or {}
    return {"endereco": info.get("ingestionAddress"), "endereco_seguro": info.get("rtmpsIngestionAddress"), "chave": info.get("streamName")}


def preparar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> dict:
    """Cria no YouTube a transmissão do jogo e a chave de envio. Idempotente: se já existe, só devolve o painel."""
    c, j = _contexto(s, campeonato_id, jogo_id, por)
    if j.status == J_ENCERRADO:
        raise ErroNegocio("Este jogo já terminou.")
    if j.yt_broadcast_id and j.yt_estado in ATIVAS:
        return painel(s, campeonato_id, jogo_id, por)
    if _ativas(s) >= settings.youtube_max_simultaneas:
        raise ErroNegocio(f"Já há {settings.youtube_max_simultaneas} transmissões preparadas ou no ar. Encerre uma antes de preparar outra.")
    inicio = (j.inicio_previsto or agora()) if j.status == J_AGENDADO else agora()
    inicio = max(inicio, agora() + timedelta(minutes=1))
    b = _api(s, "POST", "liveBroadcasts", {"part": "snippet,status,contentDetails"}, {
        "snippet": {
            "title": _titulo(c, j), "scheduledStartTime": _iso_utc(inicio),
            "description": f"{c.nome} — transmissão oficial pelo PlayGo.\nAcompanhe o placar em {settings.url_publica.rstrip('/') if settings.url_publica else ''}/campeonatos/{c.id}/ao-vivo",
        },
        "status": {"privacyStatus": privacidade_padrao(s), "selfDeclaredMadeForKids": False},
        "contentDetails": {
            "enableAutoStart": False, "enableAutoStop": False, "enableDvr": True, "enableEmbed": True, "recordFromStart": True, "latencyPreference": "low",
            "monitorStream": {"enableMonitorStream": False},
        },
    })
    j.yt_broadcast_id, j.yt_estado = b["id"], "preparada"
    s.flush()
    try:
        _criar_chave(s, j, _titulo(c, j))
    except ErroNegocio:
        try:  # não deixa uma transmissão sem chave pendurada no canal
            _api(s, "DELETE", "liveBroadcasts", {"id": j.yt_broadcast_id})
        except ErroNegocio:
            pass
        j.yt_broadcast_id = j.yt_stream_id = j.yt_estado = None
        s.commit()
        raise
    auditoria.registrar(s, por.id, "transmissao_preparar", "jogo", j.id, video=j.yt_broadcast_id)
    s.commit()
    return painel(s, campeonato_id, jogo_id, por)


def painel(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> dict:
    """Estado da transmissão + endereço e chave de envio. Só chega a quem conduz o campeonato."""
    c, j = _contexto(s, campeonato_id, jogo_id, por)
    base = {"jogo_id": j.id, "estado": j.yt_estado, "video_id": j.yt_broadcast_id if j.yt_estado in ("ao_vivo", "encerrada") else None, "assistir": f"https://www.youtube.com/watch?v={j.yt_broadcast_id}" if j.yt_broadcast_id else None}
    if not j.yt_broadcast_id or j.yt_estado not in ATIVAS:
        return base | {"envio": None, "sinal": None, "ciclo": None}
    b = _api(s, "GET", "liveBroadcasts", {"part": "status", "id": j.yt_broadcast_id}).get("items") or []
    ciclo = b[0]["status"]["lifeCycleStatus"] if b else "removida"
    if ciclo == "complete" or ciclo == "revoked" or not b:
        j.yt_estado = "encerrada" if b else "erro"
        s.commit()
        return base | {"estado": j.yt_estado, "envio": None, "sinal": None, "ciclo": ciclo}
    if ciclo == "live" and j.yt_estado != "ao_vivo":
        j.yt_estado = "ao_vivo"
        s.commit()
    ls = _api(s, "GET", "liveStreams", {"part": "status,cdn", "id": j.yt_stream_id}).get("items") or []
    sinal = None
    envio = None
    if ls:
        sinal = {"estado": ls[0]["status"]["streamStatus"], "saude": ((ls[0]["status"].get("healthStatus") or {}).get("status")) or "noData"}
        envio = _entrega(ls[0])
    return base | {"estado": j.yt_estado, "envio": envio, "sinal": sinal, "ciclo": ciclo, "video_id": j.yt_broadcast_id if j.yt_estado == "ao_vivo" else None}


def entrar_no_ar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> dict:
    c, j = _contexto(s, campeonato_id, jogo_id, por)
    if j.yt_estado != "preparada":
        raise ErroNegocio("Prepare a transmissão deste jogo primeiro." if j.yt_estado != "ao_vivo" else "A transmissão já está no ar.")
    ls = _api(s, "GET", "liveStreams", {"part": "status", "id": j.yt_stream_id}).get("items") or []
    if not ls or ls[0]["status"]["streamStatus"] != "active":
        raise ErroNegocio("O YouTube ainda não está recebendo o vídeo. Inicie o envio no aplicativo (OBS, Larix…), espere ~15 segundos e tente de novo.")
    _api(s, "POST", "liveBroadcasts/transition", {"broadcastStatus": "live", "id": j.yt_broadcast_id, "part": "status"})
    j.yt_estado = "ao_vivo"
    auditoria.registrar(s, por.id, "transmissao_no_ar", "jogo", j.id, video=j.yt_broadcast_id)
    s.commit()
    return painel(s, campeonato_id, jogo_id, por)


def _finalizar(s: SessaoORM, j: Jogo) -> None:
    """Encerra a transmissão no YouTube (a gravação fica no canal) e apaga a chave de envio do jogo."""
    if j.yt_estado == "ao_vivo":
        try:
            _api(s, "POST", "liveBroadcasts/transition", {"broadcastStatus": "complete", "id": j.yt_broadcast_id, "part": "status"})
        except ErroNegocio as e:
            if "redundantTransition" not in str(e) and "Invalid transition" not in str(e):
                raise
    elif j.yt_estado == "preparada":
        _api(s, "DELETE", "liveBroadcasts", {"id": j.yt_broadcast_id})  # nunca foi ao ar: não deixa lixo no canal
    if j.yt_stream_id:
        try:
            _api(s, "DELETE", "liveStreams", {"id": j.yt_stream_id})
        except ErroNegocio:
            pass
    j.yt_estado = "encerrada" if j.yt_estado == "ao_vivo" else None
    if j.yt_estado is None:
        j.yt_broadcast_id = None
    j.yt_stream_id = None


def encerrar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> dict:
    c, j = _contexto(s, campeonato_id, jogo_id, por)
    if j.yt_estado not in ATIVAS:
        raise ErroNegocio("Este jogo não tem transmissão ativa.")
    _finalizar(s, j)
    auditoria.registrar(s, por.id, "transmissao_encerrar", "jogo", j.id)
    s.commit()
    return painel(s, campeonato_id, jogo_id, por)


def nova_chave(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> dict:
    """A chave vazou ou o aplicativo deu problema: troca a chave de envio do jogo (só antes de entrar no ar)."""
    c, j = _contexto(s, campeonato_id, jogo_id, por)
    if j.yt_estado != "preparada":
        raise ErroNegocio("Só dá para trocar a chave antes de entrar no ar." if j.yt_estado == "ao_vivo" else "Prepare a transmissão primeiro.")
    antiga = j.yt_stream_id
    _criar_chave(s, j, _titulo(c, j))
    if antiga:
        try:
            _api(s, "DELETE", "liveStreams", {"id": antiga})
        except ErroNegocio:
            pass
    auditoria.registrar(s, por.id, "transmissao_nova_chave", "jogo", j.id)
    s.commit()
    return painel(s, campeonato_id, jogo_id, por)


def ao_encerrar_jogo(s: SessaoORM, j: Jogo) -> None:
    """Chamado quando o jogo termina no PlayGo: finaliza a transmissão sem atrapalhar o encerramento se o YouTube falhar."""
    if j.yt_estado not in ATIVAS:
        return
    try:
        _finalizar(s, j)
        auditoria.registrar(s, None, "transmissao_encerrar_auto", "jogo", j.id)
        s.commit()
    except ErroNegocio as e:
        log.warning("YouTube: não finalizou a transmissão do jogo %s: %s", j.id, e)
        s.rollback()


def manutencao(s: SessaoORM) -> int:
    """Rodada do agendador: finaliza transmissões de jogos já encerrados ou esquecidas há muitas horas. Devolve quantas fechou."""
    if not conectado(s):
        return 0
    limite = agora() - timedelta(hours=ESQUECIDA_APOS_H)
    fechadas = 0
    for j in list(s.scalars(select(Jogo).where(Jogo.yt_estado.in_(ATIVAS)))):
        velha = (j.iniciado_em or j.inicio_previsto or j.encerrado_em or agora()) < limite
        if j.status == J_ENCERRADO or velha:
            try:
                _finalizar(s, j)
                s.commit()
                fechadas += 1
            except ErroNegocio as e:
                log.warning("YouTube: manutenção não finalizou o jogo %s: %s", j.id, e)
                s.rollback()
    return fechadas
