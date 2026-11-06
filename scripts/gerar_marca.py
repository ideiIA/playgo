"""Gera a identidade visual do PlayGo: símbolo (bola de futebol com rastro de movimento), ícone do app, favicon, logo horizontal e imagem de compartilhamento.

Uso: .venv\\Scripts\\python scripts\\gerar_marca.py          (precisa de Pillow e fonttools: pip install pillow fonttools brotli)
Saída: playgo/web/static/marca/*.svg|png|ico  e  playgo/app/icone-192.png, icone-512.png, apple-touch-icon-novo.png

Conceito ("Logo 2A", escolhida no design system): a bola de futebol que todo mundo reconhece (branca, pentágono central e gomos em tinta)
com o rastro verde-limão por baixo, que é o "Go". A geometria fica nas constantes abaixo (grade 120 × 120) e alimenta o SVG e o PNG,
que ficam idênticos. O texto "PlayGo" é Outfit ExtraBold; no SVG ele vai convertido em curvas (sem depender de fonte instalada)."""

import math
from itertools import pairwise
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
MARCA = RAIZ / "playgo" / "web" / "static" / "marca"
APP = RAIZ / "playgo" / "app"
FONTE = RAIZ / "scripts" / "fontes" / "Outfit-wght.ttf"

ROXO, ROSA, LIMA, TINTA, BRANCO = (108, 76, 255), (255, 61, 129), (183, 243, 75), (20, 33, 61), (255, 255, 255)
HEX = {"roxo": "#6c4cff", "rosa": "#ff3d81", "lima": "#b7f34b", "tinta": "#14213d", "branco": "#ffffff"}

# grade do símbolo: 120 × 120
LADO = 120
BOLA = (62, 58, 40)  # cx, cy, r
ESP = 3  # espessura do contorno e dos gomos
RASTRO = ((16, 76), (66, 108), 48, 9)  # ponto inicial, ponto final, raio do arco, espessura (verde-limão, pontas redondas)
RAIO_CANTO = 0.2  # proporção do lado do ícone (iOS/Android arredondam sozinhos; favicon e site usam este)


def _poligono(cx: float, cy: float, raio: float, giro: float) -> list[tuple[float, float]]:
    return [(cx + raio * math.cos(math.radians(giro + 72 * i)), cy + raio * math.sin(math.radians(giro + 72 * i))) for i in range(5)]


def padrao() -> tuple[list, list, list]:
    """Pentágono central, 5 costuras e 5 pentágonos nas bordas (recortados pelo círculo da bola)."""
    cx, cy, r = BOLA
    k = r / 138
    central = _poligono(cx, cy, 44 * k, -90)
    costuras, bordas = [], []
    for i in range(5):
        ang = -90 + 72 * i
        ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        costuras.append(((cx + ux * 44 * k, cy + uy * 44 * k), (cx + ux * 96 * k, cy + uy * 96 * k)))
        bordas.append(_poligono(cx + ux * 150 * k, cy + uy * 150 * k, 54 * k, ang + 180))
    return central, costuras, bordas


def _centro_arco() -> tuple[float, float]:
    """Centro do arco do rastro (o do lado da bola), para desenhar o mesmo traço no PNG."""
    (x0, y0), (x1, y1), r, _ = RASTRO
    mx, my, dx, dy = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    h = math.sqrt(r * r - (d / 2) ** 2)
    px, py = -dy / d, dx / d  # perpendicular
    a, b = (mx + h * px, my + h * py), (mx - h * px, my - h * py)
    bx, by, _ = BOLA
    return a if math.hypot(a[0] - bx, a[1] - by) < math.hypot(b[0] - bx, b[1] - by) else b


# ---------------------------------------------------------------- SVG


def _pts(ps) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in ps)


