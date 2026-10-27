"""Cadastro, login e perfil do atleta (seção 2 do projeto), com nome de usuário, aceite de termos e
consentimento de localização (LGPD)."""

import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, seguranca, termos
from .db import agora
from .erros import ErroNegocio
from .models import NIVEIS, PERIODOS, AceiteTermos, Modalidade, Usuario, UsuarioModalidade

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USUARIO = re.compile(r"^[a-z0-9](?:[a-z0-9_.]{1,18})[a-z0-9]$")
RESERVADOS = {"admin", "administrador", "playgo", "suporte", "moderacao", "moderador", "oficial", "root", "sistema", "ajuda", "api", "app", "removido", "anonimo"}
# Conferido quando o e-mail não existe, para o tempo de resposta não revelar quais e-mails têm conta.
_HASH_FALSO = seguranca.gerar_hash("sem-usuario")


def normalizar_email(email: str) -> str:
    return email.strip().lower()


def validar_usuario(nome: str) -> str:
    """Devolve o nome de usuário normalizado ou levanta ErroNegocio. 3 a 20 caracteres: a-z, 0-9, _ e ."""
    u = nome.strip().lstrip("@").lower()
    if not _USUARIO.match(u) or ".." in u or "__" in u:
        raise ErroNegocio("O nome de usuário precisa ter de 3 a 20 caracteres: letras minúsculas, números, _ ou ponto, começando e terminando com letra ou número.")
    if u in RESERVADOS or u.startswith(("playgo", "removido")):
        raise ErroNegocio("Esse nome de usuário não está disponível.")
    return u


def _registrar_aceite(s: SessaoORM, u: Usuario) -> None:
    c = auditoria.contexto()
    n = agora()
    s.add(AceiteTermos(usuario_id=u.id, versao=termos.VERSAO, aceito_em=n, ip=c.get("ip"), user_agent=c.get("user_agent")))
    u.termos_versao, u.termos_em = termos.VERSAO, n
    auditoria.registrar(s, u.id, "aceite_termos", "usuario", u.id, versao=termos.VERSAO)


def cadastrar(
    s: SessaoORM, nome: str, email: str, senha: str, usuario: str, aceita_termos: bool = False, consent_localizacao: bool = False
) -> Usuario:
    email = normalizar_email(email)
    if not nome.strip():
        raise ErroNegocio("Informe seu nome.")
    if not _EMAIL.match(email):
        raise ErroNegocio("Informe um e-mail válido.")
    if len(senha) < seguranca.SENHA_MINIMA:
        raise ErroNegocio(f"A senha precisa ter pelo menos {seguranca.SENHA_MINIMA} caracteres.")
    if not aceita_termos:
        raise ErroNegocio("Para criar a conta, aceite os Termos de Uso e a Política de Privacidade.")
    nome_usuario = validar_usuario(usuario)
    if s.scalar(select(Usuario.id).where(Usuario.email == email)):
        raise ErroNegocio("Já existe uma conta com esse e-mail.")
    if s.scalar(select(Usuario.id).where(Usuario.usuario == nome_usuario)):
        raise ErroNegocio("Esse nome de usuário já está em uso.")
    primeiro = not s.scalar(select(func.count()).select_from(Usuario))
    u = Usuario(nome=nome.strip(), email=email, senha_hash=seguranca.gerar_hash(senha), usuario=nome_usuario, admin=primeiro)
    s.add(u)
    s.flush()
    _registrar_aceite(s, u)
    if consent_localizacao:
        u.consent_localizacao, u.consent_localizacao_em = True, agora()
        auditoria.registrar(s, u.id, "consentimento_localizacao", "usuario", u.id, concedido=True)
    auditoria.registrar(s, u.id, "cadastro", "usuario", u.id, usuario=nome_usuario)
    s.commit()
    return u


def autenticar(s: SessaoORM, email: str, senha: str) -> Usuario | None:
    u = s.scalar(select(Usuario).where(Usuario.email == normalizar_email(email)))
    ok = seguranca.conferir(senha, u.senha_hash if u else _HASH_FALSO)
    if u is not None and ok and u.ativo:
        auditoria.registrar(s, u.id, "login", "usuario", u.id, commit=True)
        return u
    auditoria.registrar(s, u.id if u else None, "login_falha", "usuario", u.id if u else None, commit=True)
    return None


