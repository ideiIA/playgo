"""Sorteio do chaveamento, acompanhamento dos jogos pelas chaves e jogo ao vivo com placar (RF-009).

Formatos: `eliminatoria` (mata-mata com folgas, o vencedor avança sozinho) e `pontos_corridos` (todos contra todos, com
classificação). Quem gere o campeonato (organizador, gestor da arena, admin) sorteia e conduz; os **mesários** que ele indica
também iniciam, marcam o placar e encerram. Qualquer pessoa logada acompanha.

Regras de corrida: o placar muda sob trava de linha (dois mesários clicando juntos não perdem ponto) e todo jogo guarda
uma linha do tempo (`JogoEvento`). No Vercel não há conexão aberta: quem acompanha consulta de poucos em poucos segundos."""

import math
import random
import secrets
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, campeonatos, notificacoes
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, SemPermissao
from .models import (
    ConfigExterna,
    C_ANDAMENTO,
    C_CANCELADO,
    C_ENCERRADO,
    E_CONFIRMADA,
    M_CONFIRMADO,
    J_AGENDADO,
    J_AO_VIVO,
    J_ENCERRADO,
    M_CONFIRMADO,
    Campeonato,
    CampeonatoMesario,
    Equipe,
    EquipeComponente,
    EquipeMembro,
    ChaveEquipe,
    Jogo,
    JogoEvento,
    JogoGame,
    JogoSet,
    Usuario,
)

E_ATLETA = "atleta"  # registro individual criado no sorteio do Super 8 com duplas (não é uma inscrição)

FORMATOS = {
    "eliminatoria": "Eliminatória (mata-mata)",
    "pontos_corridos": "Pontos corridos (todos contra todos)",
    "grupos": "Fase de grupos + mata-mata",
    "super8": "Super 8 (grupos de 8 atletas, duplas que mudam a cada rodada)",
}
# Tabela do Super 8: 7 rodadas × 2 jogos. Cada atleta (0-7, na ordem do sorteio) joga com cada outro uma vez como parceiro e
# enfrenta cada outro exatamente duas vezes. Cada jogo é (dupla A) x (dupla B).
SUPER8 = (
    (((0, 1), (2, 3)), ((4, 5), (6, 7))),
    (((0, 4), (1, 5)), ((2, 6), (3, 7))),
    (((0, 6), (1, 7)), ((2, 4), (3, 5))),
    (((0, 2), (4, 6)), ((1, 3), (5, 7))),
    (((0, 5), (2, 7)), ((1, 4), (3, 6))),
    (((0, 7), (3, 4)), ((1, 6), (2, 5))),
    (((0, 3), (5, 6)), ((1, 2), (4, 7))),
)
LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PONTOS = {"vitoria": 3, "empate": 1, "derrota": 0}
MAX_PLACAR = 999


# ---------------------------------------------------------------- permissões


def eh_mesario(s: SessaoORM, c: Campeonato, u: Usuario) -> bool:
    return s.get(CampeonatoMesario, (c.id, u.id)) is not None


def pode_pontuar(s: SessaoORM, c: Campeonato, u: Usuario) -> bool:
    return campeonatos.pode_gerir(c, u) or eh_mesario(s, c, u)


def _exigir_pontuar(s: SessaoORM, c: Campeonato, u: Usuario) -> None:
    if not pode_pontuar(s, c, u):
        raise SemPermissao("Só a organização e os mesários indicados conduzem os jogos.")


def mesarios(s: SessaoORM, c: Campeonato) -> list[dict]:
    linhas = s.scalars(select(CampeonatoMesario).where(CampeonatoMesario.campeonato_id == c.id).order_by(CampeonatoMesario.criado_em)).unique()
    return [{"usuario_id": m.usuario_id, "arroba": m.usuario.arroba} for m in linhas]


def adicionar_mesario(s: SessaoORM, campeonato_id: int, por: Usuario, usuario_id: int) -> list[dict]:
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    alvo = s.get(Usuario, usuario_id)
    if alvo is None or not alvo.ativo or not alvo.usuario:
        raise NaoEncontrado("Pessoa não encontrada.")
    if s.get(CampeonatoMesario, (c.id, alvo.id)) is None:
        s.add(CampeonatoMesario(campeonato_id=c.id, usuario_id=alvo.id))
        auditoria.registrar(s, por.id, "mesario_adicionar", "campeonato", c.id, mesario=alvo.id)
        notificacoes.avisar(s, alvo.id, "campeonato", f"📋 Você é mesário de {c.nome}", "Você pode iniciar jogos e marcar o placar ao vivo.", f"/campeonatos/{c.id}/ao-vivo", None, f"mesario:{c.id}:{alvo.id}")
        s.commit()
    return mesarios(s, c)


def remover_mesario(s: SessaoORM, campeonato_id: int, por: Usuario, usuario_id: int) -> list[dict]:
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    m = s.get(CampeonatoMesario, (c.id, usuario_id))
    if m is not None:
        s.delete(m)
        auditoria.registrar(s, por.id, "mesario_remover", "campeonato", c.id, mesario=usuario_id)
        s.commit()
    return mesarios(s, c)


# ---------------------------------------------------------------- sorteio


def _semeadura(tamanho: int) -> list[int]:
    """Ordem padrão de chave (1×8, 4×5, 2×7, 3×6…): os melhores cabeças só se cruzam o mais tarde possível."""
    ordem = [1]
    while len(ordem) < tamanho:
        n = len(ordem) * 2
        ordem = [x for s_ in ordem for x in (s_, n + 1 - s_)]
    return ordem


def _nome_rodada(restantes: int, rodada: int) -> str:
    return {2: "Final", 4: "Semifinal", 8: "Quartas de final", 16: "Oitavas de final"}.get(restantes, f"Fase {rodada}")


def _pessoas(s: SessaoORM, equipe_id: int) -> set[int]:
    e = s.get(Equipe, equipe_id)
    if e is None:
        return set()
    ids = set(s.scalars(select(EquipeMembro.usuario_id).where(EquipeMembro.equipe_id == equipe_id, EquipeMembro.status == M_CONFIRMADO)))
    return ids | {e.capitao_id}


def _avisar_equipes(s: SessaoORM, c: Campeonato, equipes: list[int | None], titulo: str, corpo: str, chave: str, excluir: int | None = None) -> None:
    link = f"/campeonatos/{c.id}/chaves"
    for eid in {e for e in equipes if e}:
        for uid in _pessoas(s, eid):
            if uid != excluir:
                notificacoes.avisar(s, uid, "campeonato", titulo, corpo, link, None, f"{chave}:{uid}")


