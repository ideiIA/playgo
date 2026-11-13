"""Fotos e vídeos das publicações. O tipo é decidido pelos primeiros bytes (nunca pelo nome ou pelo
Content-Type enviado). Fotos são reencodadas: some o EXIF, inclusive o GPS da câmera (LGPD), e nasce a
miniatura. Os arquivos ficam fora de /static: só saem pela rota autenticada ou por URL assinada."""

import hashlib
import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from itsdangerous import BadSignature, URLSafeTimedSerializer
from PIL import Image, ImageOps, UnidentifiedImageError

from . import armazenamento, seguranca
from .config import settings
from .db import agora
from .erros import ErroNegocio

LADO_MAX = 2048
LADO_MINIATURA = 480
VALIDADE_URL_S = 3600


@dataclass
class Recebida:
    tipo: str  # foto | video
    mime: str
    extensao: str
    dados: bytes
    miniatura: bytes | None = None


def detectar(dados: bytes) -> tuple[str, str, str] | None:
    """(tipo, mime, extensão) pelos bytes iniciais, ou None se não for foto/vídeo aceito."""
    if dados[:3] == b"\xff\xd8\xff":
        return "foto", "image/jpeg", "jpg"
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return "foto", "image/png", "png"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "foto", "image/webp", "webp"
    if dados[4:8] == b"ftyp":
        return "video", ("video/quicktime" if dados[8:12] == b"qt  " else "video/mp4"), ("mov" if dados[8:12] == b"qt  " else "mp4")
    if dados[:4] == b"\x1a\x45\xdf\xa3":
        return "video", "video/webm", "webm"
    return None


def processar(dados: bytes) -> Recebida:
    achado = detectar(dados)
    if achado is None:
        raise ErroNegocio("Arquivo não aceito. Envie fotos (JPG, PNG, WebP) ou vídeos (MP4, MOV, WebM).")
    tipo, mime, ext = achado
    teto_mb = settings.limite_foto_mb if tipo == "foto" else settings.limite_video_mb
    if len(dados) > teto_mb * 1024 * 1024:
        raise ErroNegocio(f"O arquivo passa do limite de {teto_mb} MB para {'fotos' if tipo == 'foto' else 'vídeos'}.")
    if tipo == "video":
        return Recebida("video", mime, ext, dados)

    try:
        img = Image.open(io.BytesIO(dados))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ErroNegocio("Não foi possível ler essa imagem. Tente outra foto.") from None
    img = ImageOps.exif_transpose(img)  # respeita a rotação da câmera antes de descartar o EXIF
    tem_alfa = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if tem_alfa else "RGB")
    img.thumbnail((LADO_MAX, LADO_MAX))
    saida = io.BytesIO()
    if tem_alfa:
        img.save(saida, "PNG", optimize=True)
        mime, ext = "image/png", "png"
    else:
        img.save(saida, "JPEG", quality=85, optimize=True)  # sem exif=…: nada de metadados
        mime, ext = "image/jpeg", "jpg"
    mini = img.copy()
    if tem_alfa:
        fundo = Image.new("RGB", mini.size, "white")
        fundo.paste(mini, mask=mini.split()[3])
        mini = fundo
    mini.thumbnail((LADO_MINIATURA, LADO_MINIATURA))
    mbuf = io.BytesIO()
    mini.save(mbuf, "JPEG", quality=80, optimize=True)
    return Recebida("foto", mime, ext, saida.getvalue(), mbuf.getvalue())


LADO_AVATAR = 400


def processar_avatar(dados: bytes) -> bytes:
    """Foto de perfil: só imagem, recortada em quadrado 400x400, JPEG sem nenhum metadado (EXIF/GPS)."""
    achado = detectar(dados)
    if achado is None or achado[0] != "foto":
        raise ErroNegocio("Envie uma foto (JPG, PNG ou WebP).")
    if len(dados) > settings.limite_foto_mb * 1024 * 1024:
        raise ErroNegocio(f"A foto passa do limite de {settings.limite_foto_mb} MB.")
    try:
        img = Image.open(io.BytesIO(dados))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ErroNegocio("Não foi possível ler essa imagem. Tente outra foto.") from None
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        fundo = Image.new("RGB", img.size, "white")
        fundo.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
        img = fundo
    img = ImageOps.fit(img.convert("RGB"), (LADO_AVATAR, LADO_AVATAR))
    saida = io.BytesIO()
    img.save(saida, "JPEG", quality=85, optimize=True)
    return saida.getvalue()


