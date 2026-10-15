"""Grupos esportivos locais (RF-004, RF-020): comunidade permanente com administradores, chat,
agenda, avisos e histórico."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, notificacoes, planos
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import ABERTA, Atividade, Grupo, GrupoMembro, GrupoMensagem, Modalidade, Usuario


def obter(s: SessaoORM, grupo_id: int) -> Grupo:
    g = s.get(Grupo, grupo_id)
    if g is None or not g.ativo:
        raise NaoEncontrado("Grupo não encontrado.")
    return g


def membro(s: SessaoORM, grupo_id: int, usuario: Usuario) -> GrupoMembro | None:
    """O vínculo ATIVO da pessoa com o grupo (quem está com pedido pendente ainda não é membro: veja `pedido`)."""
    m = s.get(GrupoMembro, (grupo_id, usuario.id))
    return m if m is not None and m.status == "ativo" else None


def pedido(s: SessaoORM, grupo_id: int, usuario: Usuario) -> GrupoMembro | None:
    m = s.get(GrupoMembro, (grupo_id, usuario.id))
    return m if m is not None and m.status == "pendente" else None


def _admins(s: SessaoORM, grupo_id: int) -> list[int]:
    return list(s.scalars(select(GrupoMembro.usuario_id).where(GrupoMembro.grupo_id == grupo_id, GrupoMembro.papel == "admin", GrupoMembro.status == "ativo")))


def _exigir_admin(s: SessaoORM, grupo_id: int, por: Usuario, acao: str) -> None:
    quem = membro(s, grupo_id, por)
    if (quem is None or quem.papel != "admin") and not por.admin:
        raise SemPermissao(f"Só administradores do grupo podem {acao}.")


def criar(
    s: SessaoORM,
    usuario: Usuario,
    nome: str,
    modalidade_id: int,
    descricao: str | None = None,
    cidade: str | None = None,
    local_habitual: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    entrada: str = "livre",
) -> Grupo:
    planos.exigir(s, usuario, "grupo")
    if entrada not in ("livre", "aprovacao"):
        raise ErroNegocio("Escolha como as pessoas entram: livre ou com aprovação.")
    if not nome.strip():
        raise ErroNegocio("Dê um nome ao grupo.")
    if s.get(Modalidade, modalidade_id) is None:
        raise ErroNegocio("Escolha a modalidade do grupo.")
    g = Grupo(
        nome=nome.strip(), modalidade_id=modalidade_id, descricao=descricao, cidade=cidade or usuario.cidade,
        local_habitual=local_habitual, latitude=latitude if latitude is not None else usuario.latitude,
        longitude=longitude if longitude is not None else usuario.longitude, entrada=entrada,
    )
    g.membros.append(GrupoMembro(usuario_id=usuario.id, papel="admin", status="ativo"))
    s.add(g)
    s.flush()
    auditoria.registrar(s, usuario.id, "grupo_criar", "grupo", g.id, entrada=entrada)
    s.commit()
    return g


def entrar(s: SessaoORM, grupo_id: int, usuario: Usuario) -> GrupoMembro:
    """Grupo livre: entra na hora. Grupo com aprovação: vira pedido pendente e os administradores do grupo são avisados."""
    g = obter(s, grupo_id)
    m = s.get(GrupoMembro, (g.id, usuario.id))
    if m is not None:
        return m  # já é membro ou já pediu
    status = "pendente" if g.entrada == "aprovacao" else "ativo"
    m = GrupoMembro(grupo_id=g.id, usuario_id=usuario.id, papel="membro", status=status)
    s.add(m)
    s.flush()
    if status == "pendente":
        for admin_id in _admins(s, g.id):
            notificacoes.avisar(s, admin_id, "grupo", f"{usuario.arroba} quer entrar em {g.nome}", "Aceite ou recuse o pedido no grupo.", f"/grupos/{g.id}", None, f"pedido-g:{g.id}:{usuario.id}")
    auditoria.registrar(s, usuario.id, "grupo_entrar", "grupo", g.id, situacao=status)
    s.commit()
    return m


def decidir_pedido(s: SessaoORM, grupo_id: int, alvo_id: int, por: Usuario, aprovar: bool) -> None:
    g = obter(s, grupo_id)
    _exigir_admin(s, g.id, por, "aceitar ou recusar pedidos")
    m = s.get(GrupoMembro, (g.id, alvo_id))
    if m is None or m.status != "pendente":
        raise NaoEncontrado("Esse pedido não está mais aguardando.")
    if aprovar:
        m.status = "ativo"
        notificacoes.avisar(s, alvo_id, "grupo", f"Você entrou em {g.nome}", "Seu pedido foi aceito.", f"/grupos/{g.id}", None, f"aceito-g:{g.id}:{alvo_id}")
    else:
        s.delete(m)
        notificacoes.avisar(s, alvo_id, "grupo", f"Pedido para {g.nome}", "O administrador do grupo não aceitou o pedido.", "/grupos", None, f"recusado-g:{g.id}:{alvo_id}:{agora().strftime('%d%H%M%S')}")
    auditoria.registrar(s, por.id, "grupo_pedido_aceito" if aprovar else "grupo_pedido_recusado", "grupo", g.id, alvo=alvo_id)
    s.commit()


def remover(s: SessaoORM, grupo_id: int, alvo_id: int, por: Usuario) -> None:
    """Administrador do grupo (ou do site) tira um membro. Administradores do grupo não são removidos por outros: eles saem sozinhos."""
    g = obter(s, grupo_id)
    _exigir_admin(s, g.id, por, "remover participantes")
    if alvo_id == por.id:
        raise ErroNegocio("Para sair do grupo use \"Sair\".")
    m = membro(s, g.id, s.get(Usuario, alvo_id)) if s.get(Usuario, alvo_id) else None
    if m is None:
        raise NaoEncontrado("Essa pessoa não é do grupo.")
    if m.papel == "admin":
        raise ErroNegocio("Administradores do grupo não podem ser removidos: eles saem por conta própria.")
    s.delete(m)
    notificacoes.avisar(s, alvo_id, "grupo", f"Você saiu de {g.nome}", "Um administrador do grupo removeu você.", "/grupos", None, f"removido-g:{g.id}:{alvo_id}:{agora().strftime('%d%H%M%S')}")
    auditoria.registrar(s, por.id, "grupo_remover", "grupo", g.id, alvo=alvo_id)
    s.commit()


def sair(s: SessaoORM, grupo_id: int, usuario: Usuario) -> None:
    """Sai do grupo ou cancela o pedido pendente."""
    m = s.get(GrupoMembro, (grupo_id, usuario.id))
    if m is None:
        return
    if m.papel == "admin" and m.status == "ativo":
        outros_admins = s.scalar(select(func.count()).select_from(GrupoMembro).where(GrupoMembro.grupo_id == grupo_id, GrupoMembro.papel == "admin", GrupoMembro.usuario_id != usuario.id))
        if not outros_admins:
            raise ErroNegocio("Você é o único administrador. Promova outro membro antes de sair.")
    s.delete(m)
    s.commit()


def promover(s: SessaoORM, grupo_id: int, alvo_id: int, por: Usuario) -> None:
    quem = membro(s, grupo_id, por)
    if (quem is None or quem.papel != "admin") and not por.admin:
        raise SemPermissao("Só administradores do grupo podem promover membros.")
    m = s.get(GrupoMembro, (grupo_id, alvo_id))
    if m is None or m.status != "ativo":
        raise NaoEncontrado("Essa pessoa não é do grupo.")
    m.papel = "admin"
    s.commit()


def meus(s: SessaoORM, usuario: Usuario) -> list[Grupo]:
    return list(s.scalars(select(Grupo).join(GrupoMembro, GrupoMembro.grupo_id == Grupo.id).where(GrupoMembro.usuario_id == usuario.id, GrupoMembro.status == "ativo", Grupo.ativo).order_by(Grupo.nome)))


def agenda(s: SessaoORM, grupo_id: int) -> list[Atividade]:
    return list(s.scalars(select(Atividade).where(Atividade.grupo_id == grupo_id, Atividade.status == ABERTA, Atividade.inicio >= agora()).order_by(Atividade.inicio)))


def historico(s: SessaoORM, grupo_id: int, limite: int = 20) -> list[Atividade]:
    return list(s.scalars(select(Atividade).where(Atividade.grupo_id == grupo_id, Atividade.inicio < agora()).order_by(Atividade.inicio.desc()).limit(limite)))


def postar(s: SessaoORM, grupo_id: int, usuario: Usuario, texto: str, aviso: bool = False) -> GrupoMensagem:
    m = membro(s, grupo_id, usuario)
    if m is None:
        raise SemPermissao("Entre no grupo para participar da conversa.")
    texto = texto.strip()
    if not texto:
        raise ErroNegocio("Escreva uma mensagem.")
    if aviso and m.papel != "admin":
        raise SemPermissao("Só administradores publicam avisos.")
    msg = GrupoMensagem(grupo_id=grupo_id, usuario_id=usuario.id, texto=texto[:2000], aviso=aviso)
    s.add(msg)
    s.commit()
    return msg


def mensagens(s: SessaoORM, grupo_id: int, depois_de: int = 0, limite: int = 80) -> list[GrupoMensagem]:
    """Mais recentes primeiro na consulta, devolvidas em ordem cronológica. `depois_de` serve ao polling do app."""
    q = select(GrupoMensagem).where(GrupoMensagem.grupo_id == grupo_id, GrupoMensagem.id > depois_de).order_by(GrupoMensagem.id.desc()).limit(limite)
    return list(reversed(list(s.scalars(q))))


def avisos(s: SessaoORM, grupo_id: int, limite: int = 3) -> list[GrupoMensagem]:
    return list(s.scalars(select(GrupoMensagem).where(GrupoMensagem.grupo_id == grupo_id, GrupoMensagem.aviso).order_by(GrupoMensagem.id.desc()).limit(limite)))
