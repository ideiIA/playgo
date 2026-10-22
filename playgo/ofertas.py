"""Ofertas de aulas e treinamentos (docs/especificacao-personal-escolinhas.md, RF-06 e seção 5.2).

Três tipos: **aula** avulsa/particular, **pacote** de aulas e **turma** recorrente. Na fase 1 os horários semanais são informativos
(divulgação); a fase 2 materializa as sessões, vagas e agendamentos. Publicar exige perfil aprovado e plano de profissional vigente
(`planos.exigir(..., "ensino")`) e respeita o limite de ofertas do plano."""

from datetime import time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, planos
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, PlanoNecessario, SemPermissao
from .geo import formatar_km
from .models import (
    NOME_NIVEL,
    O_AULA,
    O_PACOTE,
    O_TURMA,
    OF_ENCERRADA,
    OF_PAUSADA,
    OF_PUBLICADA,
    OF_RASCUNHO,
    Modalidade,
    Oferta,
    OfertaHorario,
    PerfilProfissional,
    Usuario,
)
from .profissionais import meu

TIPOS = {O_AULA: "Aula avulsa", O_PACOTE: "Pacote de aulas", O_TURMA: "Turma"}
FORMATOS = {"individual": "Individual", "coletiva": "Em grupo"}
NIVEIS = ("todos", "iniciante", "intermediario", "avancado")
DIAS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
STATUS_NOME = {OF_RASCUNHO: "Rascunho", OF_PUBLICADA: "Publicada", OF_PAUSADA: "Pausada", OF_ENCERRADA: "Encerrada"}


def _dec(v, nome: str) -> Decimal | None:
    if v in (None, ""):
        return None
    d = Decimal(str(v))
    if d < 0:
        raise ErroNegocio(f"{nome} não pode ser negativo.")
    return d


def _meu_perfil(s: SessaoORM, u: Usuario) -> PerfilProfissional:
    p = meu(s, u)
    if p is None:
        raise NaoEncontrado("Crie primeiro o seu perfil profissional.")
    return p


def obter(s: SessaoORM, oferta_id: int) -> Oferta:
    o = s.get(Oferta, oferta_id)
    if o is None:
        raise NaoEncontrado("Oferta não encontrada.")
    return o


def _dono(s: SessaoORM, u: Usuario, oferta_id: int) -> Oferta:
    o = obter(s, oferta_id)
    if not (u.admin or o.perfil.usuario_id == u.id):
        raise SemPermissao("Só o profissional responsável gerencia esta oferta.")
    return o


def minhas(s: SessaoORM, u: Usuario) -> list[Oferta]:
    p = meu(s, u)
    if p is None:
        return []
    return list(s.scalars(select(Oferta).where(Oferta.perfil_id == p.id).order_by(Oferta.id.desc())).unique())


