"""Importação de planilhas (CSV ou Excel .xlsx) para o guia de locais.

O tipo (local público, arena, clube/academia ou loja) pode ser escolhido no envio ou **detectado** pela categoria do Google Maps.
A planilha pode trazer redes sociais (Instagram, Facebook, site), o link do Google Maps (de onde saem as coordenadas), a categoria e
a avaliação. Sem coordenadas, o endereço é localizado (OpenStreetMap/Nominatim, 1 por segundo, no máximo `MAX_GEOCODIFICAR` por envio).
Cada linha é validada pelas regras do cadastro; as boas entram e as ruins voltam com o motivo. Locais sem horário ou telefone entram
marcados como "incompletos" (opção do envio) para a administração completar depois."""

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

CABECALHO_MODELO = ["tipo", "nome", "endereco", "cidade", "telefone", "horario_funcionamento", "email", "site", "instagram", "facebook", "link_do_maps", "regras", "descricao", "esportes", "latitude", "longitude"]
EXEMPLO_MODELO = [
    ["publico", "Praça Ary Coelho", "Av. Afonso Pena, s/n - Centro", "Campo Grande", "(67) 3000-0000", "Todos os dias: 6h às 22h", "", "", "", "", "", "Não é permitido som alto após as 21h", "Quadra poliesportiva aberta", "futsal, basquete", "", ""],
    ["loja", "Loja do Atleta", "Rua 14 de Julho, 100", "Campo Grande", "(67) 99999-0000", "Seg a sáb: 9h às 19h", "contato@loja.com.br", "https://lojadoatleta.com.br", "@lojadoatleta", "", "", "", "Material esportivo em geral", "", "", ""],
]

# nome de coluna aceito (já normalizado) -> campo
ALIAS = {
    "tipo": "tipo", "tipodolocal": "tipo", "nome": "nome", "local": "nome", "endereco": "endereco", "logradouro": "endereco",
    "cidade": "cidade", "municipio": "cidade", "telefone": "telefone", "fone": "telefone", "celular": "telefone", "whatsapp": "telefone",
    "horario": "horario_funcionamento", "horarios": "horario_funcionamento", "horariodefuncionamento": "horario_funcionamento",
    "horariofuncionamento": "horario_funcionamento", "funcionamento": "horario_funcionamento", "email": "email",
    "site": "site", "website": "site", "paginaweb": "site", "instagram": "instagram", "insta": "instagram", "facebook": "facebook",
    "linkdomaps": "maps_url", "linkmaps": "maps_url", "maps": "maps_url", "googlemaps": "maps_url", "mapa": "maps_url", "urlmaps": "maps_url",
    "categorianomaps": "categoria_maps", "categoriamaps": "categoria_maps", "categoriadomaps": "categoria_maps",
    "avaliacao": "avaliacao", "nota": "avaliacao", "busca": "busca",
    "regras": "regras", "regrasdeutilizacao": "regras", "regrasdeuso": "regras", "descricao": "descricao",
    "esportes": "esportes", "modalidades": "esportes", "esporte": "esportes", "latitude": "latitude", "lat": "latitude",
    "longitude": "longitude", "lng": "longitude", "lon": "longitude", "long": "longitude",
}
TIPOS_TEXTO = {
    "publico": "publico", "localpublico": "publico", "praca": "publico", "parque": "publico", "quadrapublica": "publico",
    "loja": "loja", "lojademateriais": "loja", "lojadematerialesportivo": "loja", "materialesportivo": "loja",
    "arena": "arena", "arenaparceira": "arena", "quadra": "arena", "arenaparticular": "arena",
    "clube": "clube", "academia": "clube", "escola": "clube", "clubeacademiaouescola": "clube",
}

