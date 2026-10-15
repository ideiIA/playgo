"""Murais: publicações e comentários com moderação.

Regras que valem aqui (e só aqui):
  * o autor edita o que é dele — ninguém mais edita, nem o admin; toda edição fica marcada ("Editado em …")
    e a versão anterior é guardada;
  * o autor só EXCLUI na primeira hora; depois, só moderador/admin OCULTA (o conteúdo continua guardado);
  * tudo entra "em análise" e só aparece para os outros depois da pré-análise automática;
  * uma publicação é uma linha só: replicada no feed geral ou não, ocultar vale em todos os lugares;
  * o que um admin oculta, só um admin restaura."""

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, escopos, midia, notificacoes, planos
from . import serializadores as ser
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .geo import caixa_sql, distancia_sql, formatar_km, haversine
from .models import (
    PublicacaoCurtida,
    P_EM_ANALISE,
    P_EXCLUIDA,
    P_OCULTA,
    P_PUBLICADA,
    P_REJEITADA,
    Comentario,
    ComentarioVersao,
    Denuncia,
    Midia,
    Publicacao,
    PublicacaoVersao,
    Usuario,
)

MOTIVOS_DENUNCIA = {
    "ofensa": "Ofensa ou discriminação",
    "ameaca": "Ameaça ou assédio",
    "sexual": "Conteúdo sexual",
    "dados": "Expõe dados pessoais",
    "spam": "Spam ou golpe",
    "outro": "Outro motivo",
}
LIMITE_COMENTARIOS_10MIN = 20
MAX_TEXTO_COMENTARIO = 1000


# ---------------------------------------------------------------- utilidades


def _janela_aberta(criado_em: datetime) -> bool:
    return agora() - criado_em <= timedelta(minutes=settings.janela_exclusao_min)


def texto_editado(dt: datetime | None) -> str | None:
    """No estilo do WhatsApp: 'Editado em 08/10 às 14:32'."""
    return f"Editado em {dt.strftime('%d/%m às %H:%M')}" if dt else None


def _excesso(s: SessaoORM, modelo, usuario: Usuario, limite: int) -> bool:
    desde = agora() - timedelta(minutes=10)
    return (s.scalar(select(func.count()).select_from(modelo).where(modelo.autor_id == usuario.id, modelo.criado_em >= desde)) or 0) >= limite


def obter(s: SessaoORM, publicacao_id: int) -> Publicacao:
    p = s.get(Publicacao, publicacao_id)
    if p is None or p.status == P_EXCLUIDA:
        raise NaoEncontrado("Publicação não encontrada.")
    return p


def eh_moderador(s: SessaoORM, usuario: Usuario, p: Publicacao) -> bool:
    return escopos.eh_moderador(s, usuario, p.escopo, p.escopo_id)


def _geral_visivel(p: Publicacao) -> bool:
    return p.escopo == "geral" or p.replicar_geral


def pode_ver(s: SessaoORM, usuario: Usuario, p: Publicacao) -> bool:
    """Quem enxerga a publicação, conforme a situação dela."""
    if p.status == P_EXCLUIDA:
        return False
    if usuario.equipe_moderacao or p.autor_id == usuario.id:
        return True
    if p.status == P_PUBLICADA:
        return _geral_visivel(p) or escopos.pode_ler(s, usuario, p.escopo, p.escopo_id)
    if p.status == P_OCULTA:
        return eh_moderador(s, usuario, p)
    return False  # em análise / rejeitada: só autor e admin


def _pode_comentar_em(s: SessaoORM, usuario: Usuario, p: Publicacao) -> bool:
    return p.status == P_PUBLICADA and (_geral_visivel(p) or escopos.pode_ler(s, usuario, p.escopo, p.escopo_id))


# ---------------------------------------------------------------- criar, editar, excluir