def termos_pendentes(u: Usuario) -> bool:
    return u.termos_versao != termos.VERSAO


def pendencias(u: Usuario) -> str | None:
    """O que a pessoa precisa resolver antes de usar o app: 'usuario' (escolher @) ou 'termos' (novo aceite)."""
    if not u.usuario:
        return "usuario"
    if termos_pendentes(u):
        return "termos"
    return None


def aceitar_termos(s: SessaoORM, u: Usuario, termos_ok: bool) -> Usuario:
    if not termos_ok:
        raise ErroNegocio("Para continuar, aceite os Termos de Uso e a Política de Privacidade.")
    _registrar_aceite(s, u)
    s.commit()
    return u


def definir_usuario(s: SessaoORM, u: Usuario, novo: str) -> Usuario:
    nome_usuario = validar_usuario(novo)
    if nome_usuario != u.usuario:
        if s.scalar(select(Usuario.id).where(Usuario.usuario == nome_usuario, Usuario.id != u.id)):
            raise ErroNegocio("Esse nome de usuário já está em uso.")
        anterior = u.usuario
        u.usuario = nome_usuario
        auditoria.registrar(s, u.id, "usuario_alterado", "usuario", u.id, de=anterior, para=nome_usuario)
        s.commit()
    return u


def buscar_por_usuario(s: SessaoORM, q: str, excluir_id: int | None = None, limite: int = 10) -> list[Usuario]:
    """Busca pelo @ (prefixo). Não expõe nome real nem e-mail."""
    termo = q.strip().lstrip("@").lower()
    if len(termo) < 2:
        return []
    termo = termo.replace("%", "").replace("_", r"\_")
    consulta = select(Usuario).where(Usuario.ativo, Usuario.usuario.like(termo + "%", escape="\\")).order_by(Usuario.usuario).limit(limite)
    if excluir_id:
        consulta = consulta.where(Usuario.id != excluir_id)
    return list(s.scalars(consulta))


def por_usuario(s: SessaoORM, arroba: str) -> Usuario | None:
    return s.scalar(select(Usuario).where(Usuario.usuario == arroba.strip().lstrip("@").lower(), Usuario.ativo))


def revogar_localizacao(s: SessaoORM, u: Usuario) -> Usuario:
    u.consent_localizacao, u.consent_localizacao_em = False, agora()
    u.latitude = u.longitude = None
    auditoria.registrar(s, u.id, "consentimento_localizacao", "usuario", u.id, concedido=False)
    s.commit()
    return u


def atualizar_perfil(
    s: SessaoORM,
    u: Usuario,
    nome: str | None = None,
    cidade: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    raio_km: int | None = None,
    disponibilidade: list[str] | None = None,
    sexo: str | None = None,
    nascimento: date | None = None,
    foto_url: str | None = None,
    notif_vagas: bool | None = None,
    notif_lembretes: bool | None = None,
    notif_campeonatos: bool | None = None,
    notif_grupos: bool | None = None,
    notif_raio_km: int | None = None,
    consent_localizacao: bool | None = None,
) -> Usuario:
    if consent_localizacao is not None and consent_localizacao != u.consent_localizacao:
        if not consent_localizacao:
            return revogar_localizacao(s, u)
        u.consent_localizacao, u.consent_localizacao_em = True, agora()
        auditoria.registrar(s, u.id, "consentimento_localizacao", "usuario", u.id, concedido=True)
    if nome is not None:
        if not nome.strip():
            raise ErroNegocio("O nome não pode ficar em branco.")
        u.nome = nome.strip()
    if cidade is not None:
        u.cidade = cidade.strip() or None
    if latitude is not None and longitude is not None:
        if not u.consent_localizacao:
            raise ErroNegocio("Para guardar sua localização, autorize o uso dela (em Perfil ou Privacidade). Você pode revogar quando quiser.")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ErroNegocio("Localização inválida.")
        u.latitude, u.longitude = latitude, longitude
    if raio_km is not None:
        u.raio_km = max(1, min(100, raio_km))
    if disponibilidade is not None:
        validos = {p for p, _ in PERIODOS}
        u.disponibilidade = [p for p in disponibilidade if p in validos]
    if sexo is not None:
        u.sexo = sexo if sexo in ("M", "F") else None
    if nascimento is not None:
        u.nascimento = nascimento
    if foto_url is not None:
        u.foto_url = foto_url.strip() or None
    for campo, valor in (("notif_vagas", notif_vagas), ("notif_lembretes", notif_lembretes), ("notif_campeonatos", notif_campeonatos), ("notif_grupos", notif_grupos)):
        if valor is not None:
            setattr(u, campo, valor)
    if notif_raio_km is not None:
        u.notif_raio_km = max(1, min(100, notif_raio_km))
    s.commit()
    return u


