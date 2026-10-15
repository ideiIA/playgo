"""Comunidades de relacionamento: gente com um interesse em comum, com mural próprio.
Entrada pública, só autorizados (um moderador aprova) ou só por link/convite. Na comunidade as pessoas
aparecem pelo @usuario."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import planos, auditoria, convites, escopos, notificacoes
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import VISIBILIDADES, Comunidade, ComunidadeMembro, Convite, Usuario

PAPEIS = ("dono", "moderador", "membro")


def obter(s: SessaoORM, comunidade_id: int) -> Comunidade:
    c = s.get(Comunidade, comunidade_id)
    if c is None or not c.ativa:
        raise NaoEncontrado("Comunidade não encontrada.")
    return c


def membro(s: SessaoORM, comunidade_id: int, usuario: Usuario) -> ComunidadeMembro | None:
    return s.get(ComunidadeMembro, (comunidade_id, usuario.id))


def criar(s: SessaoORM, usuario: Usuario, nome: str, descricao: str | None = None, regras: str | None = None, cidade: str | None = None, visibilidade: str = "publica") -> Comunidade:
    planos.exigir(s, usuario, "comunidade")
    if not nome.strip():
        raise ErroNegocio("Dê um nome à comunidade.")
    if visibilidade not in {v for v, _ in VISIBILIDADES}:
        raise ErroNegocio("Escolha quem pode entrar.")
    c = Comunidade(nome=nome.strip()[:120], descricao=descricao, regras=regras, cidade=cidade or usuario.cidade, visibilidade=visibilidade, convite_token=convites.novo_token("c") if visibilidade == "link" else None)
    c.membros.append(ComunidadeMembro(usuario_id=usuario.id, papel="dono", status="ativo"))
    s.add(c)
    s.flush()
    auditoria.registrar(s, usuario.id, "comunidade_criar", "comunidade", c.id, visibilidade=visibilidade)
    s.commit()
    return c


def entrar(s: SessaoORM, comunidade_id: int, usuario: Usuario, token: str | None = None, convidado: bool = False) -> ComunidadeMembro:
    c = obter(s, comunidade_id)
    m = membro(s, c.id, usuario)
    if m is not None:
        if m.status == "banido":
            raise SemPermissao("Você não pode entrar nesta comunidade.")
        if m.status == "ativo" or (m.status == "pendente" and not (convidado or token)):
            return m
    pelo_link = bool(token and c.convite_token and token == c.convite_token)
    tem_convite = convidado or bool(s.scalar(select(Convite.id).where(Convite.escopo == "comunidade", Convite.escopo_id == c.id, Convite.convidado_id == usuario.id, Convite.status != "recusado")))
    if c.visibilidade == "link" and not (pelo_link or tem_convite):
        raise ErroNegocio("Esta comunidade é só por link ou convite.")
    status = "ativo" if (c.visibilidade == "publica" or c.visibilidade == "link" or pelo_link or tem_convite) else "pendente"
    if m is None:
        m = ComunidadeMembro(comunidade_id=c.id, usuario_id=usuario.id, papel="membro", status=status)
        s.add(m)
    else:
        m.status = status
    s.flush()
    if status == "pendente":
        for mod in escopos.moderadores(s, "comunidade", c.id):
            notificacoes.avisar(s, mod.id, "comunidade", f"{usuario.arroba} quer entrar em {c.nome}", "Aprove ou recuse o pedido.", f"/comunidades/{c.id}", None, f"pedido:{c.id}:{usuario.id}")
    auditoria.registrar(s, usuario.id, "comunidade_entrar", "comunidade", c.id, situacao=status)
    s.commit()
    return m


def sair(s: SessaoORM, comunidade_id: int, usuario: Usuario) -> None:
    m = membro(s, comunidade_id, usuario)
    if m is None or m.status == "banido":
        return
    if m.papel == "dono":
        raise ErroNegocio("O dono não pode sair. Passe a titularidade a outro moderador antes (ou encerre a comunidade).")
    s.delete(m)
    auditoria.registrar(s, usuario.id, "comunidade_sair", "comunidade", comunidade_id)
    s.commit()


def _exigir_moderador(s: SessaoORM, comunidade_id: int, usuario: Usuario) -> None:
    if not escopos.eh_moderador(s, usuario, "comunidade", comunidade_id):
        raise SemPermissao("Só donos e moderadores da comunidade podem fazer isso.")


def decidir_pedido(s: SessaoORM, comunidade_id: int, alvo_id: int, por: Usuario, aprovar: bool) -> None:
    _exigir_moderador(s, comunidade_id, por)
    m = s.get(ComunidadeMembro, (comunidade_id, alvo_id))
    if m is None or m.status != "pendente":
        raise NaoEncontrado("Pedido não encontrado.")
    c = obter(s, comunidade_id)
    if aprovar:
        m.status = "ativo"
        notificacoes.avisar(s, alvo_id, "comunidade", f"Você entrou em {c.nome}", "", f"/comunidades/{c.id}", None, f"aprov:{c.id}:{alvo_id}")
    else:
        s.delete(m)
    auditoria.registrar(s, por.id, "comunidade_pedido_aprovar" if aprovar else "comunidade_pedido_recusar", "comunidade", comunidade_id, alvo=alvo_id)
    s.commit()


def definir_papel(s: SessaoORM, comunidade_id: int, alvo_id: int, por: Usuario, papel: str) -> None:
    """O dono promove/rebaixa moderadores. Dono e moderadores banem (e desbanem) membros comuns."""
    if papel not in ("moderador", "membro", "banido"):
        raise ErroNegocio("Papel inválido.")
    quem = membro(s, comunidade_id, por)
    m = s.get(ComunidadeMembro, (comunidade_id, alvo_id))
    if m is None:
        raise NaoEncontrado("Essa pessoa não é da comunidade.")
    if m.papel == "dono":
        raise ErroNegocio("O dono não pode ser alterado.")
    if papel in ("moderador",) or m.papel == "moderador":
        if not ((quem and quem.papel == "dono") or por.admin):
            raise SemPermissao("Só o dono promove ou rebaixa moderadores.")
    else:
        _exigir_moderador(s, comunidade_id, por)
    if papel == "banido":
        m.status = "banido"
        m.papel = "membro"
    else:
        m.status, m.papel = "ativo", papel
    auditoria.registrar(s, por.id, "comunidade_papel", "comunidade", comunidade_id, alvo=alvo_id, papel=papel)
    s.commit()


def encerrar(s: SessaoORM, comunidade_id: int, por: Usuario) -> None:
    c = obter(s, comunidade_id)
    m = membro(s, c.id, por)
    if not ((m and m.papel == "dono") or por.admin):
        raise SemPermissao("Só o dono encerra a comunidade.")
    c.ativa = False
    auditoria.registrar(s, por.id, "comunidade_encerrar", "comunidade", c.id)
    s.commit()


# ---------------------------------------------------------------- leitura


def _pessoa(m: ComunidadeMembro) -> dict:
    return {"usuario_id": m.usuario_id, "usuario": m.usuario.usuario, "arroba": m.usuario.arroba, "iniciais": m.usuario.iniciais, "papel": m.papel, "status": m.status}


def resumo(c: Comunidade, ativos: int, meu: ComunidadeMembro | None) -> dict:
    return {
        "tipo": "comunidade", "id": c.id, "nome": c.nome, "descricao": c.descricao, "cidade": c.cidade, "visibilidade": c.visibilidade, "membros": ativos,
        "meu_status": meu.status if meu else None, "meu_papel": meu.papel if meu and meu.status == "ativo" else None,
    }


def listar(s: SessaoORM, usuario: Usuario, q: str = "") -> dict:
    ativos = select(func.count()).where(ComunidadeMembro.comunidade_id == Comunidade.id, ComunidadeMembro.status == "ativo").correlate(Comunidade).scalar_subquery()
    consulta = select(Comunidade, ativos).where(Comunidade.ativa)
    if q.strip():
        t = f"%{q.strip()}%"
        consulta = consulta.where(or_(Comunidade.nome.ilike(t), Comunidade.descricao.ilike(t), Comunidade.cidade.ilike(t)))
    meus = {m.comunidade_id: m for m in s.scalars(select(ComunidadeMembro).where(ComunidadeMembro.usuario_id == usuario.id))}
    minhas, descobrir = [], []
    for c, n in s.execute(consulta.order_by(ativos.desc(), Comunidade.nome)).unique():
        m = meus.get(c.id)
        if m is not None and m.status != "banido":
            minhas.append(resumo(c, int(n), m))
        elif c.visibilidade != "link" and m is None:  # as 'só por link' não aparecem na busca
            descobrir.append(resumo(c, int(n), None))
    return {"minhas": minhas, "descobrir": descobrir}


def detalhe(s: SessaoORM, c: Comunidade, usuario: Usuario) -> dict:
    meu = membro(s, c.id, usuario)
    mod = escopos.eh_moderador(s, usuario, "comunidade", c.id)
    if meu is not None and meu.status == "banido":
        raise NaoEncontrado("Comunidade não encontrada.")
    ativos = [m for m in c.membros if m.status == "ativo"]
    d = resumo(c, len(ativos), meu)
    d.update(
        {
            "regras": c.regras,
            "sou_moderador": mod,
            "sou_dono": bool(meu and meu.papel == "dono"),
            "pode_ler": escopos.pode_ler(s, usuario, "comunidade", c.id),
            "pode_postar": escopos.pode_postar(s, usuario, "comunidade", c.id),
            "membros_lista": [_pessoa(m) for m in sorted(ativos, key=lambda m: (PAPEIS.index(m.papel), m.usuario.usuario or ""))] if (meu and meu.status == "ativo") or usuario.admin else [],
            "pendentes": [_pessoa(m) for m in c.membros if m.status == "pendente"] if mod else [],
            "link": f"/convite/{c.convite_token}" if mod and c.convite_token else None,
        }
    )
    return d
