"""API de ensino: perfil do profissional, documentos (privados), ofertas, Central "Aulas e Treinamentos" e fila de aprovação da administração."""

from datetime import date

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from .. import descoberta, midia, ofertas, planos, profissionais
from ..deps import AtualApi, Sessao
from ..erros import NaoEncontrado
from ..models import OF_PUBLICADA, PP_APROVADO, Modalidade

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------- entradas


class PerfilIn(BaseModel):
    nome_profissional: str | None = None
    apresentacao: str | None = None
    registro_tipo: str | None = None
    registro_numero: str | None = None
    telefone: str | None = None
    whatsapp: str | None = None
    email_contato: str | None = None
    cidade: str | None = None
    regiao: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    raio_atendimento_km: int | None = None
    modalidades: list[str] | None = None
    especialidades: str | None = None


class HorarioIn(BaseModel):
    dia_semana: int
    hora_inicio: str  # HH:MM
    duracao_min: int | None = None
    vigencia_inicio: date | None = None
    vigencia_fim: date | None = None


class OfertaIn(BaseModel):
    tipo: str | None = None
    titulo: str | None = None
    descricao: str | None = None
    modalidade: str | None = None  # código ou id
    nivel: str | None = None
    faixa_etaria_min: int | None = None
    faixa_etaria_max: int | None = None
    formato: str | None = None
    duracao_min: int | None = None
    capacidade: int | None = None
    aula_experimental: bool | None = None
    valor_experimental: float | None = None
    valor_aula: float | None = None
    valor_pacote: float | None = None
    aulas_pacote: int | None = None
    validade_pacote_dias: int | None = None
    valor_mensal: float | None = None
    local_texto: str | None = None
    endereco: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    inscricoes_abertas: bool | None = None
    horarios: list[HorarioIn] | None = None


class DecidirIn(BaseModel):
    aprovar: bool
    motivo: str | None = None


class SuspenderIn(BaseModel):
    motivo: str = ""
    reativar: bool = False


def _sem_cache(resposta: Response, dados):
    resposta.headers["Cache-Control"] = "no-store"
    return dados


# ---------------------------------------------------------------- o próprio profissional


@router.get("/ensino/meu")
def meu(u: AtualApi, s: Sessao):
    p = profissionais.meu(s, u)
    modalidades = [{"id": m.id, "codigo": m.codigo, "nome": m.nome, "icone": m.icone} for m in s.scalars(select(Modalidade).order_by(Modalidade.nome))]
    return {
        "perfil": profissionais.dict_privado(p) if p else None, "ofertas": [ofertas.dict_oferta(o, completo=True) for o in ofertas.minhas(s, u)],
        "plano": planos.resumo_prof(s, u), "ensino": planos.permissoes(s, u)["ensino"], "modalidades": modalidades,
        "tipos_oferta": ofertas.TIPOS, "dias": ofertas.DIAS,
    }


@router.put("/ensino/meu/perfil")
def salvar_perfil(corpo: PerfilIn, u: AtualApi, s: Sessao):
    p = profissionais.salvar(s, u, corpo.model_dump(exclude_unset=True))
    return profissionais.dict_privado(p)


@router.post("/ensino/meu/foto")
def enviar_foto(u: AtualApi, s: Sessao, arquivo: UploadFile = File()):
    """multipart/form-data, campo `arquivo`. Recorta em quadrado e remove metadados."""
    return profissionais.dict_privado(profissionais.definir_foto(s, u, midia.ler_upload(arquivo)))


@router.post("/ensino/meu/aceite")
def aceitar_termo(u: AtualApi, s: Sessao):
    return profissionais.dict_privado(profissionais.aceitar_termo(s, u))


@router.post("/ensino/meu/documentos", status_code=201)
def enviar_documento(u: AtualApi, s: Sessao, tipo: str = Form(), validade: date | None = Form(None), arquivo: UploadFile = File()):
    """multipart/form-data: `tipo`, `arquivo` (PDF ou foto) e `validade` opcional. O arquivo NUNCA fica público."""
    dados = midia.ler_upload(arquivo)
    profissionais.enviar_documento(s, u, tipo, dados, arquivo.filename, validade)
    return profissionais.dict_privado(profissionais.meu(s, u))


@router.delete("/ensino/meu/documentos/{doc_id}")
def remover_documento(doc_id: int, u: AtualApi, s: Sessao):
    profissionais.remover_documento(s, u, doc_id)
    return profissionais.dict_privado(profissionais.meu(s, u))


@router.get("/ensino/documentos/{doc_id}/arquivo")
def ver_documento(doc_id: int, u: AtualApi, s: Sessao):
    """Só o titular e a administração. Sem cache."""
    dados, mime, nome = profissionais.arquivo_documento(s, u, doc_id)
    return Response(dados, media_type=mime, headers={"Cache-Control": "no-store", "Content-Disposition": f'inline; filename="{nome}"', "X-Content-Type-Options": "nosniff"})


@router.post("/ensino/meu/analise")
def enviar_analise(u: AtualApi, s: Sessao):
    return profissionais.dict_privado(profissionais.enviar_para_analise(s, u))


# ---------------------------------------------------------------- ofertas do profissional


@router.post("/ensino/ofertas", status_code=201)
def criar_oferta(corpo: OfertaIn, u: AtualApi, s: Sessao):
    dados = corpo.model_dump(exclude_unset=True)
    if "horarios" in dados:
        dados["horarios"] = [h if isinstance(h, dict) else h.model_dump() for h in dados["horarios"]]
    return ofertas.dict_oferta(ofertas.criar(s, u, dados), completo=True)


@router.get("/ensino/ofertas/{oferta_id}")
def minha_oferta(oferta_id: int, u: AtualApi, s: Sessao):
    return ofertas.dict_oferta(ofertas._dono(s, u, oferta_id), completo=True)