LARGURA_CAPA, ALTURA_CAPA = 1600, 900


AJUSTES_CAPA = ("recortar", "auto", "inteira")  # como a arena quer enquadrar uma foto fora do padrão 16:9
PROPORCAO_CAPA = LARGURA_CAPA / ALTURA_CAPA
LARGURA_IDEAL_CAPA = 1200  # abaixo disso a capa pode ficar sem nitidez nos cartões grandes


def _abrir_imagem(dados: bytes, manter_transparencia: bool = False) -> Image.Image:
    """Valida (tipo pelos bytes, tamanho), abre, endireita pelo EXIF e tira a transparência (a não ser que `manter_transparencia`, para logos).
    Nunca confia no nome do arquivo."""
    achado = detectar(dados)
    if achado is None or achado[0] != "foto":
        raise ErroNegocio("Envie uma imagem (JPG, PNG ou WebP).")
    if len(dados) > settings.limite_foto_mb * 1024 * 1024:
        raise ErroNegocio(f"A imagem passa do limite de {settings.limite_foto_mb} MB.")
    try:
        img = Image.open(io.BytesIO(dados))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ErroNegocio("Não foi possível ler essa imagem. Tente outra.") from None
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        if manter_transparencia:
            return img.convert("RGBA")
        fundo = Image.new("RGB", img.size, "white")
        fundo.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
        return fundo
    return img.convert("RGBA" if manter_transparencia else "RGB")


LADO_LOGO = 400


