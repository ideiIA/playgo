"""Páginas do site para termos, cadastro completo, mural, comunidades, convites, moderação e privacidade."""

import json
import re

from fastapi import Form, Request
from fastapi.responses import RedirectResponse, Response

from .. import armazenamento, comunidades, contas, convites, midia, privacidade, publicacoes, termos
from ..deps import Atual, AtualLivre, Opcional, Sessao
from ..erros import NaoEncontrado
from ..models import Midia, Publicacao, Usuario
from .app import _sem_plano, app, pagina, templates, voltar

# ---------------------------------------------------------------- termos, política e cadastro completo


def _documento(request: Request, usuario, titulo: str, secoes: list[dict], aba_ativa: str):
    return templates.TemplateResponse(request, "termos.html", {"usuario": usuario, "titulo": titulo, "secoes": secoes, "versao": termos.VERSAO, "doc": aba_ativa})


@app.get("/termos")
def tela_termos(request: Request, usuario: Opcional):
    return _documento(request, usuario, "Termos de Uso", termos.termos_de_uso(), "termos")


@app.get("/politica-de-privacidade")
def tela_politica(request: Request, usuario: Opcional):
    return _documento(request, usuario, "Política de Privacidade", termos.politica_de_privacidade(), "politica")


@app.get("/termos/aceitar")
def tela_aceitar(request: Request, usuario: AtualLivre):
    if contas.pendencias(usuario) is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "aceitar_termos.html", {"usuario": usuario, "versao": termos.VERSAO, "declaracoes": termos.DECLARACOES, "erro": request.session.pop("erro", None), "sem_usuario": not usuario.usuario})


@app.post("/termos/aceitar")
def aceitar(request: Request, s: Sessao, usuario: AtualLivre, aceito_termos: str = Form("")):
    contas.aceitar_termos(s, usuario, bool(aceito_termos))
    return RedirectResponse("/cadastro/completar" if not usuario.usuario else request.session.pop("next", "/"), status_code=303)


@app.get("/cadastro/completar")
def tela_completar(request: Request, usuario: AtualLivre):
    if usuario.usuario:
        return RedirectResponse("/termos/aceitar" if contas.termos_pendentes(usuario) else "/", status_code=303)
    return templates.TemplateResponse(request, "completar_usuario.html", {"usuario": usuario, "erro": request.session.pop("erro", None)})


@app.post("/cadastro/completar")
def completar(request: Request, s: Sessao, usuario: AtualLivre, nome_usuario: str = Form("")):
    contas.definir_usuario(s, usuario, nome_usuario)
    return RedirectResponse("/termos/aceitar" if contas.termos_pendentes(usuario) else request.session.pop("next", "/"), status_code=303)


@app.post("/perfil/usuario")
def trocar_usuario(request: Request, s: Sessao, usuario: Atual, nome_usuario: str = Form("")):
    contas.definir_usuario(s, usuario, nome_usuario)
    request.session["ok"] = "Nome de usuário atualizado."
    return RedirectResponse("/perfil", status_code=303)


# ---------------------------------------------------------------- mural geral e moderação


@app.get("/mural")
def mural_geral(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "mural.html", "mural")


@app.get("/moderacao")
def tela_moderacao(request: Request, s: Sessao, usuario: Atual):
    if not usuario.equipe_moderacao:
        raise NaoEncontrado("Página não encontrada.")
    return pagina(request, s, usuario, "moderacao.html", "moderacao")


@app.get("/administracao")
def tela_administracao(request: Request, s: Sessao, usuario: Atual):
    if not usuario.admin:
        raise NaoEncontrado("Página não encontrada.")
    return pagina(request, s, usuario, "administracao.html", "administracao")


@app.get("/planos")
def tela_planos(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "planos.html", "planos")


# ---------------------------------------------------------------- comunidades


@app.get("/comunidades")
def listar_comunidades(request: Request, s: Sessao, usuario: Atual, q: str = ""):
    return pagina(request, s, usuario, "comunidades.html", "comunidades", c=comunidades.listar(s, usuario, q), busca=q)


@app.get("/comunidades/nova")
def nova_comunidade(request: Request, s: Sessao, usuario: Atual):
    if (r := _sem_plano(request, s, usuario, "comunidade")) is not None:
        return r
    return pagina(request, s, usuario, "comunidade_nova.html", "comunidades")


@app.post("/comunidades")
def criar_comunidade(
    s: Sessao, usuario: Atual, nome: str = Form(), descricao: str = Form(""), regras: str = Form(""), cidade: str = Form(""), visibilidade: str = Form("publica")
):
    c = comunidades.criar(s, usuario, nome, descricao or None, regras or None, cidade or None, visibilidade)
    return RedirectResponse(f"/comunidades/{c.id}", status_code=303)


@app.get("/comunidades/{comunidade_id}")
def ver_comunidade(comunidade_id: int, request: Request, s: Sessao, usuario: Atual):
    c = comunidades.obter(s, comunidade_id)
    return pagina(request, s, usuario, "comunidade.html", "comunidades", c=comunidades.detalhe(s, c, usuario))


@app.post("/comunidades/{comunidade_id}/entrar")
def entrar_comunidade(comunidade_id: int, request: Request, s: Sessao, usuario: Atual):
    m = comunidades.entrar(s, comunidade_id, usuario)
    request.session["ok"] = "Pedido enviado. Um moderador vai avaliar." if m.status == "pendente" else "Você entrou na comunidade."
    return RedirectResponse(f"/comunidades/{comunidade_id}", status_code=303)


@app.post("/comunidades/{comunidade_id}/sair")
def sair_comunidade(comunidade_id: int, s: Sessao, usuario: Atual):
    comunidades.sair(s, comunidade_id, usuario)
    return RedirectResponse("/comunidades", status_code=303)


