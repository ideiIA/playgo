"""API do mural (publicações, comentários, moderação), comunidades, convites e privacidade.
Mesmos serviços do site; autenticação por token Bearer ou cookie, como o resto de /api/v1."""

import secrets

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from .. import (
    administracao,
    comunidades,
    contas,
    convites,
    escopos,
    instagram,
    midia,
    moderacao,
    privacidade,
    publicacoes,
)
from ..config import settings
from ..db import agora
from ..deps import AtualApi, Sessao
from ..erros import ErroNegocio

router = APIRouter(prefix="/api/v1")


def apos(tarefas: BackgroundTasks, funcao, alvo_id: int) -> None:
    """Roda a análise de moderação depois da resposta. No serverless a função pode ser congelada assim que a resposta sai,
    então lá a análise roda dentro da própria requisição (o mural já espera o resultado)."""
    if settings.em_serverless:
        funcao(alvo_id)
    else:
        tarefas.add_task(funcao, alvo_id)


class TextoIn(BaseModel):
    texto: str


class MotivoIn(BaseModel):
    motivo: str = ""


class DenunciaIn(BaseModel):
    motivo: str
    detalhe: str = ""


class DecisaoIn(BaseModel):
    aprovar: bool


class UsuarioAlvoIn(BaseModel):
    usuario: str


class ComunidadeIn(BaseModel):
    nome: str
    descricao: str | None = None
    regras: str | None = None
    cidade: str | None = None
    visibilidade: str = "publica"


class PedidoIn(BaseModel):
    aprovar: bool


class PapelIn(BaseModel):
    papel: str


class RespostaIn(BaseModel):
    aceitar: bool


class SenhaIn(BaseModel):
    senha: str


def _alvo(s, arroba: str):
    alvo = contas.por_usuario(s, arroba)
    if alvo is None:
        raise ErroNegocio("Não encontramos esse @usuario.")
    return alvo


# ---------------------------------------------------------------- murais


@router.get("/mural/geral")
def feed_geral(u: AtualApi, s: Sessao, lat: float | None = None, lng: float | None = None, raio: float | None = None, antes_de: int | None = None):
    """Feed de todo o app. Com lat/lng, mostra a distância de cada publicação; com `raio`, só as próximas."""
    res = publicacoes.feed_geral(s, u, lat, lng, raio or None, antes_de)
    res["instagram"] = instagram.ativo(s)  # o formulário só mostra "Compartilhar no Instagram" com a conta conectada
    return res


@router.get("/mural/{escopo}/{escopo_id}")
def mural(escopo: str, escopo_id: int, u: AtualApi, s: Sessao, lat: float | None = None, lng: float | None = None, antes_de: int | None = None):
    centro = (lat, lng) if lat is not None and lng is not None else None
    res = publicacoes.mural(s, u, escopo, escopo_id, antes_de, centro=centro)
    ctx = escopos.contexto(s, escopo, escopo_id)
    res["instagram"] = instagram.ativo(s)
    res["escopo"] = {"tipo": escopo, "id": escopo_id, "titulo": ctx["titulo"], "link": ctx["link"], "local_fixo": ctx["latitude"] is not None, "pode_replicar": escopos.pode_replicar(s, escopo, escopo_id), "sou_moderador": escopos.eh_moderador(s, u, escopo, escopo_id)}
    return res


@router.post("/publicacoes", status_code=201)
def criar_publicacao(
    tarefas: BackgroundTasks, u: AtualApi, s: Sessao, escopo: str = Form("geral"), escopo_id: int | None = Form(None), texto: str = Form(""),
    latitude: float | None = Form(None), longitude: float | None = Form(None), local_nome: str = Form(""), replicar_geral: bool = Form(False),
    arquivos: list[UploadFile] = File(default=[]), compartilhar_instagram: bool = Form(False),
):
    """multipart/form-data. O local é obrigatório (vem do evento nas atividades e campeonatos). Entra 'em análise'
    e a pré-análise por IA roda em segundo plano."""
    dados = [midia.ler_upload(a) for a in arquivos if a.filename]
    p = publicacoes.criar(s, u, escopo, escopo_id, texto, latitude, longitude, local_nome, replicar_geral, dados, compartilhar_instagram)
    apos(tarefas, moderacao.processar_publicacao, p.id)
    return publicacoes.serializar(s, p, u)


