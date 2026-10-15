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


def processar_capa(dados: bytes) -> bytes:
    """Arte ou logo do campeonato/atividade: só imagem, recortada em 16:9 (1600x900), JPEG sem metadados. PNG com fundo transparente vira fundo branco."""
    achado = detectar(dados)
    if achado is None or achado[0] != "foto":
        raise ErroNegocio("Envie uma imagem (JPG, PNG ou WebP) para a capa.")
    if len(dados) > settings.limite_foto_mb * 1024 * 1024:
        raise ErroNegocio(f"A imagem passa do limite de {settings.limite_foto_mb} MB.")
    try:
        img = Image.open(io.BytesIO(dados))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ErroNegocio("Não foi possível ler essa imagem. Tente outra.") from None
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        fundo = Image.new("RGB", img.size, "white")
        fundo.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
        img = fundo
    img = ImageOps.fit(img.convert("RGB"), (LARGURA_CAPA, ALTURA_CAPA))
    saida = io.BytesIO()
    img.save(saida, "JPEG", quality=86, optimize=True)
    return saida.getvalue()


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