def processar_logo(dados: bytes) -> bytes:
    """Logo/brasão da arena: a imagem INTEIRA (sem cortar) centralizada num quadrado 400x400 com fundo transparente, PNG sem metadados."""
    img = _abrir_imagem(dados, manter_transparencia=True)
    img.thumbnail((LADO_LOGO, LADO_LOGO), Image.LANCZOS)
    tela = Image.new("RGBA", (LADO_LOGO, LADO_LOGO), (0, 0, 0, 0))
    tela.paste(img, ((LADO_LOGO - img.width) // 2, (LADO_LOGO - img.height) // 2))
    saida = io.BytesIO()
    tela.save(saida, "PNG", optimize=True)
    return saida.getvalue()


def salvar_logo(png: bytes) -> str:
    """Grava em logos/<32 hex>.png e devolve só o nome."""
    nome = uuid.uuid4().hex + ".png"
    armazenamento.salvar(f"logos/{nome}", png, "image/png")
    return nome


def _perda_do_corte(largura: int, altura: int) -> float:
    """Parte da foto (0 a 1) que o corte 16:9 descarta."""
    r = largura / altura
    return 1 - (PROPORCAO_CAPA / r if r > PROPORCAO_CAPA else r / PROPORCAO_CAPA)


def analisar_capa(dados: bytes) -> dict:
    """O que a arena precisa saber antes de enviar a capa: tamanho, proporção, quanto o corte 16:9 descartaria e se está dentro do padrão."""
    img = _abrir_imagem(dados)
    larg, alt = img.size
    perda = _perda_do_corte(larg, alt)
    avisos = []
    if alt > larg:
        avisos.append("A foto está na vertical: para virar capa (horizontal, 16:9) uma parte grande dela seria cortada.")
    elif perda > 0.06:
        avisos.append(f"A proporção da foto ({larg}×{alt}) é diferente de 16:9: o corte descartaria cerca de {round(perda * 100)}% da imagem.")
    if larg < LARGURA_IDEAL_CAPA:
        avisos.append(f"A foto é pequena ({larg}×{alt}). Para ficar nítida use pelo menos {LARGURA_IDEAL_CAPA} px de largura.")
    return {"largura": larg, "altura": alt, "perda_corte_pct": round(perda * 100), "dentro_do_padrao": not avisos, "avisos": avisos}


def _janela_de_foco(img: Image.Image, alvo_larg: int, alvo_alt: int) -> tuple[int, int, int, int]:
    """Janela de corte (proporção do alvo) sobre a região mais "rica" da foto (mais detalhe/contraste), em vez do centro puro.
    Só escolhe onde cortar: não inventa nem altera pixels. Sem biblioteca de visão: soma de bordas por faixa."""
    from PIL import ImageFilter

    larg, alt = img.size
    r = alvo_larg / alvo_alt
    if larg / alt > r:  # foto mais larga que o alvo: corta dos lados
        jan_larg, jan_alt = round(alt * r), alt
    else:  # mais alta: corta em cima e embaixo
        jan_larg, jan_alt = larg, round(larg / r)
    horizontal = jan_larg < larg
    base = min(larg, 320)
    pequena = img.convert("L").resize((max(1, base), max(1, round(base * alt / larg))))
    bordas = pequena.filter(ImageFilter.FIND_EDGES)
    n = bordas.width if horizontal else bordas.height
    faixas = list(bordas.resize((n, 1), Image.BOX).tobytes()) if horizontal else list(bordas.resize((1, n), Image.BOX).tobytes())
    janela = max(1, round(n * (jan_larg / larg if horizontal else jan_alt / alt)))
    if janela >= n:
        return 0, 0, larg, alt
    soma = sum(faixas[:janela])
    melhor, pos = -1.0, 0
    for i in range(0, n - janela + 1):
        if i:
            soma += faixas[i + janela - 1] - faixas[i - 1]
        centro = abs((i + janela / 2) - n / 2) / (n / 2)
        pontos = soma * (1 - 0.15 * centro)  # leve preferência pelo centro: o foco não "foge" para a borda
        if pontos > melhor:
            melhor, pos = pontos, i
    if horizontal:
        x = min(round(pos * larg / n), larg - jan_larg)
        return x, 0, x + jan_larg, alt
    y = min(round(pos * alt / n), alt - jan_alt)
    return 0, y, larg, y + jan_alt


def _enquadrar(img: Image.Image, ajuste: str, largura: int, altura: int) -> Image.Image:
    from PIL import ImageFilter

    if ajuste == "inteira":  # nenhuma parte da foto é cortada nem esticada: ela inteira sobre um fundo feito dela mesma, desfocado
        fundo = ImageOps.fit(img, (largura, altura)).filter(ImageFilter.GaussianBlur(max(6, largura // 60)))
        fundo = Image.blend(fundo, Image.new("RGB", fundo.size, (20, 33, 61)), 0.25)
        frente = img.copy()
        frente.thumbnail((largura, altura), Image.LANCZOS)
        fundo.paste(frente, ((largura - frente.width) // 2, (altura - frente.height) // 2))
        return fundo
    if ajuste == "auto":  # corte guiado pelo foco + nitidez leve se a foto precisou ser ampliada
        corte = img.crop(_janela_de_foco(img, largura, altura))
        ampliada = corte.width < largura
        corte = corte.resize((largura, altura), Image.LANCZOS)
        return corte.filter(ImageFilter.UnsharpMask(radius=1.4, percent=55, threshold=3)) if ampliada else corte
    return ImageOps.fit(img, (largura, altura))  # recortar: centro, como sempre foi


def processar_capa(dados: bytes, recortar: bool = True, ajuste: str = "recortar", largura: int = LARGURA_CAPA, altura: int = ALTURA_CAPA, qualidade: int = 86) -> bytes:
    """Arte ou logo do campeonato/atividade e capa da arena: só imagem, em 16:9 (1600x900), JPEG sem metadados. PNG transparente vira fundo branco.
    `ajuste` (só a capa da arena escolhe): recortar (centro), auto (corte no foco + nitidez) ou inteira (sem cortar, fundo desfocado).
    Com `recortar=False` (galeria da arena) a foto mantém a proporção e só é reduzida para caber em 1600 px."""
    if ajuste not in AJUSTES_CAPA:
        raise ErroNegocio("Escolha como ajustar a capa: recortar, ajuste automático ou foto inteira.")
    img = _abrir_imagem(dados)
    if recortar:
        img = _enquadrar(img, ajuste, largura, altura)
    else:
        img.thumbnail((LARGURA_CAPA, LARGURA_CAPA))
    saida = io.BytesIO()
    img.save(saida, "JPEG", quality=qualidade, optimize=True)
    return saida.getvalue()


def previas_capa(dados: bytes) -> dict:
    """Os três enquadramentos em miniatura (640x360), para a arena comparar e decidir antes de salvar. Nada é gravado."""
    import base64

    return {a: "data:image/jpeg;base64," + base64.b64encode(processar_capa(dados, ajuste=a, largura=640, altura=360, qualidade=72)).decode() for a in AJUSTES_CAPA}


def salvar_capa(jpeg: bytes) -> str:
    """Grava em uploads/capas/<32 hex>.jpg e devolve só o nome (a URL é a 'chave')."""
    nome = uuid.uuid4().hex + ".jpg"
    armazenamento.salvar(f"capas/{nome}", jpeg, "image/jpeg")
    return nome


def salvar_avatar(jpeg: bytes) -> str:
    """Grava em uploads/perfil/<32 hex>.jpg (nome aleatório: a URL é a 'chave') e devolve só o nome."""
    nome = uuid.uuid4().hex + ".jpg"
    armazenamento.salvar(f"perfil/{nome}", jpeg, "image/jpeg")
    return nome


def ler_upload(arquivo) -> bytes:
    """Lê o UploadFile sem deixar um arquivo gigante encher a memória."""
    teto_mb = max(settings.limite_foto_mb, settings.limite_video_mb)
    dados = arquivo.file.read(teto_mb * 1024 * 1024 + 1)
    if len(dados) > teto_mb * 1024 * 1024:
        raise ErroNegocio(f"O arquivo passa do limite de {teto_mb} MB.")
    return dados


def salvar(rec: Recebida) -> tuple[str, str | None, str]:
    """Grava em uploads/AAAA/MM/ e devolve (arquivo, miniatura, sha256), caminhos relativos à pasta."""
    n = agora()
    pasta_rel = Path(f"{n.year:04d}") / f"{n.month:02d}"
    nome = uuid.uuid4().hex
    rel = pasta_rel / f"{nome}.{rec.extensao}"
    armazenamento.salvar(rel.as_posix(), rec.dados, rec.mime)
    mini_rel = None
    if rec.miniatura:
        mini_rel = pasta_rel / f"{nome}_t.jpg"
        armazenamento.salvar(mini_rel.as_posix(), rec.miniatura, "image/jpeg")
    return rel.as_posix(), (mini_rel.as_posix() if mini_rel else None), hashlib.sha256(rec.dados).hexdigest()


def caminho(rel: str) -> Path:
    """Caminho absoluto, garantindo que continua dentro da pasta de uploads."""
    base = settings.pasta_uploads.resolve()
    alvo = (base / rel).resolve()
    if base not in alvo.parents:
        raise ErroNegocio("Caminho inválido.")
    return alvo


def ler(rel: str) -> bytes:
    return armazenamento.ler(rel)


def remover_arquivos(*rels: str | None) -> None:
    for rel in rels:
        armazenamento.remover(rel)


# ---- URLs assinadas: o <img>/<video> não manda o token Bearer do app, então a API entrega a URL já autorizada

def _serializador() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(seguranca.chave_sessao(), salt="playgo-midia")


def url(midia_id: int, usuario_id: int, miniatura: bool = False) -> str:
    token = _serializador().dumps({"m": midia_id, "u": usuario_id})
    return f"/midia/{midia_id}{'/miniatura' if miniatura else ''}?t={token}"


def usuario_do_token(token: str, midia_id: int) -> int | None:
    try:
        d = _serializador().loads(token, max_age=VALIDADE_URL_S)
    except BadSignature:
        return None
    return d["u"] if d.get("m") == midia_id else None
