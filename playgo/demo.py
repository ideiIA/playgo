"""Dados de demonstração espelhando o protótipo (Campo Grande, MS). Os horários são relativos ao
momento da carga: 'começa em 48 min' continua verdadeiro no dia em que o banco é populado.
Só para desenvolvimento: `python -m playgo demo`."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as SessaoORM

import unicodedata

from . import arenas, atividades, campeonatos, comunidades, contas, grupos, moderacao, publicacoes, vagas
from .atividades import NovaAtividade
from .campeonatos import NovoCampeonato
from .db import agora
from .models import Modalidade, Reserva, Usuario

# Senha de todas as contas de demonstração (nunca use fora do desenvolvimento).
DEMO_SENHA = "playgo-demo-1"
DEMO_EMAIL = "carlos@playgo.local"

# Pontos de Campo Grande, a poucos km do centro (-20,4697; -54,6201)
ARENA_CENTRAL = (-20.4547, -54.6201)
PARQUE_SPORTS = (-20.4697, -54.5971)
BEACH_CLUB = (-20.4967, -54.6201)
PARQUE_NACOES = (-20.4500, -54.6050)
ARENA_SUNSET = (-20.5050, -54.5900)

NOMES = ("Marcos", "Ana", "Bruno", "Júlia", "Pedro", "Lívia", "Rafael", "Tiago", "Camila", "Diego", "Fernanda", "Gustavo", "Helena", "Igor", "Larissa", "Mateus", "Natália", "Otávio")


def _hora(minutos: int) -> datetime:
    return (agora() + timedelta(minutes=minutos)).replace(second=0, microsecond=0)


def _m(s: SessaoORM, codigo: str) -> Modalidade:
    return s.scalar(select(Modalidade).where(Modalidade.codigo == codigo))


def popular(s: SessaoORM) -> str:
    if s.scalar(select(Usuario.id).where(Usuario.email == DEMO_EMAIL)):
        return "Dados de demonstração já existem (use --refazer para recomeçar)."

    # ---- atletas
    carlos = contas.cadastrar(s, "Carlos Almeida", DEMO_EMAIL, DEMO_SENHA, "carlos.cg", True, True)
    contas.atualizar_perfil(s, carlos, cidade="Campo Grande", latitude=-20.4697, longitude=-54.6201, raio_km=10, notif_raio_km=5, disponibilidade=["noite", "fim_de_semana"])
    contas.definir_esportes(s, carlos, {_m(s, "futebol").id: "intermediario", _m(s, "volei").id: "iniciante", _m(s, "beach_tennis").id: "intermediario"})

    atletas: list[Usuario] = []
    gostos = (("futebol", "futsal"), ("volei", "volei_de_areia"), ("beach_tennis", "tenis"), ("corrida", "caminhada"), ("futebol", "basquete"), ("volei", "futevolei"))
    for i, nome in enumerate(NOMES):
        slug = unicodedata.normalize('NFKD', nome.lower()).encode('ascii', 'ignore').decode()
        u = contas.cadastrar(s, f"{nome} {'Silva Souza Lima Costa Rocha Dias'.split()[i % 6]}", f"{slug}@playgo.local", DEMO_SENHA, f"{slug}.cg", True, True)
        # Espalha os atletas num raio de ~4 km do centro
        contas.atualizar_perfil(s, u, cidade="Campo Grande", latitude=-20.4697 + ((i % 5) - 2) * 0.008, longitude=-54.6201 + ((i // 3) - 3) * 0.009, raio_km=12, notif_raio_km=6)
        esportes = {_m(s, c).id: ("intermediario" if (i + j) % 3 else "avancado") for j, c in enumerate(gostos[i % len(gostos)])}
        contas.definir_esportes(s, u, esportes)
        atletas.append(u)
    marcos, ana = atletas[0], atletas[1]

    # ---- arenas e quadras
    central = arenas.criar(s, carlos, "Arena Central", *ARENA_CENTRAL, endereco="Av. Afonso Pena, 1200", cidade="Campo Grande", estrutura="Vestiário, bar, estacionamento", abre=time(18, 0), fecha=time(22, 0))
    q1 = arenas.criar_quadra(s, central, carlos, "Quadra 01", ["futsal", "futebol"], 14, Decimal(120))
    q2 = arenas.criar_quadra(s, central, carlos, "Quadra 02", ["volei"], 12, Decimal(90))
    q3 = arenas.criar_quadra(s, central, carlos, "Quadra 03", ["beach_tennis"], 4, Decimal(80))
    arenas.criar(s, marcos, "Parque Sports", *PARQUE_SPORTS, endereco="R. Bahia, 450", cidade="Campo Grande", estrutura="Quadras cobertas")
    club = arenas.criar(s, ana, "Beach Club", *BEACH_CLUB, endereco="Av. Mato Grosso, 980", cidade="Campo Grande", estrutura="Quadras de areia, bar")
    arenas.criar_quadra(s, club, ana, "Areia 1", ["beach_tennis", "volei_de_areia"], 4, Decimal(80))
    sunset = arenas.criar(s, marcos, "Arena Sunset", *ARENA_SUNSET, endereco="R. das Palmeiras, 77", cidade="Campo Grande")
    arenas.criar_quadra(s, sunset, marcos, "Quadra de areia", ["volei_de_areia"], 8, Decimal(100))

    # Agenda de hoje da Arena Central, como no protótipo (18h–21h)
    hoje = agora().date()
    ocupado = {(q1, 19): "Futebol", (q1, 20): "Futebol", (q2, 18): "Vôlei", (q2, 19): "Vôlei", (q2, 21): "Vôlei", (q3, 19): "Beach Tennis", (q3, 20): "Beach Tennis", (q3, 21): "Beach Tennis"}
    for (q, h), rotulo in ocupado.items():
        ini = datetime.combine(hoje, time(h))
        s.add(Reserva(quadra_id=q.id, inicio=ini, fim=ini + timedelta(hours=1), tipo="reserva", rotulo=rotulo))
    s.commit()

    # ---- grupos
    g_corrida = grupos.criar(s, marcos, "Corre CG", _m(s, "corrida").id, "Corrida de rua em Campo Grande, todos os ritmos.", "Campo Grande", "Parque das Nações", *PARQUE_NACOES)
    g_futebol = grupos.criar(s, ana, "Futebol dos Amigos", _m(s, "futebol").id, "Toda quarta, 20h.", "Campo Grande", "Arena Central", *ARENA_CENTRAL)
    g_volei = grupos.criar(s, marcos, "Vôlei CG", _m(s, "volei").id, "Vôlei para todos os níveis.", "Campo Grande", "Parque Sports", *PARQUE_SPORTS)
    for g in (g_corrida, g_futebol, g_volei):
        grupos.entrar(s, g.id, carlos)
        for u in atletas[2:9]:
            grupos.entrar(s, g.id, u)
    grupos.postar(s, g_corrida.id, marcos, "Terça às 6h no Parque das Nações. Quem for, leve água!", aviso=True)

    # ---- atividades ("Precisam de jogadores agora")
    def criar(org, **kw) -> int:
        return atividades.criar(s, org, NovaAtividade(**kw)).id

    futebol = criar(
        marcos, modalidade_id=_m(s, "futebol").id, nome="Futebol de Segunda", inicio=_hora(48), max_participantes=14, arena_id=central.id,
        nivel="intermediario", valor=Decimal(25), falta_gente=True, descricao="Racha da segunda, jogo corrido e divertido.",
    )
    volei = criar(
        marcos, modalidade_id=_m(s, "volei").id, nome="Vôlei Open", inicio=_hora(78), max_participantes=12, local_nome="Parque Sports",
        latitude=PARQUE_SPORTS[0], longitude=PARQUE_SPORTS[1], falta_gente=True,
    )
    beach = criar(
        ana, modalidade_id=_m(s, "beach_tennis").id, nome="Beach Sunset", inicio=_hora(108), max_participantes=8, arena_id=club.id,
        nivel="intermediario", valor=Decimal(30), falta_gente=True,
    )
    corrida = criar(
        marcos, modalidade_id=_m(s, "corrida").id, nome="Corre CG, 7 km", grupo_id=g_corrida.id, max_participantes=30,
        inicio=datetime.combine(hoje + timedelta(days=1), time(6, 0)), local_nome="Parque das Nações",
        latitude=PARQUE_NACOES[0], longitude=PARQUE_NACOES[1], percurso="Duas voltas no parque, ritmo 5:30–6:30.", duracao_min=60,
    )
    criar(
        ana, modalidade_id=_m(s, "futevolei").id, nome="Futevôlei no fim de tarde", inicio=datetime.combine(hoje + timedelta(days=1), time(18, 0)),
        max_participantes=8, arena_id=club.id, nivel="todos", valor=Decimal(15),
    )
    for atividade_id, quantos in ((futebol, 11), (volei, 10), (beach, 5), (corrida, 11)):
        for u in atletas[3 : 3 + quantos]:
            vagas.entrar(s, atividade_id, u)

    # ---- campeonatos
    copa = campeonatos.criar(s, carlos, NovoCampeonato(
        modalidade_id=_m(s, "futsal").id, nome="Copa Arena de Futsal", arena_id=central.id, data_inicio=date(2026, 11, 10), inscricao_ate=date(2026, 11, 3),
        max_equipes=16, atletas_por_equipe=7, valor_inscricao=Decimal(500), premiacao="R$ 5.000", premiacao_valor=Decimal(5000), categoria="Adulto masculino",
        regulamento="Jogos de 2 tempos de 20 minutos. Cada equipe pode inscrever até 7 atletas.",
    ))
    campeonatos.criar(s, ana, NovoCampeonato(
        modalidade_id=_m(s, "beach_tennis").id, nome="Open Beach Weekend", arena_id=club.id, data_inicio=date(2026, 11, 22), inscricao_ate=date(2026, 11, 15),
        max_equipes=32, atletas_por_equipe=2, valor_inscricao=Decimal(160), categoria="Categorias B, C e iniciante",
    ))
    campeonatos.criar(s, marcos, NovoCampeonato(
        modalidade_id=_m(s, "volei_de_areia").id, nome="Circuito Vôlei de Areia", arena_id=sunset.id, data_inicio=date(2026, 11, 29), inscricao_ate=date(2026, 11, 22),
        max_equipes=16, atletas_por_equipe=4, valor_inscricao=Decimal(240), categoria="Masculino e feminino",
    ))
    for i in range(0, 12, 2):
        e = campeonatos.inscrever_equipe(s, copa.id, atletas[i], f"Equipe {NOMES[i]}")
        campeonatos.decidir_equipe(s, e.id, carlos, True)

    # ---- horário ocioso divulgado
    amanha = datetime.combine(hoje + timedelta(days=1), time(19, 0))
    arenas.divulgar(s, q3.id, amanha, amanha + timedelta(hours=1), Decimal(80), _m(s, "beach_tennis").id, carlos)

    # ---- atividades fechadas: só autorizados e só por link
    atividades.criar(s, ana, NovaAtividade(
        modalidade_id=_m(s, "beach_tennis").id, nome="Beach Tennis avançado", inicio=_hora(60 * 26), max_participantes=4, arena_id=club.id,
        nivel="avancado", visibilidade="autorizados", valor=Decimal(40),
    ))
    secreta = atividades.criar(s, marcos, NovaAtividade(
        modalidade_id=_m(s, "futebol").id, nome="Pelada de sexta (só convidados)", inicio=_hora(60 * 30), max_participantes=14, arena_id=central.id,
        visibilidade="link",
    ))
    vagas.entrar(s, secreta.id, atletas[4], token=secreta.convite_token)

    # ---- comunidades de relacionamento
    corredoras = comunidades.criar(s, atletas[3], "Mães que correm CG", "Corrida e conversa entre mães de Campo Grande. Respeito acima de tudo.", "Sem propaganda. Fotos de crianças só com autorização dos pais.", "Campo Grande", "publica")
    arbitros = comunidades.criar(s, marcos, "Árbitros e mesários de CG", "Escalas, regras e troca de experiências.", None, "Campo Grande", "autorizados")
    comunidades.entrar(s, corredoras.id, carlos)
    comunidades.entrar(s, corredoras.id, ana)
    comunidades.entrar(s, arbitros.id, carlos)  # fica pendente: o dono (Marcos) aprova

    # ---- murais (tudo passa pela pré-análise antes de aparecer)
    def postar(autor, texto, escopo="geral", escopo_id=None, lat=None, lng=None, local=None, replicar=False):
        p = publicacoes.criar(s, autor, escopo, escopo_id, texto, lat, lng, local, replicar)
        moderacao.processar_publicacao(p.id)
        s.refresh(p)  # a análise roda em outra sessão
        return p

    p1 = postar(atletas[3], "Corrida de domingo no Parque das Nações foi demais! Quem topa o próximo 8 km?", lat=PARQUE_NACOES[0], lng=PARQUE_NACOES[1], local="Parque das Nações Indígenas")
    postar(marcos, "Vem completar o time! Faltam 2 para o racha de hoje na Arena Central.", "atividade", futebol, replicar=True)
    postar(atletas[5], "Alguém tem uma bola reserva? A minha furou.", "atividade", futebol)
    postar(ana, "Quadra de areia nova no Beach Club, ficou linda. Bora estrear?", lat=BEACH_CLUB[0], lng=BEACH_CLUB[1], local="Beach Club", replicar=False)
    postar(atletas[6], "Treino funcional hoje às 6h na praça. Aberto a todos!", lat=PARQUE_SPORTS[0], lng=PARQUE_SPORTS[1], local="Praça do Parque Sports")
    postar(atletas[3], "Boas-vindas ao grupo! Combinados: respeito, pontualidade e muita água.", "comunidade", corredoras.id, PARQUE_NACOES[0], PARQUE_NACOES[1], "Parque das Nações Indígenas")
    c1 = publicacoes.comentar(s, atletas[7], p1.id, "Eu vou! Que horas?")
    moderacao.processar_comentario(c1.id)
    c2 = publicacoes.comentar(s, atletas[3], p1.id, "Às 6h, na entrada principal 😉")
    moderacao.processar_comentario(c2.id)
    # um exemplo para a fila de moderação do admin (a regra de spam segura o texto)
    postar(atletas[8], "Ganhe dinheiro facil: investimento garantido, chama no zap!", lat=ARENA_CENTRAL[0], lng=ARENA_CENTRAL[1], local="Arena Central")
    return "Dados de demonstração criados."
