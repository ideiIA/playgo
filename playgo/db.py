from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

def _criar_engine():
    """Serverless: sem pool entre invocações (NullPool) — quem pooliza é o Supavisor do Supabase. No pooler em modo
    transação (porta 6543) o psycopg não pode usar prepared statements."""
    args: dict = {}
    if ":6543/" in settings.database_url or "pooler.supabase.com" in settings.database_url:
        args["prepare_threshold"] = None
    if settings.em_serverless:
        return create_engine(settings.database_url, poolclass=NullPool, connect_args=args)
    return create_engine(settings.database_url, pool_pre_ping=True, connect_args=args)


engine = _criar_engine()
Session = sessionmaker(engine, expire_on_commit=False)

_FUSO = ZoneInfo(settings.fuso)


class Base(DeclarativeBase):
    pass


def agora() -> datetime:
    """Horário local do servidor, sem fuso — as datas das atividades são combinadas no relógio da cidade."""
    return datetime.now(_FUSO).replace(tzinfo=None)


# create_all não altera tabelas que já existem; colunas e índices acrescentados depois entram por aqui.
_COLUNAS_NOVAS: tuple[str, ...] = (
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS formato varchar(20)",
    "ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS instagram_status varchar(10)",
    "ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS instagram_consentimento_em timestamp",
    "ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS instagram_media_id varchar(40)",
    "ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS instagram_permalink varchar(300)",
    "ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS instagram_erro varchar(200)",
    "ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS instagram_em timestamp",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS duracao_jogo_min integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS cadastro_elenco boolean NOT NULL DEFAULT false",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS regulamento_arquivo varchar(120)",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS regulamento_nome varchar(150)",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS regulamento_em timestamp",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS placar_modo varchar(8)",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS sets_melhor_de integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS pontos_set integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS pontos_tiebreak integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS diferenca_set integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS grupos_qtd integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS classificam integer",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS fase varchar(10)",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS grupo varchar(2)",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS equipe_a2_id integer REFERENCES equipes(id) ON DELETE SET NULL",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS equipe_b2_id integer REFERENCES equipes(id) ON DELETE SET NULL",
    "ALTER TABLE locais ADD COLUMN IF NOT EXISTS instagram varchar(300)",
    "ALTER TABLE locais ADD COLUMN IF NOT EXISTS facebook varchar(300)",
    "ALTER TABLE locais ADD COLUMN IF NOT EXISTS maps_url text",
    "ALTER TABLE locais ADD COLUMN IF NOT EXISTS categoria_maps varchar(120)",
    "ALTER TABLE locais ADD COLUMN IF NOT EXISTS avaliacao double precision",
    "ALTER TABLE locais ADD COLUMN IF NOT EXISTS avaliacoes integer",
    "ALTER TABLE assinaturas ADD COLUMN IF NOT EXISTS vigente_desde date",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS transmissao_permitida boolean NOT NULL DEFAULT false",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS transmissao_aceite_por_id integer",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS transmissao_aceite_em timestamp",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS yt_broadcast_id varchar(20)",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS yt_stream_id varchar(60)",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS yt_estado varchar(12)",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS lk_egress_id varchar(60)",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS familia varchar(12) DEFAULT 'geral'",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS max_ofertas integer",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS max_turmas integer",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS max_professores integer",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS comissao_pct numeric(5,2) DEFAULT 0",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS pode_cobrar boolean DEFAULT false",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS pode_escolinha boolean DEFAULT false",
    "ALTER TABLE assinaturas ADD COLUMN IF NOT EXISTS familia varchar(12) DEFAULT 'geral'",
    "ALTER TABLE assinaturas DROP CONSTRAINT IF EXISTS assinaturas_usuario_id_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_assinaturas_usuario_familia ON assinaturas (usuario_id, familia)",
    "ALTER TABLE arenas ADD COLUMN IF NOT EXISTS tipo_estabelecimento varchar(12) DEFAULT 'arena'",
    "ALTER TABLE quadras ADD COLUMN IF NOT EXISTS tipo varchar(12) DEFAULT 'quadra'",
    "ALTER TABLE comunidades ADD COLUMN IF NOT EXISTS perfil_profissional_id integer",
    "ALTER TABLE perfis_profissionais ADD COLUMN IF NOT EXISTS aceite_termo_em timestamp",
    "ALTER TABLE perfis_profissionais ADD COLUMN IF NOT EXISTS enviado_analise_em timestamp",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS valor_anual numeric(10,2)",
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS valor_evento numeric(10,2) DEFAULT 0",
    "ALTER TABLE assinaturas ADD COLUMN IF NOT EXISTS ciclo varchar(6) DEFAULT 'mensal'",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS telao_token varchar(40)",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS telao_jogo_id integer",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_campeonatos_telao_token ON campeonatos (telao_token)",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS capa varchar(60)",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS book_publicado boolean NOT NULL DEFAULT false",
    "ALTER TABLE atividades ADD COLUMN IF NOT EXISTS capa varchar(60)",
    "ALTER TABLE atividades ADD COLUMN IF NOT EXISTS book_publicado boolean NOT NULL DEFAULT false",
    "ALTER TABLE grupos ADD COLUMN IF NOT EXISTS entrada varchar(10) NOT NULL DEFAULT 'livre'",
    "ALTER TABLE grupo_membros ADD COLUMN IF NOT EXISTS status varchar(10) NOT NULL DEFAULT 'ativo'",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS ponto_a integer NOT NULL DEFAULT 0",
    "ALTER TABLE jogos ADD COLUMN IF NOT EXISTS ponto_b integer NOT NULL DEFAULT 0",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS sorteado_em timestamp",
    "ALTER TABLE campeonatos ADD COLUMN IF NOT EXISTS sorteio_semente bigint",
    # Plano "Usuário" (antes "Atleta"): só vê e publica no feed. A linha antiga, se existir, é ajustada uma única vez (só casa com o nome antigo).
    "ALTER TABLE planos ADD COLUMN IF NOT EXISTS pode_atividade boolean NOT NULL DEFAULT true",
    "UPDATE planos SET pode_atividade = false WHERE codigo = 'gratuito' AND nome = 'Atleta'",
    "UPDATE planos SET nome = 'Usuário' WHERE codigo = 'gratuito' AND nome = 'Atleta'",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS google_sub varchar(40)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_google_sub ON usuarios (google_sub)",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS usuario varchar(30)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_usuario ON usuarios (usuario)",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS termos_versao varchar(20)",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS termos_em timestamp",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS consent_localizacao boolean NOT NULL DEFAULT false",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS consent_localizacao_em timestamp",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS anonimizado_em timestamp",
    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS moderador boolean NOT NULL DEFAULT false",
    "ALTER TABLE atividades ADD COLUMN IF NOT EXISTS visibilidade varchar(12) NOT NULL DEFAULT 'publica'",
    "ALTER TABLE atividades ADD COLUMN IF NOT EXISTS convite_token varchar(40)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_atividades_convite ON atividades (convite_token)",
    # Atividades que já exigiam aprovação passam a ser "só autorizados"
    "UPDATE atividades SET visibilidade = 'autorizados' WHERE exige_aprovacao AND visibilidade = 'publica'",
)

# Os registros de interação só se acrescentam. A purga por prazo liga `playgo.purga` na própria transação.
_GATILHO_REGISTROS = (
    """CREATE OR REPLACE FUNCTION registros_somente_insercao() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' AND current_setting('playgo.purga', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'registros: a trilha de auditoria é somente de inserção';
    END; $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS tg_registros_somente_insercao ON registros",
    """CREATE TRIGGER tg_registros_somente_insercao BEFORE UPDATE OR DELETE ON registros
    FOR EACH ROW EXECUTE FUNCTION registros_somente_insercao()""",
)


def criar_tabelas() -> None:
    from . import models  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(engine)
    with engine.begin() as conexao:
        for comando in _COLUNAS_NOVAS + _GATILHO_REGISTROS:
            conexao.execute(text(comando))