@router.get("/publicacoes/{publicacao_id}")
def ver_publicacao(publicacao_id: int, u: AtualApi, s: Sessao):
    return publicacoes.detalhe(s, u, publicacao_id)


@router.patch("/publicacoes/{publicacao_id}")
def editar_publicacao(publicacao_id: int, corpo: TextoIn, tarefas: BackgroundTasks, u: AtualApi, s: Sessao):
    p = publicacoes.editar(s, publicacao_id, u, corpo.texto)
    apos(tarefas, moderacao.processar_publicacao, p.id)
    return publicacoes.serializar(s, p, u)


@router.delete("/publicacoes/{publicacao_id}")
def excluir_publicacao(publicacao_id: int, u: AtualApi, s: Sessao):
    publicacoes.excluir(s, publicacao_id, u)
    return {"ok": True}


@router.post("/publicacoes/{publicacao_id}/ocultar")
def ocultar_publicacao(publicacao_id: int, corpo: MotivoIn, u: AtualApi, s: Sessao):
    return publicacoes.serializar(s, publicacoes.ocultar(s, publicacao_id, u, corpo.motivo), u)


@router.post("/publicacoes/{publicacao_id}/restaurar")
def restaurar_publicacao(publicacao_id: int, u: AtualApi, s: Sessao):
    return publicacoes.serializar(s, publicacoes.restaurar(s, publicacao_id, u), u)


@router.post("/publicacoes/{publicacao_id}/denunciar", status_code=201)
def denunciar_publicacao(publicacao_id: int, corpo: DenunciaIn, u: AtualApi, s: Sessao):
    publicacoes.denunciar(s, u, "publicacao", publicacao_id, corpo.motivo, corpo.detalhe)
    return {"ok": True}


@router.get("/denuncias/motivos")
def motivos_de_denuncia():
    return publicacoes.MOTIVOS_DENUNCIA


# ---------------------------------------------------------------- comentários


@router.post("/publicacoes/{publicacao_id}/curtir")
def curtir(publicacao_id: int, u: AtualApi, s: Sessao):
    return publicacoes.curtir(s, u, publicacao_id)


@router.get("/publicacoes/{publicacao_id}/comentarios")
def listar_comentarios(publicacao_id: int, u: AtualApi, s: Sessao):
    return publicacoes.comentarios(s, u, publicacao_id)


@router.post("/publicacoes/{publicacao_id}/comentarios", status_code=201)
def comentar(publicacao_id: int, corpo: TextoIn, tarefas: BackgroundTasks, u: AtualApi, s: Sessao):
    c = publicacoes.comentar(s, u, publicacao_id, corpo.texto)
    apos(tarefas, moderacao.processar_comentario, c.id)
    return publicacoes.serializar_comentario(s, c, u, publicacoes.obter(s, publicacao_id))


@router.patch("/comentarios/{comentario_id}")
def editar_comentario(comentario_id: int, corpo: TextoIn, tarefas: BackgroundTasks, u: AtualApi, s: Sessao):
    c = publicacoes.editar_comentario(s, comentario_id, u, corpo.texto)
    apos(tarefas, moderacao.processar_comentario, c.id)
    return publicacoes.serializar_comentario(s, c, u, publicacoes.obter(s, c.publicacao_id))


@router.delete("/comentarios/{comentario_id}")
def excluir_comentario(comentario_id: int, u: AtualApi, s: Sessao):
    publicacoes.excluir_comentario(s, comentario_id, u)
    return {"ok": True}


@router.post("/comentarios/{comentario_id}/ocultar")
def ocultar_comentario(comentario_id: int, corpo: MotivoIn, u: AtualApi, s: Sessao):
    c = publicacoes.ocultar_comentario(s, comentario_id, u, corpo.motivo)
    return publicacoes.serializar_comentario(s, c, u, publicacoes.obter(s, c.publicacao_id))


