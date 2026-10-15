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
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
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
    tipo: Mapped[str] = mapped_column(String(20))  # publico | loja | arena
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
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    modalidade: Mapped[Modalidade] = relationship(lazy="joined")
    membros: Mapped[list["GrupoMembro"]] = relationship(cascade="all, delete-orphan", back_populates="grupo")


class GrupoMembro(Base):
    __tablename__ = "grupo_membros"

    grupo_id: Mapped[int] = mapped_column(ForeignKey("grupos.id", ondelete="CASCADE"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    papel: Mapped[str] = mapped_column(String(10), default="membro")  # admin | membro
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
    inicio_previsto: Mapped[datetime | None] = mapped_column(DateTime)
    local: Mapped[str | None] = mapped_column(String(120))  # quadra/campo
    iniciado_em: Mapped[datetime | None] = mapped_column(DateTime)
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime)

    equipe_a: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_a_id], lazy="joined")
    equipe_b: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_b_id], lazy="joined")
    # Super 8: cada lado é uma dupla (equipe_a + equipe_a2 contra equipe_b + equipe_b2); nos demais formatos ficam vazios
    equipe_a2_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    equipe_b2_id: Mapped[int | None] = mapped_column(ForeignKey("equipes.id", ondelete="SET NULL"))
    equipe_a2: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_a2_id], lazy="joined")
    equipe_b2: Mapped[Equipe | None] = relationship(foreign_keys=[equipe_b2_id], lazy="joined")
    eventos: Mapped[list["JogoEvento"]] = relationship(cascade="all, delete-orphan", order_by="JogoEvento.id")
    sets: Mapped[list["JogoSet"]] = relationship(cascade="all, delete-orphan", order_by="JogoSet.numero")


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
    max_participantes: Mapped[int | None] = mapped_column(Integer)  # None = sem limite
    max_atividades_abertas: Mapped[int | None] = mapped_column(Integer)
    pode_atividade: Mapped[bool] = mapped_column(Boolean, default=True)  # organizar atividades
    pode_campeonato: Mapped[bool] = mapped_column(Boolean, default=False)
    pode_arena: Mapped[bool] = mapped_column(Boolean, default=False)


class Assinatura(Base):
    """Plano pago de uma pessoa (no máximo uma). `teste` = 30 dias grátis; `ativa` = paga ou concedida; `cancelada` = não renova."""

    __tablename__ = "assinaturas"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), unique=True)
    plano: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10), default="teste")  # teste | ativa | cancelada
    origem: Mapped[str] = mapped_column(String(10), default="teste")  # teste | asaas | manual
    teste_ate: Mapped[date | None] = mapped_column(Date)
    vigente_ate: Mapped[date | None] = mapped_column(Date)
    testes_usados: Mapped[list[str]] = mapped_column(ARRAY(String(12)), default=list)
    asaas_customer_id: Mapped[str | None] = mapped_column(String(40))  # o CPF/CNPJ NÃO é guardado aqui
    asaas_subscription_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    cancelada_em: Mapped[datetime | None] = mapped_column(DateTime)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora)

    pagamentos: Mapped[list["Pagamento"]] = relationship(cascade="all, delete-orphan", order_by="Pagamento.id")


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
