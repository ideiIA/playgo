"""Publicação no Instagram do PlayGo (@playgo.sports) pela API oficial da Meta (Instagram API com login do Instagram).

Fluxo: a pessoa marca "Compartilhar no Instagram do PlayGo" ao publicar no feed geral (consentimento explícito, registrado) →
a moderação aprova a publicação → ela cai na fila do Instagram → a equipe aprova → o agendador publica (foto, vídeo/reel ou
carrossel) e guarda o link. Nada vai sozinho: sempre há o consentimento do autor e a aprovação da equipe.

A conta é conectada UMA vez por um administrador (botão "Conectar Instagram"): o PlayGo troca o código por um token de longa
duração, guarda no banco e o renova sozinho. O ID e a chave do app da Meta vêm do ambiente (PLAYGO_INSTAGRAM_APP_ID / _APP_SECRET).

Limites da API: não existe exclusão de post pela API (só pelo app do Instagram) e as mídias são buscadas pelo Meta numa URL pública
(usamos a URL assinada de mídia do próprio PlayGo, que vale 1 h)."""

import logging
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, midia, notificacoes
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import P_PUBLICADA, ConfigExterna, Publicacao, Usuario

log = logging.getLogger(__name__)

CHAVE = "instagram"
ESCOPOS = "instagram_business_basic,instagram_business_content_publish"
LIMITE_LEGENDA = 2200
ESPERA_VIDEO_S = 60
RENOVAR_COM_DIAS = 15


def _http() -> httpx.Client:
    """Os testes substituem esta função por um transporte simulado."""
    return httpx.Client(timeout=40)


def _pausa(segundos: float) -> None:
    time.sleep(segundos)


# ---------------------------------------------------------------- configuração e conexão


def app_configurado() -> bool:
    return bool(settings.instagram_app_id and settings.instagram_app_secret and settings.url_publica)


def _cfg(s: SessaoORM) -> ConfigExterna | None:
    c = s.get(ConfigExterna, CHAVE)
    return c if c is not None and c.valor else None


def ativo(s: SessaoORM) -> bool:
    """O compartilhamento aparece para os autores só com a conta conectada."""
    return _cfg(s) is not None


def estado(s: SessaoORM) -> dict:
    c = _cfg(s)
    meta = (c.meta or {}) if c else {}
    return {
        "app_configurado": app_configurado(), "conectado": c is not None, "usuario": meta.get("username"), "user_id": meta.get("user_id"),
        "expira_em": c.expira_em.isoformat(timespec="minutes") if c and c.expira_em else None,
        "redirect_uri": f"{settings.url_publica.rstrip('/')}/instagram/retorno" if settings.url_publica else None,
    }


def redirect_uri() -> str:
    if not settings.url_publica:
        raise ErroNegocio("Defina PLAYGO_URL_PUBLICA (o endereço público do site) para conectar o Instagram.")
    return f"{settings.url_publica.rstrip('/')}/instagram/retorno"


def url_autorizacao(state: str) -> str:
    if not app_configurado():
        raise ErroNegocio("Falta configurar PLAYGO_INSTAGRAM_APP_ID, PLAYGO_INSTAGRAM_APP_SECRET e PLAYGO_URL_PUBLICA.")
    return "https://www.instagram.com/oauth/authorize?" + urlencode({
        "client_id": settings.instagram_app_id, "redirect_uri": redirect_uri(), "response_type": "code", "scope": ESCOPOS, "state": state,
    })


def _erro_api(r: httpx.Response) -> str:
    try:
        e = r.json().get("error") or {}
        if isinstance(e, dict):
            return str(e.get("message") or e.get("error_message") or r.text)[:240]
        return str(e)[:240]
    except ValueError:
        return r.text[:240]