def _validar(d: dict, o: Oferta | None = None) -> dict:
    """Confere e normaliza os campos informados (só o que veio em `d`)."""
    saida: dict = {}
    if "tipo" in d:
        if d["tipo"] not in TIPOS:
            raise ErroNegocio("Escolha o tipo: aula avulsa, pacote de aulas ou turma.")
        saida["tipo"] = d["tipo"]
    if "titulo" in d:
        t = (d["titulo"] or "").strip()
        if len(t) < 3:
            raise ErroNegocio("Dê um título à oferta (mínimo 3 letras).")
        saida["titulo"] = t[:160]
    for campo, tam in (("descricao", 4000), ("local_texto", 200), ("endereco", 300)):
        if campo in d:
            saida[campo] = ((d[campo] or "").strip()[:tam]) or None
    if "nivel" in d:
        if d["nivel"] and d["nivel"] not in NIVEIS:
            raise ErroNegocio("Nível inválido.")
        saida["nivel"] = d["nivel"] or None
    if "formato" in d:
        if d["formato"] not in FORMATOS:
            raise ErroNegocio("Escolha o formato: individual ou em grupo.")
        saida["formato"] = d["formato"]
    for campo, minimo, maximo, rotulo in (("duracao_min", 15, 480, "A duração"), ("capacidade", 1, 500, "A quantidade de alunos")):
        if campo in d and d[campo] is not None:
            v = int(d[campo])
            if not minimo <= v <= maximo:
                raise ErroNegocio(f"{rotulo} deve ficar entre {minimo} e {maximo}.")
            saida[campo] = v
    for campo in ("faixa_etaria_min", "faixa_etaria_max", "aulas_pacote", "validade_pacote_dias"):
        if campo in d:
            saida[campo] = int(d[campo]) if d[campo] not in (None, "") else None
    for campo, nome in (("valor_aula", "O valor da aula"), ("valor_pacote", "O valor do pacote"), ("valor_mensal", "A mensalidade"), ("valor_experimental", "O valor da aula experimental")):
        if campo in d:
            saida[campo] = _dec(d[campo], nome)
    for campo in ("aula_experimental", "inscricoes_abertas"):
        if campo in d:
            saida[campo] = bool(d[campo])
    if "latitude" in d or "longitude" in d:
        lat, lng = d.get("latitude"), d.get("longitude")
        if (lat is None) != (lng is None):
            raise ErroNegocio("Marque o local no mapa (latitude e longitude juntas).")
        saida["latitude"], saida["longitude"] = (float(lat), float(lng)) if lat is not None else (None, None)
    fmin, fmax = saida.get("faixa_etaria_min", o.faixa_etaria_min if o else None), saida.get("faixa_etaria_max", o.faixa_etaria_max if o else None)
    if fmin is not None and fmax is not None and fmin > fmax:
        raise ErroNegocio("A idade mínima não pode ser maior que a máxima.")
    return saida


def _modalidade(s: SessaoORM, codigo_ou_id) -> int | None:
    if codigo_ou_id in (None, ""):
        return None
    m = s.get(Modalidade, int(codigo_ou_id)) if str(codigo_ou_id).isdigit() else s.scalar(select(Modalidade).where(Modalidade.codigo == str(codigo_ou_id)))
    if m is None:
        raise ErroNegocio("Modalidade não encontrada.")
    return m.id


def criar(s: SessaoORM, u: Usuario, d: dict) -> Oferta:
    p = _meu_perfil(s, u)
    campos = _validar(d)
    for obrigatorio in ("tipo", "titulo"):
        if obrigatorio not in campos:
            raise ErroNegocio("Informe o tipo e o título da oferta.")
    o = Oferta(perfil_id=p.id, status=OF_RASCUNHO, **campos)
    o.modalidade_id = _modalidade(s, d.get("modalidade_id") or d.get("modalidade"))
    if "formato" not in campos:
        o.formato = "individual" if o.tipo == O_AULA else "coletiva"
    if o.latitude is None and p.latitude is not None:
        o.latitude, o.longitude = p.latitude, p.longitude
    s.add(o)
    s.flush()
    if d.get("horarios"):
        _gravar_horarios(o, d["horarios"])
    auditoria.registrar(s, u.id, "oferta_criar", "oferta", o.id, tipo=o.tipo)
    s.commit()
    return o


def editar(s: SessaoORM, u: Usuario, oferta_id: int, d: dict) -> Oferta:
    o = _dono(s, u, oferta_id)
    if o.status == OF_ENCERRADA:
        raise ErroNegocio("Oferta encerrada não pode ser editada.")
    for k, v in _validar(d, o).items():
        setattr(o, k, v)
    if "modalidade_id" in d or "modalidade" in d:
        o.modalidade_id = _modalidade(s, d.get("modalidade_id") or d.get("modalidade"))
    o.atualizado_em = agora()
    auditoria.registrar(s, u.id, "oferta_editar", "oferta", o.id)
    s.commit()
    return o


