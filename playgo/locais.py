"""Guia de locais (Explorar): locais públicos de prática, arenas ainda não assinantes, clubes/academias e lojas de material esportivo.
Só informação (horário, contato, redes, endereço e regras): não reserva nem cobra. Quem mantém é a administração."""

import re
from urllib.parse import urlparse

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .geo import caixa_sql, distancia_sql, formatar_km
from .models import Local, Modalidade, Usuario

# categoria -> (ícone no mapa e nos cartões, nome, cor do pino)
TIPOS = {
    "publico": ("🌳", "Local público", "#22a55a"),
    "arena": ("🏟️", "Arena parceira", "#6c4cff"),
    "clube": ("🏋️", "Clube, academia ou escola", "#e5484d"),
    "loja": ("🛍️", "Loja de material esportivo", "#ff8a00"),
}

CAMPOS = (
    "tipo", "nome", "descricao", "endereco", "cidade", "latitude", "longitude", "horario_funcionamento", "telefone", "email", "site",
    "instagram", "facebook", "maps_url", "categoria_maps", "avaliacao", "avaliacoes", "regras", "modalidades",
)


def _exigir_admin(u: Usuario) -> None:
    if not u.admin:
        raise SemPermissao("Só a administração mantém o guia de locais.")


def _limpar(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


# ---------------------------------------------------------------- normalização de links


def _url(texto: str | None) -> str | None:
    t = (texto or "").strip()
    if not t:
        return None
    if not re.match(r"^https?://", t, re.I):
        t = "https://" + t.lstrip("/")
    return t


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.").removeprefix("m.")


def normalizar_instagram(texto: str | None) -> str | None:
    """Aceita @usuario, usuario ou o link (com utm etc.) e devolve https://instagram.com/usuario."""
    t = (texto or "").strip()
    if not t:
        return None
    if re.fullmatch(r"@?[A-Za-z0-9._]{2,30}", t):
        return "https://instagram.com/" + t.lstrip("@")
    u = _url(t)
    if u is None or "instagram.com" not in _host(u):
        return u
    caminho = [p for p in urlparse(u).path.split("/") if p]
    return f"https://instagram.com/{caminho[0]}" if caminho and caminho[0] not in ("p", "reel", "explore") else u.split("?")[0]


def normalizar_facebook(texto: str | None) -> str | None:
    u = _url(texto)
    if u is None:
        return None
    return u.split("?")[0] if "facebook.com" in _host(u) or "fb.com" in _host(u) else u


def separar_redes(site: str | None, instagram: str | None, facebook: str | None) -> tuple[str | None, str | None, str | None]:
    """O 'site' das planilhas costuma ser, na verdade, o Instagram ou o Facebook: manda cada link para o campo certo."""
    ig, fb, web = normalizar_instagram(instagram), normalizar_facebook(facebook), _url(site)
    if web:
        h = _host(web)
        if "instagram.com" in h:
            ig, web = ig or normalizar_instagram(web), None
        elif "facebook.com" in h or h == "fb.com":
            fb, web = fb or normalizar_facebook(web), None
    return web, ig, fb


_COORD_DATA = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_COORD_ARROBA = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")


def coordenadas_do_maps(url: str | None) -> tuple[float, float] | None:
    """Latitude/longitude escondidas num link do Google Maps (…!3d-20.55!4d-54.59… ou …/@-20.55,-54.59,17z)."""
    if not url:
        return None
    m = _COORD_DATA.search(url) or _COORD_ARROBA.search(url)
    return (float(m.group(1)), float(m.group(2))) if m else None


# ---------------------------------------------------------------- cadastro


def _validar(s: SessaoORM, d: dict, exigir_contato: bool = True) -> dict:
    d = dict(d)
    d["tipo"] = (d.get("tipo") or "").strip()
    if d["tipo"] not in TIPOS:
        raise ErroNegocio("Escolha o tipo do local.")
    obrigatorios = [("nome", "o nome"), ("endereco", "o endereço")]
    if exigir_contato:
        obrigatorios += [("horario_funcionamento", "o horário de funcionamento"), ("telefone", "o telefone")]
    for campo, rotulo in obrigatorios:
        d[campo] = (d.get(campo) or "").strip()
        if not d[campo]:
            raise ErroNegocio(f"Informe {rotulo}.")
    for campo in ("horario_funcionamento", "telefone"):  # sem contato = vazio ("não informado"), nunca None
        d[campo] = (d.get(campo) or "").strip()
    for campo in ("descricao", "cidade", "email", "regras", "categoria_maps"):
        d[campo] = _limpar(d.get(campo))
    d["site"], d["instagram"], d["facebook"] = separar_redes(d.get("site"), d.get("instagram"), d.get("facebook"))
    d["maps_url"] = _url(d.get("maps_url"))
    if d["email"] and ("@" not in d["email"] or " " in d["email"]):
        raise ErroNegocio("E-mail inválido.")
    if d.get("latitude") is None or d.get("longitude") is None:
        coords = coordenadas_do_maps(d["maps_url"])
        if coords:
            d["latitude"], d["longitude"] = coords
    if d.get("latitude") is None or d.get("longitude") is None:
        raise ErroNegocio("Marque o local no mapa.")
    if d.get("avaliacao") is not None and not 0 <= float(d["avaliacao"]) <= 5:
        d["avaliacao"], d["avaliacoes"] = None, None
    d.setdefault("avaliacao", None)
    d.setdefault("avaliacoes", None)
    codigos = d.get("modalidades") or []
    d["modalidades"] = sorted(set(s.scalars(select(Modalidade.codigo).where(Modalidade.codigo.in_(codigos or ["-"])))))
    return d


def montar(s: SessaoORM, criado_por_id: int | None = None, exigir_contato: bool = True, **dados) -> Local:
    """Valida os dados e devolve o Local ainda não gravado (usado no cadastro e na importação de planilhas)."""
    d = _validar(s, dados, exigir_contato)
    return Local(**{k: d.get(k) for k in CAMPOS}, criado_por_id=criado_por_id)


def criar(s: SessaoORM, u: Usuario, **dados) -> Local:
    _exigir_admin(u)
    loc = montar(s, u.id, **dados)
    s.add(loc)
    s.flush()
    auditoria.registrar(s, u.id, "local_criar", "local", loc.id, tipo=loc.tipo)
    s.commit()
    return loc


def obter(s: SessaoORM, local_id: int, incluir_inativos: bool = False) -> Local:
    loc = s.get(Local, local_id)
    if loc is None or (not loc.ativo and not incluir_inativos):
        raise NaoEncontrado("Local não encontrado.")
    return loc


def editar(s: SessaoORM, u: Usuario, local_id: int, **dados) -> Local:
    _exigir_admin(u)
    loc = obter(s, local_id, incluir_inativos=True)
    d = _validar(s, dados, exigir_contato=False)  # ao editar dá para ir completando um local importado
    for k in CAMPOS:
        if k in dados or k in ("site", "instagram", "facebook", "maps_url", "modalidades"):
            setattr(loc, k, d.get(k))
    loc.atualizado_em = agora()
    auditoria.registrar(s, u.id, "local_editar", "local", loc.id)
    s.commit()
    return loc


def definir_ativo(s: SessaoORM, u: Usuario, local_id: int, ativo: bool) -> Local:
    _exigir_admin(u)
    loc = obter(s, local_id, incluir_inativos=True)
    loc.ativo, loc.atualizado_em = ativo, agora()
    auditoria.registrar(s, u.id, "local_ativar" if ativo else "local_ocultar", "local", loc.id)
    s.commit()
    return loc


def serializar(loc: Local, dist: float | None = None) -> dict:
    icone, rotulo, cor = TIPOS.get(loc.tipo, TIPOS["publico"])
    return {
        "tipo": "local", "id": loc.id, "categoria": loc.tipo, "categoria_nome": rotulo, "icone": icone, "cor": cor,
        "nome": loc.nome, "descricao": loc.descricao, "endereco": loc.endereco, "cidade": loc.cidade,
        "latitude": loc.latitude, "longitude": loc.longitude, "horario_funcionamento": loc.horario_funcionamento or "",
        "telefone": loc.telefone or "", "email": loc.email, "site": loc.site, "instagram": loc.instagram, "facebook": loc.facebook,
        "maps_url": loc.maps_url or f"https://www.google.com/maps/search/?api=1&query={loc.latitude},{loc.longitude}",
        "categoria_maps": loc.categoria_maps, "avaliacao": loc.avaliacao, "avaliacoes": loc.avaliacoes,
        "regras": loc.regras, "modalidades": list(loc.modalidades or []), "ativo": loc.ativo,
        "incompleto": not (loc.horario_funcionamento and loc.telefone),
        "distancia_km": round(dist, 2) if dist is not None else None, "distancia": formatar_km(dist),
    }


def listar(s: SessaoORM, lat: float, lng: float, raio_km: float | None = None, q: str = "", categoria: str | None = None,
           modalidades: list[str] | None = None, limite: int = 60) -> list[dict]:
    dist = distancia_sql(Local.latitude, Local.longitude, lat, lng)
    cons = select(Local, dist).where(Local.ativo)
    if raio_km:
        cons = cons.where(caixa_sql(Local.latitude, Local.longitude, lat, lng, raio_km), dist <= raio_km)
    if categoria in TIPOS:
        cons = cons.where(Local.tipo == categoria)
    if modalidades:  # local sem esporte definido (ex.: loja) continua aparecendo
        cons = cons.where(or_(Local.modalidades.overlap(modalidades), Local.modalidades == []))
    if q.strip():
        t = f"%{q.strip()}%"
        cons = cons.where(or_(Local.nome.ilike(t), Local.cidade.ilike(t), Local.endereco.ilike(t), Local.categoria_maps.ilike(t)))
    return [serializar(loc, km) for loc, km in s.execute(cons.order_by(dist).limit(limite))]


def todos(s: SessaoORM, u: Usuario) -> list[Local]:
    _exigir_admin(u)
    return list(s.scalars(select(Local).order_by(Local.ativo.desc(), Local.nome)))
