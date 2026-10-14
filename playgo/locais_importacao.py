"""Importação de planilhas (CSV ou Excel .xlsx) para o guia de locais.

O tipo (local público, loja ou arena) é escolhido no envio e vale para as linhas sem coluna `tipo`. Linhas sem latitude/longitude
são localizadas pelo endereço (OpenStreetMap/Nominatim, 1 consulta por segundo, no máximo `MAX_GEOCODIFICAR` por envio).
Cada linha é validada pelas mesmas regras do cadastro manual; as boas entram e as ruins voltam com o motivo."""

import csv
import io
import re
import time
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessaoORM

from . import auditoria, locais
from .erros import ErroNegocio
from .models import Local, Modalidade, Usuario

MAX_LINHAS = 500
MAX_BYTES = 2 * 1024 * 1024
MAX_GEOCODIFICAR = 60

CABECALHO_MODELO = ["tipo", "nome", "endereco", "cidade", "telefone", "horario_funcionamento", "email", "site", "regras", "descricao", "esportes", "latitude", "longitude"]
EXEMPLO_MODELO = [
    ["publico", "Praça Ary Coelho", "Av. Afonso Pena, s/n - Centro", "Campo Grande", "(67) 3000-0000", "Todos os dias: 6h às 22h", "", "", "Não é permitido som alto após as 21h", "Quadra poliesportiva aberta", "futsal, basquete", "", ""],
    ["loja", "Loja do Atleta", "Rua 14 de Julho, 100", "Campo Grande", "(67) 99999-0000", "Seg a sáb: 9h às 19h", "contato@loja.com.br", "https://instagram.com/lojadoatleta", "", "Material esportivo em geral", "", "", ""],
]

# nome de coluna aceito (já normalizado) -> campo
ALIAS = {
    "tipo": "tipo", "categoria": "tipo", "nome": "nome", "local": "nome", "endereco": "endereco", "logradouro": "endereco",
    "cidade": "cidade", "municipio": "cidade", "telefone": "telefone", "fone": "telefone", "celular": "telefone", "whatsapp": "telefone",
    "horario": "horario_funcionamento", "horarios": "horario_funcionamento", "horariodefuncionamento": "horario_funcionamento",
    "horariofuncionamento": "horario_funcionamento", "funcionamento": "horario_funcionamento", "email": "email", "site": "site",
    "instagram": "site", "regras": "regras", "regrasdeutilizacao": "regras", "regrasdeuso": "regras", "descricao": "descricao",
    "esportes": "esportes", "modalidades": "esportes", "esporte": "esportes", "latitude": "latitude", "lat": "latitude",
    "longitude": "longitude", "lng": "longitude", "lon": "longitude", "long": "longitude",
}
TIPOS_TEXTO = {
    "publico": "publico", "localpublico": "publico", "praca": "publico", "parque": "publico", "quadrapublica": "publico",
    "loja": "loja", "lojademateriais": "loja", "lojadematerialesportivo": "loja", "materialesportivo": "loja",
    "arena": "arena", "arenaparceira": "arena", "quadra": "arena", "arenaparticular": "arena",
}


def _http() -> httpx.Client:
    """Os testes substituem esta função por um transporte simulado."""
    return httpx.Client(timeout=15, headers={"User-Agent": "PlayGo/1.0 (guia de locais)"})


def _pausa(segundos: float) -> None:
    time.sleep(segundos)


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", t)


# ---------------------------------------------------------------- leitura dos arquivos