def _graph(metodo: str, caminho: str, token: str | None = None, **params) -> dict:
    url = f"https://graph.instagram.com/{caminho.lstrip('/')}" if caminho.startswith(("access_token", "refresh_access_token")) else f"https://graph.instagram.com/{settings.instagram_versao}/{caminho.lstrip('/')}"
    if token:
        params["access_token"] = token
    try:
        with _http() as c:
            r = c.request(metodo, url, params=params if metodo == "GET" else None, data=params if metodo != "GET" else None)
    except httpx.HTTPError as e:
        log.error("Instagram indisponível: %s", e)
        raise ErroNegocio("Não foi possível falar com o Instagram agora. Tente de novo em instantes.") from None
    if r.status_code >= 400:
        raise ErroNegocio("Instagram: " + _erro_api(r))
    return r.json() if r.content else {}


def conectar(s: SessaoORM, admin: Usuario, codigo: str) -> dict:
    """Troca o código da autorização por um token de longa duração e guarda no banco."""
    if not admin.admin:
        raise SemPermissao("Só administradores conectam o Instagram.")
    codigo = (codigo or "").split("#", 1)[0]
    if not codigo:
        raise ErroNegocio("O Instagram não devolveu a autorização. Tente conectar de novo.")
    try:
        with _http() as c:
            r = c.post("https://api.instagram.com/oauth/access_token", data={
                "client_id": settings.instagram_app_id, "client_secret": settings.instagram_app_secret, "grant_type": "authorization_code",
                "redirect_uri": redirect_uri(), "code": codigo,
            })
    except httpx.HTTPError:
        raise ErroNegocio("Não foi possível falar com o Instagram agora. Tente de novo.") from None
    if r.status_code >= 400:
        raise ErroNegocio("Instagram: " + _erro_api(r))
    corpo = r.json()
    dado = (corpo.get("data") or [corpo])[0]
    curto, user_id = dado.get("access_token"), str(dado.get("user_id") or "")
    if not curto:
        raise ErroNegocio("O Instagram não devolveu o acesso. Tente de novo.")
    try:
        longo = _graph("GET", "access_token", grant_type="ig_exchange_token", client_secret=settings.instagram_app_secret, access_token=curto)
    except ErroNegocio as e:
        raise ErroNegocio(f"{e} (etapa: trocar o código por token de longa duração)") from None
    token = longo["access_token"]
    try:
        perfil = _graph("GET", "me", token, fields="user_id,username")
    except ErroNegocio as e:
        raise ErroNegocio(f"{e} (etapa: ler o perfil da conta)") from None
    meta = {"user_id": str(perfil.get("user_id") or user_id), "username": perfil.get("username")}
    c = s.get(ConfigExterna, CHAVE) or ConfigExterna(chave=CHAVE)
    c.valor, c.meta, c.expira_em, c.atualizado_em = token, meta, agora() + timedelta(seconds=int(longo.get("expires_in") or 60 * 86400)), agora()
    s.add(c)
    auditoria.registrar(s, admin.id, "instagram_conectar", "instagram", None, conta=meta.get("username"))
    s.commit()
    return estado(s)


def desconectar(s: SessaoORM, admin: Usuario) -> dict:
    if not admin.admin:
        raise SemPermissao("Só administradores desconectam o Instagram.")
    c = s.get(ConfigExterna, CHAVE)
    if c is not None:
        s.delete(c)
        auditoria.registrar(s, admin.id, "instagram_desconectar", "instagram", None)
        s.commit()
    return estado(s)


def renovar_token(s: SessaoORM) -> bool:
    """Renova o token de longa duração quando faltam poucos dias. Devolve True se renovou."""
    c = _cfg(s)
    if c is None or (c.expira_em and c.expira_em - agora() > timedelta(days=RENOVAR_COM_DIAS)):
        return False
    try:
        r = _graph("GET", "refresh_access_token", grant_type="ig_refresh_token", access_token=c.valor)
    except ErroNegocio as e:
        log.warning("Instagram: não renovou o token: %s", e)
        return False
    c.valor, c.expira_em, c.atualizado_em = r["access_token"], agora() + timedelta(seconds=int(r.get("expires_in") or 60 * 86400)), agora()
    s.commit()
    return True


# ---------------------------------------------------------------- legenda e fila