def sortear(
    s: SessaoORM, campeonato_id: int, por: Usuario, formato: str, grupos: int | None = None, classificam: int | None = None, cabecas: list[int] | None = None,
    classificam_geral: int | None = None,
) -> dict:
    """`cabecas` = ids das equipes cabeças de chave, em ordem (o primeiro é o nº 1). Na eliminatória ocupam as melhores posições da chave
    (e as folgas); na fase de grupos cada grupo recebe no máximo um. As demais equipes são sorteadas."""
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    if formato not in FORMATOS:
        raise ErroNegocio("Escolha o formato: eliminatória, pontos corridos, fase de grupos ou Super 8.")
    if c.status == C_CANCELADO:
        raise ErroNegocio("Este campeonato foi cancelado.")
    equipes = list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == E_CONFIRMADA).order_by(Equipe.id)))
    if len(equipes) < 2:
        raise ErroNegocio("Confirme pelo menos 2 equipes para sortear o chaveamento.")
    cabecas = list(cabecas or [])
    por_id = {e.id: e for e in equipes}
    if len(set(cabecas)) != len(cabecas) or any(i not in por_id for i in cabecas):
        raise ErroNegocio("Os cabeças de chave precisam ser equipes confirmadas, sem repetir.")
    q = None
    if formato == "grupos":
        n = len(equipes)
        if n < 4:
            raise ErroNegocio("A fase de grupos precisa de pelo menos 4 equipes confirmadas.")
        if not grupos or not 2 <= grupos <= n // 2:
            raise ErroNegocio(f"Escolha de 2 a {n // 2} grupos (cada grupo precisa de pelo menos 2 equipes).")
        if len(cabecas) > grupos:
            raise ErroNegocio("Há mais cabeças de chave do que grupos: cada grupo recebe no máximo um.")
        if classificam_geral:  # as N melhores no geral, numa tabela única: 1ºxN, 2ºx(N-1)…
            if not 2 <= classificam_geral <= n:
                raise ErroNegocio(f"Classificam de 2 a {n} equipes no geral.")
            q = None
        else:
            q = classificam or 2
            if not 1 <= q <= n // grupos:
                raise ErroNegocio(f"Classificam de 1 a {n // grupos} equipes por grupo.")
            if grupos * q < 2:
                raise ErroNegocio("O mata-mata precisa de pelo menos 2 classificados.")
    elif formato == "pontos_corridos":
        cabecas = []  # todos jogam contra todos: a posição na tabela não depende do sorteio
    elif formato == "super8":
        if c.atletas_por_equipe == 2:
            if len(equipes) < 4 or len(equipes) % 4:
                raise ErroNegocio(f"O Super 8 usa grupos de 4 duplas (8 atletas): confirme 4, 8, 12… duplas (há {len(equipes)}).")
            for e in equipes:
                if len(_atletas_da_dupla(s, e)) != 2:
                    raise ErroNegocio(f"A dupla \"{e.nome}\" precisa de 2 atletas: cadastre os dois no elenco (nome e RG) ou confirme o parceiro convidado.")
        elif c.atletas_por_equipe == 1:
            if len(equipes) < 8 or len(equipes) % 8:
                raise ErroNegocio(f"O Super 8 individual usa grupos de 8 atletas: confirme 8, 16, 24… (há {len(equipes)}).")
        else:
            raise ErroNegocio("O Super 8 usa duplas (2 atletas por equipe) ou atletas individuais (1). Edite o campeonato e ajuste 'Atletas por equipe'.")
        cabecas = []  # as duplas mudam a cada rodada; a posição no sorteio só define a tabela
    _limpar_chaves(s, c)
    if formato == "super8":  # cada jogo é um set único em games; a organização pode mudar a quantidade de games antes do 1º jogo
        if c.placar_modo != "total":  # o total fixo de games, se a organização já escolheu, é mantido
            c.placar_modo, c.sets_melhor_de, c.diferenca_set = "games", 1, 2
            if not 2 <= (c.pontos_set or 0) <= 12:
                c.pontos_set = 6
            c.pontos_tiebreak = c.pontos_tiebreak or 10

    semente = secrets.randbits(62)
    rnd = random.Random(semente)
    resto = [e for e in equipes if e.id not in cabecas]
    rnd.shuffle(resto)
    if formato == "eliminatoria":
        _montar_eliminatoria(s, c, [por_id[i] for i in cabecas] + resto)  # a ordem é a de cabeça de chave
        s.add_all(ChaveEquipe(campeonato_id=c.id, equipe_id=i, cabeca=n_ + 1) for n_, i in enumerate(cabecas))
    elif formato == "grupos":
        for g, lista in enumerate(_distribuir(por_id, cabecas, resto, grupos)):
            _montar_pontos_corridos(s, c, lista, grupo=LETRAS[g], fase="grupos")
            for e in lista:
                s.add(ChaveEquipe(campeonato_id=c.id, equipe_id=e.id, grupo=LETRAS[g], cabeca=cabecas.index(e.id) + 1 if e.id in cabecas else None))
    elif formato == "super8":
        # com duplas inscritas, a 1ª rodada é dupla × dupla; da 2ª em diante os atletas se misturam
        por_grupo = 4 if c.atletas_por_equipe == 2 else 8
        n_grupos = len(resto) // por_grupo
        for g in range(n_grupos):
            bloco = resto[g * por_grupo:(g + 1) * por_grupo]
            atletas = _criar_atletas_das_duplas(s, c, bloco) if c.atletas_por_equipe == 2 else bloco
            letra = LETRAS[g] if n_grupos > 1 else None
            _montar_super8(s, c, atletas, letra)
            if letra:
                s.add_all(ChaveEquipe(campeonato_id=c.id, equipe_id=a.id, grupo=letra) for a in atletas)
    else:
        _montar_pontos_corridos(s, c, resto)
    c.grupos_qtd, c.classificam = (grupos, q) if formato == "grupos" else (None, None)
    c.classificam_geral = classificam_geral if formato == "grupos" and classificam_geral else None
    c.formato, c.sorteado_em, c.sorteio_semente = formato, agora(), semente
    if c.status != C_ENCERRADO:
        c.status = C_ANDAMENTO
    auditoria.registrar(s, por.id, "campeonato_sorteio", "campeonato", c.id, formato=formato, equipes=len(equipes), semente=semente, grupos=grupos, cabecas=cabecas, classificam_geral=c.classificam_geral)
    s.flush()
    _avisar_equipes(s, c, [e.id for e in equipes], f"🎲 Sorteio feito: {c.nome}", "Veja seus jogos nas chaves do campeonato.", f"sorteio:{c.id}:{semente}", excluir=por.id)
    s.commit()
    return {"formato": formato, "equipes": len(equipes), "semente": semente}


def _equipes_no_sorteio(jogos: list[Jogo]) -> set[int]:
    return {i for j in jogos for i in (j.equipe_a_id, j.equipe_b_id, j.equipe_a2_id, j.equipe_b2_id) if i}


def trocar_equipes(s: SessaoORM, campeonato_id: int, por: Usuario, equipe_a_id: int, equipe_b_id: int) -> dict:
    """Troca duas equipes de lugar no sorteio (grupo, confronto, cabeça de chave) ou põe no lugar de uma equipe do sorteio outra
    confirmada que ficou de fora. Só enquanto nenhum jogo das duas começou; folgas da eliminatória acompanham. Não vale no Super 8."""
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    if c.formato is None:
        raise ErroNegocio("Faça o sorteio antes de trocar equipes.")
    if c.formato == "super8":
        raise ErroNegocio("O Super 8 não permite trocar equipes de lugar: sorteie de novo.")
    if equipe_a_id == equipe_b_id:
        raise ErroNegocio("Escolha duas equipes diferentes.")
    jogos = list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id).with_for_update(of=Jogo).execution_options(populate_existing=True)))
    no_sorteio = _equipes_no_sorteio(jogos)
    equipes = {e.id: e for e in s.scalars(select(Equipe).where(Equipe.id.in_([equipe_a_id, equipe_b_id]), Equipe.campeonato_id == c.id, Equipe.status == E_CONFIRMADA))}
    if len(equipes) != 2:
        raise ErroNegocio("As duas equipes precisam estar confirmadas neste campeonato.")
    if equipe_a_id not in no_sorteio:  # a equipe de fora entra no lugar da que está no sorteio
        equipe_a_id, equipe_b_id = equipe_b_id, equipe_a_id
    if equipe_a_id not in no_sorteio:
        raise ErroNegocio("Nenhuma das duas equipes está no sorteio.")
    ids = {equipe_a_id, equipe_b_id}
    envolvidos = [j for j in jogos if ids & {j.equipe_a_id, j.equipe_b_id, j.equipe_a2_id, j.equipe_b2_id, j.vencedor_id}]
    if any(j.status != J_AGENDADO and not j.folga for j in envolvidos):
        raise ErroNegocio("Uma das equipes já tem jogo começado ou encerrado: não dá mais para trocar de lugar.")
    troca = {equipe_a_id: equipe_b_id, equipe_b_id: equipe_a_id}
    for j in envolvidos:
        for campo in ("equipe_a_id", "equipe_b_id", "equipe_a2_id", "equipe_b2_id", "vencedor_id"):
            v = getattr(j, campo)
            if v in troca:
                setattr(j, campo, troca[v])
    marcas_antigas = list(s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id, ChaveEquipe.equipe_id.in_(ids))))
    marcas = {k.equipe_id: (k.grupo, k.cabeca) for k in marcas_antigas}
    for k in marcas_antigas:
        s.delete(k)
    s.flush()
    for origem, destino in troca.items():
        if origem in marcas:
            grupo, cabeca = marcas[origem]
            s.add(ChaveEquipe(campeonato_id=c.id, equipe_id=destino, grupo=grupo, cabeca=cabeca))
    auditoria.registrar(s, por.id, "chave_troca", "campeonato", c.id, a=equipe_a_id, b=equipe_b_id)
    s.commit()
    s.expire_all()  # a sessão não expira ao salvar: relações como jogo.equipe_a ficariam velhas
    return {"trocadas": [equipes[equipe_a_id].nome, equipes[equipe_b_id].nome]}


def _limpar_chaves(s: SessaoORM, c: Campeonato) -> None:
    """Apaga o sorteio anterior, se nada começou (folgas da eliminatória já nascem encerradas e não contam)."""
    antigos = list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id)))
    if any(j.status != J_AGENDADO and not j.folga for j in antigos):
        raise ErroNegocio("Já há jogos começados ou encerrados. Não dá para sortear de novo sem perder o placar.")
    for j in antigos:
        j.proximo_id = None
    s.flush()
    for j in antigos:
        s.delete(j)
    for k in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id)):
        s.delete(k)
    for a in s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == E_ATLETA)):
        s.delete(a)
    s.flush()


