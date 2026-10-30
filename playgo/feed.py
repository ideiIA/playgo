"""Feed esportivo personalizado (RF-025, RF-026): a tela inicial do atleta."""

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import descoberta, match, telao
from . import serializadores as ser
from .db import agora
from .descoberta import Filtros
from .models import ABERTA, Atividade, Grupo, GrupoMembro, Usuario

URGENTES = 6
PERTO = 9


def _proxima_do_grupo(s: SessaoORM, grupo_id: int) -> dict | None:
    n = agora()
    a = s.scalar(select(Atividade).where(Atividade.grupo_id == grupo_id, Atividade.status == ABERTA, Atividade.inicio >= n).order_by(Atividade.inicio).limit(1))
    return {"id": a.id, "nome": a.nome, "quando": ser.texto_quando(a.inicio, n)} if a else None


def montar(s: SessaoORM, usuario: Usuario, lat: float | None = None, lng: float | None = None, modalidade: str | None = None) -> dict:
    la, ln, informada = descoberta.localizacao(usuario, lat, lng)
    raio = usuario.raio_km or 10

    # Uma única busca por atividades perto; as seções saem dela
    perto_todas = descoberta.buscar_atividades(s, usuario, la, ln, Filtros(raio_km=raio, ordem="relevancia"), limite=200)
    if modalidade:  # chip de esporte da home: filtra as atividades (campeonatos, grupos e arenas seguem como estão)
        perto_todas = [a for a in perto_todas if (a["modalidade"] or {}).get("codigo") == modalidade]
    abertas_para_mim = [a for a in perto_todas if a["vagas"] > 0 and a["minha_participacao"] is None]

    # 🔥 Precisam de jogadores agora: pediu gente, ou poucas vagas e começa logo
    urgentes = [a for a in abertas_para_mim if a["falta_gente"] or (a["vagas"] <= 2 and a["minutos_para_inicio"] <= 180)]
    urgentes = sorted(urgentes, key=lambda a: -a["urgencia"])[:URGENTES]
    ids_urgentes = {a["id"] for a in urgentes}

    # 📍 Perto de você: o que não está acima, do mais próximo no tempo
    perto = [a for a in abertas_para_mim if a["id"] not in ids_urgentes]
    perto = sorted(perto, key=lambda a: (a["inicio"], a["distancia_km"] or 0))[:PERTO]

    # 🎯 Recomendados: pelo perfil (esporte, nível, disponibilidade, distância)
    recomendados = []
    if usuario.esportes:
        brutas = {a.id: a for a in s.scalars(select(Atividade).where(Atividade.id.in_([x["id"] for x in abertas_para_mim] or [0])))}
        for item in abertas_para_mim:
            nota = match.compatibilidade(usuario, brutas[item["id"]], item["distancia_km"])
            if nota is not None:
                recomendados.append({**item, "compatibilidade": nota})
        recomendados = sorted(recomendados, key=lambda a: -a["compatibilidade"])[:6]

    # 👥 Seus grupos
    grupos = []
    for g in s.scalars(select(Grupo).join(GrupoMembro, GrupoMembro.grupo_id == Grupo.id).where(GrupoMembro.usuario_id == usuario.id, GrupoMembro.status == "ativo", Grupo.ativo)):
        grupos.append(ser.grupo(g, len([m for m in g.membros if m.status == "ativo"]), None, True, _proxima_do_grupo(s, g.id)))

    f_perto = Filtros(raio_km=raio)
    ate_5km = [a for a in perto_todas if a["distancia_km"] is not None and a["distancia_km"] <= 5 and a["vagas"] > 0 and a["minutos_para_inicio"] <= 24 * 60]

    return {
        "centro": {"latitude": la, "longitude": ln, "informada": informada},
        "oportunidades_agora": len(ate_5km),
        "ao_vivo": telao.ao_vivo_agora(s),
        "urgentes": urgentes,
        "perto": perto,
        "campeonatos": descoberta.buscar_campeonatos(s, la, ln, Filtros(raio_km=raio * 2, com_vagas=True), limite=6),
        "grupos": grupos,
        "recomendados": recomendados,
        "arenas": descoberta.buscar_arenas(s, la, ln, f_perto, limite=6),
        "horarios": descoberta.buscar_horarios(s, la, ln, Filtros(raio_km=raio, quando="hoje"), limite=6),
        "esportes": [{"id": e.modalidade_id, "nome": e.modalidade.nome, "icone": e.modalidade.icone, "nivel": e.nivel} for e in usuario.esportes],
    }
