"""Relatórios: o resultado de cada arena (reservas, valores, ocupação, horários de pico, clientes) e a visão geral da plataforma (só administrador).

Só lê dados: nada aqui muda estado. Quem conduz a regra de acesso é este módulo (gestor da arena ou admin), não a rota nem o template.
Os números saem de `Reserva` (valor, comissão, forma e status do pagamento) e, na visão geral, de assinaturas e criações do período."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import arenas
from .db import agora
from .erros import ErroNegocio, SemPermissao
from .models import Arena, Assinatura, Atividade, Campeonato, Pagamento, Quadra, Reserva, Usuario

DIAS_PADRAO, DIAS_MAXIMO = 30, 366
SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
PAGAS = ("pago", "estornado")


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def _f(v) -> float:
    return float(_d(v))


def periodo(inicio: date | None, fim: date | None) -> tuple[date, date]:
    """Período do relatório (inclusive nas duas pontas). Sem datas: os últimos 30 dias, até hoje."""
    hoje = agora().date()
    fim = fim or hoje
    inicio = inicio or fim - timedelta(days=DIAS_PADRAO - 1)
    if inicio > fim:
        raise ErroNegocio("A data inicial precisa ser anterior à final.")
    if (fim - inicio).days + 1 > DIAS_MAXIMO:
        raise ErroNegocio(f"Escolha um período de até {DIAS_MAXIMO} dias.")
    return inicio, fim


def _limites(inicio: date, fim: date) -> tuple[datetime, datetime]:
    return datetime.combine(inicio, time.min), datetime.combine(fim + timedelta(days=1), time.min)


def _horas(r: Reserva) -> float:
    return max(0.0, (r.fim - r.inicio).total_seconds() / 3600)


def _horas_abertas(arena: Arena) -> float:
    abre, fecha = arena.abre, arena.fecha
    h = (datetime.combine(date.min, fecha) - datetime.combine(date.min, abre)).total_seconds() / 3600
    return h if h > 0 else 24.0


def _exigir_acesso(s: SessaoORM, usuario: Usuario, arena_id: int) -> Arena:
    arena = arenas.obter(s, arena_id)
    if not usuario.admin and usuario not in arena.gestores:
        raise SemPermissao("Só os gestores desta arena veem o relatório dela.")
    return arena


def arenas_do_usuario(s: SessaoORM, usuario: Usuario) -> list[dict]:
    return [{"id": a.id, "nome": a.nome} for a in arenas.minhas(s, usuario)]


def _reservas(s: SessaoORM, arena_id: int, ini: datetime, fim: datetime) -> list[tuple[Reserva, Quadra]]:
    q = (select(Reserva, Quadra).join(Quadra, Quadra.id == Reserva.quadra_id)
         .where(Quadra.arena_id == arena_id, Reserva.inicio >= ini, Reserva.inicio < fim).order_by(Reserva.inicio))
    return [(r, qd) for r, qd in s.execute(q).all()]


def arena(s: SessaoORM, usuario: Usuario, arena_id: int, inicio: date | None = None, fim: date | None = None) -> dict:
    """Relatório de uma arena no período: resumo financeiro, ocupação, quadras, dias da semana, horários de pico, série diária e melhores clientes."""
    a = _exigir_acesso(s, usuario, arena_id)
    d_ini, d_fim = periodo(inicio, fim)
    ini, lim = _limites(d_ini, d_fim)
    linhas = _reservas(s, a.id, ini, lim)
    quadras = [q for q in a.quadras if q.ativa]
    dias = (d_fim - d_ini).days + 1

    ocupadas = [(r, q) for r, q in linhas if r.tipo != "bloqueio"]
    bloqueios = [r for r, _ in linhas if r.tipo == "bloqueio"]
    de_atletas = [(r, q) for r, q in ocupadas if r.usuario_id]
    pagas = [r for r, _ in ocupadas if r.status_pagamento in PAGAS]
    pendentes = [r for r, _ in ocupadas if r.status_pagamento == "pendente"]
    na_arena = [r for r, _ in de_atletas if r.forma_pagamento == "local"]

    bruto = sum((_d(r.valor) for r in pagas), Decimal(0))
    estornos = sum((_d(r.valor_reembolsado) for r in pagas), Decimal(0))
    comissao = sum((_d(r.comissao) for r in pagas), Decimal(0))
    horas_res = sum(_horas(r) for r, _ in ocupadas)
    horas_disp = _horas_abertas(a) * len(quadras) * dias
    resumo = {
        "reservas": len(ocupadas), "reservas_atletas": len(de_atletas), "reservas_gestor": len(ocupadas) - len(de_atletas), "bloqueios": len(bloqueios),
        "pagas_pelo_app": len(pagas), "pendentes_pelo_app": len(pendentes), "combinadas_na_arena": len(na_arena),
        "recebido_bruto": _f(bruto), "estornado": _f(estornos), "comissao_playgo": _f(comissao), "liquido_estimado": _f(bruto - estornos - comissao),
        "a_receber_pelo_app": _f(sum((_d(r.valor) for r in pendentes), Decimal(0))), "previsto_na_arena": _f(sum((_d(r.valor) for r in na_arena), Decimal(0))),
        "ticket_medio": _f(bruto / len(pagas)) if pagas else 0.0,
        "horas_reservadas": round(horas_res, 1), "horas_disponiveis": round(horas_disp, 1), "ocupacao_pct": round(100 * horas_res / horas_disp) if horas_disp else 0,
    }

    por_quadra: dict[int, dict] = {q.id: {"quadra": q.nome, "reservas": 0, "horas": 0.0, "recebido": Decimal(0)} for q in quadras}
    por_semana = [{"dia": n, "reservas": 0, "recebido": Decimal(0)} for n in SEMANA]
    por_hora: dict[int, int] = defaultdict(int)
    por_dia: dict[date, dict] = {d_ini + timedelta(days=i): {"reservas": 0, "recebido": Decimal(0)} for i in range(dias)}
    clientes: dict[int, dict] = {}
    for r, q in ocupadas:
        linha = por_quadra.setdefault(q.id, {"quadra": q.nome, "reservas": 0, "horas": 0.0, "recebido": Decimal(0)})
        pago = _d(r.valor) if r.status_pagamento in PAGAS else Decimal(0)
        linha["reservas"] += 1
        linha["horas"] += _horas(r)
        linha["recebido"] += pago
        sem = por_semana[r.inicio.weekday()]
        sem["reservas"] += 1
        sem["recebido"] += pago
        por_hora[r.inicio.hour] += 1
        dia = por_dia.get(r.inicio.date())
        if dia is not None:
            dia["reservas"] += 1
            dia["recebido"] += pago
        if r.usuario_id:
            c = clientes.setdefault(r.usuario_id, {"reservas": 0, "valor": Decimal(0)})
            c["reservas"] += 1
            c["valor"] += _d(r.valor)
    nomes = {u.id: u for u in s.scalars(select(Usuario).where(Usuario.id.in_(list(clientes) or [0])))}
    top = sorted(clientes.items(), key=lambda kv: (-kv[1]["reservas"], -kv[1]["valor"]))[:10]

    return {
        "arena": {"id": a.id, "nome": a.nome}, "periodo": {"inicio": d_ini.isoformat(), "fim": d_fim.isoformat(), "dias": dias}, "resumo": resumo,
        "por_quadra": [{"quadra": v["quadra"], "reservas": v["reservas"], "horas": round(v["horas"], 1), "recebido": _f(v["recebido"])} for v in por_quadra.values()],
        "por_dia_da_semana": [{"dia": v["dia"], "reservas": v["reservas"], "recebido": _f(v["recebido"])} for v in por_semana],
        "por_hora": [{"hora": f"{h:02d}:00", "reservas": n} for h, n in sorted(por_hora.items())],
        "por_dia": [{"dia": d.isoformat(), "reservas": v["reservas"], "recebido": _f(v["recebido"])} for d, v in por_dia.items()],
        "melhores_clientes": [{"usuario": nomes[uid].arroba if uid in nomes else "(conta removida)", "reservas": v["reservas"], "valor": _f(v["valor"])} for uid, v in top],
    }


def reservas_csv(s: SessaoORM, usuario: Usuario, arena_id: int, inicio: date | None = None, fim: date | None = None) -> list[list]:
    """Linhas do CSV (cabeçalho + uma por reserva). Pessoas só pelo @usuario, nunca nome real nem e-mail."""
    a = _exigir_acesso(s, usuario, arena_id)
    d_ini, d_fim = periodo(inicio, fim)
    ini, lim = _limites(d_ini, d_fim)
    linhas = _reservas(s, a.id, ini, lim)
    ids = {r.usuario_id for r, _ in linhas if r.usuario_id}
    nomes = {u.id: u.arroba for u in s.scalars(select(Usuario).where(Usuario.id.in_(list(ids) or [0])))}
    saida = [["data", "inicio", "fim", "quadra", "tipo", "cliente", "forma_pagamento", "status_pagamento", "valor", "comissao_playgo", "devolvido"]]
    for r, q in linhas:
        saida.append([r.inicio.strftime("%d/%m/%Y"), r.inicio.strftime("%H:%M"), r.fim.strftime("%H:%M"), q.nome, r.tipo,
                      nomes.get(r.usuario_id, r.rotulo), r.forma_pagamento or "", r.status_pagamento,
                      str(_d(r.valor)).replace(".", ","), str(_d(r.comissao)).replace(".", ","), str(_d(r.valor_reembolsado)).replace(".", ",")])
    return saida


def plataforma(s: SessaoORM, usuario: Usuario, inicio: date | None = None, fim: date | None = None) -> dict:
    """Visão geral para a administração: arenas por resultado, comissão do PlayGo, crescimento e assinaturas."""
    if not usuario.admin:
        raise SemPermissao("Só a administração vê o relatório geral.")
    d_ini, d_fim = periodo(inicio, fim)
    ini, lim = _limites(d_ini, d_fim)

    por_arena = []
    tot_res, tot_bruto, tot_com = 0, Decimal(0), Decimal(0)
    for a in s.scalars(select(Arena).where(Arena.ativo).order_by(Arena.nome)):
        linhas = [(r, q) for r, q in _reservas(s, a.id, ini, lim) if r.tipo != "bloqueio"]
        pagas = [r for r, _ in linhas if r.status_pagamento in PAGAS]
        bruto = sum((_d(r.valor) for r in pagas), Decimal(0))
        com = sum((_d(r.comissao) for r in pagas), Decimal(0))
        tot_res, tot_bruto, tot_com = tot_res + len(linhas), tot_bruto + bruto, tot_com + com
        por_arena.append({"id": a.id, "nome": a.nome, "cidade": a.cidade, "reservas": len(linhas), "pagas_pelo_app": len(pagas), "recebido_bruto": _f(bruto), "comissao_playgo": _f(com),
                          "horas": round(sum(_horas(r) for r, _ in linhas), 1)})
    por_arena.sort(key=lambda x: (-x["recebido_bruto"], -x["reservas"]))

    def contar(col, tabela):
        return s.scalar(select(func.count()).select_from(tabela).where(col >= ini, col < lim)) or 0

    assinaturas = {plano: n for plano, n in s.execute(select(Assinatura.plano, func.count()).where(Assinatura.status == "ativa").group_by(Assinatura.plano)).all()}
    pagos_planos = s.scalar(select(func.coalesce(func.sum(Pagamento.valor), 0)).where(Pagamento.pago_em >= d_ini, Pagamento.pago_em <= d_fim, Pagamento.status.in_(("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH")))) or 0
    return {
        "periodo": {"inicio": d_ini.isoformat(), "fim": d_fim.isoformat(), "dias": (d_fim - d_ini).days + 1},
        "resumo": {"reservas": tot_res, "recebido_bruto": _f(tot_bruto), "comissao_playgo": _f(tot_com), "receita_planos": _f(pagos_planos),
                   "novos_usuarios": contar(Usuario.criado_em, Usuario), "atividades_criadas": contar(Atividade.criado_em, Atividade), "campeonatos_criados": contar(Campeonato.criado_em, Campeonato),
                   "arenas_ativas": len(por_arena), "arenas_com_reserva": len([a for a in por_arena if a["reservas"]])},
        "arenas": por_arena,
        "assinaturas_ativas": [{"plano": k, "total": v} for k, v in sorted(assinaturas.items())],
    }