def _distribuir(por_id: dict[int, Equipe], cabecas: list[int], resto: list[Equipe], grupos: int) -> list[list[Equipe]]:
    """Cabeça nº k vai para o grupo k; as outras equipes (já embaralhadas) vão sempre para o grupo com menos equipes."""
    n = len(por_id)
    tamanhos = [n // grupos + (1 if g < n % grupos else 0) for g in range(grupos)]
    lista: list[list[Equipe]] = [[] for _ in range(grupos)]
    for g, i in enumerate(cabecas):
        lista[g].append(por_id[i])
    for e in resto:
        g = min((g for g in range(grupos) if len(lista[g]) < tamanhos[g]), key=lambda g: (len(lista[g]), g))
        lista[g].append(e)
    return lista


def _montar_eliminatoria(s: SessaoORM, c: Campeonato, equipes: list[Equipe], fase: str | None = None) -> None:
    """`equipes` já vem na ordem de cabeça de chave (a 1ª é a nº 1); quem sobra de vaga (folga) são os melhores colocados."""
    n = len(equipes)
    tamanho = 1 << math.ceil(math.log2(n))
    rodadas = int(math.log2(tamanho))
    por_rodada: list[list[Jogo]] = []
    for r in range(1, rodadas + 1):
        restantes = tamanho >> (r - 1)
        jogos = [Jogo(campeonato_id=c.id, rodada=r, posicao=p, rodada_nome=_nome_rodada(restantes, r), fase=fase) for p in range(restantes // 2)]
        s.add_all(jogos)
        por_rodada.append(jogos)
    ordem = _semeadura(tamanho)
    for p, j in enumerate(por_rodada[0]):
        a, b = ordem[2 * p], ordem[2 * p + 1]
        j.equipe_a_id = equipes[a - 1].id if a <= n else None
        j.equipe_b_id = equipes[b - 1].id if b <= n else None
    s.flush()
    for r, jogos in enumerate(por_rodada[:-1]):
        for p, j in enumerate(jogos):
            prox = por_rodada[r + 1][p // 2]
            j.proximo_id, j.proximo_lado = prox.id, "a" if p % 2 == 0 else "b"
    s.flush()
    for j in por_rodada[0]:  # folga: quem não tem adversário já passa para a rodada seguinte
        if (j.equipe_a_id is None) != (j.equipe_b_id is None):
            j.folga, j.status = True, J_ENCERRADO
            j.vencedor_id = j.equipe_a_id or j.equipe_b_id
            j.encerrado_em = agora()
            _avancar(s, j)


def _montar_pontos_corridos(s: SessaoORM, c: Campeonato, equipes: list[Equipe], grupo: str | None = None, fase: str | None = None) -> None:
    lista: list[Equipe | None] = list(equipes)
    if len(lista) % 2:
        lista.append(None)
    n = len(lista)
    for r in range(n - 1):
        pos = 0
        for i in range(n // 2):
            a, b = lista[i], lista[n - 1 - i]
            if a is None or b is None:
                continue
            if r % 2 and i == 0:
                a, b = b, a
            nome = f"Grupo {grupo} · Rodada {r + 1}" if grupo else f"Rodada {r + 1}"
            s.add(Jogo(campeonato_id=c.id, rodada=r + 1, posicao=pos, rodada_nome=nome, equipe_a_id=a.id, equipe_b_id=b.id, grupo=grupo, fase=fase))
            pos += 1
        lista = [lista[0], lista[-1], *lista[1:-1]]


def _atletas_da_dupla(s: SessaoORM, e: Equipe) -> list[tuple[str, int]]:
    """Os atletas da dupla como (nome, quem é avisado). O elenco cadastrado (nome e RG, sem conta) vale primeiro; sem ele, o capitão
    e o parceiro que aceitou o convite (aparecem pelo @usuário)."""
    elenco = list(s.scalars(select(EquipeComponente).where(EquipeComponente.equipe_id == e.id, EquipeComponente.funcao == "atleta").order_by(EquipeComponente.id)))
    if len(elenco) == 2:
        return [(x.nome.strip()[:100], e.capitao_id) for x in elenco]
    ids = set(s.scalars(select(EquipeMembro.usuario_id).where(EquipeMembro.equipe_id == e.id, EquipeMembro.status == M_CONFIRMADO))) | {e.capitao_id}
    usuarios = [s.get(Usuario, i) for i in sorted(ids)]
    return [(u.arroba, u.id) for u in usuarios]


def _criar_atletas_das_duplas(s: SessaoORM, c: Campeonato, duplas: list[Equipe]) -> list[Equipe]:
    """Super 8 com duplas: cada atleta ganha um registro individual (status "atleta", fora de todas as listas de inscrição) para entrar
    nas duplas que mudam a cada rodada. A ordem é dupla a dupla, então os índices 0-1, 2-3, 4-5 e 6-7 da tabela são as duplas inscritas."""
    saida: list[Equipe] = []
    for d in duplas:
        for nome, avisar_id in _atletas_da_dupla(s, d):
            a = Equipe(campeonato_id=c.id, capitao_id=avisar_id, nome=nome, status=E_ATLETA)
            s.add(a)
            saida.append(a)
    s.flush()
    return saida


def _ranking_super8(s: SessaoORM, c: Campeonato, jogos: list[Jogo]) -> list[dict]:
    """Um ranking por grupo (um só quando o campeonato tem 8 atletas)."""
    atletas = _atletas_do_super8(s, c)
    letras = sorted({j.grupo for j in jogos if j.grupo})
    if not letras:
        return [{"grupo": None, "classificacao": classificacao_super8(jogos, atletas, c)}]
    marcas = {k.equipe_id: k.grupo for k in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id))}
    return [
        {"grupo": g, "classificacao": classificacao_super8([j for j in jogos if j.grupo == g], [a for a in atletas if marcas.get(a.id) == g], c)}
        for g in letras
    ]


def _atletas_do_super8(s: SessaoORM, c: Campeonato) -> list[Equipe]:
    """Os atletas que aparecem no ranking: os registros individuais criados no sorteio ou, no Super 8 individual, as próprias inscrições."""
    atletas = list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == E_ATLETA).order_by(Equipe.id)))
    return atletas or list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == E_CONFIRMADA).order_by(Equipe.id)))


def _montar_super8(s: SessaoORM, c: Campeonato, atletas: list[Equipe], grupo: str | None = None) -> None:
    """`atletas`: os 8 de um grupo, dupla a dupla. 7 rodadas de 2 jogos (duas quadras); todos jogam em todas as rodadas.
    Com vários grupos cada um faz o seu Super 8 (posição do jogo = grupo × 2 + jogo, para manter a ordem na rodada)."""
    base = 0 if grupo is None else LETRAS.index(grupo) * 2
    for r, partidas in enumerate(SUPER8, 1):
        for p, ((a1, a2), (b1, b2)) in enumerate(partidas):
            s.add(Jogo(
                campeonato_id=c.id, rodada=r, posicao=base + p, grupo=grupo,
                rodada_nome=f"Grupo {grupo} · Rodada {r}" if grupo else f"Rodada {r}",
                equipe_a_id=atletas[a1].id, equipe_a2_id=atletas[a2].id, equipe_b_id=atletas[b1].id, equipe_b2_id=atletas[b2].id,
            ))


def _ids_do_jogo(j: Jogo) -> list[int]:
    return [i for i in (j.equipe_a_id, j.equipe_a2_id, j.equipe_b_id, j.equipe_b2_id) if i]


def _nome_lado(j: Jogo, lado: str) -> str:
    e, p = (j.equipe_a, j.equipe_a2) if lado == "a" else (j.equipe_b, j.equipe_b2)
    return _nome(e) + (f" + {_nome(p)}" if p is not None else "")


def _avancar(s: SessaoORM, j: Jogo) -> None:
    if j.proximo_id is None or j.vencedor_id is None:
        return
    prox = s.get(Jogo, j.proximo_id)
    vencedor = s.get(Equipe, j.vencedor_id)  # a relação também: a sessão não expira ao salvar e `prox.equipe_a` ficaria velha
    if j.proximo_lado == "a":
        prox.equipe_a = vencedor
    else:
        prox.equipe_b = vencedor


# ---------------------------------------------------------------- jogo ao vivo


def _travar(s: SessaoORM, campeonato_id: int, jogo_id: int) -> tuple[Campeonato, Jogo]:
    c = campeonatos.obter(s, campeonato_id)
    j = s.scalar(select(Jogo).where(Jogo.id == jogo_id, Jogo.campeonato_id == c.id).with_for_update(of=Jogo).execution_options(populate_existing=True))
    if j is None:
        raise NaoEncontrado("Jogo não encontrado.")
    return c, j


def _evento(s: SessaoORM, j: Jogo, tipo: str, por: Usuario, texto: str | None = None, equipe_id: int | None = None, pa: int | None = None, pb: int | None = None) -> None:
    """`pa`/`pb`: placar a mostrar na linha do tempo (nos sets, os pontos do set); sem eles vale o placar do jogo."""
    s.add(JogoEvento(jogo_id=j.id, tipo=tipo, texto=texto, equipe_id=equipe_id, placar_a=j.placar_a if pa is None else pa, placar_b=j.placar_b if pb is None else pb, autor_id=por.id))


# ---------------------------------------------------------------- pontuação por sets (vôlei, tênis…)


def modo_total(c: Campeonato) -> bool:
    """Total fixo de games (Super 8): todos os games do jogo são disputados; cada game conta para o ranking."""
    return c.placar_modo == "total"


def _total_games(c: Campeonato) -> int:
    return c.pontos_set or 5


def modo_sets(c: Campeonato) -> bool:
    return (c.placar_modo or "simples") in ("sets", "games")


def regras_placar(c: Campeonato) -> dict:
    melhor_de = c.sets_melhor_de or 3
    return {
        "modo": "sets" if modo_sets(c) else "simples", "estilo": c.placar_modo if (modo_sets(c) or c.placar_modo == "total") else "simples",
        "total_games": (c.pontos_set or 5) if c.placar_modo == "total" else None,
        "pontos_no_game": c.placar_modo in ("games", "total"),
        "unidade": "games" if c.placar_modo == "games" else "pontos", "melhor_de": melhor_de, "sets_para_vencer": melhor_de // 2 + 1,
        "pontos_set": c.pontos_set or 25, "pontos_tiebreak": c.pontos_tiebreak or 15, "diferenca": c.diferenca_set or 2,
    }


def _alvo_do_set(c: Campeonato, numero: int) -> int:
    r = regras_placar(c)
    return r["pontos_tiebreak"] if r["melhor_de"] > 1 and numero >= r["melhor_de"] else r["pontos_set"]


def _set_decisivo(c: Campeonato, numero: int) -> bool:
    r = regras_placar(c)
    return r["melhor_de"] > 1 and numero >= r["melhor_de"]