def _ler_csv(dados: bytes) -> list[list[str]]:
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = dados.decode("latin-1")
    amostra = texto[:4096]
    delim = ";" if amostra.count(";") > amostra.count(",") else ("\t" if amostra.count("\t") > amostra.count(",") else ",")
    return [[c.strip() for c in linha] for linha in csv.reader(io.StringIO(texto), delimiter=delim)]


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _ler_xlsx(dados: bytes) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
        nomes = z.namelist()
        textos: list[str] = []
        if "xl/sharedStrings.xml" in nomes:
            raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
            textos = ["".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])) for si in raiz.findall("m:si", _NS)]
        planilha = sorted(n for n in nomes if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
        raiz = ET.fromstring(z.read(planilha))
    except (zipfile.BadZipFile, KeyError, IndexError, ET.ParseError):
        raise ErroNegocio("Não consegui ler a planilha. Envie um arquivo .xlsx ou .csv válido.") from None
    linhas: list[list[str]] = []
    for row in raiz.iter("{%s}row" % _NS["m"]):
        celulas: dict[int, str] = {}
        for c in row.findall("m:c", _NS):
            tipo, ref = c.get("t"), c.get("r") or ""
            if tipo == "inlineStr":
                v = "".join(t.text or "" for t in c.iter("{%s}t" % _NS["m"]))
            else:
                el = c.find("m:v", _NS)
                v = (el.text or "") if el is not None else ""
                if tipo == "s" and v.isdigit() and int(v) < len(textos):
                    v = textos[int(v)]
            if ref:
                celulas[_col(ref)] = v.strip()
        if celulas:
            linhas.append([celulas.get(i, "") for i in range(max(celulas) + 1)])
    return linhas


def ler_planilha(nome: str, dados: bytes) -> list[dict]:
    if len(dados) > MAX_BYTES:
        raise ErroNegocio("Arquivo grande demais (máximo 2 MB).")
    if not dados:
        raise ErroNegocio("Escolha um arquivo.")
    nome = (nome or "").lower()
    if dados[:2] == b"PK" or nome.endswith(".xlsx"):
        linhas = _ler_xlsx(dados)
    elif nome.endswith((".csv", ".txt")) or not nome.endswith(".xls"):
        linhas = _ler_csv(dados)
    else:
        raise ErroNegocio("Salve a planilha como .xlsx ou .csv e envie de novo.")
    linhas = [l for l in linhas if any(c for c in l)]
    if len(linhas) < 2:
        raise ErroNegocio("A planilha precisa ter o cabeçalho e pelo menos uma linha.")
    campos = [ALIAS.get(_norm(c)) for c in linhas[0]]
    if "nome" not in campos:
        raise ErroNegocio("Não encontrei a coluna \"nome\" no cabeçalho da planilha.")
    saida = []
    for i, l in enumerate(linhas[1:], start=2):
        d: dict = {"_linha": i}
        for campo, v in zip(campos, l):
            if campo and v and campo not in d:
                d[campo] = v
        saida.append(d)
    if len(saida) > MAX_LINHAS:
        raise ErroNegocio(f"Máximo de {MAX_LINHAS} linhas por envio. Divida a planilha.")
    return saida


def modelo_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(CABECALHO_MODELO)
    w.writerows(EXEMPLO_MODELO)
    return "﻿" + buf.getvalue()


# ---------------------------------------------------------------- importação


def _numero(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v.replace(",", "."))
    except ValueError:
        return None


def _geocodificar(endereco: str, cidade: str | None) -> tuple[float, float] | None:
    consulta = ", ".join(x for x in (endereco, cidade, "Brasil") if x)
    try:
        with _http() as c:
            r = c.get("https://nominatim.openstreetmap.org/search", params={"q": consulta, "format": "json", "limit": 1, "countrycodes": "br"})
        r.raise_for_status()
        achado = r.json()
        return (float(achado[0]["lat"]), float(achado[0]["lon"])) if achado else None
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def importar(s: SessaoORM, u: Usuario, nome_arquivo: str, dados: bytes, tipo_padrao: str, geocodificar: bool = True) -> dict:
    locais._exigir_admin(u)
    if tipo_padrao not in locais.TIPOS:
        raise ErroNegocio("Escolha o tipo dos locais da planilha.")
    linhas = ler_planilha(nome_arquivo, dados)
    codigos = {_norm(m.nome): m.codigo for m in s.scalars(select(Modalidade))} | {_norm(c): c for c in s.scalars(select(Modalidade.codigo))}
    existentes = {(n.lower(), e.lower()) for n, e in s.execute(select(Local.nome, Local.endereco))}
    criados, ignorados, erros, buscas = 0, 0, [], 0
    for d in linhas:
        n = d["_linha"]
        tipo = TIPOS_TEXTO.get(_norm(d.get("tipo", "")), tipo_padrao if not d.get("tipo") else None)
        if tipo is None:
            erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": f"Tipo \"{d['tipo']}\" desconhecido (use publico, loja ou arena)."})
            continue
        chave = ((d.get("nome") or "").strip().lower(), (d.get("endereco") or "").strip().lower())
        if chave in existentes:
            ignorados += 1
            continue
        lat, lng = _numero(d.get("latitude")), _numero(d.get("longitude"))
        if (lat is None or lng is None) and d.get("endereco") and geocodificar:
            if buscas >= MAX_GEOCODIFICAR:
                erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": "Limite de localizações automáticas do envio; informe latitude e longitude ou envie em outro lote."})
                continue
            if buscas:
                _pausa(1.1)
            buscas += 1
            ponto = _geocodificar(d["endereco"], d.get("cidade"))
            lat, lng = ponto if ponto else (None, None)
            if ponto is None:
                erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": "Não consegui localizar o endereço no mapa; informe latitude e longitude."})
                continue
        esportes = [codigos[x] for x in (_norm(p) for p in re.split(r"[,;/|]", d.get("esportes", ""))) if x in codigos]
        try:
            loc = locais.montar(s, tipo=tipo, nome=d.get("nome"), endereco=d.get("endereco"), cidade=d.get("cidade"), latitude=lat, longitude=lng,
                                horario_funcionamento=d.get("horario_funcionamento"), telefone=d.get("telefone"), email=d.get("email"), site=d.get("site"),
                                regras=d.get("regras"), descricao=d.get("descricao"), modalidades=esportes, criado_por_id=u.id)
        except ErroNegocio as e:
            erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": str(e)})
            continue
        s.add(loc)
        existentes.add(chave)
        criados += 1
    s.flush()
    auditoria.registrar(s, u.id, "local_importar", "local", None, criados=criados, ignorados=ignorados, erros=len(erros), arquivo=(nome_arquivo or "")[:80])
    s.commit()
    return {"criados": criados, "ignorados": ignorados, "erros": erros, "total": len(linhas)}


def contagem(s: SessaoORM) -> int:
    return s.scalar(select(func.count()).select_from(Local)) or 0