def _bola_svg(contorno: str | None, gomos: str, id_clip: str, completa: bool = True, rastro: bool = True) -> str:
    cx, cy, r = BOLA
    central, costuras, bordas = padrao()
    ctn = f' stroke="{contorno}" stroke-width="{ESP}"' if contorno else ""
    out = f'<defs><clipPath id="{id_clip}"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath></defs><circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff"{ctn}/>'
    if completa:
        seams = " ".join(f"M{a[0]:.1f} {a[1]:.1f}L{b[0]:.1f} {b[1]:.1f}" for a, b in costuras)
        patches = "".join(f'<polygon points="{_pts(p)}"/>' for p in bordas)
        out += (f'<g clip-path="url(#{id_clip})" fill="{gomos}" stroke="{gomos}" stroke-width="{ESP}" stroke-linejoin="round" stroke-linecap="round">'
                f'<path d="{seams}" fill="none"/>{patches}</g>')
    out += f'<polygon points="{_pts(central)}" fill="{gomos}" stroke="{gomos}" stroke-width="{ESP}" stroke-linejoin="round"/>'
    if rastro:
        (x0, y0), (x1, y1), rr, esp = RASTRO
        out += f'<path d="M{x0} {y0}A{rr} {rr} 0 0 0 {x1} {y1}" fill="none" stroke="{HEX["lima"]}" stroke-width="{esp}" stroke-linecap="round"/>'
    return out


def svg_simbolo(tema: str = "claro") -> str:
    contorno = HEX["tinta"] if tema == "claro" else None
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LADO} {LADO}" width="{LADO * 4}" height="{LADO * 4}" role="img" aria-label="PlayGo">'
            f'{_bola_svg(contorno, HEX["tinta"], "c")}</svg>\n')


def svg_icone(completa: bool = True, arredondado: bool = True) -> str:
    """Quadrado com degradê roxo→rosa e a bola branca no meio (ícone do app e favicon). `completa=False`: só o pentágono, sem rastro (favicon)."""
    lado = 512
    ocupa = 0.8 if completa else 0.92
    k = lado * ocupa / LADO
    ox, oy = (lado - LADO * k) / 2, (lado - LADO * k) / 2
    rx = round(lado * RAIO_CANTO) if arredondado else 0
    corpo = _bola_svg(None, HEX["tinta"], "c", completa=completa, rastro=completa)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lado} {lado}" width="{lado}" height="{lado}" role="img" aria-label="PlayGo">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{HEX['roxo']}"/><stop offset="1" stop-color="{HEX['rosa']}"/></linearGradient></defs>
  <rect width="{lado}" height="{lado}" rx="{rx}" fill="url(#g)"/>
  <g transform="translate({ox:.1f} {oy:.1f}) scale({k:.4f})">{corpo}</g>
</svg>
'''


class _Palavra:
    """Letras do wordmark como curvas: Outfit ExtraBold (peso 800), espaçamento -0.04em."""

    def __init__(self) -> None:
        f = instantiateVariableFont(TTFont(FONTE), {"wght": 800})
        self.f, self.gs, self.cmap = f, f.getGlyphSet(), f.getBestCmap()
        self.upm = f["head"].unitsPerEm
        self.cap = getattr(f["OS/2"], "sCapHeight", 700) or 700

    def curvas(self, texto: str, tam: float, x: float, base: float, cor_por_letra) -> tuple[str, float]:
        escala, sp, saida = tam / self.upm, -0.04 * tam, ""
        for i, ch in enumerate(texto):
            nome = self.cmap[ord(ch)]
            pen = SVGPathPen(self.gs)
            self.gs[nome].draw(TransformPen(pen, (escala, 0, 0, -escala, x, base)))
            saida += f'<path d="{pen.getCommands()}" fill="{cor_por_letra(i)}"/>'
            x += self.f["hmtx"][nome][0] * escala + sp
        return saida, x

    def largura(self, texto: str, tam: float) -> float:
        escala = tam / self.upm
        return sum(self.f["hmtx"][self.cmap[ord(c)]][0] * escala - 0.04 * tam for c in texto)


def svg_horizontal(claro: bool = True) -> str:
    """Símbolo + 'PlayGo'. `claro=False` é para fundo escuro (Play branco, Go em verde-limão, bola sem contorno)."""
    p = _Palavra()
    tam, folga = LADO * 0.6, LADO * 0.16
    base = LADO / 2 + p.cap * tam / p.upm / 2
    play = HEX["tinta"] if claro else HEX["branco"]
    go = HEX["roxo"] if claro else HEX["lima"]
    letras, fim = p.curvas("PlayGo", tam, LADO + folga, base, lambda i: play if i < 4 else go)
    simbolo = _bola_svg(HEX["tinta"] if claro else None, HEX["tinta"], "c")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {fim + 4:.1f} {LADO}" width="{(fim + 4) * 4:.0f}" height="{LADO * 4}" role="img" aria-label="PlayGo">
  <g>{simbolo}</g>
  {letras}
</svg>
'''


