"""Telão do campeonato: uma tela limpa, só com o placar, para deixar numa TV, telão ou tablet no local do jogo.

A organização (e os mesários) escolhem qual jogo aparece; sem escolha ("automático") a tela mostra o jogo que estiver ao vivo e alterna
quando há vários. O telão abre por um link secreto (token), sem login, para ligar na TV sem digitar senha; "Renovar link" derruba o
link antigo. A tela só mostra o que o campeonato já mostra a todos: nomes das equipes, placar e horários.

Também aqui: `ao_vivo_agora`, a faixa "🔴 Ao vivo agora" do início."""

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import campeonatos, chaves
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import C_CANCELADO, J_AGENDADO, J_AO_VIVO, J_ENCERRADO, Campeonato, Jogo, Usuario

PONTOS = ("0", "15", "30", "40")
ROTACAO_S = 12
JANELA_AO_VIVO_H = 8  # jogo esquecido 'ao vivo' há mais que isso não vai para a faixa do início


def _exigir_conduzir(s: SessaoORM, c: Campeonato, u: Usuario) -> None:
    if not chaves.pode_pontuar(s, c, u):
        raise SemPermissao("Só a organização e os mesários escolhem o que aparece no telão.")


def _token(s: SessaoORM, c: Campeonato) -> str:
    if not c.telao_token:
        c.telao_token = secrets.token_urlsafe(18)
        s.flush()
    return c.telao_token


def _jogo(j: Jogo, c: Campeonato) -> dict:
    """O jogo pronto para a tela: nomes, placar grande (set em andamento), sets, e o ponto do game (15, 30, 40; tie-break em números)."""
    d = chaves.jogo_dict(j)
    r = chaves.regras_placar(c)
    abertos = [x for x in j.sets if not x.encerrado]
    atual = abertos[-1] if abertos else None
    fechados = [x for x in j.sets if x.encerrado]
    if r["modo"] == "sets":
        ref = atual or (fechados[-1] if fechados else None)
        grande = {"a": ref.pontos_a if ref else 0, "b": ref.pontos_b if ref else 0, "rotulo": f"Set {ref.numero}" if ref else "", "sets_a": j.placar_a, "sets_b": j.placar_b}
    else:
        grande = {"a": j.placar_a, "b": j.placar_b, "rotulo": "", "sets_a": None, "sets_b": None}
    d["grande"] = grande
    d["ponto"] = None
    if r["pontos_no_game"] and j.status == J_AO_VIVO:
        numero = atual.numero if atual else len(j.sets) + 1
        decisivo = c.placar_modo == "games" and r["melhor_de"] > 1 and numero >= r["melhor_de"]
        tie = bool(atual) and c.placar_modo == "games" and not decisivo and atual.pontos_a == atual.pontos_b == r["pontos_set"]
        if not decisivo:
            texto = (lambda n: str(n)) if tie else (lambda n: PONTOS[n] if n < len(PONTOS) else str(n))
            d["ponto"] = {"a": texto(j.ponto_a or 0), "b": texto(j.ponto_b or 0), "rotulo": "Tie-break" if tie else "Game"}
    d["estilo"] = r["estilo"]
    return d


def _todos(s: SessaoORM, c: Campeonato) -> list[Jogo]:
    return list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id).order_by(Jogo.rodada, Jogo.posicao).execution_options(populate_existing=True)))


def link(c: Campeonato) -> str | None:
    return f"/telao/{c.telao_token}" if c.telao_token else None


# ---------------------------------------------------------------- organização


def painel(s: SessaoORM, c: Campeonato, u: Usuario) -> dict:
    """Dados da tela de controle: link do telão e os jogos para escolher."""
    _exigir_conduzir(s, c, u)
    token = _token(s, c)
    s.commit()
    jogos = [j for j in _todos(s, c) if not j.folga and j.equipe_a_id and j.equipe_b_id]
    leve = lambda j: {"id": j.id, "rodada": j.rodada_nome, "status": j.status, "a": chaves.jogo_dict(j)["a"]["nome"], "b": chaves.jogo_dict(j)["b"]["nome"],  # noqa: E731
                      "placar": f"{j.placar_a} × {j.placar_b}", "inicio_previsto": j.inicio_previsto.isoformat(timespec="minutes") if j.inicio_previsto else None, "local": j.local}
    return {
        "link": f"/telao/{token}", "escolhido": c.telao_jogo_id,
        "mostrar_classificacao": bool(c.telao_classificacao), "tem_classificacao": chaves.classificacao_resumo(s, c) is not None,
        "ao_vivo": [leve(j) for j in jogos if j.status == J_AO_VIVO],
        "proximos": [leve(j) for j in jogos if j.status == J_AGENDADO][:20],
        "encerrados": [leve(j) for j in reversed(jogos) if j.status == J_ENCERRADO][:10],
    }


def escolher_jogo(s: SessaoORM, campeonato_id: int, por: Usuario, jogo_id: int | None) -> dict:
    """`jogo_id=None` volta ao automático (o telão segue o jogo ao vivo)."""
    c = campeonatos.obter(s, campeonato_id)
    _exigir_conduzir(s, c, por)
    if jogo_id is not None:
        j = s.get(Jogo, jogo_id)
        if j is None or j.campeonato_id != c.id or j.folga or not (j.equipe_a_id and j.equipe_b_id):
            raise ErroNegocio("Esse jogo não está disponível para o telão.")
    c.telao_jogo_id = jogo_id
    s.commit()
    return painel(s, c, por)