def _arquivo_da_foto(u: Usuario) -> str | None:
    return f"perfil/{u.foto_url[6:]}" if u.foto_url and u.foto_url.startswith("/foto/") else None


def definir_foto(s: SessaoORM, u: Usuario, dados: bytes) -> Usuario:
    """Troca a foto de perfil (substitui e apaga a anterior). A foto é escolha da pessoa e aparece nas publicações dela."""
    from . import midia

    nome = midia.salvar_avatar(midia.processar_avatar(dados))
    anterior = _arquivo_da_foto(u)
    u.foto_url = f"/foto/{nome}"
    auditoria.registrar(s, u.id, "foto_perfil_alterar", "usuario", u.id)
    s.commit()
    midia.remover_arquivos(anterior)
    return u


def remover_foto(s: SessaoORM, u: Usuario) -> Usuario:
    from . import midia

    anterior = _arquivo_da_foto(u)
    u.foto_url = None
    auditoria.registrar(s, u.id, "foto_perfil_remover", "usuario", u.id)
    s.commit()
    midia.remover_arquivos(anterior)
    return u


def definir_esportes(s: SessaoORM, u: Usuario, esportes: dict[int, str]) -> Usuario:
    """Substitui os esportes do atleta: {modalidade_id: nível}."""
    niveis = {n for n, _ in NIVEIS}
    validas = set(s.scalars(select(Modalidade.id).where(Modalidade.id.in_(list(esportes) or [0]), Modalidade.ativo)))
    u.esportes.clear()
    s.flush()
    for mid, nivel in esportes.items():
        if mid in validas:
            u.esportes.append(UsuarioModalidade(modalidade_id=mid, nivel=nivel if nivel in niveis else "intermediario"))
    s.commit()
    s.refresh(u)
    return u


def _permissoes(u: Usuario) -> dict:
    from sqlalchemy.orm import object_session

    from . import planos

    s = object_session(u)
    if s is None:
        return {"atividade": False, "campeonato": False, "arena": False, "grupo": False, "comunidade": False, "ensino": False, "perfil_prof": False, "ensino_menu": False}
    return planos.permissoes(s, u)


def _aviso_plano(u: Usuario) -> dict | None:
    from sqlalchemy.orm import object_session

    from . import planos

    s = object_session(u)
    return planos.aviso_vencimento(s, u) if s is not None else None


def dados_publicos(u: Usuario) -> dict:
    """O que o app recebe sobre o próprio usuário."""
    return {
        "permissoes": _permissoes(u), "aviso_plano": _aviso_plano(u),
        "id": u.id, "nome": u.nome, "email": u.email, "usuario": u.usuario, "arroba": u.arroba, "iniciais": u.iniciais, "foto_url": u.foto_url,
        "admin": u.admin, "moderador": u.moderador, "perfil_acesso": u.perfil_acesso, "equipe_moderacao": u.equipe_moderacao, "gestor": u.gestor, "cidade": u.cidade, "latitude": u.latitude, "longitude": u.longitude,
        "raio_km": u.raio_km, "disponibilidade": list(u.disponibilidade or []), "sexo": u.sexo,
        "nascimento": u.nascimento.isoformat() if u.nascimento else None,
        "consent_localizacao": u.consent_localizacao, "pendencia": pendencias(u), "termos_versao": termos.VERSAO,
        "notif": {"vagas": u.notif_vagas, "lembretes": u.notif_lembretes, "campeonatos": u.notif_campeonatos, "grupos": u.notif_grupos, "raio_km": u.notif_raio_km},
        "esportes": [{"modalidade_id": e.modalidade_id, "codigo": e.modalidade.codigo, "nome": e.modalidade.nome, "icone": e.modalidade.icone, "nivel": e.nivel} for e in u.esportes],
    }