# Classificação por categoria do Google Maps e nome (tudo sem acento, minúsculo)
_DESCARTA_CATEGORIA = (
    "criador de cavalos", "organizacao de eventos", "prefeitura", "bar esportivo", "hospitalar", "domestic", "ferramenta", "moveis",
    "produtos naturais", "loja de armas", "militar", "roupas de trabalho", "papelaria",
)
_LOJA_SO_COM_NOME_ESPORTIVO = ("loja", "loja de roupa", "loja de calcado", "loja de roupas de praia", "loja de departamento", "shopping center")
_NOME_ESPORTIVO = ("esport", "sport", "fit", "atlet", "running", "corrida", "bike", "ciclo", "futebol", "volei", "tenis", "tennis", "nutri", "suplement", "skate", "surf", "gym", "treino", "kimono", "fight")
_PUBLICO_NOME = ("municipal", "praca", "parque", "funec", "funcespp", "centro esportivo", "poliesportivo")
_PUBLICO_CATEGORIA = ("parque", "playground", "praca", "estadio", "recreacao")
_ARENA_NOME = ("arena", "society", "quadra", "campo", "beach", "areia", "futevolei", "soccer")
_CLUBE_NOME = ("academia", "fitness", "studio", "crossfit", "cross", "box", "centro de treinamento", "ct ", "pilates", "natacao", "jiu", "muay", "sesc", "clube", "associacao")
_CLUBE_CATEGORIA = ("academia", "clube", "escola", "fitness", "ginastica", "treinamento", "treino", "centro de esportes", "crossfit", "pilates", "natacao")

# palavras da categoria/nome -> código da modalidade (as que existirem no banco)
_ESPORTES = (
    ("beach tennis", "beach_tennis"), ("beach-tennis", "beach_tennis"), ("futevolei", "futevolei"), ("volei de praia", "volei_de_areia"),
    ("volei de areia", "volei_de_areia"), ("praia", "volei_de_areia"), ("volei", "volei"), ("futsal", "futsal"), ("society", "futebol"),
    ("futebol", "futebol"), ("campo", "futebol"), ("basquete", "basquete"), ("tenis", "tenis"), ("padel", "padel"),
)


def _http() -> httpx.Client:
    """Os testes substituem esta função por um transporte simulado."""
    return httpx.Client(timeout=15, headers={"User-Agent": "PlayGo/1.0 (guia de locais)"})


def _pausa(segundos: float) -> None:
    time.sleep(segundos)


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", t)


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()


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


# ---------------------------------------------------------------- interpretação das linhas


def motivo_descarte(categoria: str | None, nome: str | None) -> str | None:
    """Resultados de busca trazem lojas e órgãos que nada têm de esporte: ficam de fora (e aparecem no relatório)."""
    cat, nm = _sem_acento(categoria or ""), _sem_acento(nome or "")
    if any(p in cat for p in _DESCARTA_CATEGORIA) and not any(p in nm for p in ("esport", "sport", "skate", "shop")):
        return f"Categoria fora do tema esportivo ({categoria})."
    if cat in _LOJA_SO_COM_NOME_ESPORTIVO and not any(p in nm for p in _NOME_ESPORTIVO):
        return f"Loja genérica sem indício de material esportivo ({categoria})."
    return None


def inferir_tipo(categoria: str | None, busca: str | None = None, nome: str | None = None) -> str:
    """publico | arena | clube | loja, pela categoria do Google Maps e pelo nome (o nome vence quando diz "Arena ...")."""
    cat, nm, bq = _sem_acento(categoria or ""), _sem_acento(nome or ""), _sem_acento(busca or "")
    if cat.startswith(("loja", "shopping", "papelaria")) or (not cat and "loja" in bq) or any(p in nm for p in ("artigos esportivos", "materiais esportivos", " shop", "store")):
        return "loja"
    if any(p in nm for p in _PUBLICO_NOME) or any(p in cat for p in _PUBLICO_CATEGORIA):
        return "publico"
    if any(p in nm for p in _ARENA_NOME):
        return "arena"
    if "ginasio" in nm or "ginasio" in cat or "poliesportivo" in cat:
        return "publico"
    if any(p in nm for p in _CLUBE_NOME) or any(p in cat for p in _CLUBE_CATEGORIA):
        return "clube"
    return "arena"


def inferir_esportes(categoria: str | None, nome: str | None, codigos: set[str]) -> list[str]:
    texto = _sem_acento(f"{categoria or ''} {nome or ''}")
    achados: list[str] = []
    for palavra, codigo in _ESPORTES:
        if palavra in texto and codigo in codigos and codigo not in achados:
            achados.append(codigo)
    # "volei de praia" já cobre "praia"/"volei": não repete o vôlei comum quando o de areia foi achado
    if "volei_de_areia" in achados and "volei" in achados and "volei de praia" in texto:
        achados.remove("volei")
    return achados


def _avaliacao(texto: str | None) -> tuple[float | None, int | None]:
    """"5,0 (16)" -> (5.0, 16)."""
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)\s*(?:\(\s*([\d.]+)\s*\))?", texto or "")
    if not m:
        return None, None
    nota = float(m.group(1).replace(",", "."))
    qtd = int(m.group(2).replace(".", "")) if m.group(2) else None
    return (nota, qtd) if 0 <= nota <= 5 else (None, None)


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