def criar(
    s: SessaoORM,
    autor: Usuario,
    escopo: str,
    escopo_id: int | None,
    texto: str,
    latitude: float | None = None,
    longitude: float | None = None,
    local_nome: str | None = None,
    replicar_geral: bool = False,
    arquivos: list[bytes] | None = None,
    compartilhar_instagram: bool = False,
) -> Publicacao:
    """Cria em análise; quem chamou dispara `moderacao.processar_publicacao`. O local é obrigatório.
    `compartilhar_instagram` é o consentimento explícito do autor para a equipe poder publicar no Instagram do PlayGo."""
    planos.exigir(s, autor, "basico")
    texto = (texto or "").strip()
    arquivos = arquivos or []
    if len(texto) > settings.max_texto_post:
        raise ErroNegocio(f"O texto passa de {settings.max_texto_post} caracteres.")
    if not texto and not arquivos:
        raise ErroNegocio("Escreva algo ou anexe uma foto ou vídeo.")
    if len(arquivos) > settings.max_midias_post:
        raise ErroNegocio(f"No máximo {settings.max_midias_post} fotos/vídeos por publicação.")
    if not escopos.pode_postar(s, autor, escopo, escopo_id):
        raise SemPermissao("Você não participa deste mural.")
    if _excesso(s, Publicacao, autor, settings.limite_posts_10min):
        raise ErroNegocio("Muitas publicações em pouco tempo. Aguarde alguns minutos.")
    if replicar_geral and not escopos.pode_replicar(s, escopo, escopo_id):
        raise ErroNegocio("Este mural não permite replicar no feed geral (atividade fechada ou comunidade restrita).")

    ctx = escopos.contexto(s, escopo, escopo_id)
    if ctx["latitude"] is not None:  # atividade/campeonato: o local é o do evento, sempre
        latitude, longitude, local_nome = ctx["latitude"], ctx["longitude"], ctx["local_nome"]
    elif latitude is None or longitude is None:
        raise ErroNegocio("Marque o local da publicação (use sua localização ou toque no mapa).")
    elif not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ErroNegocio("Localização inválida.")
    local_nome = (local_nome or "").strip()[:200] or "Local marcado no mapa"

    recebidas = [midia.processar(d) for d in arquivos]  # valida tudo antes de gravar qualquer coisa
    if compartilhar_instagram:
        from . import instagram

        if not instagram.ativo(s):
            raise ErroNegocio("O compartilhamento no Instagram do PlayGo não está ativo.")
        if not (escopo == "geral" or replicar_geral):
            raise ErroNegocio("Só publicações do feed geral (ou replicadas nele) podem ir para o Instagram do PlayGo.")
        if not recebidas:
            raise ErroNegocio("Para ir ao Instagram a publicação precisa de uma foto ou um vídeo.")
    p = Publicacao(autor_id=autor.id, escopo=escopo, escopo_id=escopo_id, texto=texto, latitude=latitude, longitude=longitude, local_nome=local_nome, replicar_geral=replicar_geral)
    if compartilhar_instagram:
        p.instagram_status, p.instagram_consentimento_em = "aguardando", agora()
    gravados: list[str | None] = []
    try:
        for rec in recebidas:
            arq, mini, sha = midia.salvar(rec)
            gravados += [arq, mini]
            p.midias.append(Midia(tipo=rec.tipo, arquivo=arq, miniatura=mini, mime=rec.mime, tamanho=len(rec.dados), sha256=sha))
        s.add(p)
        s.flush()
        auditoria.registrar(s, autor.id, "publicacao_criar", "publicacao", p.id, escopo=escopo, escopo_id=escopo_id, midias=len(recebidas), replicar_geral=replicar_geral, instagram=compartilhar_instagram)
        s.commit()
    except Exception:
        s.rollback()
        midia.remover_arquivos(*gravados)
        raise
    return p


def editar(s: SessaoORM, publicacao_id: int, usuario: Usuario, texto: str) -> Publicacao:
    """Só o autor edita — nem o admin. A versão anterior é guardada e o texto novo passa de novo pela análise."""
    p = obter(s, publicacao_id)
    if p.autor_id != usuario.id:
        raise SemPermissao("Só quem publicou pode editar. Moderadores e administradores podem apenas ocultar.")
    if p.status not in (P_PUBLICADA, P_EM_ANALISE):
        raise ErroNegocio("Esta publicação não pode mais ser editada.")
    texto = (texto or "").strip()
    if not texto and not p.midias:
        raise ErroNegocio("O texto não pode ficar vazio.")
    if len(texto) > settings.max_texto_post:
        raise ErroNegocio(f"O texto passa de {settings.max_texto_post} caracteres.")
    if texto == p.texto:
        return p
    s.add(PublicacaoVersao(publicacao_id=p.id, texto=p.texto))
    p.texto, p.editado_em, p.status, p.motivo_analise = texto, agora(), P_EM_ANALISE, None
    auditoria.registrar(s, usuario.id, "publicacao_editar", "publicacao", p.id)
    s.commit()
    return p