def _set_fechado(c: Campeonato, numero: int, a: int, b: int) -> bool:
    alvo, topo = _alvo_do_set(c, numero), max(a, b)
    if topo >= alvo and abs(a - b) >= regras_placar(c)["diferenca"]:
        return True
    # em games, o set comum termina no tie-break: 6-6 vira 7-6 (o decisivo é um super tie-break, pontos com diferença)
    return c.placar_modo == "games" and not _set_decisivo(c, numero) and topo >= alvo + 1


def _resultado_do_set_vale(c: Campeonato, numero: int, a: int, b: int) -> bool:
    """Set lançado de uma vez: em games só valem os placares que existem (6-4, 7-5, 7-6…), nunca 8-6 ou 9-7."""
    if not _set_fechado(c, numero, a, b):
        return False
    if c.placar_modo != "games" or _set_decisivo(c, numero):
        return True
    alvo, topo, base = _alvo_do_set(c, numero), max(a, b), min(a, b)
    return (topo == alvo and base <= alvo - 2) or (topo == alvo + 1 and base in (alvo - 1, alvo))


def _sets_do_jogo(s: SessaoORM, j: Jogo) -> list[JogoSet]:
    return list(s.scalars(select(JogoSet).where(JogoSet.jogo_id == j.id).order_by(JogoSet.numero).execution_options(populate_existing=True)))


def _contar_sets(j: Jogo, sets: list[JogoSet]) -> None:
    j.placar_a = sum(1 for x in sets if x.encerrado and x.pontos_a > x.pontos_b)
    j.placar_b = sum(1 for x in sets if x.encerrado and x.pontos_b > x.pontos_a)


def configurar_placar(s: SessaoORM, campeonato_id: int, por: Usuario, modo: str, melhor_de: int = 3, pontos_set: int = 25, pontos_tiebreak: int = 15, diferenca: int = 2) -> dict:
    """Regras de pontuação do campeonato. Só enquanto nenhum jogo começou (não mistura placar simples com sets)."""
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    if modo not in ("simples", "sets", "games", "total"):
        raise ErroNegocio("Escolha a pontuação: simples, por sets, por games ou total fixo de games.")
    if c.formato == "super8" and (modo not in ("games", "total") or (modo == "games" and melhor_de != 1)):
        raise ErroNegocio("No Super 8 cada jogo é um set único em games, ou um total fixo de games.")
    if any(j.status != J_AGENDADO and not j.folga for j in s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id))):
        raise ErroNegocio("Já há jogos começados: não dá mais para trocar as regras de pontuação.")
    if modo in ("sets", "games"):
        if modo == "games":
            diferenca = 2
            if not 2 <= pontos_set <= 12:
                raise ErroNegocio("Os games do set vão de 2 a 12 (o padrão do beach tennis é 6).")
        if melhor_de not in (1, 3, 5, 7):
            raise ErroNegocio("Escolha jogar em 1, 3, 5 ou 7 sets (vence quem ganhar a maioria).")
        if not 1 <= pontos_set <= 99 or not 1 <= pontos_tiebreak <= 99:
            raise ErroNegocio("Os pontos do set vão de 1 a 99.")
        if not 1 <= diferenca <= 5:
            raise ErroNegocio("A vantagem mínima para fechar o set vai de 1 a 5 pontos.")
        c.sets_melhor_de, c.pontos_set, c.pontos_tiebreak, c.diferenca_set = melhor_de, pontos_set, pontos_tiebreak, diferenca
    if modo == "total":
        if not 1 <= pontos_set <= 20:
            raise ErroNegocio("O total de games por jogo vai de 1 a 20.")
        c.sets_melhor_de, c.pontos_set = 1, pontos_set
    c.placar_modo = modo
    auditoria.registrar(s, por.id, "campeonato_regras_placar", "campeonato", c.id, modo=modo, melhor_de=melhor_de, pontos_set=pontos_set, pontos_tiebreak=pontos_tiebreak, diferenca=diferenca)
    s.commit()
    return regras_placar(c)


def _marcar_sets(s: SessaoORM, c: Campeonato, j: Jogo, por: Usuario, lado: str, delta: int) -> Jogo:
    sets = _sets_do_jogo(s, j)
    r = regras_placar(c)
    equipe = j.equipe_a if lado == "a" else j.equipe_b
    quem = _nome_lado(j, lado)
    if delta > 0:
        if max(j.placar_a, j.placar_b) >= r["sets_para_vencer"]:
            raise ErroNegocio("O jogo já está decidido. Encerre-o (ou corrija com −1).")
        atual = sets[-1] if sets and not sets[-1].encerrado else None
        if atual is None:
            atual = JogoSet(jogo_id=j.id, numero=len(sets) + 1, pontos_a=0, pontos_b=0, encerrado=False)
            s.add(atual)
            sets.append(atual)
        if lado == "a":
            atual.pontos_a += 1
        else:
            atual.pontos_b += 1
        texto = f"+1 {quem}"
    else:
        atual = sets[-1] if sets else None
        if atual is None or (atual.pontos_a if lado == "a" else atual.pontos_b) <= 0:
            return j
        if lado == "a":
            atual.pontos_a -= 1
        else:
            atual.pontos_b -= 1
        texto = f"Correção: -1 {quem}"
    antes = atual.encerrado
    atual.encerrado = _set_fechado(c, atual.numero, atual.pontos_a, atual.pontos_b)
    _contar_sets(j, sets)
    if atual.encerrado and not antes:
        ganhou = _nome_lado(j, "a" if atual.pontos_a > atual.pontos_b else "b")
        texto += f" · Fim do set {atual.numero}: {atual.pontos_a}×{atual.pontos_b} para {ganhou} (sets {j.placar_a}×{j.placar_b})"
    elif antes and not atual.encerrado:
        texto += f" · Set {atual.numero} reaberto"
    _evento(s, j, "placar", por, texto[:200], equipe.id if equipe else None, atual.pontos_a, atual.pontos_b)
    auditoria.registrar(s, por.id, "jogo_placar", "jogo", j.id, set=atual.numero, pontos=f"{atual.pontos_a}x{atual.pontos_b}", sets=f"{j.placar_a}x{j.placar_b}")
    s.commit()
    return j


def definir_sets(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, sets: list[list[int]]) -> Jogo:
    """Lança o resultado inteiro de uma vez ([[25,20],[18,25],[15,12]]). Cada set tem de estar fechado pelas regras e o último decide o jogo."""
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    _exigir_ao_vivo(j)
    if not modo_sets(c):
        raise ErroNegocio("Este campeonato usa placar simples, sem sets.")
    r = regras_placar(c)
    if not sets or len(sets) > r["melhor_de"]:
        raise ErroNegocio(f"Informe de 1 a {r['melhor_de']} sets.")
    ga = gb = 0
    for i, par in enumerate(sets, 1):
        if len(par) != 2 or any(not isinstance(x, int) or x < 0 or x > 999 for x in par):
            raise ErroNegocio(f"Set {i}: informe os pontos das duas equipes.")
        a, b = par
        if not _resultado_do_set_vale(c, i, a, b):
            if c.placar_modo == "games" and not _set_decisivo(c, i):
                raise ErroNegocio(f"Set {i} ({a}×{b}) não termina assim: são {_alvo_do_set(c, i)} games com 2 de vantagem, ou 7×5 e 7×6 (tie-break).")
            raise ErroNegocio(f"Set {i} ({a}×{b}) não termina assim: são {_alvo_do_set(c, i)} pontos e {r['diferenca']} de vantagem.")
        if max(ga, gb) >= r["sets_para_vencer"]:
            raise ErroNegocio(f"O jogo já estava decidido antes do set {i}.")
        ga += a > b
        gb += b > a
    if max(ga, gb) < r["sets_para_vencer"]:
        raise ErroNegocio(f"O resultado não decide o jogo: quem vence precisa de {r['sets_para_vencer']} sets.")
    for antigo in _sets_do_jogo(s, j):
        s.delete(antigo)
    for g in list(j.games):
        s.delete(g)
    s.flush()
    linhas = [JogoSet(jogo_id=j.id, numero=i, pontos_a=a, pontos_b=b, encerrado=True) for i, (a, b) in enumerate(sets, 1)]
    s.add_all(linhas)
    _contar_sets(j, linhas)
    _evento(s, j, "placar", por, "Sets lançados pela mesa: " + ", ".join(f"{a}-{b}" for a, b in sets), None, 0, 0)
    auditoria.registrar(s, por.id, "jogo_placar", "jogo", j.id, sets=f"{j.placar_a}x{j.placar_b}", detalhe=str(sets))
    s.commit()
    return j


def _nome(e: Equipe | None) -> str:
    return e.nome if e else "a definir"


def agendar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, inicio: datetime | None, local: str | None) -> Jogo:
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    if j.status != J_AGENDADO:
        raise ErroNegocio("Só dá para marcar horário de um jogo que ainda não começou.")
    j.inicio_previsto = inicio
    j.local = (local or "").strip()[:120] or None
    auditoria.registrar(s, por.id, "jogo_agendar", "jogo", j.id, inicio=inicio, local=j.local)
    s.commit()
    return j