@app.post("/comunidades/{comunidade_id}/pedidos/{usuario_id}")
def decidir_pedido(comunidade_id: int, usuario_id: int, s: Sessao, usuario: Atual, aprovar: str = Form("1")):
    comunidades.decidir_pedido(s, comunidade_id, usuario_id, usuario, aprovar == "1")
    return RedirectResponse(f"/comunidades/{comunidade_id}", status_code=303)


@app.post("/comunidades/{comunidade_id}/membros/{usuario_id}/papel")
def papel_membro(comunidade_id: int, usuario_id: int, s: Sessao, usuario: Atual, papel: str = Form()):
    comunidades.definir_papel(s, comunidade_id, usuario_id, usuario, papel)
    return RedirectResponse(f"/comunidades/{comunidade_id}", status_code=303)


# ---------------------------------------------------------------- convites e links


@app.get("/convites")
def tela_convites(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "convites.html", "", convites=convites.pendentes(s, usuario))


@app.post("/convites/{convite_id}/responder")
def responder_convite(convite_id: int, request: Request, s: Sessao, usuario: Atual, aceitar: str = Form("1")):
    r = convites.responder(s, convite_id, usuario, aceitar == "1")
    if r.get("escopo") == "atividade":
        return RedirectResponse(f"/atividades/{r['escopo_id']}", status_code=303)
    if r.get("escopo") == "comunidade":
        return RedirectResponse(f"/comunidades/{r['escopo_id']}", status_code=303)
    return RedirectResponse("/convites", status_code=303)


@app.get("/convite/{token}")
def abrir_link(token: str, request: Request, s: Sessao, usuario: Opcional):
    """Link de convite (estilo grupo de WhatsApp). Quem não está logado entra, volta e conclui."""
    if usuario is None:
        request.session["next"] = f"/convite/{token}"
        return RedirectResponse("/entrar", status_code=303)
    if contas.pendencias(usuario):
        request.session["next"] = f"/convite/{token}"
        return RedirectResponse("/cadastro/completar" if contas.pendencias(usuario) == "usuario" else "/termos/aceitar", status_code=303)
    return pagina(request, s, usuario, "convite_link.html", "", resumo=convites.resumo_do_link(s, token), token=token)


@app.post("/convite/{token}/entrar")
def entrar_pelo_link(token: str, request: Request, s: Sessao, usuario: Atual):
    r = convites.entrar_por_link(s, token, usuario)
    request.session["ok"] = "Pedido enviado ao organizador." if r["situacao"] == "pendente" else "Você entrou!"
    return RedirectResponse(f"/{'atividades' if r['tipo'] == 'atividade' else 'comunidades'}/{r['id']}", status_code=303)


@app.post("/escopos/{escopo}/{escopo_id}/link/renovar")
def renovar_link(escopo: str, escopo_id: int, request: Request, s: Sessao, usuario: Atual):
    convites.renovar_link(s, escopo, escopo_id, usuario)
    request.session["ok"] = "Novo link gerado. O anterior deixou de funcionar."
    return voltar(request)


# ---------------------------------------------------------------- privacidade (LGPD)


@app.get("/privacidade")
def central_de_privacidade(request: Request, s: Sessao, usuario: Atual):
    return pagina(request, s, usuario, "privacidade.html", "", versao=termos.VERSAO, aceite_em=usuario.termos_em)


@app.get("/privacidade/meus-dados.json")
def baixar_meus_dados(s: Sessao, usuario: Atual):
    conteudo = json.dumps(privacidade.exportar(s, usuario), ensure_ascii=False, indent=2)
    return Response(conteudo, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="meus-dados-playgo.json"'})


@app.post("/privacidade/revogar-localizacao")
def revogar_localizacao(request: Request, s: Sessao, usuario: Atual):
    contas.revogar_localizacao(s, usuario)
    request.session["ok"] = "Localização apagada do seu perfil e consentimento revogado."
    return RedirectResponse("/privacidade", status_code=303)


@app.post("/privacidade/excluir-conta")
def excluir_conta(request: Request, s: Sessao, usuario: Atual, senha: str = Form("")):
    privacidade.excluir_conta(s, usuario, senha)
    request.session.clear()
    return RedirectResponse("/entrar", status_code=303)


# ---------------------------------------------------------------- mídia das publicações (nunca em /static)


def _servir(midia_id: int, request: Request, s, t: str, miniatura: bool):
    uid = midia.usuario_do_token(t, midia_id) if t else request.session.get("usuario_id")
    usuario = s.get(Usuario, uid) if uid else None
    m = s.get(Midia, midia_id)
    p = s.get(Publicacao, m.publicacao_id) if m else None
    if usuario is None or not usuario.ativo or m is None or p is None or not publicacoes.pode_ver(s, usuario, p):
        raise NaoEncontrado("Arquivo não encontrado.")
    rel = m.miniatura if miniatura and m.miniatura else m.arquivo
    return armazenamento.servir(rel, "image/jpeg" if rel == m.miniatura else m.mime, "private, max-age=3600")


@app.get("/foto/{nome}")
def foto_de_perfil(nome: str):
    """Foto de perfil. O nome é aleatório (32 hex): quem não recebeu a URL não a adivinha."""
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", nome):
        raise NaoEncontrado("Foto não encontrada.")
    return armazenamento.servir(f"perfil/{nome}", "image/jpeg", "private, max-age=86400")


@app.get("/midia/{midia_id}")
def midia_original(midia_id: int, request: Request, s: Sessao, t: str = ""):
    return _servir(midia_id, request, s, t, False)


@app.get("/midia/{midia_id}/miniatura")
def midia_miniatura(midia_id: int, request: Request, s: Sessao, t: str = ""):
    return _servir(midia_id, request, s, t, True)
