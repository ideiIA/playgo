"""Busca automática de atletas (RF-015) e aviso de oportunidades próximas (RF-016).

compatibilidade = modalidade + localização + distância + nível + disponibilidade + preferências."""

import math
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import notificacoes
from .db import agora
from .geo import caixa_sql, distancia_sql, formatar_km
from .models import ATIVAS, ORDEM_NIVEL, Atividade, Participacao, Usuario, UsuarioModalidade
from .serializadores import texto_quando, valor_texto

LIMITE_CANDIDATOS = 300


def periodos_do(inicio: datetime) -> set[str]:
    p = {"manha" if inicio.hour < 12 else "tarde" if inicio.hour < 18 else "noite"}
    if inicio.weekday() >= 5:
        p.add("fim_de_semana")
    return p


def compatibilidade(usuario: Usuario, a: Atividade, distancia_km: float | None) -> float | None:
    """Nota de 0 a 100, ou None quando o atleta não serve para a atividade."""
    nivel_usuario = usuario.nivel_em(a.modalidade_id)
    if nivel_usuario is None:
        return None  # não pratica o esporte

    # Nível: exato (ou atividade aberta a todos) vale tudo; um degrau de diferença vale menos; mais que isso, não.
    if a.nivel == "todos":
        f_nivel = 1.0
    else:
        diferenca = abs(ORDEM_NIVEL[nivel_usuario] - ORDEM_NIVEL[a.nivel])
        if diferenca > 1:
            return None
        f_nivel = 1.0 if diferenca == 0 else 0.6

    # Distância: até onde o atleta aceita ir
    if distancia_km is None:
        f_dist = 0.5
    else:
        if distancia_km > usuario.raio_km:
            return None
        f_dist = math.exp(-distancia_km / max(1, usuario.raio_km) * 2)

    # Disponibilidade: vazio = qualquer horário
    if usuario.disponibilidade:
        if not (set(usuario.disponibilidade) & periodos_do(a.inicio)):
            return None
        f_disp = 1.0
    else:
        f_disp = 0.5

    # Categoria
    if a.categoria != "misto" and usuario.sexo and (a.categoria == "masculino") != (usuario.sexo == "M"):
        return None

    return round(40 * f_nivel + 40 * f_dist + 20 * f_disp, 1)


def candidatos(s: SessaoORM, a: Atividade, limite: int = LIMITE_CANDIDATOS) -> list[tuple[Usuario, float, float]]:
    """Atletas compatíveis com a atividade: (usuário, distância km, nota), do melhor para o pior."""
    dist = distancia_sql(Usuario.latitude, Usuario.longitude, a.latitude, a.longitude)
    ja_na_atividade = select(Participacao.usuario_id).where(Participacao.atividade_id == a.id, Participacao.status.in_(ATIVAS))
    consulta = (
        select(Usuario, dist)
        .join(UsuarioModalidade, UsuarioModalidade.usuario_id == Usuario.id)
        .where(
            UsuarioModalidade.modalidade_id == a.modalidade_id,
            Usuario.ativo,
            Usuario.latitude.is_not(None),
            Usuario.id != a.organizador_id,
            Usuario.id.not_in(ja_na_atividade),
            caixa_sql(Usuario.latitude, Usuario.longitude, a.latitude, a.longitude, 60),
            dist <= Usuario.raio_km,
        )
        .limit(limite)
    )
    saida = []
    for usuario, km in s.execute(consulta).unique():
        nota = compatibilidade(usuario, a, km)
        if nota is not None:
            saida.append((usuario, km, nota))
    return sorted(saida, key=lambda t: -t[2])


def avisar_compativeis(s: SessaoORM, a: Atividade, motivo: str, chave: str | None = None) -> int:
    """Avisa quem combina com a atividade e aceita receber (modalidade, distância e tipo de aviso).
    motivo: 'falta_gente' | 'nova' | 'vaga' | 'urgente'. Devolve quantas pessoas foram avisadas."""
    if a.vagas <= 0 or a.status != "aberta" or a.visibilidade == "link":
        return 0
    n = agora()
    minutos = int((a.inicio - n).total_seconds() // 60)
    if minutos < -30:
        return 0
    m = a.modalidade
    avisados = 0
    for usuario, km, _ in candidatos(s, a):
        if not usuario.notif_vagas or km > usuario.notif_raio_km:
            continue
        perto = formatar_km(km)
        quando = texto_quando(a.inicio, n).lower().replace(" • ", " às ")
        if motivo in ("falta_gente", "urgente", "vaga"):
            qtd = a.vagas
            titulo = f"{m.icone} Falta{'m' if qtd > 1 else ''} {qtd} jogador{'es' if qtd > 1 else ''} perto de você!"
            corpo = f"{m.nome} {quando}, a {perto}."
            if 0 <= minutos <= 180:
                corpo += f" Restam {minutos} minutos para o início."
            tipo = "vaga"
        else:
            titulo = f"{m.icone} Novo {m.nome.lower()} a {perto} de você."
            corpo = f"{a.nome} · {quando} • {valor_texto(a.valor)} • {a.vagas} vaga{'s' if a.vagas > 1 else ''}."
            tipo = "vaga"
        k = f"{chave or motivo}:{a.id}" if motivo != "vaga" else (chave or f"vaga:{a.id}:{a.vagas}")
        if notificacoes.avisar(s, usuario.id, tipo, titulo, corpo, f"/atividades/{a.id}", a.id, k[:80]):
            avisados += 1
    return avisados