def agendar_lote(
    s: SessaoORM, campeonato_id: int, por: Usuario, escopo: str, inicio: datetime, duracao_min: int, intervalo_min: int = 0,
    locais: list[str] | None = None, ate: time | None = None, sobrescrever: bool = True,
) -> dict:
    """Define os horários de vários jogos de uma vez, um depois do outro: o 1º começa em `inicio` e cada jogo seguinte entra depois de
    `duracao_min` + `intervalo_min`. Com várias quadras (`locais`) os jogos de uma mesma rodada acontecem juntos, uma quadra cada.
    Cada rodada começa num horário novo (a equipe não joga duas vezes ao mesmo tempo e o mata-mata espera a rodada anterior).
    `ate`: horário-limite do dia; o que não couber continua no dia seguinte, na hora de `inicio`.
    `escopo`: "todos", "grupos", "mata_mata" ou "rodada:N". Só jogos ainda não começados."""
    c = campeonatos.obter(s, campeonato_id)
    campeonatos.exigir_gestao(c, por)
    if not 5 <= duracao_min <= 600:
        raise ErroNegocio("A duração de cada jogo vai de 5 a 600 minutos.")
    if not 0 <= intervalo_min <= 240:
        raise ErroNegocio("O intervalo entre jogos vai de 0 a 240 minutos.")
    passo = timedelta(minutes=duracao_min + intervalo_min)
    if ate is not None and (datetime.combine(inicio.date(), ate) - inicio) < timedelta(minutes=duracao_min):
        raise ErroNegocio("O horário-limite do dia precisa deixar espaço para pelo menos um jogo depois do início.")
    quadras = [q.strip()[:120] for q in (locais or []) if q and q.strip()]
    jogos = [j for j in s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id, Jogo.status == J_AGENDADO)) if not j.folga]
    if escopo == "grupos":
        jogos = [j for j in jogos if j.fase == "grupos"]
    elif escopo == "mata_mata":
        jogos = [j for j in jogos if j.fase == "mata_mata"]
    elif escopo.startswith("rodada:"):
        try:
            n = int(escopo.split(":", 1)[1])
        except ValueError:
            raise ErroNegocio("Escolha o que agendar.") from None
        jogos = [j for j in jogos if j.rodada == n and j.fase != "grupos"]
    elif escopo != "todos":
        raise ErroNegocio("Escolha o que agendar.")
    if not sobrescrever:
        jogos = [j for j in jogos if j.inicio_previsto is None]
    if not jogos:
        raise ErroNegocio("Não há jogos para agendar nessa seleção (eles podem já ter começado ou ter horário).")
    jogos.sort(key=lambda j: (1 if j.fase == "mata_mata" else 0, j.rodada, j.grupo or "", j.posicao))

    cursor, ultimo_fim, total = inicio, inicio, 0
    por_rodada: dict[tuple, list[Jogo]] = {}
    for j in jogos:
        por_rodada.setdefault((j.fase or "", j.rodada), []).append(j)
    paralelo = max(1, len(quadras))
    for lista in por_rodada.values():
        for i in range(0, len(lista), paralelo):
            fim = cursor + timedelta(minutes=duracao_min)
            if ate is not None and (fim.date() > cursor.date() or fim.time() > ate):
                cursor = datetime.combine(cursor.date() + timedelta(days=1), inicio.time())  # não cabe hoje: recomeça amanhã
                fim = cursor + timedelta(minutes=duracao_min)
            for k, j in enumerate(lista[i:i + paralelo]):
                j.inicio_previsto = cursor
                if quadras:
                    j.local = quadras[k]
                total += 1
            ultimo_fim = fim
            cursor += passo
    c.duracao_jogo_min = duracao_min
    auditoria.registrar(s, por.id, "campeonato_agenda_lote", "campeonato", c.id, escopo=escopo, inicio=inicio, duracao=duracao_min, intervalo=intervalo_min, jogos=total)
    equipes = {e for j in jogos for e in (j.equipe_a_id, j.equipe_b_id) if e}
    _avisar_equipes(s, c, list(equipes), f"📅 Horários de {c.nome}", "A organização definiu os horários dos jogos. Veja nas chaves.", f"agenda:{c.id}:{agora().strftime('%d%H%M')}", excluir=por.id)
    s.commit()
    return {"agendados": total, "primeiro": _dt(inicio), "ultimo_fim": _dt(ultimo_fim)}


def iniciar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> Jogo:
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    if j.status != J_AGENDADO:
        raise ErroNegocio("Este jogo já começou ou terminou.")
    if j.equipe_a_id is None or j.equipe_b_id is None:
        raise ErroNegocio("Este jogo ainda não tem as duas equipes definidas.")
    j.status, j.iniciado_em = J_AO_VIVO, agora()
    _evento(s, j, "inicio", por, "Bola rolando!")
    auditoria.registrar(s, por.id, "jogo_iniciar", "jogo", j.id)
    _avisar_equipes(s, c, _ids_do_jogo(j), f"🔴 Ao vivo: {_nome_lado(j, 'a')} x {_nome_lado(j, 'b')}", f"{c.nome} — acompanhe o placar.", f"jogo-vivo:{j.id}")
    s.commit()
    return j


def _exigir_ao_vivo(j: Jogo) -> None:
    if j.status != J_AO_VIVO:
        raise ErroNegocio("O jogo precisa estar ao vivo para mexer no placar.")


def marcar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, lado: str, delta: int) -> Jogo:
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    _exigir_ao_vivo(j)
    if lado not in ("a", "b") or delta not in (1, -1):
        raise ErroNegocio("Marcação inválida.")
    if delta > 0 and c.placar_modo in ("games", "total"):
        j.ponto_a = j.ponto_b = 0  # game lançado à mão: os pontos do game em andamento deixam de valer
        situacao, nset = _situacao_do_game(s, c, j)
        if situacao != "pontos":
            _registrar_game(s, j, lado, nset, None, None, manual=True)
    return _dar_game(s, c, j, por, lado, delta)


def _dar_game(s: SessaoORM, c: Campeonato, j: Jogo, por: Usuario, lado: str, delta: int) -> Jogo:
    """Soma (ou tira) um game do lado: no set em andamento (por sets/games) ou no placar simples."""
    if delta < 0 and c.placar_modo in ("games", "total"):
        _desfazer_game(s, c, j, lado)
    if modo_sets(c):
        return _marcar_sets(s, c, j, por, lado, delta)
    atual = j.placar_a if lado == "a" else j.placar_b
    novo = max(0, min(MAX_PLACAR, atual + delta))
    if modo_total(c) and delta > 0 and j.placar_a + j.placar_b >= _total_games(c):
        raise ErroNegocio(f"Os {_total_games(c)} games do jogo já foram disputados. Encerre o jogo (ou corrija com −1).")
    if novo == atual:
        return j
    if lado == "a":
        j.placar_a = novo
    else:
        j.placar_b = novo
    equipe = j.equipe_a if lado == "a" else j.equipe_b
    _evento(s, j, "placar", por, f"+1 {_nome_lado(j, lado)}" if delta > 0 else f"Correção: -1 {_nome_lado(j, lado)}", equipe.id if equipe else None)
    auditoria.registrar(s, por.id, "jogo_placar", "jogo", j.id, placar=f"{j.placar_a}x{j.placar_b}")
    s.commit()
    return j


def _registrar_game(s: SessaoORM, j: Jogo, lado: str, set_numero: int | None, pa: int | None, pb: int | None, tiebreak: bool = False, manual: bool = False) -> None:
    """Guarda o resultado do game que acabou de fechar (aparece no ao vivo, como os sets do vôlei)."""
    n = (s.scalar(select(func.max(JogoGame.numero)).where(JogoGame.jogo_id == j.id)) or 0) + 1
    s.add(JogoGame(jogo_id=j.id, numero=n, set_numero=set_numero, lado=lado, pontos_a=pa, pontos_b=pb, tiebreak=tiebreak, manual=manual))


def _desfazer_game(s: SessaoORM, c: Campeonato, j: Jogo, lado: str) -> None:
    """Tirou um game do lado: some também o último game registrado dele (no set decisivo os pontos não são games)."""
    cons = select(JogoGame).where(JogoGame.jogo_id == j.id, JogoGame.lado == lado)
    if modo_sets(c):
        sets = _sets_do_jogo(s, j)
        if not sets:
            return
        ultimo = sets[-1].numero
        r = regras_placar(c)
        if r["melhor_de"] > 1 and ultimo >= r["melhor_de"]:
            return
        cons = cons.where(JogoGame.set_numero == ultimo)
    g = s.scalar(cons.order_by(JogoGame.numero.desc()).limit(1).execution_options(populate_existing=True))
    if g is not None:
        s.delete(g)
        s.flush()


PONTOS_DO_GAME = ("0", "15", "30", "40")


def texto_do_ponto(n: int) -> str:
    return PONTOS_DO_GAME[n] if n < len(PONTOS_DO_GAME) else str(n)


def _situacao_do_game(s: SessaoORM, c: Campeonato, j: Jogo) -> tuple[str, int | None]:
    """"normal" (primeiro a 4 pontos; 40-40 é o ponto de ouro), "tiebreak" (6×6: primeiro a 7 com 2 de vantagem)
    ou "pontos" (set decisivo: os pontos já são a contagem do set)."""
    if c.placar_modo == "total":
        return "normal", None
    sets = _sets_do_jogo(s, j)
    atual = sets[-1] if sets and not sets[-1].encerrado else None
    numero = atual.numero if atual else len(sets) + 1
    r = regras_placar(c)
    if r["melhor_de"] > 1 and numero >= r["melhor_de"]:
        return "pontos", numero
    ga, gb = (atual.pontos_a, atual.pontos_b) if atual else (0, 0)
    return ("tiebreak" if ga == gb == r["pontos_set"] else "normal"), numero