# ---------------------------------------------------------------- importação


def importar(s: SessaoORM, u: Usuario, nome_arquivo: str, dados: bytes, tipo_padrao: str, geocodificar: bool = True, permitir_incompleto: bool = True) -> dict:
    locais._exigir_admin(u)
    if tipo_padrao != "auto" and tipo_padrao not in locais.TIPOS:
        raise ErroNegocio("Escolha o tipo dos locais da planilha (ou detectar pela categoria do Maps).")
    linhas = ler_planilha(nome_arquivo, dados)
    modalidades = list(s.scalars(select(Modalidade)))
    codigos = {m.codigo for m in modalidades}
    por_nome = {_norm(m.nome): m.codigo for m in modalidades} | {_norm(c): c for c in codigos}
    existentes = {(n.lower(), e.lower()) for n, e in s.execute(select(Local.nome, Local.endereco))}
    criados, ignorados, erros, buscas, incompletos, descartados = 0, 0, [], 0, 0, []
    por_tipo: dict[str, int] = {}
    for d in linhas:
        n = d["_linha"]
        tipo = TIPOS_TEXTO.get(_norm(d.get("tipo", ""))) if d.get("tipo") else None
        if d.get("tipo") and tipo is None:
            erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": f"Tipo \"{d['tipo']}\" desconhecido (use publico, arena, clube ou loja)."})
            continue
        if tipo is None:
            tipo = inferir_tipo(d.get("categoria_maps"), d.get("busca"), d.get("nome")) if tipo_padrao == "auto" else tipo_padrao
        if tipo_padrao == "auto" and not d.get("tipo"):
            motivo = motivo_descarte(d.get("categoria_maps"), d.get("nome"))
            if motivo:
                descartados.append({"linha": n, "nome": d.get("nome", ""), "motivo": motivo})
                continue
        chave = ((d.get("nome") or "").strip().lower(), (d.get("endereco") or "").strip().lower())
        if chave in existentes:
            ignorados += 1
            continue
        lat, lng = _numero(d.get("latitude")), _numero(d.get("longitude"))
        if lat is None or lng is None:
            coords = locais.coordenadas_do_maps(d.get("maps_url"))
            lat, lng = coords if coords else (None, None)
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
                erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": "Não consegui localizar o endereço no mapa; informe latitude e longitude (ou o link do Google Maps)."})
                continue
        esportes = [por_nome[x] for x in (_norm(p) for p in re.split(r"[,;/|]", d.get("esportes", ""))) if x in por_nome]
        if not esportes:
            esportes = inferir_esportes(d.get("categoria_maps"), d.get("nome"), codigos)
        nota, qtd = _avaliacao(d.get("avaliacao"))
        try:
            loc = locais.montar(
                s, criado_por_id=u.id, exigir_contato=not permitir_incompleto, tipo=tipo, nome=d.get("nome"), endereco=d.get("endereco"), cidade=d.get("cidade"),
                latitude=lat, longitude=lng, horario_funcionamento=d.get("horario_funcionamento"), telefone=d.get("telefone"), email=d.get("email"),
                site=d.get("site"), instagram=d.get("instagram"), facebook=d.get("facebook"), maps_url=d.get("maps_url"), categoria_maps=d.get("categoria_maps"),
                avaliacao=nota, avaliacoes=qtd, regras=d.get("regras"), descricao=d.get("descricao"), modalidades=esportes,
            )
        except ErroNegocio as e:
            erros.append({"linha": n, "nome": d.get("nome", ""), "motivo": str(e)})
            continue
        s.add(loc)
        existentes.add(chave)
        criados += 1
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
        incompletos += 0 if (loc.horario_funcionamento and loc.telefone) else 1
    s.flush()
    auditoria.registrar(s, u.id, "local_importar", "local", None, criados=criados, ignorados=ignorados, erros=len(erros), arquivo=(nome_arquivo or "")[:80])
    s.commit()
    return {"criados": criados, "ignorados": ignorados, "erros": erros, "total": len(linhas), "incompletos": incompletos, "por_tipo": por_tipo, "descartados": descartados}


def contagem(s: SessaoORM) -> int:
    return s.scalar(select(func.count()).select_from(Local)) or 0
