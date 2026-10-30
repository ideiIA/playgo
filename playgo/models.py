from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, agora

# ---------------------------------------------------------------- vocabulário

NIVEIS = (("iniciante", "Iniciante"), ("intermediario", "Intermediário"), ("avancado", "Avançado"))
NOME_NIVEL = dict(NIVEIS) | {"todos": "Todos os níveis"}
ORDEM_NIVEL = {"iniciante": 0, "intermediario": 1, "avancado": 2}

CATEGORIAS = (("misto", "Misto"), ("masculino", "Masculino"), ("feminino", "Feminino"))

# Disponibilidade do atleta; "fim_de_semana" vale para sábado e domingo em qualquer horário.
PERIODOS = (("manha", "Manhã"), ("tarde", "Tarde"), ("noite", "Noite"), ("fim_de_semana", "Fim de semana"))

# Participação: só "confirmado" ocupa vaga.
CONFIRMADO, PENDENTE, ESPERA, RECUSADO, CANCELADO = "confirmado", "pendente", "espera", "recusado", "cancelado"
ATIVAS = (CONFIRMADO, PENDENTE, ESPERA)

# Atividade
ABERTA, ENCERRADA, CANCELADA = "aberta", "encerrada", "cancelada"

# Reserva de quadra
R_ATIVIDADE, R_RESERVA, R_BLOQUEIO = "atividade", "reserva", "bloqueio"

# Campeonato / equipe
C_ABERTO, C_ANDAMENTO, C_ENCERRADO, C_CANCELADO = "aberto", "em_andamento", "encerrado", "cancelado"
E_PENDENTE, E_CONFIRMADA, E_RECUSADA, E_CANCELADA = "pendente", "confirmada", "recusada", "cancelada"
M_CONVIDADO, M_CONFIRMADO, M_RECUSADO = "convidado", "confirmado", "recusado"