def mostrar_classificacao(s: SessaoORM, campeonato_id: int, por: Usuario, mostrar: bool) -> dict:
    """Liga/desliga a classificação geral no telão (alterna com o placar; sozinha quando não há jogo no ar)."""
    c = campeonatos.obter(s, campeonato_id)
    _exigir_conduzir(s, c, por)
    c.telao_classificacao = bool(mostrar)
    s.commit()
    return painel(s, c, por)


def renovar_link(s: SessaoORM, campeonato_id: int, por: Usuario) -> dict:
    c = campeonatos.obter(s, campeonato_id)
    _exigir_conduzir(s, c, por)
    c.telao_token = secrets.token_urlsafe(18)
    s.commit()
    return painel(s, c, por)


# ---------------------------------------------------------------- tela pública (por token)


def dados(s: SessaoORM, token: str) -> dict:
    """Telão por link secreto (TV/tablet do local, sem login): segue o que a organização escolheu."""
    c = s.scalar(select(Campeonato).where(Campeonato.telao_token == token)) if token and len(token) >= 16 else None
    if c is None or c.status == C_CANCELADO:
        raise NaoEncontrado("Telão não encontrado. Peça à organização o link atual.")
    return _montar(s, c)


def dados_espectador(s: SessaoORM, c: Campeonato, jogo_id: int | None = None) -> dict:
    """O mesmo telão para qualquer usuário logado acompanhar no próprio aparelho (com ou sem vídeo): por padrão segue a escolha da
    organização; a pessoa pode fixar outro jogo só para si. Ver não dá nenhum poder de transmitir."""
    if c.status == C_CANCELADO:
        raise NaoEncontrado("Campeonato não encontrado.")
    return _montar(s, c, jogo_id, com_opcoes=True)


def _montar(s: SessaoORM, c: Campeonato, jogo_id: int | None = None, com_opcoes: bool = False) -> dict:
    jogos = [j for j in _todos(s, c) if not j.folga and j.equipe_a_id and j.equipe_b_id]
    ao_vivo = [j for j in jogos if j.status == J_AO_VIVO]
    escolhido = next((j for j in jogos if j.id == (jogo_id or c.telao_jogo_id)), None)
    if escolhido is not None and escolhido.status == J_ENCERRADO and ao_vivo:
        escolhido = None  # o jogo fixado já acabou e outro está no ar: o telão segue o jogo ao vivo
    proximos = sorted((j for j in jogos if j.status == J_AGENDADO), key=lambda j: (j.inicio_previsto is None, j.inicio_previsto, j.rodada, j.posicao))[:4]
    ultimos = [j for j in reversed(jogos) if j.status == J_ENCERRADO][:4]
    return {
        "campeonato": {"nome": c.nome, "capa_url": f"/capa/{c.capa}" if c.capa else None, "modalidade": c.modalidade.nome, "icone": c.modalidade.icone, "status": c.status},
        "canal": chaves.canal_youtube(s, jogos), "modo": "fixo" if escolhido else "auto", "rotacao_s": ROTACAO_S,
        "opcoes": [
            {"id": j.id, "status": j.status, "rotulo": f"{'🔴 ' if j.status == J_AO_VIVO else ''}{chaves.jogo_dict(j)['a']['nome']} × {chaves.jogo_dict(j)['b']['nome']} — {j.rodada_nome}"}
            for j in (ao_vivo + [x for x in jogos if x.status == J_AGENDADO][:12] + [x for x in reversed(jogos) if x.status == J_ENCERRADO][:6])
        ] if com_opcoes else [],
        "jogos": [_jogo(j, c) for j in ([escolhido] if escolhido else ao_vivo)],
        "proximos": [_jogo(j, c) for j in proximos], "ultimos": [_jogo(j, c) for j in ultimos],
        # quem acompanha pelo app/site (com_opcoes) pode ligar a classificação só para si; o link da TV segue a escolha da organização
        "classificacao": chaves.classificacao_resumo(s, c) if (c.telao_classificacao or com_opcoes) else None, "classificacao_org": bool(c.telao_classificacao),
        "atualizado": agora().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- "ao vivo agora" no início


def ao_vivo_agora(s: SessaoORM, limite: int = 8) -> list[dict]:
    """Jogos que estão acontecendo neste momento em qualquer campeonato, para a faixa do início."""
    linhas = list(s.execute(
        select(Jogo, Campeonato).join(Campeonato, Campeonato.id == Jogo.campeonato_id)
        .where(Jogo.status == J_AO_VIVO, Campeonato.status != C_CANCELADO, Jogo.iniciado_em >= agora() - timedelta(hours=JANELA_AO_VIVO_H)).order_by(Jogo.iniciado_em.desc().nullslast(), Jogo.id.desc()).limit(limite)
    ).unique())
    saida = []
    for j, c in linhas:
        g = _jogo(j, c)["grande"]
        saida.append({
            "jogo_id": j.id, "campeonato_id": c.id, "campeonato": c.nome, "icone": c.modalidade.icone, "rodada": j.rodada_nome, "local": j.local,
            "a": chaves.jogo_dict(j)["a"]["nome"], "b": chaves.jogo_dict(j)["b"]["nome"], "placar_a": g["a"], "placar_b": g["b"],
            "sets_a": g["sets_a"], "sets_b": g["sets_b"], "link": f"/campeonatos/{c.id}/ao-vivo", "video": j.yt_estado == "ao_vivo",
        })
    return saida
