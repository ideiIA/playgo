"""Quem entra onde: acesso a atividades 'só por link', convites pelo @usuario e links de convite
(do tipo grupo de WhatsApp) para atividades e comunidades."""

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, escopos, notificacoes
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import (
    ATIVAS,
    Atividade,
    Comunidade,
    Convite,
    GrupoMembro,
    Participacao,
    Usuario,
)


def novo_token(prefixo: str) -> str:
    """O prefixo ('a' atividade, 'c' comunidade) deixa o link dizer a que se refere."""
    return prefixo + secrets.token_urlsafe(9)


def pode_acessar_atividade(s: SessaoORM, usuario: Usuario, a: Atividade, token: str | None = None) -> bool:
    """Atividades públicas e 'só autorizados' são visíveis a todos (a entrada é que muda). As 'por link'
    só aparecem para quem participa, organiza, modera, é do grupo dela, foi convidado ou tem o link."""
    if a.visibilidade != "link":
        return True
    if usuario.admin or a.organizador_id == usuario.id:
        return True
    if token and a.convite_token and secrets.compare_digest(token, a.convite_token):
        return True
    if s.scalar(select(Participacao.id).where(Participacao.atividade_id == a.id, Participacao.usuario_id == usuario.id, Participacao.status.in_(ATIVAS))):
        return True
    if s.scalar(select(Convite.id).where(Convite.escopo == "atividade", Convite.escopo_id == a.id, Convite.convidado_id == usuario.id)):
        return True
    if a.grupo_id:
        vinculo = s.get(GrupoMembro, (a.grupo_id, usuario.id))
        if vinculo is not None and vinculo.status == "ativo":
            return True
    return escopos.eh_moderador(s, usuario, "atividade", a.id)


def exigir_acesso_atividade(s: SessaoORM, usuario: Usuario, a: Atividade, token: str | None = None) -> None:
    if not pode_acessar_atividade(s, usuario, a, token):
        raise NaoEncontrado("Atividade não encontrada.")  # não confirma que existe


def convite_da_atividade(s: SessaoORM, atividade_id: int, usuario_id: int) -> Convite | None:
    return s.scalar(select(Convite).where(Convite.escopo == "atividade", Convite.escopo_id == atividade_id, Convite.convidado_id == usuario_id, Convite.status != "recusado"))


def renovar_link(s: SessaoORM, escopo: str, escopo_id: int, por: Usuario) -> str:
    """Gera outro link e derruba o antigo (útil quando o link vaza)."""
    obj = escopos.carregar(s, escopo, escopo_id)
    if escopo == "atividade":
        if obj.organizador_id != por.id and not por.admin:
            raise SemPermissao("Só o organizador gerencia o link.")
        if obj.visibilidade != "link":
            raise ErroNegocio("Esta atividade não é do tipo 'só por link'.")
    elif escopo == "comunidade":
        if not escopos.eh_moderador(s, por, "comunidade", escopo_id):
            raise SemPermissao("Só donos e moderadores gerenciam o link.")
        if obj.visibilidade != "link":
            raise ErroNegocio("Esta comunidade não é do tipo 'só por link'.")
    else:
        raise ErroNegocio("Escopo sem link de convite.")
    obj.convite_token = novo_token("a" if escopo == "atividade" else "c")
    auditoria.registrar(s, por.id, "link_renovar", escopo, escopo_id)
    s.commit()
    return obj.convite_token


def resolver_token(s: SessaoORM, token: str) -> tuple[str, object]:
    if token.startswith("a"):
        obj = s.scalar(select(Atividade).where(Atividade.convite_token == token))
        if obj is not None and obj.status == "aberta":
            return "atividade", obj
    elif token.startswith("c"):
        obj = s.scalar(select(Comunidade).where(Comunidade.convite_token == token, Comunidade.ativa))
        if obj is not None:
            return "comunidade", obj
    raise NaoEncontrado("Link de convite inválido ou expirado.")


def resumo_do_link(s: SessaoORM, token: str) -> dict:
    """O que a pessoa vê ao abrir o link, antes de entrar."""
    escopo, obj = resolver_token(s, token)
    if escopo == "atividade":
        from . import serializadores as ser

        d = ser.atividade(obj, agora())
        return {"tipo": "atividade", "id": obj.id, "titulo": obj.nome, "quando": d["quando"], "local": obj.local_nome, "vagas": obj.vagas, "organizador": obj.organizador.arroba, "modalidade": d["modalidade"]}
    return {"tipo": "comunidade", "id": obj.id, "titulo": obj.nome, "descricao": obj.descricao, "membros": len([m for m in obj.membros if m.status == "ativo"])}


