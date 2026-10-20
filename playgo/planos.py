"""Planos e mensalidades por perfil.

  gratuito     Usuário: vê tudo, publica e comenta no feed e participa de atividades. Não organiza atividades. O preço é do
               administrador: em R$ 0 não há cobrança; com valor, publicar/comentar/participar exige estar com acesso em dia.
  pro          Pro: organiza atividades (com limites de participantes e de atividades abertas).
  organizador  campeonatos e atividades sem limite.
  arena        tudo do organizador + arenas, quadras, agenda e divulgação de horários.

Teste grátis: 30 dias, começa sozinho na primeira vez que a pessoa precisa do recurso (uma vez por plano).
Ao vencer, 7 dias de tolerância (segue funcionando, com aviso); depois disso não cria nem divulga nada novo, mas o que
já existe continua e os dados ficam preservados. Administrador não paga.
Preços e limites vivem na tabela `planos` (o administrador edita); o que faltar usa DEFAULTS."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, notificacoes
from .config import settings
from .db import agora
from .erros import ErroNegocio, NaoEncontrado, PlanoNecessario, SemPermissao
from .models import Assinatura, Atividade, CreditoEvento, Pagamento, Plano, Usuario, arena_gestores

ORDEM = {"gratuito": 0, "pro": 1, "organizador": 2, "arena": 3}
CODIGOS = tuple(ORDEM)


@dataclass
class Regras:
    codigo: str
    nome: str
    valor_mensal: Decimal
    max_participantes: int | None  # None = sem limite
    max_atividades_abertas: int | None
    pode_campeonato: bool
    pode_arena: bool
    descricao: str = ""
    pode_atividade: bool = True
    valor_anual: Decimal | None = None  # None = sem opção anual
    valor_evento: Decimal = Decimal(0)  # 0 = sem venda avulsa de eventos


# Valores iniciais. Os preços ficam em R$ 0 até o administrador defini-los: sem preço, não há como assinar.
DEFAULTS = {
    "gratuito": Regras("gratuito", "Usuário", Decimal(0), 30, 5, False, False, "Vê tudo, publica no feed e participa de atividades. Não organiza atividades.", False),
    "pro": Regras("pro", "Pro", Decimal(0), 30, 5, False, False, "Organiza atividades, com limite de participantes e de atividades abertas."),
    "organizador": Regras("organizador", "Organizador", Decimal(0), None, None, True, False, "Campeonatos e atividades sem limite."),
    "arena": Regras("arena", "Arena", Decimal(0), None, None, True, True, "Arenas, quadras, agenda e divulgação de horários, mais tudo do Organizador."),
}


def regras(s: SessaoORM, codigo: str) -> Regras:
    base = DEFAULTS[codigo]
    p = s.get(Plano, codigo)
    if p is None:
        return base
    return Regras(codigo, p.nome or base.nome, p.valor_mensal, p.max_participantes, p.max_atividades_abertas, p.pode_campeonato, p.pode_arena, base.descricao, p.pode_atividade,
                  p.valor_anual if p.valor_anual and p.valor_anual > 0 else None, p.valor_evento or Decimal(0))


def todas(s: SessaoORM) -> list[Regras]:
    return [regras(s, c) for c in CODIGOS]


def vitrine(s: SessaoORM) -> list[dict]:
    """Os planos como a página de divulgação os apresenta (preço e limites vêm da tabela, que o administrador edita)."""
    itens = {
        "gratuito": ["Ver tudo e participar de atividades", "Publicar e comentar no feed", "Entrar em grupos e comunidades", "Acompanhar campeonatos e jogos ao vivo"],
        "pro": ["Organizar atividades", "{participantes}", "{abertas}", "Convidar por @usuario ou link"],
        "organizador": ["Atividades sem limite", "Campeonatos com sorteio, grupos e chaves", "Placar ao vivo, mesários e agenda em lote", "Regulamento em PDF e elenco das equipes"],
        "arena": ["Tudo do plano Organizador", "Arenas, quadras e agenda", "Divulgação de horários para atletas próximos", "Painel de ocupação e alcance"],
    }
    resumo = {
        "gratuito": "Para quem joga e acompanha.",
        "pro": "Para quem organiza os rachas e jogos do grupo.",
        "organizador": "Para quem faz campeonatos e torneios.",
        "arena": "Para quem administra quadras e arenas.",
    }
    saida = []
    for r in todas(s):
        lista = []
        for item in itens[r.codigo]:
            if item == "{participantes}":
                item = f"Até {r.max_participantes} participantes por atividade" if r.max_participantes else "Participantes sem limite"
            elif item == "{abertas}":
                item = f"Até {r.max_atividades_abertas} atividades abertas ao mesmo tempo" if r.max_atividades_abertas else "Atividades abertas sem limite"
            lista.append(item)
        saida.append({"codigo": r.codigo, "nome": r.nome, "valor": float(r.valor_mensal), "valor_anual": float(r.valor_anual) if r.valor_anual else None, "resumo": resumo[r.codigo], "itens": lista})
    return saida


def _dict(r: Regras) -> dict:
    return {
        "codigo": r.codigo, "nome": r.nome, "valor_mensal": float(r.valor_mensal), "max_participantes": r.max_participantes,
        "max_atividades_abertas": r.max_atividades_abertas, "pode_campeonato": r.pode_campeonato, "pode_arena": r.pode_arena,
        "descricao": r.descricao, "pode_atividade": r.pode_atividade, "assinavel": r.valor_mensal > 0,
        "valor_anual": float(r.valor_anual) if r.valor_anual else None, "desconto_anual": desconto_anual(r), "anual_disponivel": bool(r.valor_anual and r.valor_mensal > 0),
        "valor_evento": float(r.valor_evento), "evento_disponivel": bool(r.valor_evento and r.valor_evento > 0 and r.codigo == "organizador"),
    }


def desconto_anual(r: Regras) -> int:
    """Quanto o anual economiza em relação a 12 mensalidades, em %."""
    if not r.valor_anual or r.valor_mensal <= 0:
        return 0
    return max(0, round(100 * (1 - float(r.valor_anual) / (12 * float(r.valor_mensal)))))


def salvar_regras(s: SessaoORM, por: Usuario, codigo: str, nome: str | None = None, valor_mensal: Decimal | None = None, max_participantes: int | None = ..., max_atividades_abertas: int | None = ..., pode_campeonato: bool | None = None, pode_arena: bool | None = None, pode_atividade: bool | None = None, valor_anual: Decimal | None = ..., valor_evento: Decimal | None = None) -> dict:
    if not por.admin:
        raise SemPermissao("Só administradores definem preços e limites dos planos.")
    if codigo not in CODIGOS:
        raise NaoEncontrado("Plano não encontrado.")
    if (valor_mensal is not None and valor_mensal < 0) or (valor_evento is not None and valor_evento < 0) or (valor_anual is not ... and valor_anual is not None and valor_anual < 0):
        raise ErroNegocio("O valor não pode ser negativo.")
    p = s.get(Plano, codigo)
    if p is None:
        base = DEFAULTS[codigo]
        p = Plano(codigo=codigo, nome=base.nome, valor_mensal=base.valor_mensal, max_participantes=base.max_participantes, max_atividades_abertas=base.max_atividades_abertas, pode_campeonato=base.pode_campeonato, pode_arena=base.pode_arena, pode_atividade=base.pode_atividade)
        s.add(p)
    if nome:
        p.nome = nome.strip()[:60]
    if valor_mensal is not None:
        p.valor_mensal = valor_mensal
    if valor_anual is not ...:
        p.valor_anual = valor_anual if valor_anual and valor_anual > 0 else None
    if valor_evento is not None:
        p.valor_evento = valor_evento
    if max_participantes is not ...:
        p.max_participantes = max_participantes if max_participantes and max_participantes > 0 else None
    if max_atividades_abertas is not ...:
        p.max_atividades_abertas = max_atividades_abertas if max_atividades_abertas and max_atividades_abertas > 0 else None
    if pode_campeonato is not None:
        p.pode_campeonato = pode_campeonato
    if pode_arena is not None:
        p.pode_arena = pode_arena
    if pode_atividade is not None:
        p.pode_atividade = pode_atividade
    auditoria.registrar(s, por.id, "plano_alterar", "plano", None, codigo=codigo, valor=str(p.valor_mensal), anual=str(p.valor_anual), evento=str(p.valor_evento))
    s.commit()
    return _dict(regras(s, codigo))


# ---------------------------------------------------------------- situação da pessoa


def assinatura_de(s: SessaoORM, u: Usuario) -> Assinatura | None:
    return s.scalar(select(Assinatura).where(Assinatura.usuario_id == u.id))


def fim_do_acesso(a: Assinatura) -> date | None:
    datas = [d for d in ((a.vigente_ate if a.status in ("ativa", "cancelada") else None), a.teste_ate if a.status == "teste" else None) if d]
    if a.status in ("ativa", "cancelada") and a.teste_ate:
        datas.append(a.teste_ate)  # pagou durante o teste: vale o que for mais longo
    return max(datas) if datas else None


def situacao(s: SessaoORM, u: Usuario, hoje: date | None = None) -> dict:
    """Em que plano a pessoa está agora. `plano` é o que vale hoje; `plano_contratado` é o que ela assinou/testa.
    `teste_disponivel` só lista planos acima do atual e ainda não testados."""
    sit = _situacao(s, u, hoje)
    sit["teste_disponivel"] = [c for c in sit["teste_disponivel"] if ORDEM[c] > ORDEM[sit["plano"]]]
    return sit


def _situacao(s: SessaoORM, u: Usuario, hoje: date | None) -> dict:
    hoje = hoje or agora().date()
    a = assinatura_de(s, u)
    base = {"plano": "gratuito", "plano_contratado": None, "status": "gratuito", "inicio": None, "fim": None, "dias_restantes": None, "tolerancia_ate": None, "teste_disponivel": [c for c in CODIGOS if c != "gratuito"], "assinatura_asaas": False, "cancelada": False}
    if u.admin:
        return base | {"plano": "arena", "status": "administrador", "teste_disponivel": []}
    if a is None:
        return base
    usados = set(a.testes_usados or [])
    base["teste_disponivel"] = [c for c in CODIGOS if c != "gratuito" and c not in usados]
    base["assinatura_asaas"] = bool(a.asaas_subscription_id) and a.status != "cancelada"
    base["cancelada"] = a.status == "cancelada"
    fim = fim_do_acesso(a)
    if fim is None:
        return base
    tol = fim + timedelta(days=settings.tolerancia_dias)
    base |= {"plano_contratado": a.plano, "inicio": a.vigente_desde, "fim": fim, "dias_restantes": (fim - hoje).days, "tolerancia_ate": tol}
    if a.vigente_desde and hoje < a.vigente_desde and a.status == "ativa":
        return base | {"status": "agendada"}  # concedido para começar numa data futura: até lá vale o plano Usuário
    if hoje <= fim:
        return base | {"plano": a.plano, "status": a.status}
    if hoje <= tol:
        return base | {"plano": a.plano, "status": "tolerancia"}
    return base | {"status": "vencida"}


def arenas_premium(s: SessaoORM, ids: list[int]) -> set[int]:
    """Quais dessas arenas têm um gestor com plano Arena pago (ativo ou cancelado ainda dentro do período). Só elas ganham o pino com coroa."""
    if not ids:
        return set()
    hoje = agora().date()
    q = (
        select(arena_gestores.c.arena_id).join(Assinatura, Assinatura.usuario_id == arena_gestores.c.usuario_id)
        .where(arena_gestores.c.arena_id.in_(ids), Assinatura.plano == "arena", Assinatura.status.in_(("ativa", "cancelada")),
               Assinatura.vigente_ate >= hoje, or_(Assinatura.vigente_desde.is_(None), Assinatura.vigente_desde <= hoje))
    )
    return set(s.scalars(q))


# ---------------------------------------------------------------- eventos avulsos (créditos)

CREDITO_VALIDADE_DIAS = 365  # a pessoa tem 12 meses, depois da confirmação do pagamento, para criar os campeonatos comprados
AVISO_DIAS = 7  # na última semana a pessoa vê o aviso e recebe a notificação


def _creditos_validos(s: SessaoORM, u: Usuario, hoje: date | None = None) -> list[CreditoEvento]:
    hoje = hoje or agora().date()
    return list(s.scalars(select(CreditoEvento).where(
        CreditoEvento.usuario_id == u.id, CreditoEvento.status == "pago", CreditoEvento.valida_ate >= hoje, CreditoEvento.usados < CreditoEvento.quantidade,
    ).order_by(CreditoEvento.valida_ate, CreditoEvento.id)))


def saldo_eventos(s: SessaoORM, u: Usuario) -> int:
    return sum(c.quantidade - c.usados for c in _creditos_validos(s, u))


def consumir_evento(s: SessaoORM, u: Usuario, campeonato_id: int | None = None) -> bool:
    """Gasta 1 crédito (o que vence primeiro) quando o campeonato foi criado SEM plano que já o permita. Devolve se gastou."""
    if u.admin or regras(s, situacao(s, u)["plano"]).pode_campeonato:
        return False
    for c in _creditos_validos(s, u):
        c.usados += 1
        auditoria.registrar(s, u.id, "evento_credito_usar", "campeonato", campeonato_id, credito=c.id, restam=c.quantidade - c.usados)
        return True
    return False


def eventos(s: SessaoORM, u: Usuario) -> dict:
    """Situação dos eventos avulsos: saldo, validade mais próxima e as compras (com o link da fatura)."""
    hoje = agora().date()
    validos = _creditos_validos(s, u, hoje)
    compras = s.scalars(select(CreditoEvento).where(CreditoEvento.usuario_id == u.id).order_by(CreditoEvento.id.desc()).limit(20)).all()
    prox = validos[0].valida_ate if validos else None
    return {
        "saldo": sum(c.quantidade - c.usados for c in validos), "valida_ate": prox.isoformat() if prox else None, "dias_restantes": (prox - hoje).days if prox else None,
        "compras": [{"id": c.id, "quantidade": c.quantidade, "usados": c.usados, "valor": float(c.valor), "status": c.status, "valida_ate": c.valida_ate.isoformat() if c.valida_ate else None,
                     "criado_em": c.criado_em.date().isoformat(), "link": c.invoice_url if (c.invoice_url or "").startswith("https://") and c.status == "pendente" else None} for c in compras],
    }


def aviso_vencimento(s: SessaoORM, u: Usuario) -> dict | None:
    """Na última semana do acesso (plano que não renova sozinho, ou eventos comprados): texto para mostrar em destaque."""
    if u.admin:
        return None
    sit = situacao(s, u)
    a = assinatura_de(s, u)
    dias = sit["dias_restantes"]
    if sit["status"] in ("teste", "ativa", "cancelada") and dias is not None and 0 <= dias <= AVISO_DIAS and not (a and a.status == "ativa" and a.asaas_subscription_id):
        nome = DEFAULTS[sit["plano"]].nome
        quando = "termina hoje" if dias == 0 else f"termina em {dias} dia{'s' if dias > 1 else ''}"
        return {"tipo": "plano", "dias": dias, "texto": f"Seu acesso ao plano {nome} {quando}. Depois disso você volta ao plano Usuário (o que já existe continua)."}
    ev = eventos(s, u)
    if ev["saldo"] and ev["dias_restantes"] is not None and ev["dias_restantes"] <= AVISO_DIAS:
        d = ev["dias_restantes"]
        return {"tipo": "eventos", "dias": d, "texto": f"Seus {ev['saldo']} evento{'s' if ev['saldo'] > 1 else ''} comprado{'s' if ev['saldo'] > 1 else ''} vence{'m' if ev['saldo'] > 1 else ''} {'hoje' if d == 0 else 'em ' + str(d) + ' dia' + ('s' if d > 1 else '')}. Crie o campeonato antes disso."}
    return None


def resumo(s: SessaoORM, u: Usuario) -> dict:
    sit = situacao(s, u)
    r = regras(s, sit["plano"])
    return {
        "eventos": eventos(s, u), "aviso_vencimento": aviso_vencimento(s, u), "ciclo": (assinatura_de(s, u).ciclo if assinatura_de(s, u) else None),
        **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in sit.items()},
        "regras": _dict(r), "planos": [_dict(x) for x in todas(s)],
        "acesso_basico": acesso_basico(s, u), "atividades_abertas": _abertas(s, u), "teste_dias": settings.teste_dias, "tolerancia_dias": settings.tolerancia_dias,
        "cobrancas": cobrancas(s, u),
    }


def permissoes(s: SessaoORM, u: Usuario) -> dict:
    """O que o plano da pessoa libera na tela: organizar atividade, criar campeonato e cadastrar/gerir arena.
    A tela esconde o que não vale; a regra de verdade continua em `exigir` dentro dos serviços."""
    if u.admin:
        return {"atividade": True, "campeonato": True, "arena": True, "grupo": True, "comunidade": True}
    r = regras(s, situacao(s, u)["plano"])
    return {
        "atividade": bool(r.pode_atividade), "campeonato": bool(r.pode_campeonato or saldo_eventos(s, u) > 0), "arena": bool(r.pode_arena),  # evento avulso comprado também libera criar campeonato
        "grupo": bool(r.pode_atividade),  # grupos: do Pro em diante (quem organiza atividades)
        "comunidade": bool(r.pode_campeonato or r.pode_arena),  # comunidades: só Organizador e Arena; os demais participam
    }


ACESSO_VIGENTE = ("administrador", "teste", "ativa", "cancelada", "tolerancia")


def acesso_basico(s: SessaoORM, u: Usuario) -> bool:
    """Pode publicar, comentar e participar? Sempre, enquanto o plano Usuário estiver em R$ 0; com valor, só com algum plano em dia."""
    if u.admin or regras(s, "gratuito").valor_mensal <= 0:
        return True
    return situacao(s, u)["status"] in ACESSO_VIGENTE


def cobrancas(s: SessaoORM, u: Usuario) -> list[dict]:
    """Faturas da pessoa, da mais recente à mais antiga; `link` abre a fatura no Asaas (2ª via, Pix, boleto, recibo)."""
    a = assinatura_de(s, u)
    if a is None:
        return []
    pgs = s.scalars(select(Pagamento).where(Pagamento.assinatura_id == a.id).order_by(Pagamento.vencimento.desc(), Pagamento.id.desc())).all()
    return [
        {"id": p.id, "valor": float(p.valor), "status": p.status, "vencimento": p.vencimento.isoformat(), "pago_em": p.pago_em.isoformat() if p.pago_em else None,
         "link": p.invoice_url if (p.invoice_url or "").startswith("https://") else None}
        for p in pgs
    ]


def _abertas(s: SessaoORM, u: Usuario) -> int:
    return s.scalar(select(func.count()).select_from(Atividade).where(Atividade.organizador_id == u.id, Atividade.status == "aberta", Atividade.inicio >= agora())) or 0


# ---------------------------------------------------------------- teste grátis e concessão manual


def _iniciar_teste(s: SessaoORM, u: Usuario, plano: str) -> bool:
    """Teste de 30 dias, uma vez por plano. Só começa se a pessoa ainda não tem acesso a esse plano."""
    sit = situacao(s, u)
    if ORDEM[sit["plano"]] >= ORDEM[plano] or plano not in sit["teste_disponivel"]:
        return False
    a = assinatura_de(s, u)
    hoje = agora().date()
    if a is None:
        a = Assinatura(usuario_id=u.id, plano=plano, status="teste", origem="teste", testes_usados=[])
        s.add(a)
    a.plano, a.status, a.origem = plano, "teste", "teste"
    a.teste_ate = hoje + timedelta(days=settings.teste_dias)
    a.testes_usados = sorted({*(a.testes_usados or []), plano})
    s.flush()
    nome = DEFAULTS[plano].nome
    notificacoes.avisar(s, u.id, "plano", f"🎁 Teste do plano {nome} começou", f"São {settings.teste_dias} dias, até {a.teste_ate.strftime('%d/%m/%Y')}. Você assina quando quiser, em Meu plano.", "/planos", None, f"plano:teste:{u.id}:{plano}")
    auditoria.registrar(s, u.id, "plano_teste_iniciar", "usuario", u.id, plano=plano, ate=a.teste_ate)
    return True


def conceder(s: SessaoORM, admin: Usuario, alvo_id: int, plano: str, ate: date | None, inicio: date | None = None) -> dict:
    """Cortesia ou cobrança fora do app: o administrador libera um plano de `inicio` (hoje, se não informado) até `ate`
    (ou volta a pessoa para o gratuito). Antes do início a pessoa continua no plano Usuário."""
    if not admin.admin:
        raise SemPermissao("Só administradores concedem planos.")
    alvo = s.get(Usuario, alvo_id)
    if alvo is None or not alvo.ativo:
        raise NaoEncontrado("Usuário não encontrado.")
    if plano not in CODIGOS:
        raise ErroNegocio("Plano inválido.")
    a = assinatura_de(s, alvo)
    if plano == "gratuito" and ate is None:
        if a is not None:
            a.status, a.vigente_ate, a.teste_ate = "cancelada", agora().date() - timedelta(days=settings.tolerancia_dias + 1), None
    else:
        if ate is None or ate < agora().date():
            raise ErroNegocio("Informe uma data de validade no futuro.")  # sem data, só o plano Usuário "volta ao básico"
        inicio = inicio or agora().date()
        if ate < inicio:
            raise ErroNegocio("A validade não pode ser anterior ao início do acesso.")
        if a is None:
            a = Assinatura(usuario_id=alvo.id, plano=plano, status="ativa", origem="manual", testes_usados=[])
            s.add(a)
        a.plano, a.status, a.origem, a.vigente_ate, a.vigente_desde = plano, "ativa", "manual", ate, inicio
    auditoria.registrar(s, admin.id, "plano_conceder", "usuario", alvo.id, plano=plano, inicio=inicio, ate=ate)
    if ate is not None:
        quando = f"De {inicio.strftime('%d/%m/%Y')} até {ate.strftime('%d/%m/%Y')}." if inicio > agora().date() else f"Válido até {ate.strftime('%d/%m/%Y')}."
        notificacoes.avisar(s, alvo.id, "plano", f"Plano {DEFAULTS[plano].nome} liberado", quando, "/planos", None, f"plano:conceder:{alvo.id}:{plano}:{inicio}:{ate}")
    s.commit()
    return resumo(s, alvo)


# ---------------------------------------------------------------- o portão


def exigir(s: SessaoORM, u: Usuario, recurso: str, max_participantes: int | None = None) -> None:
    """Levanta PlanoNecessario se o plano da pessoa não cobre o recurso ('atividade' | 'campeonato' | 'arena').
    Antes de recusar, tenta o teste grátis (só se ainda não foi usado para aquele plano)."""
    if u.admin:
        return
    if recurso == "basico":
        if not acesso_basico(s, u):
            raise PlanoNecessario("gratuito", f"Para publicar e participar, assine o plano {regras(s, 'gratuito').nome}.")
        return
    r = regras(s, situacao(s, u)["plano"])
    if recurso == "atividade":
        if not r.pode_atividade:
            motivo, necessario = "organizar atividades", "pro"
        elif r.max_participantes is not None and max_participantes and max_participantes > r.max_participantes:
            motivo, necessario = f"atividades com mais de {r.max_participantes} participantes", "organizador"
        elif r.max_atividades_abertas is not None and _abertas(s, u) >= r.max_atividades_abertas:
            motivo, necessario = f"mais de {r.max_atividades_abertas} atividades abertas ao mesmo tempo", "organizador"
        else:
            return
    elif recurso == "campeonato":
        if r.pode_campeonato or saldo_eventos(s, u) > 0:
            return
        motivo, necessario = "criar campeonatos", "organizador"
    elif recurso == "grupo":
        if r.pode_atividade:
            return
        motivo, necessario = "criar e gerir grupos (aceitar e remover participantes)", "pro"
    elif recurso == "comunidade":
        if r.pode_campeonato or r.pode_arena:
            return
        motivo, necessario = "criar comunidades (os demais usuários participam delas)", "organizador"
    elif recurso == "arena":
        if r.pode_arena:
            return
        motivo, necessario = "cadastrar e gerir arenas, quadras, agenda e horários divulgados", "arena"
    else:
        raise ValueError(recurso)
    if _iniciar_teste(s, u, necessario):
        return
    sit = situacao(s, u)
    nome = DEFAULTS[necessario].nome
    if sit["status"] in ("vencida",) or necessario not in sit["teste_disponivel"]:
        raise PlanoNecessario(necessario, f"Seu acesso ao plano {nome} terminou. Para {motivo}, assine o plano {nome}.")
    raise PlanoNecessario(necessario, f"Para {motivo}, assine o plano {nome}.")


# ---------------------------------------------------------------- avisos de vencimento (agendador)

MARCOS = (7, 3, 1, 0, -1)


def avisar_vencimentos(s: SessaoORM) -> int:
    """Avisa 7, 3 e 1 dia antes do fim, no dia, e quando a tolerância começa/acaba. Uma vez por marco."""
    hoje = agora().date()
    total = 0
    for a in s.scalars(select(Assinatura)):
        fim = fim_do_acesso(a)
        if fim is None:
            continue
        dias = (fim - hoje).days
        nome = DEFAULTS[a.plano].nome
        tol_fim = (fim + timedelta(days=settings.tolerancia_dias) - hoje).days
        if a.status == "ativa" and a.asaas_subscription_id:
            continue  # a renovação automática cuida; só avisamos se o Asaas reportar atraso
        if dias in MARCOS[:4]:
            titulo, corpo = f"Seu plano {nome} {'vence hoje' if dias == 0 else 'vence em ' + str(dias) + ' dia' + ('s' if dias > 1 else '')}", "Assine em Meu plano para não perder o acesso."
            marco = f"d{dias}"
        elif dias == -1 or tol_fim == 0:
            titulo, corpo = f"Seu plano {nome} venceu", f"Você tem até {(fim + timedelta(days=settings.tolerancia_dias)).strftime('%d/%m')} de tolerância. Depois, não dá mais para criar nem divulgar nada novo (o que já existe continua)."
            marco = "tol" if dias == -1 else "fim"
            if marco == "fim":
                titulo = f"Acabou a tolerância do plano {nome}"
        else:
            continue
        if notificacoes.avisar(s, a.usuario_id, "plano", titulo, corpo, "/planos", None, f"plano:{a.id}:{fim}:{marco}"):
            total += 1
    for c in s.scalars(select(CreditoEvento).where(CreditoEvento.status == "pago", CreditoEvento.usados < CreditoEvento.quantidade, CreditoEvento.valida_ate.is_not(None))):
        dias = (c.valida_ate - hoje).days
        restam = c.quantidade - c.usados
        plural = "s" if restam > 1 else ""
        if dias in (7, 3, 1, 0):
            titulo = f"Seus {restam} evento{plural} " + ("vence hoje" if dias == 0 else f"vence em {dias} dia{'s' if dias > 1 else ''}")
            corpo, marco = "Crie o campeonato antes do prazo; depois disso o crédito expira.", f"d{dias}"
        elif dias == -1:
            titulo, corpo, marco = f"Seus {restam} evento{plural} expirou", "O prazo de 12 meses acabou. Você pode comprar novos eventos em Meu plano.", "fim"
        else:
            continue
        if notificacoes.avisar(s, c.usuario_id, "plano", titulo, corpo, "/planos", None, f"evento:{c.id}:{marco}"):
            total += 1
    s.commit()
    return total
