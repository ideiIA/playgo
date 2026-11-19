"""Os testes usam um banco próprio (playgo_teste) e nunca disparam o agendador."""

import os
import tempfile
import uuid

# Precisa valer antes de qualquer import de `playgo`: variáveis de ambiente vencem o .env.
os.environ["PLAYGO_DATABASE_URL"] = "postgresql+psycopg://playgo@localhost:5434/playgo_teste"
os.environ["PLAYGO_AGENDADOR_NA_WEB"] = "false"
os.environ["PLAYGO_CHAVE_SESSAO"] = "chave-apenas-para-testes"
os.environ["PLAYGO_PASTA_UPLOADS"] = tempfile.mkdtemp(prefix="playgo_uploads_")
os.environ["PLAYGO_ANTHROPIC_API_KEY"] = ""  # os testes nunca chamam uma IA de verdade
os.environ["PLAYGO_GEMINI_API_KEY"] = ""
os.environ["PLAYGO_IA_PROVEDOR"] = ""
os.environ["PLAYGO_ARMAZENAMENTO"] = "local"  # nunca grava no Storage de produção
os.environ["PLAYGO_SUPABASE_URL"] = ""
os.environ["PLAYGO_SUPABASE_SERVICE_KEY"] = ""
# todos os testes chegam do mesmo "endereço" (testclient) e o banco de teste guarda a trilha: sem teto, o freio de cadastro/login travaria a suíte
os.environ["PLAYGO_MAX_CADASTROS_IP_HORA"] = "1000000"
os.environ["PLAYGO_MAX_FALHAS_LOGIN_IP"] = "1000000"
os.environ["PLAYGO_MAX_FALHAS_LOGIN_CONTA"] = "1000000"

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError

CENTRO = (-20.4697, -54.6201)  # Campo Grande


@pytest.fixture(scope="session")
def banco():
    """Banco de teste zerado. Pula os testes de integração se o Postgres de desenvolvimento estiver fora do ar."""
    administracao = create_engine("postgresql+psycopg://playgo@localhost:5434/postgres", isolation_level="AUTOCOMMIT")
    try:
        with administracao.connect() as conexao:
            if not conexao.scalar(text("select 1 from pg_database where datname = 'playgo_teste'")):
                conexao.execute(text("create database playgo_teste"))
    except OperationalError:
        pytest.skip("Postgres de desenvolvimento (porta 5434) fora do ar")

    from playgo import models  # noqa: F401
    from playgo.db import Base, Session, criar_tabelas, engine
    from playgo.modalidades import semear

    Base.metadata.drop_all(engine)
    criar_tabelas()
    with Session() as s:
        semear(s)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture()
def s(banco):
    from playgo.db import Session

    with Session() as sessao:
        yield sessao


@pytest.fixture()
def fabrica(s):
    """Cria atletas e dá atalhos para modalidades."""
    from playgo import contas
    from playgo.models import Modalidade

    class Fabrica:
        def mod(self, codigo):
            return s.scalar(select(Modalidade).where(Modalidade.codigo == codigo))

        def atleta(self, nome="Atleta", esportes=(("futebol", "intermediario"),), lat=CENTRO[0], lng=CENTRO[1], **perfil):
            u = contas.cadastrar(s, nome, f"{uuid.uuid4().hex[:10]}@teste.local", "senha-de-teste-1", f"u{uuid.uuid4().hex[:12]}", True, True)
            contas.atualizar_perfil(s, u, latitude=lat, longitude=lng, **perfil)
            contas.definir_esportes(s, u, {self.mod(c).id: n for c, n in esportes})
            return u

    return Fabrica()