def legenda(p: Publicacao) -> str:
    """Texto do autor (sem @, para não marcar contas erradas do Instagram) + local + créditos + #playgo."""
    texto = (p.texto or "").replace("@", "＠").strip()
    rodape = f"📍 {p.local_nome}\n📲 por {p.autor.usuario or 'um atleta'} no PlayGo\n#playgo"
    sobra = LIMITE_LEGENDA - len(rodape) - 2
    if len(texto) > sobra:
        texto = texto[: sobra - 1].rstrip() + "…"
    return (texto + "\n\n" + rodape).strip()


def _item(p: Publicacao, viewer_id: int) -> dict:
    return {
        "id": p.id, "texto": p.texto, "legenda": legenda(p), "local": p.local_nome, "autor": p.autor.arroba, "criado_em": p.criado_em.isoformat(timespec="minutes"),
        "consentimento_em": p.instagram_consentimento_em.isoformat(timespec="minutes") if p.instagram_consentimento_em else None,
        "status": p.instagram_status, "erro": p.instagram_erro, "link": p.instagram_permalink,
        "midias": [{"id": m.id, "tipo": m.tipo, "url": midia.url(m.id, viewer_id), "miniatura": midia.url(m.id, viewer_id, True) if m.miniatura else None} for m in p.midias],
    }


def fila(s: SessaoORM, u: Usuario) -> dict:
    if not u.equipe_moderacao:
        raise SemPermissao("Só a equipe de moderação vê a fila do Instagram.")
    aguardando = list(s.scalars(select(Publicacao).where(Publicacao.instagram_status == "aguardando", Publicacao.status == P_PUBLICADA).order_by(Publicacao.id)))
    recentes = list(s.scalars(select(Publicacao).where(Publicacao.instagram_status.in_(("aprovada", "publicada", "erro", "recusada"))).order_by(Publicacao.instagram_em.desc().nullslast(), Publicacao.id.desc()).limit(15)))
    return {"estado": estado(s), "itens": [_item(p, u.id) for p in aguardando], "recentes": [_item(p, u.id) for p in recentes]}


def _publicacao(s: SessaoORM, pid: int) -> Publicacao:
    p = s.get(Publicacao, pid)
    if p is None or not p.instagram_status:
        raise NaoEncontrado("Publicação não encontrada na fila do Instagram.")
    return p


def aprovar(s: SessaoORM, u: Usuario, publicacao_id: int) -> dict:
    if not u.equipe_moderacao:
        raise SemPermissao("Só a equipe de moderação aprova o envio ao Instagram.")
    p = _publicacao(s, publicacao_id)
    if p.instagram_status not in ("aguardando", "erro"):
        raise ErroNegocio("Esta publicação não está aguardando aprovação.")
    if p.status != P_PUBLICADA:
        raise ErroNegocio("A publicação precisa estar visível (aprovada pela moderação) para ir ao Instagram.")
    p.instagram_status, p.instagram_erro = "aprovada", None
    auditoria.registrar(s, u.id, "instagram_aprovar", "publicacao", p.id)
    s.commit()
    return _item(p, u.id)


def recusar(s: SessaoORM, u: Usuario, publicacao_id: int, motivo: str | None = None) -> dict:
    if not u.equipe_moderacao:
        raise SemPermissao("Só a equipe de moderação recusa o envio ao Instagram.")
    p = _publicacao(s, publicacao_id)
    if p.instagram_status not in ("aguardando", "aprovada", "erro"):
        raise ErroNegocio("Esta publicação já foi tratada.")
    p.instagram_status, p.instagram_erro, p.instagram_em = "recusada", (motivo or "")[:200] or None, agora()
    auditoria.registrar(s, u.id, "instagram_recusar", "publicacao", p.id)
    notificacoes.avisar(s, p.autor_id, "publicacao", "Sua publicação não foi escolhida para o Instagram", "Ela continua no PlayGo normalmente.", "/mural", None, f"ig:{p.id}")
    s.commit()
    return _item(p, u.id)