def marcar_ponto(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, lado: str, delta: int) -> Jogo:
    """Ponto dentro do game, como no beach tennis: 15, 30, 40 e o ponto de ouro (sem vantagem: no 40×40 o próximo ponto decide).
    Quem fecha o game soma um game ao placar. No 6×6 joga-se o tie-break (7 pontos, 2 de vantagem) e no set decisivo os pontos
    contam direto. Disponível nas pontuações por games e total fixo de games."""
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    _exigir_ao_vivo(j)
    if lado not in ("a", "b") or delta not in (1, -1):
        raise ErroNegocio("Marcação inválida.")
    if c.placar_modo not in ("games", "total"):
        raise ErroNegocio("Este campeonato não conta os pontos dentro do game: use a pontuação por games ou total fixo de games.")
    situacao, nset = _situacao_do_game(s, c, j)
    if situacao == "pontos":
        return _dar_game(s, c, j, por, lado, delta)  # set decisivo: cada ponto já é um ponto do set
    nome = _nome_lado(j, lado)
    pa, pb = j.ponto_a, j.ponto_b
    if delta < 0:
        if (pa if lado == "a" else pb) <= 0:
            return _dar_game(s, c, j, por, lado, -1)  # game recém-fechado: corrige o game
        if lado == "a":
            j.ponto_a -= 1
        else:
            j.ponto_b -= 1
        _evento(s, j, "ponto", por, f"Correção: −1 ponto para {nome}", None)
        s.commit()
        return j
    if modo_total(c) and j.placar_a + j.placar_b >= _total_games(c):
        raise ErroNegocio(f"Os {_total_games(c)} games do jogo já foram disputados. Encerre o jogo (ou corrija com −1).")
    if modo_sets(c) and max(j.placar_a, j.placar_b) >= regras_placar(c)["sets_para_vencer"]:
        raise ErroNegocio("O jogo já está decidido. Encerre-o (ou corrija com −1).")
    if lado == "a":
        j.ponto_a += 1
    else:
        j.ponto_b += 1
    pa, pb = j.ponto_a, j.ponto_b
    minimo = 7 if situacao == "tiebreak" else 4
    meu, outro = (pa, pb) if lado == "a" else (pb, pa)
    fechou = meu >= minimo and (meu - outro >= 2 if situacao == "tiebreak" else True)
    if not fechou:
        legenda = f"{pa}–{pb}" if situacao == "tiebreak" else f"{texto_do_ponto(pa)}–{texto_do_ponto(pb)}" + (" (ponto de ouro)" if pa == pb == 3 else "")
        _evento(s, j, "ponto", por, f"Ponto {nome} · {legenda}", None)
        s.commit()
        return j
    j.ponto_a = j.ponto_b = 0
    _registrar_game(s, j, lado, nset, pa, pb, tiebreak=situacao == "tiebreak")
    _evento(s, j, "ponto", por, f"Ponto {nome} fecha o game ({pa}–{pb}{' no tie-break' if situacao == 'tiebreak' else ''})", None)
    return _dar_game(s, c, j, por, lado, 1)


def definir_placar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, a: int, b: int) -> Jogo:
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    _exigir_ao_vivo(j)
    if modo_sets(c):
        raise ErroNegocio("Neste campeonato o placar é por sets: marque os pontos ou lance os sets.")
    if not (0 <= a <= MAX_PLACAR and 0 <= b <= MAX_PLACAR):
        raise ErroNegocio(f"O placar vai de 0 a {MAX_PLACAR}.")
    if modo_total(c) and a + b > _total_games(c):
        raise ErroNegocio(f"O jogo tem {_total_games(c)} games no total: {a}×{b} passa disso.")
    if (a, b) != (j.placar_a, j.placar_b):
        for g in list(j.games):
            s.delete(g)
        j.placar_a, j.placar_b = a, b
        _evento(s, j, "placar", por, "Placar ajustado pela mesa")
        auditoria.registrar(s, por.id, "jogo_placar", "jogo", j.id, placar=f"{a}x{b}")
        s.commit()
    return j


def lance(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, texto: str, equipe_id: int | None = None) -> Jogo:
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    _exigir_ao_vivo(j)
    texto = (texto or "").strip()
    if not texto:
        raise ErroNegocio("Escreva o lance.")
    if len(texto) > 200:
        raise ErroNegocio("O lance passa de 200 caracteres.")
    if equipe_id is not None and equipe_id not in _ids_do_jogo(j):
        raise ErroNegocio("A equipe do lance não joga esta partida.")
    _evento(s, j, "lance", por, texto, equipe_id)
    s.commit()
    return j


def encerrar(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario, vencedor_id: int | None = None) -> Jogo:
    c, j = _travar(s, campeonato_id, jogo_id)
    _exigir_pontuar(s, c, por)
    _exigir_ao_vivo(j)
    eliminatoria = c.formato == "eliminatoria" or j.fase == "mata_mata"
    if modo_total(c) and j.placar_a + j.placar_b != _total_games(c):
        faltam = _total_games(c) - j.placar_a - j.placar_b
        raise ErroNegocio(f"O jogo tem {_total_games(c)} games e todos são disputados: faltam {faltam}.")
    if modo_sets(c) and max(j.placar_a, j.placar_b) < regras_placar(c)["sets_para_vencer"]:
        raise ErroNegocio(f"O jogo ainda não terminou: quem vence precisa de {regras_placar(c)['sets_para_vencer']} sets.")
    if j.placar_a != j.placar_b:
        j.vencedor_id = j.equipe_a_id if j.placar_a > j.placar_b else j.equipe_b_id
        j.desempate = False
    elif eliminatoria:
        if vencedor_id not in (j.equipe_a_id, j.equipe_b_id) or vencedor_id is None:
            raise ErroNegocio("Empate no mata-mata: informe quem venceu no desempate.")
        j.vencedor_id, j.desempate = vencedor_id, True
    else:
        j.vencedor_id, j.desempate = None, False
    j.status, j.encerrado_em = J_ENCERRADO, agora()
    j.ponto_a = j.ponto_b = 0
    vencedor = j.equipe_a if j.vencedor_id == j.equipe_a_id else j.equipe_b if j.vencedor_id == j.equipe_b_id else None
    parciais = " (" + ", ".join(f"{x.pontos_a}-{x.pontos_b}" for x in _sets_do_jogo(s, j) if x.encerrado) + ")" if modo_sets(c) else ""
    texto = f"Fim de jogo: {j.placar_a} x {j.placar_b}{parciais}" + (f" — {_nome(vencedor)} vence no desempate" if j.desempate else "")
    _evento(s, j, "fim", por, texto)
    auditoria.registrar(s, por.id, "jogo_encerrar", "jogo", j.id, placar=f"{j.placar_a}x{j.placar_b}", vencedor=j.vencedor_id)
    _avancar(s, j)
    s.flush()
    _gerar_mata_mata_se_pronto(s, c)
    _fechar_campeonato_se_acabou(s, c)
    _avisar_equipes(s, c, _ids_do_jogo(j), f"🏁 Fim de jogo: {_nome_lado(j, 'a')} {j.placar_a} x {j.placar_b} {_nome_lado(j, 'b')}", c.nome, f"jogo-fim:{j.id}")
    s.commit()
    if j.yt_estado in ("preparada", "ao_vivo"):  # o jogo acabou: finaliza a transmissão no YouTube (a gravação fica no canal)
        from . import youtube

        youtube.ao_encerrar_jogo(s, j)
    return j


def _classificados(s: SessaoORM, c: Campeonato, jogos: list[Jogo]) -> list[Equipe]:
    """Os `classificam` primeiros de cada grupo, na ordem de cabeça de chave do mata-mata: todos os 1º (A, B, C…), depois os 2º…
    Com 1º×último no cruzamento padrão, grupos diferentes se enfrentam já na primeira rodada."""
    membros = list(s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id, ChaveEquipe.grupo.is_not(None))))
    por_grupo: dict[str, list[Equipe]] = {}
    for m in membros:
        por_grupo.setdefault(m.grupo, []).append(s.get(Equipe, m.equipe_id))
    if c.classificam_geral:  # tabela única entre os grupos: a ordem final já é a de cabeça de chave (1ºxN, 2ºx(N-1)…), sem reembaralhar
        return [s.get(Equipe, L["equipe"]["id"]) for L in _classificacao_geral(jogos, por_grupo, c)[: c.classificam_geral]]
    tabelas = {g: classificacao([j for j in jogos if j.grupo == g], eqs, c) for g, eqs in sorted(por_grupo.items())}
    ordem: list[Equipe] = []
    nivel: list[int] = []  # colocação no grupo de cada equipe de `ordem` (0 = 1º)
    grupo_de: dict[int, str] = {}
    for pos in range(c.classificam or 2):
        for g in sorted(tabelas):
            e = s.get(Equipe, tabelas[g][pos]["equipe"]["id"])
            ordem.append(e)
            nivel.append(pos)
            grupo_de[e.id] = g
    _evitar_revanche(ordem, nivel, grupo_de)
    return ordem


def _classificacao_geral(jogos: list[Jogo], por_grupo: dict[str, list[Equipe]], c: Campeonato) -> list[dict]:
    """Todas as equipes da fase de grupos numa tabela só (pontos, saldo de sets, saldo de pontos). Com grupos de tamanhos diferentes
    há jogos a mais para alguns: a organização é quem decide se isso é justo para o regulamento."""
    return classificacao([j for j in jogos if j.fase == "grupos"], [e for eqs in por_grupo.values() for e in eqs], c)


