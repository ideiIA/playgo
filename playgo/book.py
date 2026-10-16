"""Book do campeonato e da atividade: a história do evento (capa, números, campeões e finalistas, melhores momentos e as publicações
mais curtidas e comentadas) numa página para a organização divulgar.

Só a organização (e o administrador) vê o book enquanto ele não é publicado; depois de publicado, qualquer usuário logado que
pode ver o evento o acompanha. Pessoas aparecem sempre pelo @usuario."""

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

from . import campeonatos, chaves, convites, midia, publicacoes, vagas
from .db import agora
from .erros import NaoEncontrado
from .models import CONFIRMADO, P_PUBLICADA, Atividade, Campeonato, Jogo, Publicacao, Usuario

TOP = 3
MAX_GALERIA = 12


def _capa(arquivo: str | None) -> str | None:
    return f"/capa/{arquivo}" if arquivo else None


def _publicacoes(s: SessaoORM, usuario: Usuario, escopo: str, escopo_id: int) -> dict:
    """Mais curtidas, mais comentadas, galeria de fotos e totais das publicações visíveis do evento."""
    pubs = list(s.scalars(select(Publicacao).where(Publicacao.escopo == escopo, Publicacao.escopo_id == escopo_id, Publicacao.status == P_PUBLICADA).order_by(Publicacao.id.desc()).limit(500)).unique())
    ids = [p.id for p in pubs]
    curtidas = publicacoes.contar_curtidas(s, ids)
    comentarios = publicacoes._contagens(s, ids)
    minhas = publicacoes._minhas_curtidas(s, usuario, ids)
    ctx: dict = {}
    por_id = {p.id: p for p in pubs}

    def card(pid: int) -> dict:
        d = publicacoes.serializar(s, por_id[pid], usuario, ctx, comentarios, None, (curtidas, minhas))
        return {
            "id": d["id"], "autor": d["autor"]["arroba"], "texto": d["texto"], "quando": d["quando"], "n_curtidas": d["n_curtidas"], "n_comentarios": d["n_comentarios"],
            "midias": [m for m in d["midias"] if m["tipo"] == "foto"][:1],
        }

    mais_curtidas = [i for i in sorted(ids, key=lambda i: (-curtidas.get(i, 0), -i)) if curtidas.get(i, 0) > 0][:TOP]
    mais_comentadas = [i for i in sorted(ids, key=lambda i: (-comentarios.get(i, 0), -i)) if comentarios.get(i, 0) > 0][:TOP]
    fotos = []
    for p in pubs:
        for m in p.midias:
            if m.tipo == "foto" and len(fotos) < MAX_GALERIA:
                fotos.append({"url": midia.url(m.id, usuario.id), "miniatura": midia.url(m.id, usuario.id, True) if m.miniatura else midia.url(m.id, usuario.id), "autor": p.autor.arroba})
    return {
        "mais_curtidas": [card(i) for i in mais_curtidas],
        "mais_comentadas": [card(i) for i in mais_comentadas],
        "galeria": fotos,
        "totais": {"publicacoes": len(pubs), "curtidas": sum(curtidas.values()), "comentarios": sum(comentarios.values())},
    }


def _exigir_visivel(publicado: bool, gere: bool) -> None:
    if not gere and not publicado:
        raise NaoEncontrado("O book ainda não foi publicado pela organização.")


# ---------------------------------------------------------------- campeonato


def _podio(c: Campeonato, dados: dict) -> dict:
    """Campeão, vice e semifinalistas (eliminatória/grupos) ou os 3 primeiros da tabela/ranking (pontos corridos e Super 8)."""
    formato = dados.get("formato")
    podio: dict = {"campeao": dados.get("campeao"), "vice": None, "semifinalistas": [], "ranking": [], "grupos": []}
    rodadas = dados.get("rodadas") or []
    if formato in ("eliminatoria", "grupos") and rodadas:
        final = rodadas[-1]["jogos"][0] if rodadas[-1]["jogos"] else None
        if final and final["status"] == "encerrado" and final.get("vencedor_id"):
            a, b = final["a"], final["b"]
            vice = b if final["vencedor_id"] == (a or {}).get("id") else a
            podio["vice"] = vice
            podio["final"] = {"a": a, "b": b, "placar_a": final["placar_a"], "placar_b": final["placar_b"], "sets": final.get("sets") or []}
        if len(rodadas) >= 2:
            for j in rodadas[-2]["jogos"]:
                if j["status"] == "encerrado" and j.get("vencedor_id"):
                    perdedor = j["b"] if j["vencedor_id"] == (j["a"] or {}).get("id") else j["a"]
                    if perdedor:
                        podio["semifinalistas"].append(perdedor)
    elif formato == "pontos_corridos":
        tab = dados.get("classificacao") or []
        podio["ranking"] = [{"posicao": L["posicao"], "nome": L["equipe"]["nome"], "pontos": L["pontos"], "jogos": L["jogos"], "vitorias": L["vitorias"], "saldo": L["saldo"]} for L in tab[:TOP]]
    elif formato == "super8":
        for g in dados.get("ranking_super8") or []:
            podio["grupos"].append({"grupo": g["grupo"], "ranking": [{"posicao": L["posicao"], "nome": L["equipe"]["nome"], "pontos": L["pontos"], "vitorias": L["vitorias"], "saldo": L["saldo"]} for L in g["classificacao"][:TOP]]})
    return podio


