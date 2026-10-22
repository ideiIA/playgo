"""Perfil de Personal / Instrutor (docs/especificacao-personal-escolinhas.md, RF-01 e seção 5.1).

Quem quer dar aulas cadastra o perfil, anexa os documentos legais e envia para análise; só depois de **aprovado** pela administração
o profissional publica ofertas e aparece na busca. Os documentos NUNCA são públicos: só o titular e a administração leem, por rota autenticada.
Toda decisão é auditada e avisa o profissional."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM
from sqlalchemy.orm import object_session

from . import armazenamento, auditoria, midia, notificacoes
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .geo import formatar_km
from .models import (
    DOC_APROVADO,
    DOC_PENDENTE,
    DOC_RECUSADO,
    PP_APROVADO,
    PP_EM_ANALISE,
    PP_RASCUNHO,
    PP_REPROVADO,
    PP_SUSPENSO,
    DocumentoProfissional,
    Modalidade,
    PerfilProfissional,
    Usuario,
)

TIPOS_DOCUMENTO = {
    "identidade": "Documento de identidade com foto",
    "registro_profissional": "Registro profissional (CREF ou federação)",
    "comprovante_endereco": "Comprovante de endereço",
    "certificado": "Diploma ou certificado",
}
STATUS_NOME = {PP_RASCUNHO: "Rascunho", PP_EM_ANALISE: "Em análise", PP_APROVADO: "Aprovado", PP_REPROVADO: "Recusado", PP_SUSPENSO: "Suspenso"}
CAMPOS = ("nome_profissional", "apresentacao", "registro_tipo", "registro_numero", "telefone", "whatsapp", "email_contato", "cidade", "regiao",
          "latitude", "longitude", "raio_atendimento_km", "modalidades", "especialidades")


def _limpo(v, tam: int | None = None) -> str | None:
    v = (v or "").strip()
    return (v[:tam] if tam else v) or None


def meu(s: SessaoORM, u: Usuario) -> PerfilProfissional | None:
    return s.scalar(select(PerfilProfissional).where(PerfilProfissional.usuario_id == u.id))


def obter(s: SessaoORM, perfil_id: int) -> PerfilProfissional:
    p = s.get(PerfilProfissional, perfil_id)
    if p is None:
        raise NaoEncontrado("Profissional não encontrado.")
    return p


def _exigir_meu(s: SessaoORM, u: Usuario) -> PerfilProfissional:
    p = meu(s, u)
    if p is None:
        raise NaoEncontrado("Crie primeiro o seu perfil profissional.")
    return p


def _modalidades_validas(s: SessaoORM, codigos: list[str]) -> list[str]:
    achados = set(s.scalars(select(Modalidade.codigo).where(Modalidade.codigo.in_(codigos or ["-"]))))
    return sorted(achados)


# ---------------------------------------------------------------- cadastro do próprio profissional


def salvar(s: SessaoORM, u: Usuario, dados: dict) -> PerfilProfissional:
    """Cria (rascunho) ou atualiza o perfil. Mudar o registro profissional depois de aprovado devolve o perfil para análise."""
    p = meu(s, u)
    novo = p is None
    nome = _limpo(dados.get("nome_profissional"), 120) or (p.nome_profissional if p else u.nome)
    if len(nome) < 2:
        raise ErroNegocio("Informe o nome profissional.")
    if p is None:
        p = PerfilProfissional(usuario_id=u.id, nome_profissional=nome, status=PP_RASCUNHO, modalidades=[])
        s.add(p)
    registro_antes = (p.registro_tipo, p.registro_numero)
    p.nome_profissional = nome
    for campo, tam in (("apresentacao", 4000), ("registro_tipo", 30), ("registro_numero", 40), ("telefone", 40), ("whatsapp", 40), ("email_contato", 200), ("cidade", 100), ("regiao", 120), ("especialidades", 2000)):
        if campo in dados:
            setattr(p, campo, _limpo(dados.get(campo), tam))
    if "latitude" in dados or "longitude" in dados:
        lat, lng = dados.get("latitude"), dados.get("longitude")
        if (lat is None) != (lng is None):
            raise ErroNegocio("Marque o ponto de atendimento no mapa (latitude e longitude juntas).")
        if lat is not None and not (-90 <= float(lat) <= 90 and -180 <= float(lng) <= 180):
            raise ErroNegocio("Localização inválida.")
        p.latitude, p.longitude = (float(lat), float(lng)) if lat is not None else (None, None)
    if "raio_atendimento_km" in dados and dados["raio_atendimento_km"] is not None:
        raio = int(dados["raio_atendimento_km"])
        if not 1 <= raio <= 200:
            raise ErroNegocio("O raio de atendimento deve ficar entre 1 e 200 km.")
        p.raio_atendimento_km = raio
    if "modalidades" in dados and dados["modalidades"] is not None:
        p.modalidades = _modalidades_validas(s, list(dados["modalidades"]))
    p.atualizado_em = agora()
    if p.status == PP_APROVADO and (p.registro_tipo, p.registro_numero) != registro_antes:
        p.status = PP_EM_ANALISE  # o registro profissional mudou: a administração confere de novo
        p.enviado_analise_em = agora()
        notificacoes.avisar(s, u.id, "ensino", "Seu perfil voltou para análise", "Você alterou o registro profissional. Enquanto a administração confere, suas ofertas ficam fora da busca.", "/ensino", None, f"ensino:reanalise:{p.id}:{agora():%Y%m%d%H%M%S}")
    s.flush()
    auditoria.registrar(s, u.id, "perfil_profissional_criar" if novo else "perfil_profissional_editar", "perfil_prof", p.id)
    s.commit()
    return p


def definir_foto(s: SessaoORM, u: Usuario, dados: bytes) -> PerfilProfissional:
    p = _exigir_meu(s, u)
    nome = midia.salvar_avatar(midia.processar_avatar(dados))
    anterior = p.foto_url
    p.foto_url = f"/foto/{nome}"
    auditoria.registrar(s, u.id, "perfil_profissional_foto", "perfil_prof", p.id)
    s.commit()
    if anterior and anterior.startswith("/foto/"):
        midia.remover_arquivos(f"perfil/{anterior.rsplit('/', 1)[-1]}")
    return p


def aceitar_termo(s: SessaoORM, u: Usuario) -> PerfilProfissional:
    p = _exigir_meu(s, u)
    p.aceite_termo_em = agora()
    auditoria.registrar(s, u.id, "perfil_profissional_aceite_termo", "perfil_prof", p.id)
    s.commit()
    return p


# ---------------------------------------------------------------- documentos (sempre privados)


def enviar_documento(s: SessaoORM, u: Usuario, tipo: str, dados: bytes, nome_original: str | None = None, validade: date | None = None) -> DocumentoProfissional:
    p = _exigir_meu(s, u)
    if tipo not in TIPOS_DOCUMENTO:
        raise ErroNegocio("Tipo de documento inválido.")
    if not dados:
        raise ErroNegocio("Envie o arquivo do documento.")
    if dados[:5] == b"%PDF-":
        if len(dados) > settings.limite_pdf_mb * 1024 * 1024:
            raise ErroNegocio(f"O PDF passa do limite de {settings.limite_pdf_mb} MB.")
        mime, ext = "application/pdf", "pdf"
    else:
        achado = midia.detectar(dados)
        if achado is None or achado[0] != "foto":
            raise ErroNegocio("Envie o documento em PDF ou como foto (JPG, PNG ou WebP).")
        if len(dados) > settings.limite_foto_mb * 1024 * 1024:
            raise ErroNegocio(f"A imagem passa do limite de {settings.limite_foto_mb} MB.")
        _, mime, ext = achado
    if validade is not None and validade < agora().date():
        raise ErroNegocio("A validade informada já passou.")
    rel = f"documentos/{uuid.uuid4().hex}.{ext}"
    armazenamento.salvar(rel, dados, mime)
    doc = DocumentoProfissional(perfil_id=p.id, tipo=tipo, arquivo=rel, mime=mime, nome_original=_limpo(nome_original, 160), status=DOC_PENDENTE, validade=validade)
    s.add(doc)
    s.flush()
    auditoria.registrar(s, u.id, "documento_profissional_enviar", "documento_prof", doc.id, tipo=tipo)
    s.commit()
    return doc


def remover_documento(s: SessaoORM, u: Usuario, doc_id: int) -> None:
    p = _exigir_meu(s, u)
    doc = s.get(DocumentoProfissional, doc_id)
    if doc is None or doc.perfil_id != p.id:
        raise NaoEncontrado("Documento não encontrado.")
    if doc.status == DOC_APROVADO and p.status == PP_APROVADO:
        raise ErroNegocio("Documento aprovado não pode ser apagado enquanto o perfil está aprovado. Envie o novo e aguarde a análise.")
    rel = doc.arquivo
    s.delete(doc)
    auditoria.registrar(s, u.id, "documento_profissional_remover", "documento_prof", doc_id)
    s.commit()
    armazenamento.remover(rel)


def arquivo_documento(s: SessaoORM, usuario: Usuario, doc_id: int) -> tuple[bytes, str, str]:
    """Só o titular e a administração leem o documento. Devolve (bytes, mime, nome)."""
    doc = s.get(DocumentoProfissional, doc_id)
    if doc is None:
        raise NaoEncontrado("Documento não encontrado.")
    perfil = s.get(PerfilProfissional, doc.perfil_id)
    if not (usuario.admin or perfil.usuario_id == usuario.id):
        raise SemPermissao("Só o titular e a administração acessam este documento.")
    if usuario.admin and perfil.usuario_id != usuario.id:
        auditoria.registrar(s, usuario.id, "documento_profissional_ver", "documento_prof", doc.id, perfil=perfil.id)
        s.commit()
    return armazenamento.ler(doc.arquivo), doc.mime, doc.nome_original or f"{doc.tipo}.{doc.mime.split('/')[-1]}"


# ---------------------------------------------------------------- envio para análise e decisão


def documentos_de(p: PerfilProfissional) -> list[DocumentoProfissional]:
    """Documentos do perfil lidos do BANCO (a sessão não expira ao salvar: a coleção `p.documentos` pode estar velha)."""
    s = object_session(p)
    return list(s.scalars(select(DocumentoProfissional).where(DocumentoProfissional.perfil_id == p.id).order_by(DocumentoProfissional.id)))


def pendencias(p: PerfilProfissional) -> list[str]:
    """O que falta para enviar o perfil para análise."""
    falta = []
    if not p.nome_profissional:
        falta.append("nome profissional")
    if not p.cidade:
        falta.append("cidade")
    if p.latitude is None or p.longitude is None:
        falta.append("ponto de atendimento no mapa")
    if not p.modalidades:
        falta.append("pelo menos uma modalidade")
    if not (p.telefone or p.whatsapp or p.email_contato):
        falta.append("um canal de contato")
    ativos = {d.tipo for d in documentos_de(p) if d.status != DOC_RECUSADO}
    if "identidade" not in ativos:
        falta.append("documento de identidade")
    if p.registro_numero and "registro_profissional" not in ativos:
        falta.append("comprovante do registro profissional")
    if p.aceite_termo_em is None:
        falta.append("aceite do Termo de Adesão do Profissional")
    return falta


def enviar_para_analise(s: SessaoORM, u: Usuario) -> PerfilProfissional:
    p = _exigir_meu(s, u)
    if p.status == PP_APROVADO:
        raise ErroNegocio("Seu perfil já está aprovado.")
    if p.status == PP_EM_ANALISE:
        raise ErroNegocio("Seu perfil já está em análise.")
    if p.status == PP_SUSPENSO:
        raise ErroNegocio("Seu perfil está suspenso. Fale com a administração.")
    falta = pendencias(p)
    if falta:
        raise ErroNegocio("Antes de enviar, complete: " + ", ".join(falta) + ".")
    p.status, p.enviado_analise_em, p.motivo_recusa = PP_EM_ANALISE, agora(), None
    auditoria.registrar(s, u.id, "perfil_profissional_enviar_analise", "perfil_prof", p.id)
    for adm in s.scalars(select(Usuario).where(Usuario.admin, Usuario.ativo)):
        notificacoes.avisar(s, adm.id, "ensino", "Novo profissional para aprovar", f"{p.nome_profissional} enviou o perfil e os documentos.", "/administracao", None, f"ensino:analise:{p.id}:{p.enviado_analise_em:%Y%m%d%H%M%S}")
    s.commit()
    return p


def _exigir_admin(admin: Usuario) -> None:
    if not admin.admin:
        raise SemPermissao("Só a administração decide sobre profissionais.")


def fila(s: SessaoORM, admin: Usuario, status: str | None = PP_EM_ANALISE) -> list[PerfilProfissional]:
    _exigir_admin(admin)
    q = select(PerfilProfissional).order_by(PerfilProfissional.enviado_analise_em.asc().nullslast(), PerfilProfissional.id)
    if status:
        q = q.where(PerfilProfissional.status == status)
    return list(s.scalars(q).unique())


def decidir_documento(s: SessaoORM, admin: Usuario, doc_id: int, aprovar: bool, motivo: str | None = None) -> DocumentoProfissional:
    _exigir_admin(admin)
    doc = s.get(DocumentoProfissional, doc_id)
    if doc is None:
        raise NaoEncontrado("Documento não encontrado.")
    if not aprovar and not _limpo(motivo):
        raise ErroNegocio("Informe o motivo da recusa do documento.")
    doc.status = DOC_APROVADO if aprovar else DOC_RECUSADO
    doc.motivo = None if aprovar else _limpo(motivo, 500)
    doc.decidido_por_id, doc.decidido_em = admin.id, agora()
    perfil = s.get(PerfilProfissional, doc.perfil_id)
    auditoria.registrar(s, admin.id, "documento_profissional_aprovar" if aprovar else "documento_profissional_recusar", "documento_prof", doc.id, perfil=perfil.id)
    if not aprovar:
        notificacoes.avisar(s, perfil.usuario_id, "ensino", "Um documento foi recusado", f"{TIPOS_DOCUMENTO[doc.tipo]}: {doc.motivo}", "/ensino", None, f"ensino:doc:{doc.id}:recusado")
    s.commit()
    return doc


def decidir(s: SessaoORM, admin: Usuario, perfil_id: int, aprovar: bool, motivo: str | None = None) -> PerfilProfissional:
    _exigir_admin(admin)
    p = obter(s, perfil_id)
    if p.status not in (PP_EM_ANALISE, PP_REPROVADO):
        raise ErroNegocio("Este perfil não está aguardando decisão.")
    if aprovar:
        aprovados = {d.tipo for d in documentos_de(p) if d.status == DOC_APROVADO}
        exigidos = {"identidade"} | ({"registro_profissional"} if p.registro_numero else set())
        faltam = [TIPOS_DOCUMENTO[t] for t in sorted(exigidos - aprovados)]
        if faltam:
            raise ErroNegocio("Aprove antes os documentos: " + ", ".join(faltam) + ".")
        p.status, p.aprovado_por_id, p.aprovado_em, p.motivo_recusa = PP_APROVADO, admin.id, agora(), None
        notificacoes.avisar(s, p.usuario_id, "ensino", "Perfil profissional aprovado", "Você já pode divulgar aulas e aparecer na busca. Cadastre suas ofertas em Minhas aulas.", "/ensino", None, f"ensino:aprovado:{p.id}:{p.aprovado_em:%Y%m%d%H%M%S}")
    else:
        if not _limpo(motivo):
            raise ErroNegocio("Informe o motivo da recusa.")
        p.status, p.motivo_recusa = PP_REPROVADO, _limpo(motivo, 1000)
        notificacoes.avisar(s, p.usuario_id, "ensino", "Perfil profissional recusado", p.motivo_recusa, "/ensino", None, f"ensino:recusado:{p.id}:{agora():%Y%m%d%H%M%S}")
    auditoria.registrar(s, admin.id, "perfil_profissional_aprovar" if aprovar else "perfil_profissional_recusar", "perfil_prof", p.id, motivo=motivo)
    s.commit()
    return p


def suspender(s: SessaoORM, admin: Usuario, perfil_id: int, motivo: str, reativar: bool = False) -> PerfilProfissional:
    _exigir_admin(admin)
    p = obter(s, perfil_id)
    if reativar:
        if p.status != PP_SUSPENSO:
            raise ErroNegocio("Este perfil não está suspenso.")
        p.status = PP_APROVADO
        titulo, corpo = "Perfil profissional reativado", "Suas ofertas voltaram para a busca."
    else:
        if not _limpo(motivo):
            raise ErroNegocio("Informe o motivo da suspensão.")
        p.status = PP_SUSPENSO
        titulo, corpo = "Perfil profissional suspenso", _limpo(motivo, 500)
    auditoria.registrar(s, admin.id, "perfil_profissional_reativar" if reativar else "perfil_profissional_suspender", "perfil_prof", p.id, motivo=motivo)
    notificacoes.avisar(s, p.usuario_id, "ensino", titulo, corpo or "", "/ensino", None, f"ensino:susp:{p.id}:{agora():%Y%m%d%H%M%S}")
    s.commit()
    return p


# ---------------------------------------------------------------- serializadores


def _doc_dict(d: DocumentoProfissional) -> dict:
    return {"id": d.id, "tipo": d.tipo, "tipo_nome": TIPOS_DOCUMENTO.get(d.tipo, d.tipo), "status": d.status, "motivo": d.motivo, "nome_original": d.nome_original,
            "validade": d.validade.isoformat() if d.validade else None, "enviado_em": d.enviado_em.isoformat(timespec="minutes")}


def dict_publico(p: PerfilProfissional, dist: float | None = None) -> dict:
    """O que qualquer usuário logado vê. Documentos e dados de análise nunca aparecem aqui."""
    return {
        "tipo": "profissional", "id": p.id, "nome": p.nome_profissional, "apresentacao": p.apresentacao, "foto_url": p.foto_url, "verificado": p.status == PP_APROVADO,
        "registro_tipo": p.registro_tipo, "registro_numero": p.registro_numero, "telefone": p.telefone, "whatsapp": p.whatsapp, "email_contato": p.email_contato,
        "cidade": p.cidade, "regiao": p.regiao, "latitude": p.latitude, "longitude": p.longitude, "raio_atendimento_km": p.raio_atendimento_km,
        "modalidades": list(p.modalidades or []), "especialidades": p.especialidades, "nota_media": p.nota_media if (p.n_avaliacoes or 0) >= 3 else None, "n_avaliacoes": p.n_avaliacoes or 0,
        "distancia_km": round(dist, 2) if dist is not None else None, "distancia": formatar_km(dist),
    }


def dict_privado(p: PerfilProfissional) -> dict:
    """Visão do próprio profissional (e da administração): inclui status, pendências e documentos."""
    return dict_publico(p) | {
        "status": p.status, "status_nome": STATUS_NOME[p.status], "motivo_recusa": p.motivo_recusa, "aceite_termo_em": p.aceite_termo_em.isoformat(timespec="minutes") if p.aceite_termo_em else None,
        "enviado_analise_em": p.enviado_analise_em.isoformat(timespec="minutes") if p.enviado_analise_em else None, "pendencias": pendencias(p),
        "documentos": [_doc_dict(d) for d in documentos_de(p)], "tipos_documento": TIPOS_DOCUMENTO, "usuario": p.usuario.arroba,
    }