@router.post("/comentarios/{comentario_id}/restaurar")
def restaurar_comentario(comentario_id: int, u: AtualApi, s: Sessao):
    c = publicacoes.restaurar_comentario(s, comentario_id, u)
    return publicacoes.serializar_comentario(s, c, u, publicacoes.obter(s, c.publicacao_id))


@router.post("/comentarios/{comentario_id}/denunciar", status_code=201)
def denunciar_comentario(comentario_id: int, corpo: DenunciaIn, u: AtualApi, s: Sessao):
    publicacoes.denunciar(s, u, "comentario", comentario_id, corpo.motivo, corpo.detalhe)
    return {"ok": True}


# ---------------------------------------------------------------- fila do administrador


@router.get("/moderacao/fila")
def fila(u: AtualApi, s: Sessao):
    return publicacoes.fila(s, u)


@router.get("/moderacao/contagem")
def contagem_fila(u: AtualApi, s: Sessao):
    return {"total": publicacoes.contar_fila(s) if u.equipe_moderacao else 0}


@router.post("/moderacao/{tipo}/{alvo_id}/decidir")
def decidir(tipo: str, alvo_id: int, corpo: DecisaoIn, u: AtualApi, s: Sessao):
    if tipo not in ("publicacao", "comentario"):
        raise HTTPException(404, "Tipo desconhecido.")
    publicacoes.decidir_analise(s, tipo, alvo_id, u, corpo.aprovar)
    return {"ok": True}


# ---------------------------------------------------------------- moderadores delegados e convites


@router.get("/escopos/{escopo}/{escopo_id}/moderadores")
def listar_moderadores(escopo: str, escopo_id: int, u: AtualApi, s: Sessao):
    escopos.validar(escopo, escopo_id)
    if not escopos.pode_ler(s, u, escopo, escopo_id):
        raise HTTPException(403, "Sem acesso.")
    return [{"usuario_id": m.id, "arroba": m.arroba, "iniciais": m.iniciais} for m in escopos.moderadores(s, escopo, escopo_id)]


@router.post("/escopos/{escopo}/{escopo_id}/moderadores", status_code=201)
def adicionar_moderador(escopo: str, escopo_id: int, corpo: UsuarioAlvoIn, u: AtualApi, s: Sessao):
    escopos.definir_moderador(s, escopo, escopo_id, u, _alvo(s, corpo.usuario), True)
    return {"ok": True}


@router.delete("/escopos/{escopo}/{escopo_id}/moderadores/{arroba}")
def remover_moderador(escopo: str, escopo_id: int, arroba: str, u: AtualApi, s: Sessao):
    escopos.definir_moderador(s, escopo, escopo_id, u, _alvo(s, arroba), False)
    return {"ok": True}


@router.post("/escopos/{escopo}/{escopo_id}/convites", status_code=201)
def convidar(escopo: str, escopo_id: int, corpo: UsuarioAlvoIn, u: AtualApi, s: Sessao):
    c = convites.convidar(s, escopo, escopo_id, u, _alvo(s, corpo.usuario))
    return {"id": c.id, "status": c.status}


@router.post("/escopos/{escopo}/{escopo_id}/link/renovar")
def renovar_link(escopo: str, escopo_id: int, u: AtualApi, s: Sessao):
    return {"link": f"/convite/{convites.renovar_link(s, escopo, escopo_id, u)}"}


@router.get("/convites")
def meus_convites(u: AtualApi, s: Sessao):
    return convites.pendentes(s, u)


@router.post("/convites/{convite_id}/responder")
def responder_convite(convite_id: int, corpo: RespostaIn, u: AtualApi, s: Sessao):
    return convites.responder(s, convite_id, u, corpo.aceitar)


@router.get("/convites/link/{token}")
def resumo_link(token: str, u: AtualApi, s: Sessao):
    return convites.resumo_do_link(s, token)


@router.post("/convites/link/{token}/entrar")
def entrar_por_link(token: str, u: AtualApi, s: Sessao):
    return convites.entrar_por_link(s, token, u)


# ---------------------------------------------------------------- comunidades


@router.get("/comunidades")
def listar_comunidades(u: AtualApi, s: Sessao, q: str = ""):
    return comunidades.listar(s, u, q)


