"""Importação de planilhas do guia de locais: CSV e XLSX, tipo no envio, geocodificação e relatório de erros."""

import io
import json
import uuid
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from playgo import locais_importacao as imp
from playgo.db import Session
from playgo.erros import ErroNegocio
from playgo.models import Local, Usuario
from playgo.web.app import app


def _xlsx(linhas: list[list[str]]) -> bytes:
    """Monta um .xlsx mínimo (textos inline) só para o teste."""
    def ref(i, j):
        return "ABCDEFGHIJKLM"[j] + str(i + 1)

    linhas_xml = "".join(
        f'<row r="{i + 1}">' + "".join(f'<c r="{ref(i, j)}" t="inlineStr"><is><t>{v}</t></is></c>' for j, v in enumerate(l) if v) + "</row>"
        for i, l in enumerate(linhas)
    )
    sheet = f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{linhas_xml}</sheetData></worksheet>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_leitura_csv_e_xlsx_e_cabecalhos_flexiveis():
    csv_ = "Nome;Endereço;Telefone;Horário de Funcionamento;Esportes\nQuadra A;Rua 1;1111;8h às 18h;futsal, vôlei\n".encode("utf-8")
    d = imp.ler_planilha("a.csv", csv_)[0]
    assert d["nome"] == "Quadra A" and d["endereco"] == "Rua 1" and d["horario_funcionamento"] == "8h às 18h" and d["esportes"] == "futsal, vôlei"
    x = imp.ler_planilha("a.xlsx", _xlsx([["nome", "endereco", "telefone"], ["Loja B", "Rua 2", "2222"]]))[0]
    assert x["nome"] == "Loja B" and x["telefone"] == "2222"
    with pytest.raises(ErroNegocio):
        imp.ler_planilha("a.csv", b"foo;bar\n1;2\n")  # sem coluna nome
    with pytest.raises(ErroNegocio):
        imp.ler_planilha("a.csv", b"")