# ---------------------------------------------------------------- PNG (mesma geometria, com suavização por superamostragem)


def _mascara(tam: tuple[int, int], desenho) -> Image.Image:
    m = Image.new("L", tam, 0)
    desenho(ImageDraw.Draw(m))
    return m


def simbolo_png(altura: int, contorno: bool = True, completa: bool = True, rastro: bool = True, ss: int = 4) -> Image.Image:
    """Bola transparente (quadrada, `altura` px). `contorno`: aro tinta fino (fundo claro). `completa`: gomos; senão só o pentágono central."""
    k = altura * ss / LADO
    tam = (round(LADO * k), round(LADO * k))
    img = Image.new("RGBA", tam, (0, 0, 0, 0))

    def camada(mascara: Image.Image, cor: tuple) -> None:
        c = Image.new("RGBA", tam, cor + (255,))
        c.putalpha(mascara)
        img.alpha_composite(c)

    cx, cy, r = BOLA
    elipse = lambda rr: (lambda d: d.ellipse([(cx - rr) * k, (cy - rr) * k, (cx + rr) * k, (cy + rr) * k], fill=255))
    if contorno:
        camada(_mascara(tam, elipse(r + ESP / 2)), TINTA)
    camada(_mascara(tam, elipse(r - ESP / 2 if contorno else r)), BRANCO)
    central, costuras, bordas = padrao()

    def traco(d, pontos, fechar=True):
        pp = [(x * k, y * k) for x, y in pontos]
        d.polygon(pp, fill=255) if fechar else None
        seg = list(pairwise(pp + pp[:1])) if fechar else list(pairwise(pp))
        for a, b in seg:
            d.line([a, b], fill=255, width=round(ESP * k))
        for x, y in pp:
            rr = ESP * k / 2
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=255)

    if completa:
        def gomos(d):
            for a, b in costuras:
                traco(d, [a, b], fechar=False)
            for p in bordas:
                traco(d, p)
        recorte = _mascara(tam, elipse(r))
        gm = _mascara(tam, gomos)
        camada(Image.composite(gm, Image.new("L", tam, 0), recorte), TINTA)
    camada(_mascara(tam, lambda d: traco(d, central)), TINTA)
    if rastro:
        (x0, y0), (x1, y1), rr, esp = RASTRO
        acx, acy = _centro_arco()
        ini = math.degrees(math.atan2(y0 - acy, x0 - acx)) % 360
        fim = math.degrees(math.atan2(y1 - acy, x1 - acx)) % 360
        a0, a1 = (fim, ini) if ini > fim else (ini, fim)

        def arco(d):
            re_ = rr + esp / 2  # o PIL desenha o traço para dentro do retângulo: o contorno externo fica em rr + esp/2
            d.arc([(acx - re_) * k, (acy - re_) * k, (acx + re_) * k, (acy + re_) * k], start=a0, end=a1, fill=255, width=round(esp * k))
            for x, y in ((x0, y0), (x1, y1)):
                q = esp * k / 2
                d.ellipse([x * k - q, y * k - q, x * k + q, y * k + q], fill=255)

        camada(_mascara(tam, arco), LIMA)
    return img.resize((altura, altura), Image.LANCZOS)


def _degrade(tam: int) -> Image.Image:
    pequeno = Image.new("RGB", (64, 64))
    px = pequeno.load()
    for j in range(64):
        for i in range(64):
            t = (i + j) / 126
            px[i, j] = tuple(round(a + (b - a) * t) for a, b in zip(ROXO, ROSA, strict=True))
    return pequeno.resize((tam, tam), Image.BICUBIC)