def excluir(s: SessaoORM, publicacao_id: int, usuario: Usuario) -> None:
    p = obter(s, publicacao_id)
    if p.autor_id != usuario.id:
        raise SemPermissao("Só quem publicou pode excluir. Moderadores e administradores podem ocultar.")
    if not _janela_aberta(p.criado_em):
        raise ErroNegocio("A exclusão só é possível na primeira hora após publicar. Agora só um moderador ou administrador pode ocultar.")
    p.status, p.excluido_em = P_EXCLUIDA, agora()
    auditoria.registrar(s, usuario.id, "publicacao_excluir", "publicacao", p.id)
    s.commit()


# ---------------------------------------------------------------- moderação


def _papel_de(u: Usuario) -> str:
    """Quem ocultou, para saber quem pode desfazer: admin > moderacao (moderador geral) > moderador (do mural)."""
    return "admin" if u.admin else "moderacao" if u.moderador else "moderador"


def _pode_desfazer(u: Usuario, papel: str | None) -> bool:
    if u.admin:
        return True
    if papel == "admin":
        return False
    if papel == "moderacao":
        return u.equipe_moderacao
    return True


def _resolver_denuncias(s: SessaoORM, tipo: str, alvo_id: int, procedente: bool, por: Usuario) -> None:
    for d in s.scalars(select(Denuncia).where(Denuncia.alvo_tipo == tipo, Denuncia.alvo_id == alvo_id, Denuncia.status == "aberta")):
        d.status, d.resolvida_em, d.resolvida_por = ("procedente" if procedente else "improcedente"), agora(), por.id


def ocultar(s: SessaoORM, publicacao_id: int, usuario: Usuario, motivo: str = "") -> Publicacao:
    p = obter(s, publicacao_id)
    if not eh_moderador(s, usuario, p):
        raise SemPermissao("Só moderadores deste mural e administradores podem ocultar.")
    if p.status == P_OCULTA:
        return p
    papel = _papel_de(usuario)
    p.status, p.oculta_por_id, p.oculta_por_papel, p.oculta_motivo, p.oculta_em = P_OCULTA, usuario.id, papel, (motivo or "").strip()[:500] or None, agora()
    _resolver_denuncias(s, "publicacao", p.id, True, usuario)
    if p.autor_id != usuario.id:
        notificacoes.avisar(s, p.autor_id, "moderacao", "Uma publicação sua foi ocultada", (motivo or "Viola os termos de uso.")[:200], None, None, f"oculta:p:{p.id}")
    auditoria.registrar(s, usuario.id, "publicacao_ocultar", "publicacao", p.id, papel=papel, motivo=motivo)
    s.commit()
    return p


def restaurar(s: SessaoORM, publicacao_id: int, usuario: Usuario) -> Publicacao:
    p = obter(s, publicacao_id)
    if p.status != P_OCULTA:
        return p
    if not _pode_desfazer(usuario, p.oculta_por_papel):
        raise SemPermissao("Esta publicação foi ocultada pela administração ou moderação geral; só quem integra essa equipe pode restaurá-la.")
    if not eh_moderador(s, usuario, p):
        raise SemPermissao("Só moderadores deste mural e administradores podem restaurar.")
    p.status = P_PUBLICADA
    p.oculta_por_id = p.oculta_por_papel = p.oculta_motivo = p.oculta_em = None
    _resolver_denuncias(s, "publicacao", p.id, False, usuario)
    auditoria.registrar(s, usuario.id, "publicacao_restaurar", "publicacao", p.id)
    s.commit()
    return p


def decidir_analise(s: SessaoORM, tipo: str, alvo_id: int, admin: Usuario, aprovar: bool) -> None:
    """O administrador decide o que a pré-análise (ou as denúncias) segurou: libera para todos ou rejeita."""
    if not admin.equipe_moderacao:
        raise SemPermissao("Só a equipe de moderação (administradores e moderadores gerais) avalia a fila.")
    alvo = s.get(Publicacao if tipo == "publicacao" else Comentario, alvo_id)
    if alvo is None:
        raise NaoEncontrado("Conteúdo não encontrado.")
    if alvo.status != P_EM_ANALISE:
        raise ErroNegocio("Este conteúdo não está mais em análise.")
    alvo.status = P_PUBLICADA if aprovar else P_REJEITADA
    if aprovar:
        alvo.motivo_analise = None
    _resolver_denuncias(s, tipo, alvo.id, not aprovar, admin)
    rotulo = "publicação" if tipo == "publicacao" else "comentário"
    notificacoes.avisar(
        s, alvo.autor_id, "moderacao",
        f"Seu {rotulo} foi aprovado e já está visível" if aprovar else f"Seu {rotulo} não foi aprovado",
        "" if aprovar else "Ele viola os termos de uso e não será exibido. Se discordar, peça revisão pelo contato da Política de Privacidade.",
        None, None, f"decisao:{tipo[0]}:{alvo.id}:{alvo.status}",
    )
    auditoria.registrar(s, admin.id, "analise_aprovar" if aprovar else "analise_rejeitar", tipo, alvo.id)
    s.commit()


