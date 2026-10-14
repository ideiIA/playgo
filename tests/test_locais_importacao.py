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

        r = cli.post("/api/v1/gestao/locais/importar", data={"tipo": "loja"}, files={"arquivo": ("l.csv", csv_)}, headers=h)
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
        res = cli.post("/api/v1/gestao/locais/importar", data={"tipo": "loja"}, files={"arquivo": ("l.csv", csv_)}, headers=h).json()
        assert res["criados"] == 0 and res["ignorados"] == 3 and len(res["erros"]) == 2

        # aparecem no Explorar
        ex = cli.get("/api/v1/explorar", params={"raio": 0, "tipos": "local"}, headers=h_x).json()
        assert len(ex["locais"]) == 3
