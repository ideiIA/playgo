"""Guia de locais (Explorar): locais públicos de prática, lojas de material esportivo e arenas ainda não assinantes.
Só informação (horário, contato, endereço e regras): não reserva nem cobra. Quem mantém é a administração."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .geo import caixa_sql, distancia_sql, formatar_km
from .models import Local, Modalidade, Usuario

TIPOS = {"publico": ("🌳", "Local público"), "loja": ("🛍️", "Loja de material esportivo"), "arena": ("🏟️", "Arena parceira")}


def _exigir_admin(u: Usuario) -> None:
    if not u.admin:
        raise SemPermissao("Só a administração mantém o guia de locais.")


def _limpar(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _validar(s: SessaoORM, d: dict) -> dict:
    d = dict(d)
    d["tipo"] = (d.get("tipo") or "").strip()
    if d["tipo"] not in TIPOS:
        raise ErroNegocio("Escolha o tipo do local.")
    for campo, rotulo in (("nome", "o nome"), ("endereco", "o endereço"), ("horario_funcionamento", "o horário de funcionamento"), ("telefone", "o telefone")):
        d[campo] = (d.get(campo) or "").strip()
        if not d[campo]:
            raise ErroNegocio(f"Informe {rotulo}.")
    for campo in ("descricao", "cidade", "email", "site", "regras"):
        d[campo] = _limpar(d.get(campo))
    if d["email"] and ("@" not in d["email"] or " " in d["email"]):
        raise ErroNegocio("E-mail inválido.")
    if d.get("latitude") is None or d.get("longitude") is None:
        raise ErroNegocio("Marque o local no mapa.")
    codigos = d.get("modalidades") or []
    d["modalidades"] = sorted(set(s.scalars(select(Modalidade.codigo).where(Modalidade.codigo.in_(codigos or ["-"])))))
    return d


def montar(s: SessaoORM, criado_por_id: int | None = None, **dados) -> Local:
    """Valida os dados e devolve o Local ainda não gravado (usado no cadastro e na importação de planilhas)."""
    d = _validar(s, dados)
    return Local(**{k: d[k] for k in ("tipo", "nome", "descricao", "endereco", "cidade", "latitude", "longitude", "horario_funcionamento", "telefone", "email", "site", "regras", "modalidades")}, criado_por_id=criado_por_id)


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
    d = _validar(s, dados)
    for k in ("tipo", "nome", "descricao", "endereco", "cidade", "latitude", "longitude", "horario_funcionamento", "telefone", "email", "site", "regras", "modalidades"):
        setattr(loc, k, d[k])
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
    icone, rotulo = TIPOS[loc.tipo]
    return {
        "tipo": "local", "id": loc.id, "categoria": loc.tipo, "categoria_nome": rotulo, "icone": icone,
        "nome": loc.nome, "descricao": loc.descricao, "endereco": loc.endereco, "cidade": loc.cidade,
        "latitude": loc.latitude, "longitude": loc.longitude, "horario_funcionamento": loc.horario_funcionamento,
        "telefone": loc.telefone, "email": loc.email, "site": loc.site, "regras": loc.regras,
        "modalidades": list(loc.modalidades or []), "ativo": loc.ativo,
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
        cons = cons.where(or_(Local.nome.ilike(t), Local.cidade.ilike(t), Local.endereco.ilike(t)))
    return [serializar(loc, km) for loc, km in s.execute(cons.order_by(dist).limit(limite))]


def todos(s: SessaoORM, u: Usuario) -> list[Local]:
    _exigir_admin(u)
    return list(s.scalars(select(Local).order_by(Local.ativo.desc(), Local.nome)))