# ---------------------------------------------------------------- publicação


def _url_publica(m, p: Publicacao) -> str:
    return settings.url_publica.rstrip("/") + midia.url(m.id, p.autor_id)


def _esperar(token: str, container: str) -> None:
    limite = time.monotonic() + ESPERA_VIDEO_S
    while True:
        r = _graph("GET", container, token, fields="status_code")
        codigo = r.get("status_code")
        if codigo == "FINISHED":
            return
        if codigo in ("ERROR", "EXPIRED"):
            raise ErroNegocio("O Instagram não aceitou a mídia (formato, tamanho ou endereço da imagem).")
        if time.monotonic() > limite:
            raise ErroNegocio("O Instagram ainda está processando a mídia; nova tentativa no próximo ciclo.")
        _pausa(4)


def publicar(s: SessaoORM, p: Publicacao) -> str:
    """Publica uma publicação aprovada. Devolve o id da mídia no Instagram."""
    c = _cfg(s)
    if c is None:
        raise ErroNegocio("O Instagram não está conectado.")
    token, ig = c.valor, (c.meta or {}).get("user_id")
    if not ig:
        raise ErroNegocio("A conta do Instagram conectada não tem ID. Conecte de novo.")
    if not p.midias:
        raise ErroNegocio("A publicação não tem foto nem vídeo.")
    cap = legenda(p)
    itens = []
    for m in p.midias:
        itens.append((m.tipo, _url_publica(m, p)))
    if len(itens) == 1:
        tipo, url = itens[0]
        dados = {"image_url": url} if tipo == "foto" else {"media_type": "REELS", "video_url": url}
        container = _graph("POST", f"{ig}/media", token, caption=cap, **dados)["id"]
        _esperar(token, container)
    else:
        filhos = []
        for tipo, url in itens:
            dados = {"image_url": url} if tipo == "foto" else {"media_type": "VIDEO", "video_url": url}
            filho = _graph("POST", f"{ig}/media", token, is_carousel_item="true", **dados)["id"]
            _esperar(token, filho)
            filhos.append(filho)
        container = _graph("POST", f"{ig}/media", token, media_type="CAROUSEL", children=",".join(filhos), caption=cap)["id"]
        _esperar(token, container)
    return _graph("POST", f"{ig}/media_publish", token, creation_id=container)["id"]


def publicar_aprovadas(s: SessaoORM, limite: int = 3) -> int:
    """Rodada do agendador: renova o token e publica as aprovadas. Erros ficam na própria publicação (a equipe vê e pode tentar de novo)."""
    if not ativo(s):
        return 0
    renovar_token(s)
    feitas = 0
    for p in list(s.scalars(select(Publicacao).where(Publicacao.instagram_status == "aprovada").order_by(Publicacao.id).limit(limite))):
        if p.status != P_PUBLICADA:
            p.instagram_status, p.instagram_erro, p.instagram_em = "recusada", "A publicação deixou de estar visível.", agora()
            s.commit()
            continue
        try:
            media_id = publicar(s, p)
            link = None
            try:
                link = _graph("GET", media_id, _cfg(s).valor, fields="permalink").get("permalink")
            except ErroNegocio:
                pass
            p.instagram_status, p.instagram_media_id, p.instagram_permalink, p.instagram_erro, p.instagram_em = "publicada", media_id, link, None, agora()
            auditoria.registrar(s, None, "instagram_publicada", "publicacao", p.id, media=media_id)
            notificacoes.avisar(s, p.autor_id, "publicacao", "📸 Sua publicação foi para o Instagram do PlayGo", "Obrigado por compartilhar!", link or "/mural", None, f"ig:{p.id}")
            feitas += 1
        except ErroNegocio as e:
            p.instagram_status, p.instagram_erro, p.instagram_em = "erro", str(e)[:200], agora()
            log.warning("Instagram: falha ao publicar %s: %s", p.id, e)
        s.commit()
    return feitas


def novo_estado() -> str:
    return secrets.token_urlsafe(24)