def _gravar_horarios(o: Oferta, lista: list[dict]) -> None:
    novos = []
    vistos = set()
    for h in lista:
        dia = int(h["dia_semana"])
        if not 0 <= dia <= 6:
            raise ErroNegocio("Dia da semana inválido.")
        hora = h["hora_inicio"]
        if isinstance(hora, str):
            hh, mm = hora.split(":")[:2]
            hora = time(int(hh), int(mm))
        dur = int(h.get("duracao_min") or o.duracao_min or 60)
        if not 15 <= dur <= 480:
            raise ErroNegocio("A duração do horário deve ficar entre 15 e 480 minutos.")
        if (dia, hora) in vistos:
            raise ErroNegocio("Há dois horários iguais na lista.")
        vistos.add((dia, hora))
        novos.append(OfertaHorario(dia_semana=dia, hora_inicio=hora, duracao_min=dur, vigencia_inicio=h.get("vigencia_inicio"), vigencia_fim=h.get("vigencia_fim")))
    o.horarios[:] = novos


def definir_horarios(s: SessaoORM, u: Usuario, oferta_id: int, lista: list[dict]) -> Oferta:
    o = _dono(s, u, oferta_id)
    if o.status == OF_ENCERRADA:
        raise ErroNegocio("Oferta encerrada não pode ser editada.")
    _gravar_horarios(o, lista)
    o.atualizado_em = agora()
    auditoria.registrar(s, u.id, "oferta_horarios", "oferta", o.id, quantidade=len(lista))
    s.commit()
    return o


def _publicadas(s: SessaoORM, perfil_id: int, exceto: int | None = None) -> int:
    q = select(func.count()).select_from(Oferta).where(Oferta.perfil_id == perfil_id, Oferta.status == OF_PUBLICADA)
    if exceto:
        q = q.where(Oferta.id != exceto)
    return s.scalar(q) or 0


def publicar(s: SessaoORM, u: Usuario, oferta_id: int) -> Oferta:
    o = _dono(s, u, oferta_id)
    dono = o.perfil.usuario
    planos.exigir(s, dono, "ensino")  # perfil aprovado + plano de profissional (abre o teste grátis na 1ª vez)
    if o.status == OF_ENCERRADA:
        raise ErroNegocio("Oferta encerrada não pode ser publicada.")
    r = planos.regras_prof_da_pessoa(s, dono)
    if r is not None and r.max_ofertas is not None and _publicadas(s, o.perfil_id, exceto=o.id) >= r.max_ofertas:
        raise PlanoNecessario("prof_pro", f"Seu plano {r.nome} permite até {r.max_ofertas} ofertas publicadas. Pause outra ou assine um plano maior.")
    falta = []
    if not o.modalidade_id:
        falta.append("a modalidade")
    if o.latitude is None and not o.local_texto:
        falta.append("o local")
    if o.tipo == O_TURMA and not o.horarios:
        falta.append("pelo menos um horário semanal")
    if o.tipo == O_PACOTE and not (o.valor_pacote is not None and o.aulas_pacote):
        falta.append("o valor e a quantidade de aulas do pacote")
    if o.tipo == O_AULA and o.valor_aula is None and not o.aula_experimental:
        falta.append("o valor da aula")
    if falta:
        raise ErroNegocio("Antes de publicar, informe " + ", ".join(falta) + ".")
    o.status = OF_PUBLICADA
    auditoria.registrar(s, u.id, "oferta_publicar", "oferta", o.id)
    s.commit()
    return o


def pausar(s: SessaoORM, u: Usuario, oferta_id: int) -> Oferta:
    o = _dono(s, u, oferta_id)
    if o.status != OF_PUBLICADA:
        raise ErroNegocio("Só uma oferta publicada pode ser pausada.")
    o.status = OF_PAUSADA
    auditoria.registrar(s, u.id, "oferta_pausar", "oferta", o.id)
    s.commit()
    return o