arena_gestores = Table(
    "arena_gestores",
    Base.metadata,
    Column("arena_id", ForeignKey("arenas.id", ondelete="CASCADE"), primary_key=True),
    Column("usuario_id", ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------- pessoas e modalidades


class Modalidade(Base):
    """Cadastro de esportes (RF-012): um novo esporte é uma linha aqui, sem mudar a aplicação."""

    __tablename__ = "modalidades"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True)
    nome: Mapped[str] = mapped_column(String(60))
    icone: Mapped[str] = mapped_column(String(8), default="🏅")
    cor: Mapped[str] = mapped_column(String(10), default="roxo")  # roxo | verde | laranja
    usa_quadra: Mapped[bool] = mapped_column(Boolean, default=True)  # corrida, ciclismo etc. usam ponto de encontro
    vagas_padrao: Mapped[int] = mapped_column(Integer, default=12)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    ordem: Mapped[int] = mapped_column(Integer, default=100)


class Usuario(Base):
    """Atleta. Quem cria uma atividade é organizador; quem administra uma arena tem `gestor`."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), unique=True)  # sempre em minúsculas
    senha_hash: Mapped[str] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(String(40), unique=True)  # id da conta Google (login social); a senha vira aleatória
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Moderador geral: aprova a fila e oculta em qualquer mural, mas não gere usuários nem perfis
    moderador: Mapped[bool] = mapped_column(Boolean, default=False)
    gestor: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    # Nome de usuário público (@): aparece nas publicações e serve para convites. Sempre em minúsculas.
    usuario: Mapped[str | None] = mapped_column(String(30), unique=True)
    # Termos: versão aceita e quando (o aceite completo, com IP, fica em AceiteTermos)
    termos_versao: Mapped[str | None] = mapped_column(String(20))
    termos_em: Mapped[datetime | None] = mapped_column(DateTime)
    # Consentimento específico e revogável para guardar a localização do perfil (LGPD)
    consent_localizacao: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_localizacao_em: Mapped[datetime | None] = mapped_column(DateTime)
    anonimizado_em: Mapped[datetime | None] = mapped_column(DateTime)

    foto_url: Mapped[str | None] = mapped_column(Text)
    sexo: Mapped[str | None] = mapped_column(String(1))  # M | F, opcional (categorias)
    nascimento: Mapped[date | None] = mapped_column(Date)

    # Onde o atleta joga e até onde aceita ir
    cidade: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    raio_km: Mapped[int] = mapped_column(Integer, default=10)
    disponibilidade: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)  # vazio = qualquer

    # Notificações (RF-016): o que e até onde ele quer receber
    notif_vagas: Mapped[bool] = mapped_column(Boolean, default=True)
    notif_lembretes: Mapped[bool] = mapped_column(Boolean, default=True)
    notif_campeonatos: Mapped[bool] = mapped_column(Boolean, default=True)
    notif_grupos: Mapped[bool] = mapped_column(Boolean, default=True)
    notif_raio_km: Mapped[int] = mapped_column(Integer, default=5)

    esportes: Mapped[list["UsuarioModalidade"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="UsuarioModalidade.modalidade_id"
    )
    arenas: Mapped[list["Arena"]] = relationship(secondary=arena_gestores, back_populates="gestores")

    @property
    def iniciais(self) -> str:
        partes = [p for p in self.nome.split() if p]
        return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else "")).upper() if partes else "?"

    @property
    def equipe_moderacao(self) -> bool:
        """Administrador ou moderador geral: quem pode ver e decidir a fila de moderação."""
        return self.admin or self.moderador

    @property
    def perfil_acesso(self) -> str:
        return "admin" if self.admin else "moderador" if self.moderador else "usuario"

    @property
    def arroba(self) -> str:
        return f"@{self.usuario}" if self.usuario else self.nome

    @property
    def tem_localizacao(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def nivel_em(self, modalidade_id: int) -> str | None:
        return next((e.nivel for e in self.esportes if e.modalidade_id == modalidade_id), None)


class UsuarioModalidade(Base):
    """Esporte que o atleta pratica e em que nível."""

    __tablename__ = "usuario_modalidades"

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    modalidade_id: Mapped[int] = mapped_column(ForeignKey("modalidades.id", ondelete="CASCADE"), primary_key=True)
    nivel: Mapped[str] = mapped_column(String(15), default="intermediario")

    modalidade: Mapped[Modalidade] = relationship(lazy="joined")


# ---------------------------------------------------------------- arenas


class Arena(Base):
    __tablename__ = "arenas"
    __table_args__ = (Index("ix_arenas_geo", "latitude", "longitude"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    descricao: Mapped[str | None] = mapped_column(Text)
    endereco: Mapped[str] = mapped_column(String(300), default="")
    cidade: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    estrutura: Mapped[str | None] = mapped_column(Text)  # vestiário, bar, estacionamento…
    regras: Mapped[str | None] = mapped_column(Text)
    fotos: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    abre: Mapped[time] = mapped_column(Time, default=time(6, 0))
    fecha: Mapped[time] = mapped_column(Time, default=time(23, 0))
    tipo_estabelecimento: Mapped[str] = mapped_column(String(12), default="arena")  # arena | academia | clube | escola
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    recebimento_liberado: Mapped[bool] = mapped_column(Boolean, default=False)  # fase "avaliacao": só Arenas liberadas pelo admin criam subconta
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    gestores: Mapped[list[Usuario]] = relationship(secondary=arena_gestores, back_populates="arenas")
    quadras: Mapped[list["Quadra"]] = relationship(
        back_populates="arena", cascade="all, delete-orphan", order_by="Quadra.nome"
    )


class Local(Base):
    """Guia de locais: lugares públicos de prática, lojas de material esportivo e arenas ainda não assinantes.
    Só informação para o usuário (não reserva nem cobra). Mantido pela administração."""

    __tablename__ = "locais"
    __table_args__ = (Index("ix_locais_geo", "latitude", "longitude"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20))  # publico | arena | clube | loja
    nome: Mapped[str] = mapped_column(String(200))
    descricao: Mapped[str | None] = mapped_column(Text)
    endereco: Mapped[str] = mapped_column(String(300))
    cidade: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    horario_funcionamento: Mapped[str] = mapped_column(Text)
    telefone: Mapped[str] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    site: Mapped[str | None] = mapped_column(String(300))
    instagram: Mapped[str | None] = mapped_column(String(300))
    facebook: Mapped[str | None] = mapped_column(String(300))
    maps_url: Mapped[str | None] = mapped_column(Text)  # link do Google Maps do local
    categoria_maps: Mapped[str | None] = mapped_column(String(120))  # como o Maps classifica ("Quadra de vôlei de praia")
    avaliacao: Mapped[float | None] = mapped_column(Float)  # nota de 0 a 5 no Maps
    avaliacoes: Mapped[int | None] = mapped_column(Integer)
    regras: Mapped[str | None] = mapped_column(Text)
    modalidades: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Quadra(Base):
    __tablename__ = "quadras"

    id: Mapped[int] = mapped_column(primary_key=True)
    arena_id: Mapped[int] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"))
    nome: Mapped[str] = mapped_column(String(100))
    tipo: Mapped[str] = mapped_column(String(12), default="quadra")  # quadra | sala | piscina | campo | outro (na tela: "espaço")
    modalidades: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)  # códigos de Modalidade
    capacidade: Mapped[int | None] = mapped_column(Integer)
    valor_hora: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)

    arena: Mapped[Arena] = relationship(back_populates="quadras")


class Reserva(Base):
    """Horário ocupado de uma quadra. Horário sem reserva está livre."""

    __tablename__ = "reservas"
    __table_args__ = (Index("ix_reservas_quadra_inicio", "quadra_id", "inicio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quadra_id: Mapped[int] = mapped_column(ForeignKey("quadras.id", ondelete="CASCADE"))
    inicio: Mapped[datetime] = mapped_column(DateTime)
    fim: Mapped[datetime] = mapped_column(DateTime)
    tipo: Mapped[str] = mapped_column(String(12), default=R_RESERVA)  # atividade | reserva | bloqueio
    atividade_id: Mapped[int | None] = mapped_column(ForeignKey("atividades.id", ondelete="CASCADE"))
    rotulo: Mapped[str] = mapped_column(String(120), default="")
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    # Cobrança pela subconta da Arena (recebimentos.py). Reservas antigas e bloqueios ficam "sem_cobranca".
    valor: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    comissao: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # parte do PlayGo no split, calculada ao criar a cobrança
    status_pagamento: Mapped[str] = mapped_column(String(14), default="sem_cobranca")  # sem_cobranca | pendente | pago | cancelado | estornado
    asaas_payment_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    invoice_url: Mapped[str | None] = mapped_column(String(300))

    quadra: Mapped[Quadra] = relationship()


class HorarioDivulgado(Base):
    """Horário ocioso que o gestor transformou em oportunidade pública (RF-024)."""

    __tablename__ = "horarios_divulgados"
    __table_args__ = (UniqueConstraint("quadra_id", "inicio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quadra_id: Mapped[int] = mapped_column(ForeignKey("quadras.id", ondelete="CASCADE"))
    modalidade_id: Mapped[int | None] = mapped_column(ForeignKey("modalidades.id"))
    inicio: Mapped[datetime] = mapped_column(DateTime)
    fim: Mapped[datetime] = mapped_column(DateTime)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    quadra: Mapped[Quadra] = relationship(lazy="joined")
    modalidade: Mapped[Modalidade | None] = relationship(lazy="joined")


# ---------------------------------------------------------------- grupos


class Grupo(Base):
    __tablename__ = "grupos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(150))
    modalidade_id: Mapped[int] = mapped_column(ForeignKey("modalidades.id"))
    descricao: Mapped[str | None] = mapped_column(Text)
    cidade: Mapped[str | None] = mapped_column(String(100))
    local_habitual: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    foto_url: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    entrada: Mapped[str] = mapped_column(String(10), default="livre")  # livre | aprovacao (um admin do grupo aceita cada pedido)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    modalidade: Mapped[Modalidade] = relationship(lazy="joined")
    membros: Mapped[list["GrupoMembro"]] = relationship(cascade="all, delete-orphan", back_populates="grupo")


class GrupoMembro(Base):
    __tablename__ = "grupo_membros"

    grupo_id: Mapped[int] = mapped_column(ForeignKey("grupos.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    papel: Mapped[str] = mapped_column(String(10), default="membro")  # admin | membro
    status: Mapped[str] = mapped_column(String(10), default="ativo")  # ativo | pendente (aguarda aceite de um admin do grupo)
    entrou_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    grupo: Mapped[Grupo] = relationship(back_populates="membros")
    usuario: Mapped[Usuario] = relationship(lazy="joined")


class GrupoMensagem(Base):
    """Chat do grupo. `aviso` marca o comunicado de um administrador, fixado no topo."""

    __tablename__ = "grupo_mensagens"
    __table_args__ = (Index("ix_grupo_mensagens_grupo", "grupo_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    grupo_id: Mapped[int] = mapped_column(ForeignKey("grupos.id", ondelete="CASCADE"))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    texto: Mapped[str] = mapped_column(Text)
    aviso: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    usuario: Mapped[Usuario] = relationship(lazy="joined")


# ---------------------------------------------------------------- atividades


class Atividade(Base):
    """Jogo, treino, corrida, pedal… qualquer encontro esportivo com vagas."""

    __tablename__ = "atividades"
    __table_args__ = (
        Index("ix_atividades_inicio", "inicio"),
        Index("ix_atividades_geo", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organizador_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    modalidade_id: Mapped[int] = mapped_column(ForeignKey("modalidades.id"))
    grupo_id: Mapped[int | None] = mapped_column(ForeignKey("grupos.id", ondelete="SET NULL"))
    arena_id: Mapped[int | None] = mapped_column(ForeignKey("arenas.id", ondelete="SET NULL"))
    quadra_id: Mapped[int | None] = mapped_column(ForeignKey("quadras.id", ondelete="SET NULL"))
    capa: Mapped[str | None] = mapped_column(String(60))  # arte/logo da atividade (nome do arquivo em capas/)
    book_publicado: Mapped[bool] = mapped_column(Boolean, default=False)

    nome: Mapped[str] = mapped_column(String(150))
    descricao: Mapped[str | None] = mapped_column(Text)
    regras: Mapped[str | None] = mapped_column(Text)

    # Onde: arena/quadra, ou só um ponto de encontro (RF-019)
    local_nome: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    percurso: Mapped[str | None] = mapped_column(Text)

    inicio: Mapped[datetime] = mapped_column(DateTime)
    duracao_min: Mapped[int] = mapped_column(Integer, default=90)

    max_participantes: Mapped[int] = mapped_column(Integer)
    confirmados: Mapped[int] = mapped_column(Integer, default=0)  # mantido sob trava, junto de Participacao
    nivel: Mapped[str] = mapped_column(String(15), default="todos")
    categoria: Mapped[str] = mapped_column(String(10), default="misto")
    idade_min: Mapped[int | None] = mapped_column(Integer)
    idade_max: Mapped[int | None] = mapped_column(Integer)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    exige_aprovacao: Mapped[bool] = mapped_column(Boolean, default=False)
    # Quem pode entrar: publica (qualquer um, aparece na busca) | autorizados (aparece, mas o organizador
    # aprova cada pessoa) | link (não aparece em lugar nenhum; só entra quem tem o link ou convite)
    visibilidade: Mapped[str] = mapped_column(String(12), default="publica")
    convite_token: Mapped[str | None] = mapped_column(String(40), unique=True)

    falta_gente: Mapped[bool] = mapped_column(Boolean, default=False)  # RF-014
    falta_gente_em: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default=ABERTA)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    modalidade: Mapped[Modalidade] = relationship(lazy="joined")
    organizador: Mapped[Usuario] = relationship(lazy="joined")
    arena: Mapped[Arena | None] = relationship(lazy="joined")
    grupo: Mapped[Grupo | None] = relationship(lazy="select")
    participacoes: Mapped[list["Participacao"]] = relationship(
        back_populates="atividade", cascade="all, delete-orphan", order_by="Participacao.id"
    )

    @property
    def vagas(self) -> int:
        return max(0, self.max_participantes - self.confirmados)

    @property
    def fim(self) -> datetime:
        from datetime import timedelta

        return self.inicio + timedelta(minutes=self.duracao_min)


class Participacao(Base):
    __tablename__ = "participacoes"
    __table_args__ = (
        UniqueConstraint("atividade_id", "usuario_id"),
        Index("ix_participacoes_usuario", "usuario_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    atividade_id: Mapped[int] = mapped_column(ForeignKey("atividades.id", ondelete="CASCADE"))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(10))  # confirmado | pendente | espera | recusado | cancelado
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)  # a ordem da fila de espera
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora, onupdate=agora)

    atividade: Mapped[Atividade] = relationship(back_populates="participacoes")
    usuario: Mapped[Usuario] = relationship(lazy="joined")


# ---------------------------------------------------------------- campeonatos


class Campeonato(Base):
    __tablename__ = "campeonatos"
    __table_args__ = (Index("ix_campeonatos_geo", "latitude", "longitude"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organizador_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    arena_id: Mapped[int | None] = mapped_column(ForeignKey("arenas.id", ondelete="SET NULL"))
    modalidade_id: Mapped[int] = mapped_column(ForeignKey("modalidades.id"))
    capa: Mapped[str | None] = mapped_column(String(60))  # arte/logo do campeonato (nome do arquivo em capas/)
    book_publicado: Mapped[bool] = mapped_column(Boolean, default=False)  # o book fica visível para todos os usuários
    telao_token: Mapped[str | None] = mapped_column(String(40), unique=True)  # link secreto do telão (placar em TV/tablet, sem login)
    telao_jogo_id: Mapped[int | None] = mapped_column(Integer)  # jogo fixado no telão; vazio = automático (o que estiver ao vivo)
    telao_classificacao: Mapped[bool] = mapped_column(Boolean, default=False)  # o telão alterna o placar com a classificação geral
    transmissao_permitida: Mapped[bool] = mapped_column(Boolean, default=False)  # a organização liberou filmar/transmitir no YouTube do PlayGo
    transmissao_aceite_por_id: Mapped[int | None] = mapped_column(Integer)  # quem declarou a ciência dos participantes
    transmissao_aceite_em: Mapped[datetime | None] = mapped_column(DateTime)

    nome: Mapped[str] = mapped_column(String(150))
    categoria: Mapped[str | None] = mapped_column(String(120))  # "B, C e iniciante", "Masculino"…
    descricao: Mapped[str | None] = mapped_column(Text)
    regulamento: Mapped[str | None] = mapped_column(Text)
    premiacao: Mapped[str | None] = mapped_column(String(200))
    premiacao_valor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    local_nome: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)

    data_inicio: Mapped[date] = mapped_column(Date)
    data_fim: Mapped[date | None] = mapped_column(Date)
    inscricao_ate: Mapped[date] = mapped_column(Date)
    max_equipes: Mapped[int] = mapped_column(Integer)
    atletas_por_equipe: Mapped[int] = mapped_column(Integer, default=5)  # 2 = duplas, 4 = quartetos
    valor_inscricao: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # por equipe

    status: Mapped[str] = mapped_column(String(14), default=C_ABERTO)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    # Chaveamento: formato escolhido no sorteio (eliminatoria | pontos_corridos), quando foi e a semente (reproduz o sorteio)
    formato: Mapped[str | None] = mapped_column(String(20))
    sorteado_em: Mapped[datetime | None] = mapped_column(DateTime)
    sorteio_semente: Mapped[int | None] = mapped_column(BigInteger)
    grupos_qtd: Mapped[int | None] = mapped_column(Integer)  # formato "grupos": quantos grupos
    classificam: Mapped[int | None] = mapped_column(Integer)  # formato "grupos": quantas equipes de cada grupo vão ao mata-mata
    classificam_geral: Mapped[int | None] = mapped_column(Integer)  # formato "grupos": as N melhores no geral (tabela única) avançam, cruzando 1ºxN, 2ºx(N-1)…
    classificam_melhores: Mapped[int | None] = mapped_column(Integer)  # formato "grupos": além dos `classificam` de cada grupo, os N melhores do colocado seguinte (ex.: 2 melhores terceiros)
    chave_manual: Mapped[list | None] = mapped_column(JSONB)  # mata-mata definido pela organização: [{"numero": 13, "a": {"t": "geral", "n": 1}, "b": {...}}]
    duracao_jogo_min: Mapped[int | None] = mapped_column(Integer)  # última duração usada na agenda em lote (preenche o formulário)
    # A organização libera o cadastro do elenco (nome e RG dos componentes e o técnico, com o capitão marcado) por equipe
    cadastro_elenco: Mapped[bool] = mapped_column(Boolean, default=False)
    # Regulamento em PDF (definido em reuniões): arquivo no armazenamento, nome original e quando foi enviado
    regulamento_arquivo: Mapped[str | None] = mapped_column(String(120))
    regulamento_nome: Mapped[str | None] = mapped_column(String(150))
    regulamento_em: Mapped[datetime | None] = mapped_column(DateTime)
    # Regras de pontuação: "simples" (gols/pontos corridos) ou "sets" (vôlei, tênis…). Nulo = simples.
    placar_modo: Mapped[str | None] = mapped_column(String(8))
    sets_melhor_de: Mapped[int | None] = mapped_column(Integer)  # 3 = melhor de 3 (vence com 2 sets)
    pontos_set: Mapped[int | None] = mapped_column(Integer)  # pontos para ganhar um set
    pontos_tiebreak: Mapped[int | None] = mapped_column(Integer)  # pontos do set decisivo (tiebreak)
    diferenca_set: Mapped[int | None] = mapped_column(Integer)  # vantagem mínima para fechar o set

    modalidade: Mapped[Modalidade] = relationship(lazy="joined")
    organizador: Mapped[Usuario] = relationship(lazy="joined")
    arena: Mapped[Arena | None] = relationship(lazy="joined")
    equipes: Mapped[list["Equipe"]] = relationship(
        back_populates="campeonato", cascade="all, delete-orphan", order_by="Equipe.id"
    )


class Equipe(Base):
    __tablename__ = "equipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    campeonato_id: Mapped[int] = mapped_column(ForeignKey("campeonatos.id", ondelete="CASCADE"))
    capitao_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    nome: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10), default=E_PENDENTE)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    campeonato: Mapped[Campeonato] = relationship(back_populates="equipes")
    capitao: Mapped[Usuario] = relationship(lazy="joined")
    membros: Mapped[list["EquipeMembro"]] = relationship(
        back_populates="equipe", cascade="all, delete-orphan", order_by="EquipeMembro.usuario_id"
    )


class EquipeMembro(Base):
    __tablename__ = "equipe_membros"

    equipe_id: Mapped[int] = mapped_column(ForeignKey("equipes.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default=M_CONVIDADO)

    equipe: Mapped[Equipe] = relationship(back_populates="membros")
    usuario: Mapped[Usuario] = relationship(lazy="joined")


J_AGENDADO, J_AO_VIVO, J_ENCERRADO = "agendado", "ao_vivo", "encerrado"


class Jogo(Base):
    """Um confronto do campeonato. Na eliminatória `proximo_id`/`proximo_lado` dizem para onde vai o vencedor."""

    __tablename__ = "jogos"
    __table_args__ = (Index("ix_jogos_campeonato", "campeonato_id", "rodada", "posicao"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campeonato_id: Mapped[int] = mapped_column(ForeignKey("campeonatos.id", ondelete="CASCADE"))
    rodada: Mapped[int] = mapped_column(Integer)  # 1 = primeira fase
    posicao: Mapped[int] = mapped_column(Integer)  # ordem dentro da rodada
    rodada_nome: Mapped[str] = mapped_column(String(40))  # "Quartas de final", "Final", "Rodada 2", "Grupo A · Rodada 1"
    fase: Mapped[str | None] = mapped_column(String(10))  # só no formato "grupos": "grupos" | "mata_mata"
    grupo: Mapped[str | None] = mapped_column(String(2))  # letra do grupo (jogos da fase de grupos)
    equipe_a_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    equipe_b_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    placar_a: Mapped[int] = mapped_column(Integer, default=0)
    placar_b: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default=J_AGENDADO)
    vencedor_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    desempate: Mapped[bool] = mapped_column(Boolean, default=False)  # venceu no desempate (pênaltis, ponto de ouro…) com placar igual
    folga: Mapped[bool] = mapped_column(Boolean, default=False)  # classificação direta (sem adversário)
    proximo_id: Mapped[int | None] = mapped_column(ForeignKey("jogos.id", ondelete="SET NULL"))
    proximo_lado: Mapped[str | None] = mapped_column(String(1))  # a | b
    perdedor_proximo_id: Mapped[int | None] = mapped_column(ForeignKey("jogos.id", ondelete="SET NULL"))  # chave manual: o perdedor vai jogar este (disputa de 3º lugar)
    perdedor_proximo_lado: Mapped[str | None] = mapped_column(String(1))
    numero: Mapped[int | None] = mapped_column(Integer)  # número do jogo no regulamento (chave manual: "jogo 13")
    origem_a: Mapped[str | None] = mapped_column(String(60))  # de onde vem o lado enquanto vazio: "1º colocado geral", "Vencedor do jogo 13"
    origem_b: Mapped[str | None] = mapped_column(String(60))
    inicio_previsto: Mapped[datetime | None] = mapped_column(DateTime)
    local: Mapped[str | None] = mapped_column(String(120))  # quadra/campo
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime)
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime)

    equipe_a: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_a_id], lazy="joined")
    equipe_b: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_b_id], lazy="joined")
    # Super 8: cada lado é uma dupla (equipe_a + equipe_a2 contra equipe_b + equipe_b2); nos demais formatos ficam vazios
    yt_broadcast_id: Mapped[str | None] = mapped_column(String(20))  # transmissão no YouTube (é também o ID do vídeo para incorporar)
    yt_stream_id: Mapped[str | None] = mapped_column(String(60))  # chave de envio deste jogo (a chave em si nunca fica no banco)
    yt_estado: Mapped[str | None] = mapped_column(String(12))  # preparada | ao_vivo | encerrada
    lk_egress_id: Mapped[str | None] = mapped_column(String(60))  # saída LiveKit→YouTube em andamento (câmera pelo navegador)
    ponto_a: Mapped[int] = mapped_column(Integer, default=0)  # pontos do game em andamento (15/30/40…); zeram quando o game termina
    ponto_b: Mapped[int] = mapped_column(Integer, default=0)
    equipe_a2_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    equipe_b2_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    equipe_a2: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_a2_id], lazy="joined")
    equipe_b2: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_b2_id], lazy="joined")
    eventos: Mapped[list["JogoEvento"]] = relationship(cascade="all, delete-orphan", order_by="JogoEvento.id")
    sets: Mapped[list["JogoSet"]] = relationship(cascade="all, delete-orphan", order_by="JogoSet.numero")
    games: Mapped[list["JogoGame"]] = relationship(cascade="all, delete-orphan", order_by="JogoGame.numero", lazy="selectin")


class JogoSet(Base):
    """Placar de cada set (campeonatos com `placar_modo = "sets"`). `Jogo.placar_a/b` guarda quantos sets cada lado ganhou."""

    __tablename__ = "jogo_sets"
    __table_args__ = (UniqueConstraint("jogo_id", "numero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id", ondelete="CASCADE"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    pontos_a: Mapped[int] = mapped_column(Integer, default=0)
    pontos_b: Mapped[int] = mapped_column(Integer, default=0)
    encerrado: Mapped[bool] = mapped_column(Boolean, default=False)


class JogoGame(Base):
    """Cada game terminado de um jogo contado em games: quem ganhou e como (pontos do game; tie-break). `manual` = lançado como game
    inteiro, sem os pontos."""

    __tablename__ = "jogo_games"

    id: Mapped[int] = mapped_column(primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id", ondelete="CASCADE"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    set_numero: Mapped[int | None] = mapped_column(Integer)
    lado: Mapped[str] = mapped_column(String(1))  # a | b: quem ganhou o game
    pontos_a: Mapped[int | None] = mapped_column(Integer)
    pontos_b: Mapped[int | None] = mapped_column(Integer)
    tiebreak: Mapped[bool] = mapped_column(Boolean, default=False)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)


class JogoEvento(Base):
    """Linha do tempo do jogo ao vivo: início, mudanças de placar, lances escritos e fim."""

    __tablename__ = "jogo_eventos"

    id: Mapped[int] = mapped_column(primary_key=True)
    jogo_id: Mapped[int] = mapped_column(ForeignKey("jogos.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(10))  # inicio | placar | lance | fim | reaberto
    texto: Mapped[str | None] = mapped_column(String(200))
    equipe_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    placar_a: Mapped[int] = mapped_column(Integer, default=0)
    placar_b: Mapped[int] = mapped_column(Integer, default=0)
    autor_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class ChaveEquipe(Base):
    """Posição de uma equipe no sorteio: o grupo em que caiu e/ou o número de cabeça de chave (1 = o mais forte)."""

    __tablename__ = "chave_equipes"

    campeonato_id: Mapped[int] = mapped_column(ForeignKey("campeonatos.id", ondelete="CASCADE"), index=True)
    equipe_id: Mapped[int] = mapped_column(ForeignKey("equipes.id", ondelete="CASCADE"), primary_key=True)
    grupo: Mapped[str | None] = mapped_column(String(2))
    cabeca: Mapped[int | None] = mapped_column(Integer)


class EquipeComponente(Base):
    """Componente cadastrado pelo capitão (ou pela organização): nome e RG, sem precisar de conta no PlayGo. `funcao` = atleta | tecnico;
    `capitao` marca o capitão da equipe em quadra (no máximo um atleta). O RG só é visto por quem organiza e pelo capitão da equipe."""

    __tablename__ = "equipe_componentes"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipe_id: Mapped[int] = mapped_column(ForeignKey("equipes.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    rg: Mapped[str] = mapped_column(String(20))
    funcao: Mapped[str] = mapped_column(String(8), default="atleta")
    capitao: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class CampeonatoMesario(Base):
    """Pessoa autorizada pela organização a conduzir jogos (iniciar, marcar placar, encerrar)."""

    __tablename__ = "campeonato_mesarios"

    campeonato_id: Mapped[int] = mapped_column(ForeignKey("campeonatos.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    usuario: Mapped[Usuario] = relationship(lazy="joined")


# ---------------------------------------------------------------- notificações


class Notificacao(Base):
    """Caixa de entrada do atleta. `chave` evita avisar duas vezes a mesma coisa (UNIQUE por usuário)."""

    __tablename__ = "notificacoes"
    __table_args__ = (
        UniqueConstraint("usuario_id", "chave"),
        Index("ix_notificacoes_usuario", "usuario_id", "lida", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(20))  # vaga | lembrete | reposicao | campeonato | grupo | arena | equipe
    titulo: Mapped[str] = mapped_column(String(200))
    corpo: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(200))
    atividade_id: Mapped[int | None] = mapped_column(ForeignKey("atividades.id", ondelete="CASCADE"))
    chave: Mapped[str | None] = mapped_column(String(80))
    lida: Mapped[bool] = mapped_column(Boolean, default=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


# ---------------------------------------------------------------- comunidades, convites, moderação

VISIBILIDADES = (("publica", "Pública"), ("autorizados", "Só autorizados"), ("link", "Só por link"))
ESCOPOS = ("geral", "atividade", "campeonato", "comunidade")

P_EM_ANALISE, P_PUBLICADA, P_OCULTA, P_REJEITADA, P_EXCLUIDA = "em_analise", "publicada", "oculta", "rejeitada", "excluida"


class Comunidade(Base):
    """Comunidade de relacionamento dentro do app: gente com um interesse em comum, com mural próprio."""

    __tablename__ = "comunidades"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str | None] = mapped_column(Text)
    regras: Mapped[str | None] = mapped_column(Text)
    cidade: Mapped[str | None] = mapped_column(String(100))
    visibilidade: Mapped[str] = mapped_column(String(12), default="publica")  # publica | autorizados | link
    convite_token: Mapped[str | None] = mapped_column(String(40), unique=True)
    perfil_profissional_id: Mapped[int | None] = mapped_column(ForeignKey("perfis_profissionais.id", ondelete="SET NULL"))  # dono: profissional (escolinha: fase 3)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    membros: Mapped[list["ComunidadeMembro"]] = relationship(cascade="all, delete-orphan", back_populates="comunidade")


class ComunidadeMembro(Base):
    __tablename__ = "comunidade_membros"

    comunidade_id: Mapped[int] = mapped_column(ForeignKey("comunidades.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    papel: Mapped[str] = mapped_column(String(12), default="membro")  # dono | moderador | membro
    status: Mapped[str] = mapped_column(String(10), default="ativo")  # ativo | pendente | banido
    entrou_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    comunidade: Mapped[Comunidade] = relationship(back_populates="membros")
    usuario: Mapped[Usuario] = relationship(lazy="joined")


class Moderador(Base):
    """Quem o organizador (ou o gestor) delegou para cuidar do mural de uma atividade ou campeonato."""

    __tablename__ = "moderadores"

    escopo: Mapped[str] = mapped_column(String(12), primary_key=True)  # atividade | campeonato
    escopo_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    concedido_por: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    usuario: Mapped[Usuario] = relationship(lazy="joined")


class Convite(Base):
    """Convite pessoal pelo @usuario para uma atividade ou comunidade."""

    __tablename__ = "convites"
    __table_args__ = (UniqueConstraint("escopo", "escopo_id", "convidado_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    escopo: Mapped[str] = mapped_column(String(12))  # atividade | comunidade
    escopo_id: Mapped[int] = mapped_column(Integer)
    convidado_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    convidado_por: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    status: Mapped[str] = mapped_column(String(10), default="pendente")  # pendente | aceito | recusado
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    convidado: Mapped[Usuario] = relationship(foreign_keys=[convidado_id], lazy="joined")
    autor: Mapped[Usuario] = relationship(foreign_keys=[convidado_por], lazy="joined")


# ---------------------------------------------------------------- publicações


class Publicacao(Base):
    """Postagem do mural. Sempre carrega o local (o local fica sempre marcado).
    Uma só linha vale para o mural do escopo e, se replicada, para o feed geral — por isso ocultar
    vale em todos os lugares."""

    __tablename__ = "publicacoes"
    __table_args__ = (
        Index("ix_publicacoes_escopo", "escopo", "escopo_id", "status", "id"),
        Index("ix_publicacoes_geral", "replicar_geral", "status", "id"),
        Index("ix_publicacoes_geo", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    escopo: Mapped[str] = mapped_column(String(12))  # geral | atividade | campeonato | comunidade
    escopo_id: Mapped[int | None] = mapped_column(Integer)
    texto: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    local_nome: Mapped[str] = mapped_column(String(200))
    replicar_geral: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(12), default=P_EM_ANALISE)
    motivo_analise: Mapped[str | None] = mapped_column(Text)  # o que a IA/regra apontou, para o admin
    analisado_em: Mapped[datetime | None] = mapped_column(DateTime)
    oculta_por_id: Mapped[int | None] = mapped_column(Integer)
    oculta_por_papel: Mapped[str | None] = mapped_column(String(10))  # moderador | admin
    oculta_motivo: Mapped[str | None] = mapped_column(Text)
    oculta_em: Mapped[datetime | None] = mapped_column(DateTime)

    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    editado_em: Mapped[datetime | None] = mapped_column(DateTime)
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime)

    # Compartilhamento no Instagram do PlayGo: aguardando | aprovada | publicada | recusada | erro (nulo = não pedido)
    instagram_status: Mapped[str | None] = mapped_column(String(10))
    instagram_consentimento_em: Mapped[datetime | None] = mapped_column(DateTime)
    instagram_media_id: Mapped[str | None] = mapped_column(String(40))
    instagram_permalink: Mapped[str | None] = mapped_column(String(300))
    instagram_erro: Mapped[str | None] = mapped_column(String(200))
    instagram_em: Mapped[datetime | None] = mapped_column(DateTime)

    autor: Mapped[Usuario] = relationship(lazy="joined")
    midias: Mapped[list["Midia"]] = relationship(cascade="all, delete-orphan", order_by="Midia.id", lazy="selectin")


class ConfigExterna(Base):
    """Credenciais de integrações guardadas pelo próprio sistema (ex.: token do Instagram, que se renova sozinho)."""

    __tablename__ = "config_externa"

    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    valor: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class PublicacaoCurtida(Base):
    """❤ numa publicação (uma por pessoa)."""

    __tablename__ = "publicacao_curtidas"

    publicacao_id: Mapped[int] = mapped_column(ForeignKey("publicacoes.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class PublicacaoVersao(Base):
    """Texto anterior de cada edição: o app mostra 'editado em…', e a guarda protege o autor e a plataforma."""

    __tablename__ = "publicacao_versoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    publicacao_id: Mapped[int] = mapped_column(ForeignKey("publicacoes.id", ondelete="CASCADE"))
    texto: Mapped[str] = mapped_column(Text)
    substituido_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Midia(Base):
    __tablename__ = "midias"

    id: Mapped[int] = mapped_column(primary_key=True)
    publicacao_id: Mapped[int] = mapped_column(ForeignKey("publicacoes.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(6))  # foto | video
    arquivo: Mapped[str] = mapped_column(String(300))  # relativo a settings.pasta_uploads
    miniatura: Mapped[str | None] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(40))
    tamanho: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    analise: Mapped[str] = mapped_column(String(10), default="pendente")  # pendente | ok | suspeita | sem_ia
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Comentario(Base):
    __tablename__ = "comentarios"
    __table_args__ = (Index("ix_comentarios_pub", "publicacao_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    publicacao_id: Mapped[int] = mapped_column(ForeignKey("publicacoes.id", ondelete="CASCADE"))
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    texto: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default=P_EM_ANALISE)  # mesmos estados da publicação
    motivo_analise: Mapped[str | None] = mapped_column(Text)
    oculta_por_id: Mapped[int | None] = mapped_column(Integer)
    oculta_por_papel: Mapped[str | None] = mapped_column(String(10))
    oculta_motivo: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    editado_em: Mapped[datetime | None] = mapped_column(DateTime)
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime)

    autor: Mapped[Usuario] = relationship(lazy="joined")


class ComentarioVersao(Base):
    __tablename__ = "comentario_versoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    comentario_id: Mapped[int] = mapped_column(ForeignKey("comentarios.id", ondelete="CASCADE"))
    texto: Mapped[str] = mapped_column(Text)
    substituido_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Denuncia(Base):
    __tablename__ = "denuncias"
    __table_args__ = (UniqueConstraint("alvo_tipo", "alvo_id", "denunciante_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alvo_tipo: Mapped[str] = mapped_column(String(12))  # publicacao | comentario
    alvo_id: Mapped[int] = mapped_column(Integer)
    denunciante_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    motivo: Mapped[str] = mapped_column(String(30))
    detalhe: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="aberta")  # aberta | procedente | improcedente
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    resolvida_em: Mapped[datetime | None] = mapped_column(DateTime)
    resolvida_por: Mapped[int | None] = mapped_column(Integer)


# ---------------------------------------------------------------- LGPD e guarda legal


class AceiteTermos(Base):
    __tablename__ = "aceites_termos"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer, index=True)  # sem FK: o aceite sobrevive à anonimização
    versao: Mapped[str] = mapped_column(String(20))
    aceito_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class Registro(Base):
    """Trilha de interações guardada para resguardo legal. Só se acrescenta: um gatilho do banco
    recusa UPDATE e DELETE (a purga por prazo é a única exceção, em `privacidade.purgar_registros`)."""

    __tablename__ = "registros"
    __table_args__ = (Index("ix_registros_usuario", "usuario_id", "id"), Index("ix_registros_objeto", "objeto_tipo", "objeto_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    usuario_id: Mapped[int | None] = mapped_column(Integer)  # sem FK, de propósito
    acao: Mapped[str] = mapped_column(String(40))
    objeto_tipo: Mapped[str | None] = mapped_column(String(20))
    objeto_id: Mapped[int | None] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(64))
    porta: Mapped[int | None] = mapped_column(Integer)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    detalhes: Mapped[dict | None] = mapped_column(JSONB)


# ---------------------------------------------------------------- planos e mensalidades


class Plano(Base):
    """Preço e limites de cada plano. Linhas ausentes usam `planos.DEFAULTS`."""

    __tablename__ = "planos"

    codigo: Mapped[str] = mapped_column(String(12), primary_key=True)  # gratuito (Usuário) | pro | organizador | arena
    nome: Mapped[str] = mapped_column(String(60))
    valor_mensal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    valor_anual: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # plano anual com desconto; vazio = não oferece anual
    valor_evento: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)  # preço de 1 evento (campeonato) avulso; só o Organizador vende
    max_participantes: Mapped[int | None] = mapped_column(Integer)  # None = sem limite
    max_atividades_abertas: Mapped[int | None] = mapped_column(Integer)
    pode_atividade: Mapped[bool] = mapped_column(Boolean, default=True)  # organizar atividades
    pode_campeonato: Mapped[bool] = mapped_column(Boolean, default=False)
    pode_arena: Mapped[bool] = mapped_column(Boolean, default=False)
    familia: Mapped[str] = mapped_column(String(12), default="geral")  # geral | profissional
    max_ofertas: Mapped[int | None] = mapped_column(Integer)  # profissional: None = sem limite
    max_turmas: Mapped[int | None] = mapped_column(Integer)
    max_professores: Mapped[int | None] = mapped_column(Integer)
    comissao_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)  # fatia do PlayGo nas vendas do profissional (fase financeira)
    pode_cobrar: Mapped[bool] = mapped_column(Boolean, default=False)
    pode_escolinha: Mapped[bool] = mapped_column(Boolean, default=False)


class Assinatura(Base):
    """Plano pago de uma pessoa (no máximo uma). `teste` = 30 dias grátis; `ativa` = paga ou concedida; `cancelada` = não renova."""

    __tablename__ = "assinaturas"
    __table_args__ = (UniqueConstraint("usuario_id", "familia", name="uq_assinaturas_usuario_familia"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    familia: Mapped[str] = mapped_column(String(12), default="geral")  # geral (Usuário/Pro/Organizador/Arena) | profissional
    plano: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10), default="teste")  # teste | ativa | cancelada
    origem: Mapped[str] = mapped_column(String(10), default="teste")  # teste | asaas | manual
    ciclo: Mapped[str] = mapped_column(String(6), default="mensal")  # mensal | anual (como a assinatura no Asaas se renova)
    teste_ate: Mapped[date | None] = mapped_column(Date)
    vigente_ate: Mapped[date | None] = mapped_column(Date)
    vigente_desde: Mapped[date | None] = mapped_column(Date)  # concessão do administrador: o acesso só vale a partir desta data
    testes_usados: Mapped[list[str]] = mapped_column(ARRAY(String(12)), default=list)
    asaas_customer_id: Mapped[str | None] = mapped_column(String(40))  # o CPF/CNPJ NÃO é guardado aqui
    asaas_subscription_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    cancelada_em: Mapped[datetime | None] = mapped_column(DateTime)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    pagamentos: Mapped[list["Pagamento"]] = relationship(cascade="all, delete-orphan", order_by="Pagamento.id")


class CreditoEvento(Base):
    """Eventos (campeonatos) comprados avulsos: a pessoa paga só pelos que vai realizar. Cada campeonato criado gasta 1 crédito; vencem em 12 meses."""

    __tablename__ = "creditos_evento"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), index=True)
    quantidade: Mapped[int] = mapped_column(Integer)
    usados: Mapped[int] = mapped_column(Integer, default=0)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # total cobrado
    status: Mapped[str] = mapped_column(String(10), default="pendente")  # pendente | pago | cancelado
    valida_ate: Mapped[date | None] = mapped_column(Date)  # definida quando o pagamento é confirmado
    asaas_customer_id: Mapped[str | None] = mapped_column(String(40))
    asaas_payment_id: Mapped[str | None] = mapped_column(String(40))
    invoice_url: Mapped[str | None] = mapped_column(String(300))
    pago_em: Mapped[date | None] = mapped_column(Date)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Recebedor(Base):
    """Subconta Asaas (BaaS) de um parceiro, criada e controlada pelo PlayGo. A Arena nunca acessa o painel do Asaas.
    A apiKey da subconta e a chave Pix de saque ficam **criptografadas** (`recebimentos.cifrar`); o CPF/CNPJ não é guardado, só os 4 últimos dígitos."""

    __tablename__ = "recebedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    arena_id: Mapped[int] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), unique=True)
    asaas_account_id: Mapped[str] = mapped_column(String(60), unique=True)
    wallet_id: Mapped[str] = mapped_column(String(60))
    api_key_cripto: Mapped[str] = mapped_column(Text)
    api_key_id: Mapped[str | None] = mapped_column(String(60))  # id da chave no Asaas (para rotação)
    modelo: Mapped[str] = mapped_column(String(8), default="baas")
    ambiente: Mapped[str] = mapped_column(String(8), default="sandbox")  # sandbox | producao (só a produção conta na avaliação)
    status_kyc: Mapped[str] = mapped_column(String(22), default="criando")  # criando | documentos_pendentes | em_analise | aprovado | recusado
    documentos_pendentes: Mapped[list | None] = mapped_column(JSONB)  # [{id, tipo, titulo, onboarding_url}]
    motivo_recusa: Mapped[str | None] = mapped_column(String(300))
    documento_final: Mapped[str | None] = mapped_column(String(4))  # 4 últimos dígitos do CPF/CNPJ, só para exibir
    email_mascara: Mapped[str | None] = mapped_column(String(120))  # e-mail do cadastro no Asaas, mascarado (j•••@dominio): só para a Arena saber onde olhar
    consultar_apos: Mapped[datetime | None] = mapped_column(DateTime)  # o Asaas pede >= 15 s antes de consultar as pendências
    destino_tipo: Mapped[str | None] = mapped_column(String(8))  # CPF | CNPJ | EMAIL | PHONE | EVP
    destino_cripto: Mapped[str | None] = mapped_column(Text)
    destino_mascara: Mapped[str | None] = mapped_column(String(80))
    destino_confirmado: Mapped[bool] = mapped_column(Boolean, default=False)  # a Arena declarou que a chave é de mesma titularidade da subconta
    dados_atualizados_em: Mapped[datetime | None] = mapped_column(DateTime)  # atualização anual dos dados cadastrais (exigida pelo Asaas)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    arena: Mapped[Arena] = relationship(lazy="joined")


class LancamentoRecebedor(Base):
    """Livro-razão da subconta (só inserção, com gatilho no Postgres). `valor_liquido` tem sinal: crédito > 0, débito < 0; o saldo interno é a soma.
    `chave` torna cada fato único (um evento do Asaas reenviado não duplica)."""

    __tablename__ = "lancamentos_recebedor"
    __table_args__ = (UniqueConstraint("recebedor_id", "chave"), Index("ix_lancamentos_recebedor", "recebedor_id", "id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    recebedor_id: Mapped[int] = mapped_column(ForeignKey("recebedores.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(14))  # recebimento | taxa_asaas | comissao | estorno | saque | saque_falho
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    valor_liquido: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    referencia: Mapped[str | None] = mapped_column(String(80))  # asaas_payment_id / transfer_id / reserva:{id}
    chave: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class SaqueRecebedor(Base):
    """Pedido de saque (Pix da subconta para o destino cadastrado). A validação de saque do Asaas confere esta linha antes de liberar a transferência."""

    __tablename__ = "saques_recebedor"

    id: Mapped[int] = mapped_column(primary_key=True)
    recebedor_id: Mapped[int] = mapped_column(ForeignKey("recebedores.id", ondelete="CASCADE"), index=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(12), default="solicitado")  # solicitado | enviado | concluido | falhou
    asaas_transfer_id: Mapped[str | None] = mapped_column(String(60), unique=True)
    destino_mascara: Mapped[str | None] = mapped_column(String(80))
    chave_idempotencia: Mapped[str] = mapped_column(String(60), unique=True)
    motivo: Mapped[str | None] = mapped_column(String(200))
    destino_selo: Mapped[str | None] = mapped_column(String(64))  # HMAC da chave Pix no momento do pedido (a chave em si não é repetida aqui)
    validacao: Mapped[str | None] = mapped_column(String(10))  # aprovado | recusado: resposta já dada à validação de saque do Asaas
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime)


class EventoRecebimento(Base):
    """Eventos do Asaas já processados (idempotência dos webhooks de parceiros) e o registro de cada validação de saque (sem dados sensíveis)."""

    __tablename__ = "eventos_recebimento"

    chave: Mapped[str] = mapped_column(String(120), primary_key=True)
    tipo: Mapped[str | None] = mapped_column(String(20))  # validacao_saque
    resultado: Mapped[str | None] = mapped_column(String(10))  # aprovado | recusado
    motivo: Mapped[str | None] = mapped_column(String(200))
    saque_id: Mapped[int | None] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Pagamento(Base):
    __tablename__ = "pagamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    assinatura_id: Mapped[int] = mapped_column(ForeignKey("assinaturas.id", ondelete="CASCADE"))
    asaas_payment_id: Mapped[str] = mapped_column(String(40), unique=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(30))  # status do Asaas: PENDING, CONFIRMED, RECEIVED, OVERDUE…
    vencimento: Mapped[date] = mapped_column(Date)
    pago_em: Mapped[date | None] = mapped_column(Date)
    invoice_url: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


# ---------------------------------------------------------------- ensino: profissionais, ofertas e agenda (docs/especificacao-personal-escolinhas.md)

PP_RASCUNHO, PP_EM_ANALISE, PP_APROVADO, PP_REPROVADO, PP_SUSPENSO = "rascunho", "em_analise", "aprovado", "reprovado", "suspenso"
DOC_PENDENTE, DOC_APROVADO, DOC_RECUSADO = "pendente", "aprovado", "recusado"
O_AULA, O_PACOTE, O_TURMA = "aula", "pacote", "turma"
OF_RASCUNHO, OF_PUBLICADA, OF_PAUSADA, OF_ENCERRADA = "rascunho", "publicada", "pausada", "encerrada"


class PerfilProfissional(Base):
    """Perfil de Personal / Instrutor. Só publica depois de aprovado pela administração (documentos legais)."""

    __tablename__ = "perfis_profissionais"
    __table_args__ = (Index("ix_perfis_prof_geo", "latitude", "longitude"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(12), default=PP_RASCUNHO)
    nome_profissional: Mapped[str] = mapped_column(String(120))
    apresentacao: Mapped[str | None] = mapped_column(Text)
    foto_url: Mapped[str | None] = mapped_column(Text)
    registro_tipo: Mapped[str | None] = mapped_column(String(30))  # CREF, federação, etc.
    registro_numero: Mapped[str | None] = mapped_column(String(40))
    telefone: Mapped[str | None] = mapped_column(String(40))
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    email_contato: Mapped[str | None] = mapped_column(String(200))
    cidade: Mapped[str | None] = mapped_column(String(100))
    regiao: Mapped[str | None] = mapped_column(String(120))  # bairros/zonas atendidas
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    raio_atendimento_km: Mapped[int] = mapped_column(Integer, default=10)
    modalidades: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)  # códigos de Modalidade
    especialidades: Mapped[str | None] = mapped_column(Text)
    aprovado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime)
    motivo_recusa: Mapped[str | None] = mapped_column(Text)
    aceite_termo_em: Mapped[datetime | None] = mapped_column(DateTime)  # aceite do Termo de Adesão do Profissional
    enviado_analise_em: Mapped[datetime | None] = mapped_column(DateTime)
    nota_media: Mapped[float | None] = mapped_column(Float)
    n_avaliacoes: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    usuario: Mapped[Usuario] = relationship(foreign_keys=[usuario_id], lazy="joined")
    documentos: Mapped[list["DocumentoProfissional"]] = relationship(cascade="all, delete-orphan", order_by="DocumentoProfissional.id")


class DocumentoProfissional(Base):
    """Documento legal do profissional. NUNCA público: só o titular e a administração leem (rota autenticada, sem URL aberta)."""

    __tablename__ = "documentos_profissionais"

    id: Mapped[int] = mapped_column(primary_key=True)
    perfil_id: Mapped[int] = mapped_column(ForeignKey("perfis_profissionais.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(30))  # identidade | registro_profissional | comprovante_endereco | certificado | termo_adesao
    arquivo: Mapped[str] = mapped_column(String(120))  # caminho no armazenamento (documentos/<hex>.<ext>)
    mime: Mapped[str] = mapped_column(String(40))
    nome_original: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(10), default=DOC_PENDENTE)
    motivo: Mapped[str | None] = mapped_column(Text)
    validade: Mapped[date | None] = mapped_column(Date)
    decidido_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime)
    enviado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)


class Oferta(Base):
    """O que o profissional divulga: aula avulsa, pacote de aulas ou turma recorrente."""

    __tablename__ = "ofertas"
    __table_args__ = (Index("ix_ofertas_geo", "latitude", "longitude"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    perfil_id: Mapped[int] = mapped_column(ForeignKey("perfis_profissionais.id", ondelete="CASCADE"), index=True)
    tipo: Mapped[str] = mapped_column(String(8), default=O_AULA)  # aula | pacote | turma
    titulo: Mapped[str] = mapped_column(String(160))
    descricao: Mapped[str | None] = mapped_column(Text)
    modalidade_id: Mapped[int | None] = mapped_column(ForeignKey("modalidades.id"))
    nivel: Mapped[str | None] = mapped_column(String(14))
    faixa_etaria_min: Mapped[int | None] = mapped_column(Integer)
    faixa_etaria_max: Mapped[int | None] = mapped_column(Integer)
    formato: Mapped[str] = mapped_column(String(10), default="individual")  # individual | coletiva
    duracao_min: Mapped[int] = mapped_column(Integer, default=60)
    capacidade: Mapped[int] = mapped_column(Integer, default=1)  # alunos por aula/turma
    aula_experimental: Mapped[bool] = mapped_column(Boolean, default=False)
    valor_experimental: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    valor_aula: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    valor_pacote: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    aulas_pacote: Mapped[int | None] = mapped_column(Integer)
    validade_pacote_dias: Mapped[int | None] = mapped_column(Integer)
    valor_mensal: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    local_texto: Mapped[str | None] = mapped_column(String(200))  # local externo (parque, condomínio…); espaços de arena vêm na fase de vínculos
    endereco: Mapped[str | None] = mapped_column(String(300))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default=OF_RASCUNHO)
    inscricoes_abertas: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    perfil: Mapped[PerfilProfissional] = relationship(lazy="joined")
    modalidade: Mapped[Modalidade | None] = relationship(lazy="joined")
    horarios: Mapped[list["OfertaHorario"]] = relationship(cascade="all, delete-orphan", order_by="OfertaHorario.dia_semana, OfertaHorario.hora_inicio")


class OfertaHorario(Base):
    """Regra semanal da oferta (ex.: terças e quintas, 19h). Na fase 1 é informativa; a fase 2 materializa as sessões."""

    __tablename__ = "oferta_horarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    oferta_id: Mapped[int] = mapped_column(ForeignKey("ofertas.id", ondelete="CASCADE"), index=True)
    dia_semana: Mapped[int] = mapped_column(Integer)  # 0 = segunda … 6 = domingo
    hora_inicio: Mapped[time] = mapped_column(Time)
    duracao_min: Mapped[int] = mapped_column(Integer, default=60)
    vigencia_inicio: Mapped[date | None] = mapped_column(Date)
    vigencia_fim: Mapped[date | None] = mapped_column(Date)