def test_importa_com_tipo_geocodifica_e_relata_erros(banco, monkeypatch):
    chamadas = []

    def falso(req: httpx.Request) -> httpx.Response:
        chamadas.append(str(req.url))
        if "Rua+Achada" in str(req.url) or "Rua%20Achada" in str(req.url):
            return httpx.Response(200, json=[{"lat": "-20.5", "lon": "-54.6"}])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(imp, "_http", lambda: httpx.Client(transport=httpx.MockTransport(falso)))
    monkeypatch.setattr(imp, "_pausa", lambda s: None)

    csv_ = (
        "nome;endereco;cidade;telefone;horario_funcionamento;latitude;longitude;esportes;tipo\n"
        "Com coordenadas;Rua 1;Campo Grande;1111;8h às 18h;-20,45;-54,60;futsal;\n"
        "Pelo endereco;Rua Achada;Campo Grande;2222;9h às 17h;;;;\n"
        "Sem telefone;Rua 3;Campo Grande;;9h;-20,4;-54,6;;\n"
        "Nao acha;Rua Perdida;Campo Grande;3333;9h;;;;\n"
        "Arena por linha;Rua 5;Campo Grande;4444;7h às 23h;-20,41;-54,61;;arena\n"
    ).encode("utf-8")

    with TestClient(app) as cli:
        r = cli.post("/api/v1/auth/cadastro", json={"nome": "Adm", "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
        h, uid = {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]
        with Session() as s:
            s.query(Local).delete()
            s.query(Usuario).filter(Usuario.id == uid).update({Usuario.admin: True})
            s.commit()

        r2 = cli.post("/api/v1/auth/cadastro", json={"nome": "X", "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
        h_x = {"Authorization": "Bearer " + r2.json()["token"]}
        assert cli.post("/api/v1/gestao/locais/importar", data={"tipo": "loja"}, files={"arquivo": ("l.csv", csv_)}, headers=h_x).status_code == 403
        assert cli.post("/api/v1/gestao/locais/importar", data={"tipo": "xxx"}, files={"arquivo": ("l.csv", csv_)}, headers=h).status_code == 400

        r = cli.post("/api/v1/gestao/locais/importar", data={"tipo": "loja", "incompleto": "false"}, files={"arquivo": ("l.csv", csv_)}, headers=h)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["criados"] == 3 and res["ignorados"] == 0 and len(res["erros"]) == 2
        motivos = {e["nome"]: e["motivo"] for e in res["erros"]}
        assert "telefone" in motivos["Sem telefone"].lower() and "localizar" in motivos["Nao acha"].lower()

        por_nome = {x["nome"]: x for x in cli.get("/api/v1/gestao/locais", headers=h).json()}
        assert por_nome["Com coordenadas"]["categoria"] == "loja" and por_nome["Com coordenadas"]["modalidades"] == ["futsal"]
        assert por_nome["Pelo endereco"]["latitude"] == -20.5
        assert por_nome["Arena por linha"]["categoria"] == "arena"  # a coluna tipo vence o tipo do envio

        # reenviar a planilha: os que entraram são ignorados
        res = cli.post("/api/v1/gestao/locais/importar", data={"tipo": "loja", "incompleto": "false"}, files={"arquivo": ("l.csv", csv_)}, headers=h).json()
        assert res["criados"] == 0 and res["ignorados"] == 3 and len(res["erros"]) == 2

        # aparecem no Explorar
        ex = cli.get("/api/v1/explorar", params={"raio": 0, "tipos": "local"}, headers=h_x).json()
        assert len(ex["locais"]) == 3


# ---------------------------------------------------------------- planilha do Google Maps (redes, link do Maps, categoria, avaliação)

MAPS_CSV = "\n".join([
    "Município;Busca;Nome;Endereço;Telefone;Avaliação;Categoria no Maps;Horário;Website;Facebook;Instagram;Link do Maps",
    "Campo Grande;quadra esportiva;Arena 67 Locação de Campo de Futebol;Av. Gury Marques, 8395 - Campo Grande - MS;(67) 99105-4041;;Campo de futebol;;"
    "https://www.instagram.com/arena67ms/;;https://www.instagram.com/arena67ms;"
    "https://www.google.com/maps/place/Arena+67/data=!4m7!3m6!1s0x1:0x2!8m2!3d-20.5543085!4d-54.5914755!16s%2Fg%2F11srm_j2nt?authuser=0&hl=pt-BR",
    "Campo Grande;quadra esportiva;Arena Bartz;R. Xingu, 536 - Campo Grande - MS;(67) 99241-3985;5,0 (16);Quadra de vôlei de praia;;https://www.arenabartz.com.br/;;;"
    "https://www.google.com/maps/place/Arena+Bartz/data=!3m1!4b1!8m2!3d-20.4392356!4d-54.5935437",
    "Campo Grande;loja de artigos esportivos;Centauro;Av. Afonso Pena, 4909;;4,4 (1.204);Loja de artigos esportivos;;https://www.centauro.com.br;https://facebook.com/centauro?ref=x;;"
    "https://www.google.com/maps/place/Centauro/@-20.4601,-54.5815,17z",
    "Ponta Porã;ginásio de esportes;Ginásio Municipal de Esportes;Rua Marechal Floriano, 100;;;Academia de ginástica olímpica;;;;;"
    "https://www.google.com/maps/place/Ginasio/data=!8m2!3d-22.5365!4d-55.7258",
    "Dourados;loja de artigos esportivos;Lojas Veneza Móveis;Rua X, 1;(67) 3000-0000;;Loja de móveis;;;;;https://www.google.com/maps/place/V/data=!8m2!3d-22.2!4d-54.8",
    "Dourados;arena esportiva;PANTANAL FIT ACADEMIA;Rua Y, 2;;;Academia;;;;;https://www.google.com/maps/place/P/data=!8m2!3d-22.21!4d-54.81",
])



def test_planilha_do_maps_detecta_tipo_le_coordenadas_redes_e_avaliacao():
    linhas = imp.ler_planilha("maps.csv", MAPS_CSV.encode("utf-8"))
    assert [d["nome"] for d in linhas][:2] == ["Arena 67 Locação de Campo de Futebol", "Arena Bartz"]
    assert imp.locais.coordenadas_do_maps(linhas[0]["maps_url"]) == (-20.5543085, -54.5914755)
    assert imp.locais.coordenadas_do_maps(linhas[2]["maps_url"]) == (-20.4601, -54.5815)  # formato /@lat,lng
    assert imp._avaliacao("5,0 (16)") == (5.0, 16) and imp._avaliacao("4,4 (1.204)") == (4.4, 1204)
    # tipo: o nome "Arena" e a categoria do Maps decidem; lojas e ginásios municipais saem do lugar certo
    assert imp.inferir_tipo("Campo de futebol", "quadra esportiva", "Arena 67") == "arena"
    assert imp.inferir_tipo("Academia", "arena esportiva", "Arena Craque Dema") == "arena"
    assert imp.inferir_tipo("Academia", "ginásio de esportes", "PANTANAL FIT ACADEMIA") == "clube"
    assert imp.inferir_tipo("Academia de ginástica olímpica", "ginásio de esportes", "Ginásio Municipal de Esportes") == "publico"
    assert imp.inferir_tipo("Loja de artigos esportivos", "loja de artigos esportivos", "Centauro") == "loja"
    # lojas fora do tema são descartadas, e loja genérica só passa com nome esportivo
    assert imp.motivo_descarte("Loja de móveis", "Lojas Veneza Móveis")
    assert imp.motivo_descarte("Loja de calçado", "Casa Vitória")
    assert imp.motivo_descarte("Loja de calçado", "World Tennis") is None
    assert imp.motivo_descarte("Loja de roupas de trabalho", "Madala Skate Shop") is None
    # redes: o Instagram que veio na coluna "site" vai para o campo certo
    web, ig, fb = imp.locais.separar_redes("https://www.instagram.com/arena67ms/", "https://www.instagram.com/arena67ms?igsh=x", None)
    assert (web, ig, fb) == (None, "https://instagram.com/arena67ms", None)
    assert imp.locais.separar_redes("https://www.arenabartz.com.br/", None, "https://facebook.com/centauro?ref=x") == ("https://www.arenabartz.com.br/", None, "https://facebook.com/centauro")
    assert imp.locais.normalizar_instagram("@minha.arena") == "https://instagram.com/minha.arena"


def test_importa_planilha_do_maps_pela_api(banco, monkeypatch):
    monkeypatch.setattr(imp, "_http", lambda: (_ for _ in ()).throw(AssertionError("não deve geocodificar: as coordenadas vêm do link do Maps")))
    with TestClient(app) as cli:
        r = cli.post("/api/v1/auth/cadastro", json={"nome": "Adm", "email": f"{uuid.uuid4().hex[:10]}@teste.local", "senha": "senha-de-teste-1", "usuario": f"u{uuid.uuid4().hex[:12]}", "aceito_termos": True})
        h, uid = {"Authorization": "Bearer " + r.json()["token"]}, r.json()["usuario"]["id"]
        with Session() as s:
            s.query(Local).delete()
            s.query(Usuario).filter(Usuario.id == uid).update({Usuario.admin: True})
            s.commit()
        r = cli.post("/api/v1/gestao/locais/importar", data={"tipo": "auto"}, files={"arquivo": ("maps.csv", MAPS_CSV.encode("utf-8"))}, headers=h)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["criados"] == 5 and len(res["descartados"]) == 1 and res["erros"] == []
        assert res["por_tipo"] == {"arena": 2, "loja": 1, "publico": 1, "clube": 1}
        assert res["incompletos"] == 5  # nenhuma linha tem horário
        por_nome = {x["nome"]: x for x in cli.get("/api/v1/gestao/locais", headers=h).json()}
        a = por_nome["Arena 67 Locação de Campo de Futebol"]
        assert a["categoria"] == "arena" and a["instagram"] == "https://instagram.com/arena67ms" and a["site"] is None
        assert a["latitude"] == -20.5543085 and a["cidade"] == "Campo Grande" and a["modalidades"] == ["futebol"] and a["incompleto"] is True
        assert "google.com/maps/place/Arena+67" in a["maps_url"]
        b = por_nome["Arena Bartz"]
        assert b["avaliacao"] == 5.0 and b["avaliacoes"] == 16 and b["site"] == "https://www.arenabartz.com.br/" and b["modalidades"] == ["volei_de_areia"]
        assert por_nome["Centauro"]["categoria"] == "loja" and por_nome["Centauro"]["facebook"] == "https://facebook.com/centauro"
        assert "Lojas Veneza Móveis" not in por_nome

        # sem aceitar incompletos, as linhas sem horário/telefone voltam com o motivo
        with Session() as s:
            s.query(Local).delete()
            s.commit()
        r = cli.post("/api/v1/gestao/locais/importar", data={"tipo": "auto", "incompleto": "false"}, files={"arquivo": ("maps.csv", MAPS_CSV.encode("utf-8"))}, headers=h)
        assert r.json()["criados"] == 0 and len(r.json()["erros"]) == 5

        # pontos do mapa levam o ícone e a cor do tipo
        with Session() as s:
            s.query(Local).delete()
            s.commit()
        cli.post("/api/v1/gestao/locais/importar", data={"tipo": "auto"}, files={"arquivo": ("maps.csv", MAPS_CSV.encode("utf-8"))}, headers=h)
        ex = cli.get("/api/v1/explorar", params={"raio": 0, "tipos": "local", "lat": -20.5, "lng": -54.6}, headers=h).json()
        pontos = {p["rotulo"].split(" ", 1)[1]: p for p in ex["pontos"] if p["tipo"] == "local"}
        assert pontos["Centauro"]["icone"] == "🛍️" and pontos["Centauro"]["cor"] == "#ff8a00"
        assert pontos["Ginásio Municipal de Esportes"]["icone"] == "🌳"