def encerrar(s: SessaoORM, u: Usuario, oferta_id: int) -> Oferta:
    o = _dono(s, u, oferta_id)
    o.status = OF_ENCERRADA
    o.inscricoes_abertas = False
    auditoria.registrar(s, u.id, "oferta_encerrar", "oferta", o.id)
    s.commit()
    return o


# ---------------------------------------------------------------- serializador


def _horario_texto(h: OfertaHorario) -> str:
    return f"{DIAS[h.dia_semana]} {h.hora_inicio.strftime('%H:%M')}"


def dict_oferta(o: Oferta, dist: float | None = None, completo: bool = False) -> dict:
    from .profissionais import dict_publico

    m = o.modalidade
    d = {
        "tipo": "oferta", "id": o.id, "tipo_oferta": o.tipo, "tipo_oferta_nome": TIPOS[o.tipo], "titulo": o.titulo, "descricao": o.descricao if completo else None,
        "modalidade": {"id": m.id, "codigo": m.codigo, "nome": m.nome, "icone": m.icone, "cor": m.cor} if m else None,
        "nivel": o.nivel, "nivel_nome": NOME_NIVEL.get(o.nivel or "todos"), "faixa_etaria_min": o.faixa_etaria_min, "faixa_etaria_max": o.faixa_etaria_max,
        "formato": o.formato, "formato_nome": FORMATOS[o.formato], "duracao_min": o.duracao_min, "capacidade": o.capacidade,
        "aula_experimental": o.aula_experimental, "valor_experimental": float(o.valor_experimental) if o.valor_experimental is not None else None,
        "valor_aula": float(o.valor_aula) if o.valor_aula is not None else None, "valor_pacote": float(o.valor_pacote) if o.valor_pacote is not None else None,
        "aulas_pacote": o.aulas_pacote, "validade_pacote_dias": o.validade_pacote_dias, "valor_mensal": float(o.valor_mensal) if o.valor_mensal is not None else None,
        "local_texto": o.local_texto, "endereco": o.endereco, "latitude": o.latitude if o.latitude is not None else o.perfil.latitude, "longitude": o.longitude if o.longitude is not None else o.perfil.longitude,
        "status": o.status, "status_nome": STATUS_NOME[o.status], "inscricoes_abertas": o.inscricoes_abertas,
        "horarios": [{"id": h.id, "dia_semana": h.dia_semana, "dia": DIAS[h.dia_semana], "hora_inicio": h.hora_inicio.strftime("%H:%M"), "duracao_min": h.duracao_min,
                      "vigencia_inicio": h.vigencia_inicio.isoformat() if h.vigencia_inicio else None, "vigencia_fim": h.vigencia_fim.isoformat() if h.vigencia_fim else None} for h in o.horarios],
        "horarios_texto": ", ".join(_horario_texto(h) for h in o.horarios[:4]) + (" …" if len(o.horarios) > 4 else ""),
        "preco_texto": _preco_texto(o), "distancia_km": round(dist, 2) if dist is not None else None, "distancia": formatar_km(dist),
        "profissional": {"id": o.perfil.id, "nome": o.perfil.nome_profissional, "foto_url": o.perfil.foto_url, "verificado": True, "nota_media": o.perfil.nota_media if (o.perfil.n_avaliacoes or 0) >= 3 else None},
        "icone": m.icone if m else "🎓",
    }
    if completo:
        d["profissional"] = dict_publico(o.perfil)
    return d


def _preco_texto(o: Oferta) -> str:
    def brl(v):
        return "R$ " + f"{float(v):.2f}".replace(".", ",")

    if o.tipo == O_TURMA and o.valor_mensal is not None:
        return f"{brl(o.valor_mensal)}/mês"
    if o.tipo == O_PACOTE and o.valor_pacote is not None:
        return f"{brl(o.valor_pacote)} · {o.aulas_pacote or '?'} aulas"
    if o.valor_aula is not None:
        return f"{brl(o.valor_aula)}/aula"
    return "A combinar" if not o.aula_experimental else "Aula experimental"