def campeonato(s: SessaoORM, c: Campeonato, usuario: Usuario) -> dict:
    gere = campeonatos.pode_gerir(c, usuario)
    _exigir_visivel(c.book_publicado, gere)
    dados = chaves.chaveamento(s, c, usuario)
    jogos = [j for r in (dados.get("rodadas") or []) for j in r["jogos"]] + [j for g in (dados.get("grupos") or []) for r in g["rodadas"] for j in r["jogos"]]
    encerrados = [j for j in jogos if j["status"] == "encerrado" and not j.get("folga")]
    pontos = 0
    for j in encerrados:
        sets = j.get("sets") or []
        pontos += sum(x["a"] + x["b"] for x in sets) if sets else j["placar_a"] + j["placar_b"]
    equipes = [e["nome"] for e in dados.get("equipes") or []]
    return {
        "tipo": "campeonato",
        "id": c.id,
        "nome": c.nome,
        "capa_url": _capa(c.capa),
        "modalidade": {"nome": c.modalidade.nome, "icone": c.modalidade.icone},
        "categoria": c.categoria,
        "descricao": c.descricao,
        "premiacao": c.premiacao,
        "local": c.arena.nome if c.arena is not None else c.local_nome,
        "cidade": c.arena.cidade if c.arena is not None else None,
        "organizador": c.organizador.arroba,
        "status": c.status,
        "encerrado": c.status == "encerrado",
        "periodo": {"inicio": c.data_inicio.isoformat(), "fim": (c.data_fim or c.data_inicio).isoformat()},
        "formato": dados.get("formato_nome"),
        "numeros": {"equipes": len(equipes), "jogos": len(jogos), "jogos_encerrados": len(encerrados), "pontos_marcados": pontos},
        "equipes": equipes,
        "transmissoes": [
            {"video_id": j.yt_broadcast_id, "titulo": f"{chaves._nome_lado(j, 'a')} × {chaves._nome_lado(j, 'b')}", "rodada": j.rodada_nome}
            for j in s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id, Jogo.yt_estado == "encerrada", Jogo.yt_broadcast_id.is_not(None)).order_by(Jogo.rodada, Jogo.posicao))
        ],
        "podio": _podio(c, dados),
        "publicacoes": _publicacoes(s, usuario, "campeonato", c.id),
        "publicado": bool(c.book_publicado),
        "sou_organizacao": gere,
        "link_evento": f"/campeonatos/{c.id}",
        "link_chaves": f"/campeonatos/{c.id}/chaves" if dados.get("formato") else None,
    }


# ---------------------------------------------------------------- atividade


def atividade(s: SessaoORM, a: Atividade, usuario: Usuario) -> dict:
    gere = usuario.admin or usuario.id == a.organizador_id
    _exigir_visivel(a.book_publicado, gere)
    if not gere and not convites.pode_acessar_atividade(s, usuario, a):
        raise NaoEncontrado("Atividade não encontrada.")
    por_status = vagas.lista(s, a.id)
    confirmados = [p.usuario for p in por_status[CONFIRMADO]]
    return {
        "tipo": "atividade",
        "id": a.id,
        "nome": a.nome,
        "capa_url": _capa(a.capa),
        "modalidade": {"nome": a.modalidade.nome, "icone": a.modalidade.icone},
        "descricao": a.descricao,
        "local": a.local_nome,
        "organizador": a.organizador.arroba,
        "status": a.status,
        "encerrado": a.status != "aberta" or a.fim < agora(),
        "periodo": {"inicio": a.inicio.isoformat(timespec="minutes"), "fim": a.fim.isoformat(timespec="minutes")},
        "numeros": {"participantes": len(confirmados), "vagas": a.max_participantes, "nivel": a.nivel},
        "participantes": [{"arroba": u.arroba, "iniciais": u.iniciais, "organizador": u.id == a.organizador_id} for u in confirmados],
        "publicacoes": _publicacoes(s, usuario, "atividade", a.id),
        "publicado": bool(a.book_publicado),
        "sou_organizacao": gere,
        "link_evento": f"/atividades/{a.id}",
    }