def _evitar_revanche(ordem: list[Equipe], nivel: list[int], grupo_de: dict[int, str]) -> None:
    """Troca equipes da mesma colocação entre confrontos para que, na 1ª rodada do mata-mata, ninguém reencontre o próprio grupo."""
    n = len(ordem)
    tam = 1 << math.ceil(math.log2(n))
    semeadura = _semeadura(tam)
    pares = [(semeadura[2 * p] - 1, semeadura[2 * p + 1] - 1) for p in range(tam // 2)]

    def conflitos() -> int:
        return sum(1 for a, b in pares if a < n and b < n and grupo_de[ordem[a].id] == grupo_de[ordem[b].id])

    atual = conflitos()
    melhorou = True
    while atual and melhorou:
        melhorou = False
        for a in range(n):
            for b in range(a + 1, n):
                if nivel[a] != nivel[b]:
                    continue
                ordem[a], ordem[b] = ordem[b], ordem[a]
                novo = conflitos()
                if novo < atual:
                    atual, melhorou = novo, True
                else:
                    ordem[a], ordem[b] = ordem[b], ordem[a]


def _gerar_mata_mata_se_pronto(s: SessaoORM, c: Campeonato) -> None:
    """Quando o último jogo da fase de grupos termina, monta o mata-mata com os classificados."""
    if c.formato != "grupos":
        return
    jogos = list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id)))
    grupos = [j for j in jogos if j.fase == "grupos"]
    if not grupos or any(j.fase == "mata_mata" for j in jogos) or any(j.status != J_ENCERRADO for j in grupos):
        return
    classificados = _classificados(s, c, grupos)
    _montar_eliminatoria(s, c, classificados, "mata_mata")
    _avisar_equipes(s, c, [e.id for e in classificados], f"➡️ Mata-mata de {c.nome} definido", "Sua equipe se classificou. Veja o cruzamento nas chaves.", f"mata:{c.id}")


def _fechar_campeonato_se_acabou(s: SessaoORM, c: Campeonato) -> None:
    jogos = list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id)))
    if c.formato == "grupos" and not any(j.fase == "mata_mata" for j in jogos):
        return  # ainda falta o mata-mata
    if jogos and all(j.status == J_ENCERRADO for j in jogos) and c.status != C_CANCELADO:
        c.status = C_ENCERRADO
        if c.formato == "super8":
            for r in _ranking_super8(s, c, jogos):
                if r["classificacao"]:
                    lider = r["classificacao"][0]["equipe"]
                    onde = f" (grupo {r['grupo']})" if r["grupo"] else ""
                    _avisar_equipes(s, c, [lider["id"]], f"🏆 Campeão do Super 8{onde}: {c.nome}!", f"{lider['nome']} terminou em 1º lugar.", f"campeao:{c.id}:{r['grupo'] or ''}")
        if c.formato in ("eliminatoria", "grupos"):
            final = max((j for j in jogos if j.fase != "grupos"), key=lambda j: j.rodada)
            campeao = final.equipe_a if final.vencedor_id == final.equipe_a_id else final.equipe_b
            if campeao is not None:
                _avisar_equipes(s, c, [campeao.id], f"🏆 Campeões de {c.nome}!", f"{campeao.nome} venceu o campeonato.", f"campeao:{c.id}")


def reabrir(s: SessaoORM, campeonato_id: int, jogo_id: int, por: Usuario) -> Jogo:
    """Corrige um encerramento errado. Só a organização, e só se a rodada seguinte ainda não começou."""
    c, j = _travar(s, campeonato_id, jogo_id)
    campeonatos.exigir_gestao(c, por)
    if j.status != J_ENCERRADO or j.folga:
        raise ErroNegocio("Só dá para reabrir um jogo encerrado.")
    if j.fase == "grupos":  # o mata-mata saiu desta classificação: só dá para refazê-lo se ainda não começou
        mata = list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id, Jogo.fase == "mata_mata")))
        if any(x.status != J_AGENDADO and not x.folga for x in mata):
            raise ErroNegocio("O mata-mata já começou; não dá mais para reabrir jogos da fase de grupos.")
        for x in mata:
            x.proximo_id = None
        s.flush()
        for x in mata:
            s.delete(x)
        s.flush()
    if j.proximo_id:
        prox = s.get(Jogo, j.proximo_id)
        if prox.status != J_AGENDADO:
            raise ErroNegocio("O próximo jogo já começou; não dá mais para reabrir este.")
        if j.proximo_lado == "a":
            prox.equipe_a = None
        else:
            prox.equipe_b = None
    j.status, j.vencedor_id, j.desempate, j.encerrado_em = J_AO_VIVO, None, False, None
    if c.status == C_ENCERRADO:
        c.status = C_ANDAMENTO
    _evento(s, j, "reaberto", por, "Jogo reaberto pela organização")
    auditoria.registrar(s, por.id, "jogo_reabrir", "jogo", j.id)
    s.commit()
    return j


# ---------------------------------------------------------------- leitura (chaves, classificação, jogo)


def _eq(e: Equipe | None) -> dict | None:
    return {"id": e.id, "nome": e.nome} if e else None