def icone_png(tam: int, arredondado: bool = True, completa: bool = True, ocupa: float = 0.8, ss: int = 4) -> Image.Image:
    S = tam * ss
    img = _degrade(S).convert("RGBA")
    sim = simbolo_png(round(S * ocupa), contorno=False, completa=completa, rastro=completa, ss=1)
    img.alpha_composite(sim, ((S - sim.width) // 2, (S - sim.height) // 2))
    if arredondado:
        canto = _mascara((S, S), lambda d: d.rounded_rectangle([0, 0, S - 1, S - 1], radius=round(S * RAIO_CANTO), fill=255))
        img.putalpha(canto)
    return img.resize((tam, tam), Image.LANCZOS)


def _fonte_outfit(tam: int, peso: int = 800):
    f = ImageFont.truetype(str(FONTE), tam)
    f.set_variation_by_axes([peso])
    return f


def logo_horizontal_png(largura: int, claro: bool = True) -> Image.Image:
    p = _Palavra()
    tam_svg = LADO * 0.6
    total = LADO + LADO * 0.16 + p.largura("PlayGo", tam_svg) + 4
    k = largura / total
    alt = round(LADO * k)
    img = Image.new("RGBA", (largura, alt), (0, 0, 0, 0))
    img.alpha_composite(simbolo_png(alt, contorno=claro))
    fonte = _fonte_outfit(round(tam_svg * k))
    d = ImageDraw.Draw(img)
    x = (LADO + LADO * 0.16) * k
    base = (LADO / 2 + p.cap * tam_svg / p.upm / 2) * k
    play = TINTA if claro else BRANCO
    go = ROXO if claro else LIMA
    for i, ch in enumerate("PlayGo"):
        d.text((x, base), ch, font=fonte, fill=(play if i < 4 else go) + (255,), anchor="ls")
        x += d.textlength(ch, font=fonte) - 0.04 * tam_svg * k
    return img


def og_png() -> Image.Image:
    """Imagem de compartilhamento (1200×630): fundo da marca com a logo para fundo escuro e a frase."""
    img = _degrade(1200).crop((0, 285, 1200, 915)).convert("RGBA")
    logo = logo_horizontal_png(760, claro=False)
    img.alpha_composite(logo, ((1200 - logo.width) // 2, (630 - logo.height) // 2 - 36))
    d = ImageDraw.Draw(img)
    d.text((600, 500), "Encontre onde jogar. Encontre com quem jogar.", font=_fonte_outfit(38, 600), fill=BRANCO + (255,), anchor="mm")
    return img.convert("RGB")


def main() -> None:
    MARCA.mkdir(parents=True, exist_ok=True)
    (MARCA / "simbolo.svg").write_text(svg_simbolo("claro"), encoding="utf-8")
    (MARCA / "simbolo-branco.svg").write_text(svg_simbolo("escuro"), encoding="utf-8")
    (MARCA / "icone.svg").write_text(svg_icone(True), encoding="utf-8")
    (MARCA / "favicon.svg").write_text(svg_icone(False), encoding="utf-8")  # em 16-32 px os gomos e o rastro viram borrão: só o pentágono
    (MARCA / "logo-horizontal.svg").write_text(svg_horizontal(True), encoding="utf-8")
    (MARCA / "logo-horizontal-branco.svg").write_text(svg_horizontal(False), encoding="utf-8")

    icone_png(512, True).save(MARCA / "icone-512.png")
    for t in (16, 32):
        icone_png(t, True, completa=False).save(MARCA / f"favicon-{t}.png")
    icone_png(48, True, completa=False).save(MARCA / "favicon-48.png")
    icone_png(64, True, completa=False).save(MARCA / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    icone_png(180, False).convert("RGB").save(MARCA / "apple-touch-icon.png")
    for t in (192, 512):  # ícones do app (ainda não ligados ao manifest: a troca no PWA é outra etapa)
        icone_png(t, False, ocupa=0.7).convert("RGB").save(APP / f"icone-{t}.png")
    icone_png(180, False, ocupa=0.7).convert("RGB").save(APP / "apple-touch-icon-novo.png")
    logo_horizontal_png(1920, True).save(MARCA / "logo-horizontal.png")
    logo_horizontal_png(1920, False).save(MARCA / "logo-horizontal-branco.png")
    og_png().save(MARCA / "og-playgo.png")
    for p in sorted(MARCA.iterdir()):
        print("marca/" + p.name)
    print("app/icone-192.png, app/icone-512.png, app/apple-touch-icon-novo.png")


if __name__ == "__main__":
    main()