@router.put("/ensino/ofertas/{oferta_id}")
def editar_oferta(oferta_id: int, corpo: OfertaIn, u: AtualApi, s: Sessao):
    dados = corpo.model_dump(exclude_unset=True)
    horarios = dados.pop("horarios", None)
    o = ofertas.editar(s, u, oferta_id, dados)
    if horarios is not None:
        o = ofertas.definir_horarios(s, u, oferta_id, horarios)
    return ofertas.dict_oferta(o, completo=True)


@router.put("/ensino/ofertas/{oferta_id}/horarios")
def horarios_da_oferta(oferta_id: int, corpo: list[HorarioIn], u: AtualApi, s: Sessao):
    return ofertas.dict_oferta(ofertas.definir_horarios(s, u, oferta_id, [h.model_dump() for h in corpo]), completo=True)


@router.post("/ensino/ofertas/{oferta_id}/publicar")
def publicar_oferta(oferta_id: int, u: AtualApi, s: Sessao):
    return ofertas.dict_oferta(ofertas.publicar(s, u, oferta_id), completo=True)


@router.post("/ensino/ofertas/{oferta_id}/pausar")
def pausar_oferta(oferta_id: int, u: AtualApi, s: Sessao):
    return ofertas.dict_oferta(ofertas.pausar(s, u, oferta_id), completo=True)


@router.post("/ensino/ofertas/{oferta_id}/encerrar")
def encerrar_oferta(oferta_id: int, u: AtualApi, s: Sessao):
    return ofertas.dict_oferta(ofertas.encerrar(s, u, oferta_id), completo=True)


# ---------------------------------------------------------------- Central "Aulas e Treinamentos" (qualquer usuário logado)


@router.get("/aulas")
def central(
    u: AtualApi, s: Sessao, lat: float | None = None, lng: float | None = None, raio: float | None = None, modalidades: str = "", q: str = "", nivel: str | None = None,
    periodo: str | None = None, dia_semana: int | None = Query(None, ge=0, le=6), preco_max: float | None = None, tipo_oferta: str | None = None, formato: str | None = None,
    experimental: bool = False, idade: int | None = None, nota_min: float | None = None,
):
    f = descoberta.Filtros(
        raio_km=raio if raio is not None else descoberta.settings.raio_padrao_km * 2, modalidades=[m for m in modalidades.split(",") if m], q=q, nivel=nivel, periodo=periodo,
        tipos=("profissional", "oferta"), preco_max=preco_max, tipo_oferta=tipo_oferta, formato=formato, experimental=experimental, idade=idade, dia_semana=dia_semana, nota_min=nota_min,
    )
    return descoberta.aulas(s, u, lat, lng, f)


@router.get("/profissionais/{perfil_id}")
def perfil_publico(perfil_id: int, u: AtualApi, s: Sessao):
    p = profissionais.obter(s, perfil_id)
    dono = p.usuario_id == u.id or u.admin
    if not dono and not (p.status == PP_APROVADO and planos._ensino_liberado(s, p.usuario)):
        raise NaoEncontrado("Profissional não encontrado.")
    la, ln, _ = descoberta.localizacao(u)
    from ..geo import haversine

    dist = haversine(la, ln, p.latitude, p.longitude) if p.latitude is not None else None
    lista = [o for o in ofertas.minhas(s, p.usuario) if o.status == OF_PUBLICADA]
    return profissionais.dict_publico(p, dist) | {"ofertas": [ofertas.dict_oferta(o) for o in lista], "sou_eu": p.usuario_id == u.id}


@router.get("/ofertas/{oferta_id}")
def oferta_publica(oferta_id: int, u: AtualApi, s: Sessao):
    o = ofertas.obter(s, oferta_id)
    dono = o.perfil.usuario_id == u.id or u.admin
    if not dono and not (o.status == OF_PUBLICADA and o.perfil.status == PP_APROVADO and planos._ensino_liberado(s, o.perfil.usuario)):
        raise NaoEncontrado("Oferta não encontrada.")
    la, ln, _ = descoberta.localizacao(u)
    from ..geo import haversine

    olat = o.latitude if o.latitude is not None else o.perfil.latitude
    olng = o.longitude if o.longitude is not None else o.perfil.longitude
    dist = haversine(la, ln, olat, olng) if olat is not None else None
    return ofertas.dict_oferta(o, dist, completo=True) | {"sou_eu": o.perfil.usuario_id == u.id}


# ---------------------------------------------------------------- administração


@router.get("/admin/profissionais")
def admin_profissionais(u: AtualApi, s: Sessao, status: str = "em_analise"):
    lista = profissionais.fila(s, u, status or None)
    return [profissionais.dict_privado(p) for p in lista]


@router.post("/admin/profissionais/{perfil_id}/decidir")
def admin_decidir(perfil_id: int, corpo: DecidirIn, u: AtualApi, s: Sessao):
    return profissionais.dict_privado(profissionais.decidir(s, u, perfil_id, corpo.aprovar, corpo.motivo))


@router.post("/admin/profissionais/{perfil_id}/suspender")
def admin_suspender(perfil_id: int, corpo: SuspenderIn, u: AtualApi, s: Sessao):
    return profissionais.dict_privado(profissionais.suspender(s, u, perfil_id, corpo.motivo, corpo.reativar))


@router.post("/admin/profissionais/documentos/{doc_id}/decidir")
def admin_decidir_documento(doc_id: int, corpo: DecidirIn, u: AtualApi, s: Sessao):
    doc = profissionais.decidir_documento(s, u, doc_id, corpo.aprovar, corpo.motivo)
    return profissionais.dict_privado(profissionais.obter(s, doc.perfil_id))
