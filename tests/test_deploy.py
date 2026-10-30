"""Adaptação para Vercel + Supabase: Storage, limites do serverless, análise síncrona, cron e pool de conexões."""

import io
import json

import httpx
import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.pool import NullPool

from playgo import armazenamento, db, midia, seguranca
from playgo.config import settings
from playgo.erros import ErroNegocio, NaoEncontrado


@pytest.fixture()
def supabase(monkeypatch):
    """Configura o Storage do Supabase com transporte simulado e devolve a lista de requisições feitas."""
    chamadas: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        chamadas.append(req)
        if "/object/sign/" in req.url.path:
            return httpx.Response(200, json={"signedURL": "/object/sign/playgo-midia/x.jpg?token=abc"})
        if req.method == "GET":
            return httpx.Response(200, content=b"conteudo-do-arquivo")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "eyJ.chave.de-servico")
    monkeypatch.setattr(settings, "armazenamento", "")
    monkeypatch.setattr(armazenamento, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return chamadas


def test_escolha_automatica_do_armazenamento(monkeypatch):
    assert not armazenamento.usa_supabase()  # testes: sem URL/chave → disco
    monkeypatch.setattr(settings, "armazenamento", "")
    monkeypatch.setattr(settings, "supabase_url", "https://abc.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "k")
    assert armazenamento.usa_supabase()
    monkeypatch.setattr(settings, "armazenamento", "local")
    assert not armazenamento.usa_supabase()  # força o disco


def test_storage_salvar_ler_remover(supabase):
    armazenamento.salvar("2026/10/x.jpg", b"bytes", "image/jpeg")
    req = supabase[-1]
    assert req.method == "POST" and req.url.path == "/storage/v1/object/playgo-midia/2026/10/x.jpg"
    assert req.headers["authorization"] == "Bearer eyJ.chave.de-servico" and req.headers["apikey"] == "eyJ.chave.de-servico"
    assert req.headers["content-type"] == "image/jpeg" and req.headers["x-upsert"] == "true" and req.content == b"bytes"

    assert armazenamento.ler("2026/10/x.jpg") == b"conteudo-do-arquivo"
    assert supabase[-1].url.path == "/storage/v1/object/authenticated/playgo-midia/2026/10/x.jpg"

    armazenamento.remover("2026/10/x.jpg")
    assert supabase[-1].method == "DELETE" and json.loads(supabase[-1].content) == {"prefixes": ["2026/10/x.jpg"]}
    armazenamento.remover(None)  # nada a fazer


def test_storage_servir_redireciona_para_url_assinada(supabase):
    r = armazenamento.servir("2026/10/x.jpg", "image/jpeg", "private, max-age=60")
    assert r.status_code == 302 and r.headers["location"] == "https://abc.supabase.co/storage/v1/object/sign/playgo-midia/x.jpg?token=abc"
    assert json.loads(supabase[-1].content) == {"expiresIn": armazenamento.VALIDADE_URL_ASSINADA_S}


def test_falhas_do_storage(monkeypatch, supabase):
    monkeypatch.setattr(armazenamento, "_http", lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="erro"))))
    with pytest.raises(ErroNegocio, match="guardar"):
        armazenamento.salvar("a.jpg", b"x", "image/jpeg")
    with pytest.raises(NaoEncontrado):
        armazenamento.servir("a.jpg", "image/jpeg", "no-store")
    armazenamento.remover("a.jpg")  # falha ao remover só é registrada


def test_foto_de_perfil_e_midia_vao_para_o_storage(supabase):
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (10, 200, 10)).save(buf, "JPEG")
    nome = midia.salvar_avatar(midia.processar_avatar(buf.getvalue()))
    assert supabase[-1].url.path == f"/storage/v1/object/playgo-midia/perfil/{nome}"
    rec = midia.processar(buf.getvalue())
    arq, mini, _ = midia.salvar(rec)
    caminhos = [r.url.path for r in supabase]
    assert f"/storage/v1/object/playgo-midia/{arq}" in caminhos and f"/storage/v1/object/playgo-midia/{mini}" in caminhos
    midia.remover_arquivos(arq, mini)
    assert [r.method for r in supabase[-2:]] == ["DELETE", "DELETE"]