# ---------------------------------------------------------------- denúncias


def denunciar(s: SessaoORM, usuario: Usuario, tipo: str, alvo_id: int, motivo: str, detalhe: str = "") -> Denuncia:
    if tipo not in ("publicacao", "comentario"):
        raise ErroNegocio("Tipo de denúncia inválido.")
    if motivo not in MOTIVOS_DENUNCIA:
        raise ErroNegocio("Escolha o motivo da denúncia.")
    if tipo == "publicacao":
        alvo = obter(s, alvo_id)
        p = alvo
    else:
        alvo = s.get(Comentario, alvo_id)
        if alvo is None or alvo.status == P_EXCLUIDA:
            raise NaoEncontrado("Comentário não encontrado.")
        p = obter(s, alvo.publicacao_id)
    if alvo.autor_id == usuario.id:
        raise ErroNegocio("Você não pode denunciar o próprio conteúdo.")
    if not pode_ver(s, usuario, p):
        raise SemPermissao("Você não tem acesso a este conteúdo.")
    if s.scalar(select(Denuncia.id).where(Denuncia.alvo_tipo == tipo, Denuncia.alvo_id == alvo_id, Denuncia.denunciante_id == usuario.id)):
        raise ErroNegocio("Você já denunciou este conteúdo.")
    d = Denuncia(alvo_tipo=tipo, alvo_id=alvo_id, denunciante_id=usuario.id, motivo=motivo, detalhe=(detalhe or "").strip()[:500] or None)
    s.add(d)
    s.flush()
    auditoria.registrar(s, usuario.id, "denuncia_criar", tipo, alvo_id, motivo=motivo)
    # Quem cuida do mural fica sabendo; muitas denúncias seguram o conteúdo para o admin
    for m in escopos.moderadores(s, p.escopo, p.escopo_id) if p.escopo != "geral" else []:
        if m.id != usuario.id:
            notificacoes.avisar(s, m.id, "moderacao", f"🚩 {'Publicação' if tipo == 'publicacao' else 'Comentário'} denunciado", MOTIVOS_DENUNCIA[motivo], escopos.contexto(s, p.escopo, p.escopo_id)["link"], None, f"den:{tipo[0]}:{alvo_id}:{m.id}")
    abertas = s.scalar(select(func.count()).select_from(Denuncia).where(Denuncia.alvo_tipo == tipo, Denuncia.alvo_id == alvo_id, Denuncia.status == "aberta")) or 0
    if abertas >= settings.denuncias_para_analise and alvo.status == P_PUBLICADA:
        alvo.status, alvo.motivo_analise = P_EM_ANALISE, f"{abertas} denúncias de usuários"
        from .moderacao import _avisar_fila

        _avisar_fila(s, "publicação" if tipo == "publicacao" else "comentário", alvo.id, alvo.autor, alvo.motivo_analise)
    s.commit()
    return d


# ---------------------------------------------------------------- comentários


def comentar(s: SessaoORM, usuario: Usuario, publicacao_id: int, texto: str) -> Comentario:
    planos.exigir(s, usuario, "basico")
    p = obter(s, publicacao_id)
    if not _pode_comentar_em(s, usuario, p):
        raise SemPermissao("Você não pode comentar nesta publicação.")
    texto = (texto or "").strip()
    if not texto:
        raise ErroNegocio("Escreva o comentário.")
    if len(texto) > MAX_TEXTO_COMENTARIO:
        raise ErroNegocio(f"O comentário passa de {MAX_TEXTO_COMENTARIO} caracteres.")
    if _excesso(s, Comentario, usuario, LIMITE_COMENTARIOS_10MIN):
        raise ErroNegocio("Muitos comentários em pouco tempo. Aguarde alguns minutos.")
    c = Comentario(publicacao_id=p.id, autor_id=usuario.id, texto=texto)
    s.add(c)
    s.flush()
    auditoria.registrar(s, usuario.id, "comentario_criar", "comentario", c.id, publicacao=p.id)
    s.commit()
    return c


