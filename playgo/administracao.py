"""Perfis de acesso: usuário, moderador geral e administrador. Só o administrador promove ou rebaixa.

  usuario    — comum.
  moderador  — equipe de moderação: vê e decide a fila, oculta em qualquer mural. Não gere usuários.
  admin      — tudo, inclusive mudar perfis. Nunca fica sem administrador (o último não se rebaixa)."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, notificacoes
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import Usuario

PERFIS = ("usuario", "moderador", "admin")
NOME_PERFIL = {"usuario": "Usuário", "moderador": "Moderador geral", "admin": "Administrador"}


def _exigir_admin(por: Usuario) -> None:
    if not por.admin:
        raise SemPermissao("Só administradores gerenciam perfis de acesso.")


def _item(u: Usuario) -> dict:
    return {
        "id": u.id, "usuario": u.usuario, "arroba": u.arroba, "nome": u.nome, "email": u.email, "iniciais": u.iniciais, "foto_url": u.foto_url,
        "perfil": u.perfil_acesso, "perfil_nome": NOME_PERFIL[u.perfil_acesso], "gestor": u.gestor, "criado_em": u.criado_em.isoformat(timespec="minutes"),
    }


def listar(s: SessaoORM, por: Usuario, q: str = "", perfil: str | None = None, limite: int = 50, vencendo: bool = False) -> list[dict]:
    _exigir_admin(por)
    consulta = select(Usuario).where(Usuario.ativo, Usuario.anonimizado_em.is_(None))
    termo = q.strip().lstrip("@")
    if termo:
        t = f"%{termo}%"
        consulta = consulta.where(or_(Usuario.usuario.ilike(t), Usuario.nome.ilike(t), Usuario.email.ilike(t)))
    if perfil == "admin":
        consulta = consulta.where(Usuario.admin)
    elif perfil == "moderador":
        consulta = consulta.where(Usuario.moderador, ~Usuario.admin)
    elif perfil == "usuario":
        consulta = consulta.where(~Usuario.admin, ~Usuario.moderador)
    if vencendo:  # só quem tem assinatura ou eventos comprados pode estar perto do fim do acesso
        from .models import Assinatura, CreditoEvento

        com_prazo = select(Assinatura.usuario_id).union(select(CreditoEvento.usuario_id).where(CreditoEvento.status == "pago"))
        consulta = consulta.where(Usuario.id.in_(com_prazo))
    consulta = consulta.order_by(Usuario.admin.desc(), Usuario.moderador.desc(), Usuario.usuario).limit(2000 if vencendo else limite)
    from . import planos

    saida = []
    for u in s.scalars(consulta):
        sit = planos.situacao(s, u)
        dias = sit["dias_restantes"]
        ev = planos.eventos(s, u)
        if vencendo:  # só quem está na última semana, em tolerância ou com eventos perto de vencer
            termina = sit["status"] in ("teste", "ativa", "cancelada", "tolerancia") and dias is not None and dias <= planos.AVISO_DIAS
            if not termina and not (ev["saldo"] and ev["dias_restantes"] is not None and ev["dias_restantes"] <= planos.AVISO_DIAS):
                continue
        saida.append(_item(u) | {
            "plano": sit["plano"], "plano_status": sit["status"], "plano_fim": sit["fim"].isoformat() if sit["fim"] else None, "plano_inicio": sit["inicio"].isoformat() if sit.get("inicio") else None,
            "plano_dias": dias, "eventos_saldo": ev["saldo"], "eventos_ate": ev["valida_ate"],
        })
    return saida[:limite]


def resumo(s: SessaoORM, por: Usuario) -> dict:
    _exigir_admin(por)
    ativos = (Usuario.ativo, Usuario.anonimizado_em.is_(None))
    return {
        "administradores": s.scalar(select(func.count()).select_from(Usuario).where(*ativos, Usuario.admin)) or 0,
        "moderadores": s.scalar(select(func.count()).select_from(Usuario).where(*ativos, Usuario.moderador, ~Usuario.admin)) or 0,
        "usuarios": s.scalar(select(func.count()).select_from(Usuario).where(*ativos)) or 0,
    }


def definir_perfil(s: SessaoORM, por: Usuario, alvo_id: int, perfil: str) -> dict:
    _exigir_admin(por)
    if perfil not in PERFIS:
        raise ErroNegocio("Perfil inválido.")
    alvo = s.get(Usuario, alvo_id)
    if alvo is None or not alvo.ativo or alvo.anonimizado_em:
        raise NaoEncontrado("Usuário não encontrado.")
    if not alvo.usuario:
        raise ErroNegocio("Essa pessoa ainda não concluiu o cadastro (falta escolher o @usuario).")
    anterior = alvo.perfil_acesso
    if perfil == anterior:
        return _item(alvo)
    if anterior == "admin":  # nunca ficar sem administrador
        outros = s.scalar(select(func.count()).select_from(Usuario).where(Usuario.admin, Usuario.ativo, Usuario.id != alvo.id)) or 0
        if outros == 0:
            raise ErroNegocio("Este é o único administrador. Promova outra pessoa antes de rebaixá-lo.")
    alvo.admin, alvo.moderador = perfil == "admin", perfil == "moderador"
    auditoria.registrar(s, por.id, "perfil_alterar", "usuario", alvo.id, de=anterior, para=perfil, alvo=alvo.usuario)
    if alvo.id != por.id:
        notificacoes.avisar(
            s, alvo.id, "moderacao", f"Seu perfil agora é: {NOME_PERFIL[perfil]}",
            {"admin": "Você pode gerir usuários e perfis e cuidar da moderação de todo o app.", "moderador": "Você pode aprovar a fila de moderação e ocultar conteúdo em qualquer mural.", "usuario": "Você voltou a ser usuário comum."}[perfil],
            "/moderacao" if perfil != "usuario" else None, None, f"perfil:{alvo.id}:{perfil}:{agora().strftime('%d%H%M%S')}",
        )
    s.commit()
    return _item(alvo)
