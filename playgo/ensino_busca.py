"""Busca de profissionais e ofertas de aulas por proximidade (RF-03, seção 6 da especificação).

Só aparece na busca quem está **aprovado** e com **plano de profissional vigente** (ou é administrador): se o plano vence, as ofertas saem
da busca sem apagar nada. Mesmo caminho de `descoberta.py` (caixa + haversine no banco) e mesmos filtros."""

from datetime import date, time, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import ofertas as srv_ofertas
from . import profissionais as srv_prof
from .config import settings
from .db import agora
from .geo import caixa_sql, distancia_sql
from .models import (
    OF_PUBLICADA,
    PP_APROVADO,
    Assinatura,
    Modalidade,
    Oferta,
    OfertaHorario,
    PerfilProfissional,
    Usuario,
)

PERIODOS = {"manha": (time(0, 0), time(12, 0)), "tarde": (time(12, 0), time(18, 0)), "noite": (time(18, 0), time(23, 59, 59))}


def ativos_subquery():
    """Ids de usuários com plano de profissional vigente (teste, ativo ou cancelado dentro do período, mais a tolerância) ou administradores."""
    hoje = agora().date()
    limite = hoje - timedelta(days=settings.tolerancia_dias)
    fim = func.greatest(Assinatura.vigente_ate, Assinatura.teste_ate)
    com_plano = select(Assinatura.usuario_id).where(
        Assinatura.familia == "profissional", Assinatura.status.in_(("teste", "ativa", "cancelada")), fim >= limite,
        or_(Assinatura.vigente_desde.is_(None), Assinatura.vigente_desde <= hoje),
    )
    admins = select(Usuario.id).where(Usuario.admin, Usuario.ativo)
    return com_plano.union(admins)


def _lat_lng_oferta():
    return func.coalesce(Oferta.latitude, PerfilProfissional.latitude), func.coalesce(Oferta.longitude, PerfilProfissional.longitude)


def buscar_ofertas(s: SessaoORM, lat: float, lng: float, f, limite: int | None = None) -> list[dict]:
    la, ln = _lat_lng_oferta()
    dist = distancia_sql(la, ln, lat, lng)
    q = (
        select(Oferta, dist).join(PerfilProfissional, PerfilProfissional.id == Oferta.perfil_id).outerjoin(Modalidade, Modalidade.id == Oferta.modalidade_id)
        .where(Oferta.status == OF_PUBLICADA, PerfilProfissional.status == PP_APROVADO, PerfilProfissional.usuario_id.in_(ativos_subquery()), la.is_not(None))
    )
    if f.raio_km:
        q = q.where(caixa_sql(la, ln, lat, lng, f.raio_km), dist <= f.raio_km)
    if f.modalidades:
        q = q.where(Modalidade.codigo.in_(f.modalidades))
    if f.nivel:
        q = q.where(or_(Oferta.nivel.is_(None), Oferta.nivel.in_((f.nivel, "todos"))))
    valor = func.coalesce(Oferta.valor_aula, Oferta.valor_mensal, Oferta.valor_pacote)
    if f.preco == "gratis":
        q = q.where(valor == 0)
    elif f.preco == "pago":
        q = q.where(valor > 0)
    if getattr(f, "preco_max", None) is not None:
        q = q.where(valor <= f.preco_max)
    if getattr(f, "tipo_oferta", None):
        q = q.where(Oferta.tipo == f.tipo_oferta)
    if getattr(f, "experimental", False):
        q = q.where(Oferta.aula_experimental)
    if getattr(f, "formato", None):
        q = q.where(Oferta.formato == f.formato)
    idade = getattr(f, "idade", None)
    if idade is not None:
        q = q.where(or_(Oferta.faixa_etaria_min.is_(None), Oferta.faixa_etaria_min <= idade), or_(Oferta.faixa_etaria_max.is_(None), Oferta.faixa_etaria_max >= idade))
    dia = getattr(f, "dia_semana", None)
    if dia is not None:
        q = q.where(Oferta.horarios.any(OfertaHorario.dia_semana == dia))
    if f.periodo in PERIODOS:
        ini, fim = PERIODOS[f.periodo]
        q = q.where(Oferta.horarios.any(and_(OfertaHorario.hora_inicio >= ini, OfertaHorario.hora_inicio < fim)))
    if f.q.strip():
        t = f"%{f.q.strip()}%"
        q = q.where(or_(Oferta.titulo.ilike(t), Oferta.local_texto.ilike(t), PerfilProfissional.nome_profissional.ilike(t), Modalidade.nome.ilike(t)))
    q = q.order_by(dist).limit(limite or settings.limite_resultados)
    return [srv_ofertas.dict_oferta(o, km) for o, km in s.execute(q).unique()]


def buscar_profissionais(s: SessaoORM, lat: float, lng: float, f, limite: int | None = None) -> list[dict]:
    dist = distancia_sql(PerfilProfissional.latitude, PerfilProfissional.longitude, lat, lng)
    n_ofertas = (
        select(func.count()).select_from(Oferta).where(Oferta.perfil_id == PerfilProfissional.id, Oferta.status == OF_PUBLICADA).correlate(PerfilProfissional).scalar_subquery()
    )
    q = select(PerfilProfissional, dist, n_ofertas).where(
        PerfilProfissional.status == PP_APROVADO, PerfilProfissional.usuario_id.in_(ativos_subquery()), PerfilProfissional.latitude.is_not(None),
    )
    if f.raio_km:
        q = q.where(caixa_sql(PerfilProfissional.latitude, PerfilProfissional.longitude, lat, lng, f.raio_km), dist <= f.raio_km)
    if f.modalidades:
        q = q.where(PerfilProfissional.modalidades.overlap(f.modalidades))
    nota = getattr(f, "nota_min", None)
    if nota is not None:
        q = q.where(PerfilProfissional.nota_media >= nota, PerfilProfissional.n_avaliacoes >= 3)
    if f.q.strip():
        t = f"%{f.q.strip()}%"
        q = q.where(or_(PerfilProfissional.nome_profissional.ilike(t), PerfilProfissional.especialidades.ilike(t), PerfilProfissional.regiao.ilike(t), PerfilProfissional.cidade.ilike(t)))
    q = q.order_by(dist).limit(limite or settings.limite_resultados)
    return [srv_prof.dict_publico(p, km) | {"ofertas_publicadas": int(n)} for p, km, n in s.execute(q).unique()]