@router.post("/comunidades", status_code=201)
def criar_comunidade(corpo: ComunidadeIn, u: AtualApi, s: Sessao):
    return comunidades.detalhe(s, comunidades.criar(s, u, **corpo.model_dump()), u)


@router.get("/comunidades/{comunidade_id}")
def ver_comunidade(comunidade_id: int, u: AtualApi, s: Sessao):
    return comunidades.detalhe(s, comunidades.obter(s, comunidade_id), u)


@router.post("/comunidades/{comunidade_id}/entrar")
def entrar_comunidade(comunidade_id: int, u: AtualApi, s: Sessao):
    comunidades.entrar(s, comunidade_id, u)
    return comunidades.detalhe(s, comunidades.obter(s, comunidade_id), u)


@router.post("/comunidades/{comunidade_id}/sair")
def sair_comunidade(comunidade_id: int, u: AtualApi, s: Sessao):
    comunidades.sair(s, comunidade_id, u)
    return {"ok": True}


@router.post("/comunidades/{comunidade_id}/pedidos/{usuario_id}")
def decidir_pedido(comunidade_id: int, usuario_id: int, corpo: PedidoIn, u: AtualApi, s: Sessao):
    comunidades.decidir_pedido(s, comunidade_id, usuario_id, u, corpo.aprovar)
    return comunidades.detalhe(s, comunidades.obter(s, comunidade_id), u)


@router.post("/comunidades/{comunidade_id}/membros/{usuario_id}/papel")
def definir_papel(comunidade_id: int, usuario_id: int, corpo: PapelIn, u: AtualApi, s: Sessao):
    comunidades.definir_papel(s, comunidade_id, usuario_id, u, corpo.papel)
    return comunidades.detalhe(s, comunidades.obter(s, comunidade_id), u)


@router.post("/comunidades/{comunidade_id}/encerrar")
def encerrar_comunidade(comunidade_id: int, u: AtualApi, s: Sessao):
    comunidades.encerrar(s, comunidade_id, u)
    return {"ok": True}


# ---------------------------------------------------------------- administração de perfis (só administrador)


class PerfilIn(BaseModel):
    perfil: str


@router.get("/admin/usuarios")
def admin_usuarios(u: AtualApi, s: Sessao, q: str = "", perfil: str = "", vencendo: bool = False):
    return {"itens": administracao.listar(s, u, q, perfil or None, vencendo=vencendo), "resumo": administracao.resumo(s, u)}


@router.post("/admin/usuarios/{usuario_id}/perfil")
def admin_definir_perfil(usuario_id: int, corpo: PerfilIn, u: AtualApi, s: Sessao):
    return administracao.definir_perfil(s, u, usuario_id, corpo.perfil)


# ---------------------------------------------------------------- privacidade (LGPD)


@router.get("/privacidade/meus-dados")
def meus_dados(u: AtualApi, s: Sessao):
    return privacidade.exportar(s, u)


@router.post("/privacidade/revogar-localizacao")
def revogar_localizacao(u: AtualApi, s: Sessao):
    contas.revogar_localizacao(s, u)
    return contas.dados_publicos(u)


@router.post("/privacidade/excluir-conta")
def excluir_conta(corpo: SenhaIn, u: AtualApi, s: Sessao):
    privacidade.excluir_conta(s, u, corpo.senha)
    return {"ok": True, "em": agora().isoformat(timespec="seconds")}


# ---------------------------------------------------------------- rotinas periódicas (Vercel Cron / Supabase pg_cron)


@router.get("/cron/ciclo")
def cron_ciclo(authorization: str = Header(default="")):
    """Lembretes, reforço de 'falta gente' e encerramento. Chamado por Vercel Cron (que envia `Authorization: Bearer $CRON_SECRET`)
    ou pelo pg_cron do Supabase. Sem segredo configurado, fica desligado."""
    from .. import agendador

    segredo = settings.segredo_cron
    if not segredo or not secrets.compare_digest(authorization, f"Bearer {segredo}"):
        raise HTTPException(403, "Não autorizado.")
    return agendador.ciclo()