def _dt(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def _lado(j: Jogo, lado: str) -> dict | None:
    """Um lado do jogo. No Super 8 é a dupla: o nome vem junto ("Ana + Bia") e o `id` é o do 1º atleta."""
    e, p = (j.equipe_a, j.equipe_a2) if lado == "a" else (j.equipe_b, j.equipe_b2)
    if e is None or p is None:
        return _eq(e)
    return {"id": e.id, "nome": f"{e.nome} + {p.nome}", "parceiro_id": p.id, "dupla": [e.nome, p.nome]}


def canal_youtube(s: SessaoORM, jogos: list[Jogo]) -> dict | None:
    """Link do canal do PlayGo no YouTube (abre a transmissão que estiver no ar) e se algum jogo deste campeonato está no ar agora."""
    cfg = s.get(ConfigExterna, "youtube")
    canal = ((cfg.meta or {}).get("canal_id") if cfg is not None and cfg.valor else None)
    if not canal:
        return None
    return {"url": f"https://www.youtube.com/channel/{canal}/live", "ao_vivo": any(j.yt_estado == "ao_vivo" for j in jogos)}


def jogo_dict(j: Jogo) -> dict:
    return {
        "id": j.id, "rodada": j.rodada, "rodada_nome": j.rodada_nome, "fase": j.fase, "grupo": j.grupo, "posicao": j.posicao, "a": _lado(j, "a"), "b": _lado(j, "b"),
        "placar_a": j.placar_a, "placar_b": j.placar_b, "ponto_a": j.ponto_a or 0, "ponto_b": j.ponto_b or 0, "status": j.status, "vencedor_id": j.vencedor_id, "desempate": j.desempate, "folga": j.folga,
        "inicio_previsto": _dt(j.inicio_previsto), "local": j.local, "iniciado_em": _dt(j.iniciado_em),
        "video": {"id": j.yt_broadcast_id, "estado": j.yt_estado} if j.yt_broadcast_id and j.yt_estado in ("ao_vivo", "encerrada") else None,
        "sets": [{"numero": x.numero, "a": x.pontos_a, "b": x.pontos_b, "encerrado": x.encerrado} for x in j.sets],
        "games": [{"numero": g.numero, "set": g.set_numero, "lado": g.lado, "a": g.pontos_a, "b": g.pontos_b, "tiebreak": g.tiebreak, "manual": g.manual} for g in j.games],
    }


def classificacao(jogos: list[Jogo], equipes: list[Equipe], c: Campeonato | None = None) -> list[dict]:
    """Em sets (vôlei): vitória sem ir ao set decisivo = 3 pontos; vitória no decisivo = 2 e derrota no decisivo = 1.
    Desempate: saldo de sets, depois saldo de pontos marcados."""
    por_sets = c is not None and modo_sets(c)
    para = regras_placar(c)["sets_para_vencer"] if por_sets else 0
    linhas = {e.id: {"equipe": {"id": e.id, "nome": e.nome}, "pontos": 0, "jogos": 0, "vitorias": 0, "empates": 0, "derrotas": 0, "pro": 0, "contra": 0, "pts_pro": 0, "pts_contra": 0} for e in equipes}
    for j in jogos:
        if j.status != J_ENCERRADO or j.folga or j.equipe_a_id not in linhas or j.equipe_b_id not in linhas:
            continue
        if por_sets:
            for x in j.sets:
                if x.encerrado:
                    linhas[j.equipe_a_id]["pts_pro"] += x.pontos_a
                    linhas[j.equipe_a_id]["pts_contra"] += x.pontos_b
                    linhas[j.equipe_b_id]["pts_pro"] += x.pontos_b
                    linhas[j.equipe_b_id]["pts_contra"] += x.pontos_a
        for eid, pro, contra in ((j.equipe_a_id, j.placar_a, j.placar_b), (j.equipe_b_id, j.placar_b, j.placar_a)):
            L = linhas[eid]
            L["jogos"] += 1
            L["pro"] += pro
            L["contra"] += contra
            if por_sets:
                if pro > contra:
                    L["vitorias"] += 1
                    L["pontos"] += 3 if contra < para - 1 else 2
                else:
                    L["derrotas"] += 1
                    L["pontos"] += 1 if pro == para - 1 and para > 1 else 0
            elif pro > contra:
                L["vitorias"] += 1
                L["pontos"] += PONTOS["vitoria"]
            elif pro == contra:
                L["empates"] += 1
                L["pontos"] += PONTOS["empate"]
            else:
                L["derrotas"] += 1
    out = list(linhas.values())
    for L in out:
        L["saldo"] = L["pro"] - L["contra"]
    for L in out:
        L["pts_saldo"] = L["pts_pro"] - L["pts_contra"]
    out.sort(key=lambda L: (-L["pontos"], -L["saldo"], -L["pts_saldo"], -L["pro"], L["equipe"]["nome"].lower()))
    for i, L in enumerate(out, 1):
        L["posicao"] = i
    return out


def _games_do_lado(j: Jogo) -> tuple[int, int]:
    """Games de cada lado no jogo (soma dos sets em games; sem sets, o próprio placar)."""
    fechados = [x for x in j.sets if x.encerrado]
    if fechados:
        return sum(x.pontos_a for x in fechados), sum(x.pontos_b for x in fechados)
    return j.placar_a, j.placar_b


def classificacao_super8(jogos: list[Jogo], atletas: list[Equipe], c: Campeonato | None = None) -> list[dict]:
    """Ranking individual: vitórias, depois saldo de games e games feitos. Cada atleta recebe o resultado da dupla em que jogou."""
    linhas = {e.id: {"equipe": {"id": e.id, "nome": e.nome}, "pontos": 0, "jogos": 0, "vitorias": 0, "empates": 0, "derrotas": 0, "pro": 0, "contra": 0, "pts_pro": 0, "pts_contra": 0} for e in atletas}
    for j in jogos:
        if j.status != J_ENCERRADO or j.folga:
            continue
        ga, gb = _games_do_lado(j)
        for ids, pro, contra, venceu in (
            ((j.equipe_a_id, j.equipe_a2_id), ga, gb, j.placar_a > j.placar_b),
            ((j.equipe_b_id, j.equipe_b2_id), gb, ga, j.placar_b > j.placar_a),
        ):
            for eid in ids:
                L = linhas.get(eid)
                if L is None:
                    continue
                L["jogos"] += 1
                L["pro"] += pro
                L["contra"] += contra
                if venceu:
                    L["vitorias"] += 1
                    L["pontos"] += 1
                elif j.placar_a != j.placar_b:
                    L["derrotas"] += 1
                else:
                    L["empates"] += 1
    out = list(linhas.values())
    total = c is not None and modo_total(c)
    for L in out:
        L["saldo"] = L["pro"] - L["contra"]
        L["pts_saldo"] = 0
        if total:
            L["pontos"] = L["pro"]  # cada game que a dupla faz vale um ponto para o atleta
    if total:
        out.sort(key=lambda L: (-L["pontos"], -L["vitorias"], -L["saldo"], L["equipe"]["nome"].lower()))
    else:
        out.sort(key=lambda L: (-L["vitorias"], -L["saldo"], -L["pro"], L["equipe"]["nome"].lower()))
    for i, L in enumerate(out, 1):
        L["posicao"] = i
    return out


def chaveamento(s: SessaoORM, c: Campeonato, u: Usuario) -> dict:
    jogos = list(s.scalars(select(Jogo).where(Jogo.campeonato_id == c.id).order_by(Jogo.rodada, Jogo.posicao).execution_options(populate_existing=True)))
    de_grupo = [j for j in jogos if j.fase == "grupos"]
    chave = [j for j in jogos if j.fase != "grupos"]  # eliminatória, pontos corridos ou o mata-mata depois dos grupos
    rodadas: list[dict] = []
    for j in chave:
        if not rodadas or rodadas[-1]["rodada"] != j.rodada:
            rodadas.append({"rodada": j.rodada, "nome": j.rodada_nome, "jogos": []})
        rodadas[-1]["jogos"].append(jogo_dict(j))
    confirmadas = list(s.scalars(select(Equipe).where(Equipe.campeonato_id == c.id, Equipe.status == E_CONFIRMADA).order_by(Equipe.id)))
    marcas = {k.equipe_id: k for k in s.scalars(select(ChaveEquipe).where(ChaveEquipe.campeonato_id == c.id))}
    grupos_out: list[dict] = []
    geral: list[dict] = []
    if c.classificam_geral and c.formato == "grupos":
        por_g: dict[str, list[Equipe]] = {}
        for e in confirmadas:
            if e.id in marcas and marcas[e.id].grupo:
                por_g.setdefault(marcas[e.id].grupo, []).append(e)
        geral = _classificacao_geral(de_grupo, por_g, c)
        for L in geral:
            L["classifica"] = L["posicao"] <= c.classificam_geral
            L["grupo"] = marcas[L["equipe"]["id"]].grupo
    geral_ids = {L["equipe"]["id"] for L in geral if L["classifica"]}
    for letra in sorted({k.grupo for k in marcas.values() if k.grupo} if c.formato != "super8" else ()):
        eqs = [e for e in confirmadas if e.id in marcas and marcas[e.id].grupo == letra]
        js = [j for j in de_grupo if j.grupo == letra]
        rod: list[dict] = []
        for j in js:
            if not rod or rod[-1]["rodada"] != j.rodada:
                rod.append({"rodada": j.rodada, "nome": f"Rodada {j.rodada}", "jogos": []})
            rod[-1]["jogos"].append(jogo_dict(j))
        tabela = classificacao(js, eqs, c)
        for L in tabela:
            L["classifica"] = L["equipe"]["id"] in geral_ids if c.classificam_geral else L["posicao"] <= (c.classificam or 0)
            L["cabeca"] = marcas[L["equipe"]["id"]].cabeca
        grupos_out.append({"grupo": letra, "classificacao": tabela, "rodadas": rod})
    cabecas = sorted(({"ordem": k.cabeca, "equipe": _eq(next((e for e in confirmadas if e.id == k.equipe_id), None))} for k in marcas.values() if k.cabeca), key=lambda x: x["ordem"])
    campeao = None
    if c.formato in ("eliminatoria", "grupos") and chave:
        final = max(chave, key=lambda j: j.rodada)
        if final.status == J_ENCERRADO and final.vencedor_id:
            campeao = _eq(final.equipe_a if final.vencedor_id == final.equipe_a_id else final.equipe_b)
    elif c.formato == "pontos_corridos" and chave and all(j.status == J_ENCERRADO for j in chave):
        campeao = classificacao(jogos, confirmadas, c)[0]["equipe"]
    elif c.formato == "super8" and chave and all(j.status == J_ENCERRADO for j in chave):
        ranking = _ranking_super8(s, c, jogos)
        campeao = ranking[0]["classificacao"][0]["equipe"] if len(ranking) == 1 else None  # com vários grupos há um campeão por grupo
    no_sorteio = _equipes_no_sorteio(jogos)
    pendentes = campeonatos.contar_pendentes(s, c)
    return {
        "campeonato": {"id": c.id, "nome": c.nome, "status": c.status, "modalidade": c.modalidade.nome, "icone": c.modalidade.icone},
        "formato": c.formato, "formato_nome": FORMATOS.get(c.formato or ""), "sorteado_em": _dt(c.sorteado_em), "semente": str(c.sorteio_semente) if c.sorteio_semente else None,  # texto: passa de 2^53 e o JavaScript perderia dígitos
        "rodadas": rodadas, "ao_vivo": [jogo_dict(j) for j in jogos if j.status == J_AO_VIVO], "canal": canal_youtube(s, jogos),
        "classificacao": classificacao(chave, confirmadas, c) if c.formato == "pontos_corridos" else [],
        "ranking_super8": _ranking_super8(s, c, chave) if c.formato == "super8" and chave else [],
        "campeao": campeao,
        "regras_placar": regras_placar(c), "placar_travado": any(j.status != J_AGENDADO and not j.folga for j in jogos),
        "grupos": grupos_out, "classificam": c.classificam, "classificam_geral": c.classificam_geral, "classificacao_geral": geral, "cabecas": cabecas, "duracao_jogo_min": c.duracao_jogo_min,
        "jogos_grupos_restantes": sum(1 for j in de_grupo if j.status != J_ENCERRADO) if c.formato == "grupos" and not chave else 0,
        "equipes": [_eq(e) for e in confirmadas],
        "equipes_lugar": [{"id": e.id, "nome": e.nome, "grupo": marcas[e.id].grupo if e.id in marcas else None, "no_sorteio": e.id in no_sorteio} for e in confirmadas],
        "equipes_confirmadas": len(confirmadas), "equipes_pendentes": pendentes,
        "pode_gerir": campeonatos.pode_gerir(c, u), "pode_pontuar": pode_pontuar(s, c, u),
        "pode_sortear_de_novo": all(j.status == J_AGENDADO or j.folga for j in jogos),
        "mesarios": mesarios(s, c) if campeonatos.pode_gerir(c, u) else [],
    }


def detalhe_jogo(s: SessaoORM, c: Campeonato, jogo_id: int, u: Usuario) -> dict:
    j = s.scalar(select(Jogo).where(Jogo.id == jogo_id, Jogo.campeonato_id == c.id).execution_options(populate_existing=True))
    if j is None:
        raise NaoEncontrado("Jogo não encontrado.")
    eventos = [
        {"id": e.id, "tipo": e.tipo, "texto": e.texto, "equipe_id": e.equipe_id, "placar_a": e.placar_a, "placar_b": e.placar_b, "em": _dt(e.criado_em)}
        for e in j.eventos
    ]
    return jogo_dict(j) | {
        "campeonato": {"id": c.id, "nome": c.nome, "formato": c.formato, "status": c.status},
        "eventos": eventos, "pode_pontuar": pode_pontuar(s, c, u), "pode_gerir": campeonatos.pode_gerir(c, u),
        "mata_mata": c.formato == "eliminatoria" or j.fase == "mata_mata", "agora": _dt(agora()), "regras_placar": regras_placar(c),
    }