def curtir(s: SessaoORM, usuario: Usuario, publicacao_id: int) -> dict:
    """Liga/desliga o ❤ da pessoa numa publicação que ela pode ver."""
    planos.exigir(s, usuario, "basico")
    p = obter(s, publicacao_id)
    if p.status != P_PUBLICADA or not pode_ver(s, usuario, p):
        raise SemPermissao("Você não pode curtir esta publicação.")
    existente = s.get(PublicacaoCurtida, (p.id, usuario.id))
    if existente is None:
        s.add(PublicacaoCurtida(publicacao_id=p.id, usuario_id=usuario.id))
        curtiu = True
    else:
        s.delete(existente)
        curtiu = False
    s.flush()
    auditoria.registrar(s, usuario.id, "publicacao_curtir" if curtiu else "publicacao_descurtir", "publicacao", p.id)
    s.commit()
    return {"curtiu": curtiu, "n_curtidas": contar_curtidas(s, [p.id]).get(p.id, 0)}


def contar_curtidas(s: SessaoORM, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict(s.execute(select(PublicacaoCurtida.publicacao_id, func.count()).where(PublicacaoCurtida.publicacao_id.in_(ids)).group_by(PublicacaoCurtida.publicacao_id)).all())


def _minhas_curtidas(s: SessaoORM, usuario: Usuario, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return set(s.scalars(select(PublicacaoCurtida.publicacao_id).where(PublicacaoCurtida.usuario_id == usuario.id, PublicacaoCurtida.publicacao_id.in_(ids))))


def _comentario(s: SessaoORM, comentario_id: int) -> tuple[Comentario, Publicacao]:
    c = s.get(Comentario, comentario_id)
    if c is None or c.status == P_EXCLUIDA:
        raise NaoEncontrado("Comentário não encontrado.")
    return c, obter(s, c.publicacao_id)


def editar_comentario(s: SessaoORM, comentario_id: int, usuario: Usuario, texto: str) -> Comentario:
    c, _ = _comentario(s, comentario_id)
    if c.autor_id != usuario.id:
        raise SemPermissao("Só quem comentou pode editar.")
    if c.status not in (P_PUBLICADA, P_EM_ANALISE):
        raise ErroNegocio("Este comentário não pode mais ser editado.")
    texto = (texto or "").strip()
    if not texto or len(texto) > MAX_TEXTO_COMENTARIO:
        raise ErroNegocio("Escreva o comentário (até 1.000 caracteres).")
    if texto == c.texto:
        return c
    s.add(ComentarioVersao(comentario_id=c.id, texto=c.texto))
    c.texto, c.editado_em, c.status, c.motivo_analise = texto, agora(), P_EM_ANALISE, None
    auditoria.registrar(s, usuario.id, "comentario_editar", "comentario", c.id)
    s.commit()
    return c


def excluir_comentario(s: SessaoORM, comentario_id: int, usuario: Usuario) -> None:
    c, _ = _comentario(s, comentario_id)
    if c.autor_id != usuario.id:
        raise SemPermissao("Só quem comentou pode excluir.")
    if not _janela_aberta(c.criado_em):
        raise ErroNegocio("A exclusão só é possível na primeira hora após comentar. Agora só um moderador ou administrador pode ocultar.")
    c.status, c.excluido_em = P_EXCLUIDA, agora()
    auditoria.registrar(s, usuario.id, "comentario_excluir", "comentario", c.id)
    s.commit()


def ocultar_comentario(s: SessaoORM, comentario_id: int, usuario: Usuario, motivo: str = "") -> Comentario:
    c, p = _comentario(s, comentario_id)
    if not eh_moderador(s, usuario, p):
        raise SemPermissao("Só moderadores deste mural e administradores podem ocultar.")
    if c.status != P_OCULTA:
        papel = _papel_de(usuario)
        c.status, c.oculta_por_id, c.oculta_por_papel, c.oculta_motivo = P_OCULTA, usuario.id, papel, (motivo or "").strip()[:500] or None
        _resolver_denuncias(s, "comentario", c.id, True, usuario)
        if c.autor_id != usuario.id:
            notificacoes.avisar(s, c.autor_id, "moderacao", "Um comentário seu foi ocultado", (motivo or "Viola os termos de uso.")[:200], None, None, f"oculta:c:{c.id}")
        auditoria.registrar(s, usuario.id, "comentario_ocultar", "comentario", c.id, papel=papel, motivo=motivo)
        s.commit()
    return c


def restaurar_comentario(s: SessaoORM, comentario_id: int, usuario: Usuario) -> Comentario:
    c, p = _comentario(s, comentario_id)
    if c.status != P_OCULTA:
        return c
    if not _pode_desfazer(usuario, c.oculta_por_papel):
        raise SemPermissao("Ocultado pela administração ou moderação geral; só quem integra essa equipe pode restaurar.")
    if not eh_moderador(s, usuario, p):
        raise SemPermissao("Só moderadores deste mural e administradores podem restaurar.")
    c.status = P_PUBLICADA
    c.oculta_por_id = c.oculta_por_papel = c.oculta_motivo = None
    _resolver_denuncias(s, "comentario", c.id, False, usuario)
    auditoria.registrar(s, usuario.id, "comentario_restaurar", "comentario", c.id)
    s.commit()
    return c


# ---------------------------------------------------------------- leitura (feeds)


def _contagens(s: SessaoORM, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict(s.execute(select(Comentario.publicacao_id, func.count()).where(Comentario.publicacao_id.in_(ids), Comentario.status == P_PUBLICADA).group_by(Comentario.publicacao_id)).all())


def serializar_comentario(s: SessaoORM, c: Comentario, usuario: Usuario, p: Publicacao) -> dict:
    n = agora()
    mod = eh_moderador(s, usuario, p)
    return {
        "id": c.id,
        "autor": {"id": c.autor_id, "usuario": c.autor.usuario, "arroba": c.autor.arroba, "iniciais": c.autor.iniciais, "foto_url": c.autor.foto_url},
        "texto": c.texto,
        "status": c.status,
        "quando": ser.texto_quando(c.criado_em, n),
        "editado": texto_editado(c.editado_em),
        "motivo_analise": c.motivo_analise if (usuario.equipe_moderacao or c.autor_id == usuario.id) and c.status == P_EM_ANALISE else None,
        "oculta_motivo": c.oculta_motivo if mod or c.autor_id == usuario.id else None,
        "oculta_por_papel": c.oculta_por_papel,
        "minha": c.autor_id == usuario.id,
        "pode_editar": c.autor_id == usuario.id and c.status in (P_PUBLICADA, P_EM_ANALISE),
        "pode_excluir": c.autor_id == usuario.id and _janela_aberta(c.criado_em),
        "pode_ocultar": mod and c.status in (P_PUBLICADA, P_EM_ANALISE),
        "pode_restaurar": mod and c.status == P_OCULTA and _pode_desfazer(usuario, c.oculta_por_papel),
        "pode_denunciar": c.autor_id != usuario.id and c.status == P_PUBLICADA,
    }


def comentarios(s: SessaoORM, usuario: Usuario, publicacao_id: int, limite: int = 100) -> list[dict]:
    p = obter(s, publicacao_id)
    if not pode_ver(s, usuario, p):
        raise SemPermissao("Você não tem acesso a esta publicação.")
    mod = eh_moderador(s, usuario, p)
    q = select(Comentario).where(Comentario.publicacao_id == p.id, Comentario.status != P_EXCLUIDA)
    cond = [Comentario.status == P_PUBLICADA, Comentario.autor_id == usuario.id]
    if mod:
        cond.append(Comentario.status == P_OCULTA)
    q = q.where(or_(*cond)).order_by(Comentario.id).limit(limite)
    return [serializar_comentario(s, c, usuario, p) for c in s.scalars(q).unique()]


def serializar(
    s: SessaoORM, p: Publicacao, usuario: Usuario, ctx: dict | None = None, contagens: dict | None = None, centro: tuple[float, float] | None = None,
    curtidas: tuple[dict, set] | None = None,
) -> dict:
    n = agora()
    if curtidas is None:
        curtidas = (contar_curtidas(s, [p.id]), _minhas_curtidas(s, usuario, [p.id]))
    ctx = ctx if ctx is not None else {}
    chave = (p.escopo, p.escopo_id)
    if chave not in ctx:
        try:
            ctx[chave] = escopos.contexto(s, p.escopo, p.escopo_id)
        except NaoEncontrado:
            ctx[chave] = {"titulo": "(removido)", "link": None}
    esc = ctx[chave]
    mod = eh_moderador(s, usuario, p)
    dist = haversine(centro[0], centro[1], p.latitude, p.longitude) if centro else None
    dono = p.autor_id == usuario.id
    visiveis_midia = pode_ver(s, usuario, p)
    return {
        "id": p.id,
        "instagram": (
            {"status": p.instagram_status, "link": p.instagram_permalink if p.instagram_status == "publicada" else None}
            if p.instagram_status and (p.instagram_status == "publicada" or dono or usuario.equipe_moderacao) else None
        ),
        "autor": {"id": p.autor_id, "usuario": p.autor.usuario, "arroba": p.autor.arroba, "iniciais": p.autor.iniciais, "foto_url": p.autor.foto_url},
        "escopo": p.escopo,
        "escopo_id": p.escopo_id,
        "escopo_titulo": esc["titulo"],
        "escopo_link": esc["link"],
        "replicada": p.replicar_geral and p.escopo != "geral",
        "texto": p.texto,
        "local": {"nome": p.local_nome, "latitude": p.latitude, "longitude": p.longitude, "distancia": formatar_km(dist), "distancia_km": round(dist, 2) if dist is not None else None},
        "midias": [{"id": m.id, "tipo": m.tipo, "url": midia.url(m.id, usuario.id), "miniatura": midia.url(m.id, usuario.id, True) if m.miniatura else None} for m in p.midias] if visiveis_midia else [],
        "status": p.status,
        "motivo_analise": p.motivo_analise if (usuario.equipe_moderacao or dono) and p.status == P_EM_ANALISE else None,
        "oculta_motivo": p.oculta_motivo if (mod or dono) and p.status == P_OCULTA else None,
        "oculta_por_papel": p.oculta_por_papel,
        "quando": ser.texto_quando(p.criado_em, n),
        "criado_em": p.criado_em.isoformat(timespec="seconds"),
        "editado": texto_editado(p.editado_em),
        "n_comentarios": (contagens or {}).get(p.id, 0),
        "n_curtidas": curtidas[0].get(p.id, 0),
        "curtiu": p.id in curtidas[1],
        "minha": dono,
        "pode_editar": dono and p.status in (P_PUBLICADA, P_EM_ANALISE),
        "pode_excluir": dono and _janela_aberta(p.criado_em),
        "exclusao_ate": (p.criado_em + timedelta(minutes=settings.janela_exclusao_min)).isoformat(timespec="seconds") if dono and _janela_aberta(p.criado_em) else None,
        "pode_ocultar": mod and p.status in (P_PUBLICADA, P_EM_ANALISE),
        "pode_restaurar": mod and p.status == P_OCULTA and _pode_desfazer(usuario, p.oculta_por_papel),
        "pode_denunciar": not dono and p.status == P_PUBLICADA,
        "pode_comentar": _pode_comentar_em(s, usuario, p),
    }


def _pagina(s: SessaoORM, usuario: Usuario, consulta, antes_de: int | None, limite: int, centro) -> dict:
    if antes_de:
        consulta = consulta.where(Publicacao.id < antes_de)
    linhas = list(s.scalars(consulta.order_by(Publicacao.id.desc()).limit(limite + 1)).unique())
    mais = len(linhas) > limite
    linhas = linhas[:limite]
    ids = [p.id for p in linhas]
    contagens = _contagens(s, ids)
    curtidas = (contar_curtidas(s, ids), _minhas_curtidas(s, usuario, ids))
    ctx: dict = {}
    return {"itens": [serializar(s, p, usuario, ctx, contagens, centro, curtidas) for p in linhas], "proxima": linhas[-1].id if mais and linhas else None}


def mural(s: SessaoORM, usuario: Usuario, escopo: str, escopo_id: int | None, antes_de: int | None = None, limite: int = 20, centro=None) -> dict:
    """Mural de uma atividade, campeonato ou comunidade: restrito a quem participa dele."""
    if not escopos.pode_ler(s, usuario, escopo, escopo_id):
        raise SemPermissao("Entre na atividade, campeonato ou comunidade para ver o mural.")
    mod = escopos.eh_moderador(s, usuario, escopo, escopo_id)
    visivel = [Publicacao.status == P_PUBLICADA, and_(Publicacao.autor_id == usuario.id, Publicacao.status.in_((P_EM_ANALISE, P_OCULTA, P_REJEITADA)))]
    if mod:
        visivel.append(Publicacao.status == P_OCULTA)
    consulta = select(Publicacao).where(Publicacao.escopo == escopo, Publicacao.escopo_id == escopo_id, or_(*visivel))
    return _pagina(s, usuario, consulta, antes_de, limite, centro)


def feed_geral(s: SessaoORM, usuario: Usuario, lat: float | None = None, lng: float | None = None, raio_km: float | None = None, antes_de: int | None = None, limite: int = 20) -> dict:
    """Feed de todo o app: o que foi postado no geral + o que os autores replicaram de atividades/campeonatos/comunidades públicos."""
    visivel = [Publicacao.status == P_PUBLICADA, and_(Publicacao.autor_id == usuario.id, Publicacao.status.in_((P_EM_ANALISE, P_REJEITADA)))]
    if usuario.equipe_moderacao:
        visivel.append(Publicacao.status == P_OCULTA)
    consulta = select(Publicacao).where(or_(Publicacao.escopo == "geral", Publicacao.replicar_geral), or_(*visivel))
    centro = (lat, lng) if lat is not None and lng is not None else None
    if centro and raio_km:
        consulta = consulta.where(caixa_sql(Publicacao.latitude, Publicacao.longitude, lat, lng, raio_km), distancia_sql(Publicacao.latitude, Publicacao.longitude, lat, lng) <= raio_km)
    return _pagina(s, usuario, consulta, antes_de, limite, centro)


def detalhe(s: SessaoORM, usuario: Usuario, publicacao_id: int) -> dict:
    p = obter(s, publicacao_id)
    if not pode_ver(s, usuario, p):
        raise SemPermissao("Você não tem acesso a esta publicação.")
    d = serializar(s, p, usuario, None, _contagens(s, [p.id]))
    d["comentarios"] = comentarios(s, usuario, p.id)
    return d


# ---------------------------------------------------------------- fila do administrador


def fila(s: SessaoORM, admin: Usuario) -> dict:
    if not admin.equipe_moderacao:
        raise SemPermissao("Só a equipe de moderação acessa a fila.")
    n = agora()
    itens = []
    ctx: dict = {}
    for p in s.scalars(select(Publicacao).where(Publicacao.status == P_EM_ANALISE, Publicacao.motivo_analise.is_not(None)).order_by(Publicacao.id)).unique():
        d = serializar(s, p, admin, ctx)
        d["tipo"] = "publicacao"
        d["denuncias"] = _denuncias_de(s, "publicacao", p.id)
        itens.append(d)
    for c in s.scalars(select(Comentario).where(Comentario.status == P_EM_ANALISE, Comentario.motivo_analise.is_not(None)).order_by(Comentario.id)).unique():
        p = s.get(Publicacao, c.publicacao_id)
        itens.append({
            "tipo": "comentario", "id": c.id, "texto": c.texto, "autor": {"arroba": c.autor.arroba, "iniciais": c.autor.iniciais},
            "quando": ser.texto_quando(c.criado_em, n), "motivo_analise": c.motivo_analise, "escopo_titulo": escopos.contexto(s, p.escopo, p.escopo_id)["titulo"] if p else "",
            "publicacao_id": c.publicacao_id, "denuncias": _denuncias_de(s, "comentario", c.id), "midias": [],
        })
    return {"total": len(itens), "itens": itens}


def _denuncias_de(s: SessaoORM, tipo: str, alvo_id: int) -> list[dict]:
    return [{"motivo": MOTIVOS_DENUNCIA.get(d.motivo, d.motivo), "detalhe": d.detalhe} for d in s.scalars(select(Denuncia).where(Denuncia.alvo_tipo == tipo, Denuncia.alvo_id == alvo_id, Denuncia.status == "aberta"))]


def contar_fila(s: SessaoORM) -> int:
    return (s.scalar(select(func.count()).select_from(Publicacao).where(Publicacao.status == P_EM_ANALISE, Publicacao.motivo_analise.is_not(None))) or 0) + (s.scalar(select(func.count()).select_from(Comentario).where(Comentario.status == P_EM_ANALISE, Comentario.motivo_analise.is_not(None))) or 0)