def test_limites_efetivos_no_serverless(monkeypatch):
    assert (settings.limite_foto_mb, settings.limite_video_mb) == (settings.max_foto_mb, settings.max_video_mb)
    monkeypatch.setattr(settings, "serverless", True)
    assert settings.em_serverless and settings.limite_video_mb == 4 and settings.limite_foto_mb == 4
    assert not settings.migrar_ao_subir
    grande = b"\x00\x00\x00\x18ftypmp42" + b"\0" * (5 * 1024 * 1024)
    with pytest.raises(ErroNegocio, match="4 MB"):
        midia.processar(grande)


def test_vercel_liga_o_modo_serverless(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert settings.em_serverless and not settings.migrar_ao_subir


def test_chave_de_sessao_obrigatoria_no_serverless(monkeypatch):
    monkeypatch.setattr(settings, "serverless", True)
    monkeypatch.setattr(settings, "chave_sessao", "")
    with pytest.raises(RuntimeError, match="PLAYGO_CHAVE_SESSAO"):
        seguranca.chave_sessao()


def test_pool_e_pgbouncer(monkeypatch):
    monkeypatch.setattr(settings, "serverless", True)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u:p@aws-0-sa-east-1.pooler.supabase.com:6543/postgres")
    e = db._criar_engine()
    assert isinstance(e.pool, NullPool)
    monkeypatch.setattr(settings, "serverless", False)
    assert not isinstance(db._criar_engine().pool, NullPool)


def test_analise_roda_na_propria_requisicao_no_serverless(monkeypatch):
    from playgo.api import mural

    chamadas = []
    tarefas = BackgroundTasks()
    mural.apos(tarefas, chamadas.append, 1)
    assert chamadas == [] and len(tarefas.tasks) == 1  # servidor comum: depois da resposta
    monkeypatch.setattr(settings, "serverless", True)
    mural.apos(tarefas, chamadas.append, 2)
    assert chamadas == [2] and len(tarefas.tasks) == 1  # serverless: já rodou


def test_cron_exige_segredo(banco, monkeypatch):
    from playgo.web.app import app

    with TestClient(app) as c:
        assert c.get("/api/v1/cron/ciclo").status_code == 403  # sem segredo configurado, desligado
        monkeypatch.setattr(settings, "cron_secret", "segredo-do-cron")
        assert c.get("/api/v1/cron/ciclo").status_code == 403
        assert c.get("/api/v1/cron/ciclo", headers={"Authorization": "Bearer errado"}).status_code == 403
        r = c.get("/api/v1/cron/ciclo", headers={"Authorization": "Bearer segredo-do-cron"})
        assert r.status_code == 200 and set(r.json()) == {"encerradas", "lembretes", "urgentes", "planos", "recebimentos", "reservas", "instagram", "youtube"}
        monkeypatch.setattr(settings, "cron_secret", "")
        monkeypatch.setenv("CRON_SECRET", "do-vercel")  # o Vercel injeta CRON_SECRET
        assert c.get("/api/v1/cron/ciclo", headers={"Authorization": "Bearer do-vercel"}).status_code == 200


def test_entrada_do_vercel_exporta_o_app():
    import importlib.util
    from pathlib import Path

    arquivo = Path(__file__).resolve().parent.parent / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("api_index", arquivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.app.title == "PlayGo"
    config = json.loads((arquivo.parent.parent / "vercel.json").read_text(encoding="utf-8"))
    assert config["rewrites"][0]["destination"] == "/api/index" and config["crons"][0]["path"] == "/api/v1/cron/ciclo"


def test_chave_nova_do_supabase_vai_so_no_apikey(monkeypatch, supabase):
    monkeypatch.setattr(settings, "supabase_service_key", "sb_secret_abc123")
    armazenamento.salvar("a.jpg", b"x", "image/jpeg")
    assert supabase[-1].headers["apikey"] == "sb_secret_abc123" and "authorization" not in supabase[-1].headers