def entrar_por_link(s: SessaoORM, token: str, usuario: Usuario) -> dict:
    escopo, obj = resolver_token(s, token)
    if escopo == "atividade":
        from . import vagas

        p = vagas.entrar(s, obj.id, usuario, token=token)
        return {"tipo": "atividade", "id": obj.id, "situacao": p.status}
    from . import comunidades

    m = comunidades.entrar(s, obj.id, usuario, token=token)
    return {"tipo": "comunidade", "id": obj.id, "situacao": m.status}


# ---------------------------------------------------------------- convites pelo @usuario


def convidar(s: SessaoORM, escopo: str, escopo_id: int, por: Usuario, alvo: Usuario) -> Convite:
    if escopo not in ("atividade", "comunidade"):
        raise ErroNegocio("Só atividades e comunidades recebem convites.")
    obj = escopos.carregar(s, escopo, escopo_id)
    if not escopos.eh_moderador(s, por, escopo, escopo_id):
        raise SemPermissao("Só quem organiza ou modera pode convidar.")
    if alvo.id == por.id:
        raise ErroNegocio("Você não pode convidar a si mesmo.")
    if escopo == "atividade":
        if obj.status != "aberta" or obj.inicio < agora():
            raise ErroNegocio("Esta atividade não aceita mais convites.")
        if s.scalar(select(Participacao.id).where(Participacao.atividade_id == obj.id, Participacao.usuario_id == alvo.id, Participacao.status.in_(ATIVAS))):
            raise ErroNegocio(f"{alvo.arroba} já está nesta atividade.")
        titulo, _link = obj.nome, f"/atividades/{obj.id}"
    else:
        if escopos._membro_comunidade(s, obj.id, alvo) and escopos._membro_comunidade(s, obj.id, alvo).status == "ativo":
            raise ErroNegocio(f"{alvo.arroba} já é da comunidade.")
        titulo, _link = obj.nome, f"/comunidades/{obj.id}"
    c = s.scalar(select(Convite).where(Convite.escopo == escopo, Convite.escopo_id == escopo_id, Convite.convidado_id == alvo.id))
    if c is None:
        c = Convite(escopo=escopo, escopo_id=escopo_id, convidado_id=alvo.id, convidado_por=por.id)
        s.add(c)
    elif c.status == "pendente":
        return c
    c.status, c.convidado_por, c.criado_em = "pendente", por.id, agora()
    s.flush()
    notificacoes.avisar(s, alvo.id, "convite", f"{por.arroba} convidou você: {titulo}", "Toque para ver e aceitar o convite.", "/convites", None, f"conv:{escopo}:{escopo_id}:{alvo.id}:{agora().strftime('%d%H%M%S')}")
    auditoria.registrar(s, por.id, "convite_enviar", escopo, escopo_id, alvo=alvo.id)
    s.commit()
    return c


def pendentes(s: SessaoORM, usuario: Usuario) -> list[dict]:
    saida = []
    for c in s.scalars(select(Convite).where(Convite.convidado_id == usuario.id, Convite.status == "pendente").order_by(Convite.id.desc())):
        try:
            ctx = escopos.contexto(s, c.escopo, c.escopo_id)
        except NaoEncontrado:
            continue
        saida.append({"id": c.id, "escopo": c.escopo, "escopo_id": c.escopo_id, "titulo": ctx["titulo"], "link": ctx["link"], "de": c.autor.arroba, "quando": c.criado_em.isoformat(timespec="minutes")})
    return saida


def responder(s: SessaoORM, convite_id: int, usuario: Usuario, aceitar: bool) -> dict:
    c = s.get(Convite, convite_id)
    if c is None or c.convidado_id != usuario.id or c.status != "pendente":
        raise NaoEncontrado("Convite não encontrado.")
    if not aceitar:
        c.status = "recusado"
        auditoria.registrar(s, usuario.id, "convite_recusar", c.escopo, c.escopo_id)
        s.commit()
        return {"situacao": "recusado"}
    if c.escopo == "atividade":
        from . import vagas

        p = vagas.entrar(s, c.escopo_id, usuario)  # convite aceito pula a aprovação (o convite já é a autorização)
        situacao = p.status
    else:
        from . import comunidades

        situacao = comunidades.entrar(s, c.escopo_id, usuario, convidado=True).status
    c.status = "aceito"
    auditoria.registrar(s, usuario.id, "convite_aceitar", c.escopo, c.escopo_id)
    s.commit()
    return {"situacao": situacao, "escopo": c.escopo, "escopo_id": c.escopo_id}
