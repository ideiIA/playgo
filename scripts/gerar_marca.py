"""Gera a identidade visual do PlayGo: ícone (bola + botão de play + linhas de velocidade), logo horizontal, favicon e ícones do app.

Uso: .venv\\Scripts\\python scripts\\gerar_marca.py
Saída: playgo/web/static/marca/*.svg|png|ico  e  playgo/app/icon-192.png, icon-512.png

Conceito: uma bola em movimento (esporte) com as linhas de velocidade em lima ("Go"), sobre o degradê roxo→rosa da marca.
A geometria fica nas constantes abaixo (grade de 512) e alimenta o SVG e o PNG, que ficam idênticos."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
MARCA = RAIZ / "playgo" / "web" / "static" / "marca"
APP = RAIZ / "playgo" / "app"

ROXO, ROSA, LIMA, ESCURO, BRANCO = (108, 76, 255), (255, 61, 129), (183, 243, 75), (20, 33, 61), (255, 255, 255)
HEX = {"roxo": "#6c4cff", "rosa": "#ff3d81", "lima": "#b7f34b", "escuro": "#14213d"}

# grade 512 × 512
BOLA = (292, 256, 138)  # cx, cy, r
LINHAS = [(66, 200, 122), (46, 256, 122), (66, 312, 122)]  # x0, y, x1 (lima, ponta arredondada)
LARG_LINHA = 22
RAIO_CANTO = 112
LARG_COSTURA = 11


def _poligono(cx: float, cy: float, raio: float, giro_graus: float) -> list[tuple[float, float]]:
    return [(cx + raio * math.cos(math.radians(giro_graus + 72 * i)), cy + raio * math.sin(math.radians(giro_graus + 72 * i))) for i in range(5)]


def padrao() -> tuple[list, list, list]:
    """Padrão de bola de futebol: pentágono central, 5 costuras e 5 pentágonos nas bordas (recortados pelo círculo)."""
    cx, cy, _ = BOLA
    central = _poligono(cx, cy, 44, -90)
    costuras, bordas = [], []
    for i in range(5):
        ang = -90 + 72 * i
        ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        costuras.append(((cx + ux * 44, cy + uy * 44), (cx + ux * 96, cy + uy * 96)))
        # pentágono da borda: um vértice aponta para o centro, o resto passa da borda e é recortado
        bordas.append(_poligono(cx + ux * 150, cy + uy * 150, 54, ang + 180))
    return central, costuras, bordas


# ---------------------------------------------------------------- SVG


def svg_icone(rounded: bool = True) -> str:
    cx, cy, r = BOLA
    central, costuras, bordas = padrao()
    pts = lambda ps: " ".join(f"{x:.1f},{y:.1f}" for x, y in ps)  # noqa: E731
    linhas = "".join(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" />' for x0, y, x1 in LINHAS)
    seams = "".join(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" />' for a, b in costuras)
    patches = "".join(f'<polygon points="{pts(p)}" />' for p in bordas)
    rx = RAIO_CANTO if rounded else 0
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" role="img" aria-label="PlayGo">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{HEX['roxo']}"/><stop offset="1" stop-color="{HEX['rosa']}"/></linearGradient>
    <clipPath id="b"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>
  </defs>
  <rect width="512" height="512" rx="{rx}" fill="url(#g)"/>
  <g stroke="{HEX['lima']}" stroke-width="{LARG_LINHA}" stroke-linecap="round">{linhas}</g>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff"/>
  <g clip-path="url(#b)" fill="{HEX['escuro']}" stroke="{HEX['escuro']}" stroke-width="{LARG_COSTURA}" stroke-linejoin="round" stroke-linecap="round">
    <g fill="none">{seams}</g>{patches}
  </g>
  <polygon points="{pts(central)}" fill="{HEX['escuro']}" stroke="{HEX['escuro']}" stroke-width="{LARG_COSTURA}" stroke-linejoin="round"/>
  <circle cx="{cx}" cy="{cy}" r="{r - 1}" fill="none" stroke="{HEX['escuro']}" stroke-opacity=".12" stroke-width="2"/>
</svg>
'''


def svg_horizontal(escuro_sobre_claro: bool = True) -> str:
    """Ícone + palavra. `escuro_sobre_claro=False` é a versão para fundo escuro (texto branco, Go em lima)."""
    interno = svg_icone(True).split("\n", 1)[1].rsplit("</svg>", 1)[0]
    play = HEX["escuro"] if escuro_sobre_claro else "#ffffff"
    go = 'url(#t)' if escuro_sobre_claro else HEX["lima"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 240" width="960" height="240" role="img" aria-label="PlayGo">
  <defs>
    <linearGradient id="t" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{HEX['roxo']}"/><stop offset="1" stop-color="{HEX['rosa']}"/></linearGradient>
  </defs>
  <g transform="translate(20 20) scale(0.3906)">{interno}</g>
  <text x="250" y="166" font-family="'Arial Black','Segoe UI Black','Inter','Arial',sans-serif" font-weight="900" font-size="128" letter-spacing="-3" fill="{play}">Play<tspan fill="{go}">Go</tspan></text>
</svg>
'''


# ---------------------------------------------------------------- PNG (mesma geometria, com suavização por superamostragem)


def _degrade(tam: int) -> Image.Image:
    pequeno = Image.new("RGB", (64, 64))
    px = pequeno.load()
    for j in range(64):
        for i in range(64):
            t = (i + j) / 126
            px[i, j] = tuple(round(a + (b - a) * t) for a, b in zip(ROXO, ROSA, strict=True))
    return pequeno.resize((tam, tam), Image.BICUBIC)


def icone_png(tam: int, arredondado: bool = True, ss: int = 4, fundo: bool = True) -> Image.Image:
    """`fundo=False` gera só o emblema (bola + linhas), transparente: usado na abertura do app, sobre a cor da marca."""
    S = tam * ss
    k = S / 512
    img = _degrade(S).convert("RGBA") if fundo else Image.new("RGBA", (S, S), (0, 0, 0, 0))

    d = ImageDraw.Draw(img)
    for x0, y, x1 in LINHAS:  # linhas de velocidade
        d.line([(x0 * k, y * k), (x1 * k, y * k)], fill=LIMA + (255,), width=round(LARG_LINHA * k))
        for x in (x0, x1):
            d.ellipse([(x - LARG_LINHA / 2) * k, (y - LARG_LINHA / 2) * k, (x + LARG_LINHA / 2) * k, (y + LARG_LINHA / 2) * k], fill=LIMA + (255,))
    cx, cy, r = BOLA
    d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=BRANCO + (255,))

    # padrão da bola recortado pelo círculo
    central, costuras, bordas = padrao()
    camada = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(camada)
    cor = ESCURO + (255,)
    larg = round(LARG_COSTURA * k)

    def contorno(ps):
        ps = [(x * k, y * k) for x, y in ps]
        cd.polygon(ps, fill=cor)
        for (xa, ya), (xb, yb) in zip(ps, ps[1:] + ps[:1], strict=True):
            cd.line([(xa, ya), (xb, yb)], fill=cor, width=larg)
        for x, y in ps:
            cd.ellipse([x - larg / 2, y - larg / 2, x + larg / 2, y + larg / 2], fill=cor)

    for a_, b_ in costuras:
        cd.line([(a_[0] * k, a_[1] * k), (b_[0] * k, b_[1] * k)], fill=cor, width=larg)
    for pol in bordas:
        contorno(pol)
    mascara = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mascara).ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=255)
    img = Image.alpha_composite(img, Image.composite(camada, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mascara))
    camada = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cd = ImageDraw.Draw(camada)
    contorno(central)
    img = Image.alpha_composite(img, camada)

    if arredondado:  # cantos arredondados do ícone (favicon/site); o ícone do app vai quadrado, a plataforma recorta
        canto = Image.new("L", (S, S), 0)
        ImageDraw.Draw(canto).rounded_rectangle([0, 0, S - 1, S - 1], radius=round(RAIO_CANTO * k), fill=255)
        img.putalpha(canto)
    return img.resize((tam, tam), Image.LANCZOS)


def icone_app_png(tam: int, escala: float = 0.84) -> Image.Image:
    """Ícone do app (maskable): roxo sólido da marca, igual à cor da abertura, só com o emblema por cima (centralizado e dentro da zona segura).
    Sem degradê nem quadrado visível: na abertura do Android o ícone se funde ao fundo."""
    emblema = icone_png(tam, False, fundo=False)
    caixa = emblema.getbbox()
    recorte = emblema.crop(caixa)
    k = escala * tam / max(recorte.size)
    recorte = recorte.resize((round(recorte.width * k), round(recorte.height * k)), Image.LANCZOS)
    img = Image.new("RGBA", (tam, tam), ROXO + (255,))
    img.alpha_composite(recorte, ((tam - recorte.width) // 2, (tam - recorte.height) // 2))
    return img.convert("RGB")


def _fonte(tam: int) -> ImageFont.FreeTypeFont:
    for caminho in ("C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(caminho).exists():
            return ImageFont.truetype(caminho, tam)
    return ImageFont.load_default()


def logo_horizontal_png(largura: int, escuro_sobre_claro: bool = True) -> Image.Image:
    k = largura / 960
    alt = round(240 * k)
    img = Image.new("RGBA", (largura, alt), (0, 0, 0, 0))
    ico = icone_png(round(200 * k), arredondado=True)
    img.alpha_composite(ico, (round(20 * k), round(20 * k)))
    fonte = _fonte(round(128 * k))
    d = ImageDraw.Draw(img)
    x, y = round(250 * k), round(166 * k)
    cor_play = ESCURO if escuro_sobre_claro else BRANCO
    d.text((x, y), "Play", font=fonte, fill=cor_play + (255,), anchor="ls")
    larg_play = d.textlength("Play", font=fonte)
    if escuro_sobre_claro:  # "Go" com degradê roxo→rosa
        larg_go = round(d.textlength("Go", font=fonte)) + 2
        mascara = Image.new("L", (larg_go, alt), 0)
        ImageDraw.Draw(mascara).text((0, y), "Go", font=fonte, fill=255, anchor="ls")
        grad = Image.new("RGBA", (larg_go, alt))
        gp = grad.load()
        for i in range(larg_go):
            t = i / max(1, larg_go - 1)
            c = tuple(round(a + (b - a) * t) for a, b in zip(ROXO, ROSA, strict=True)) + (255,)
            for j in range(alt):
                gp[i, j] = c
        go = Image.new("RGBA", (larg_go, alt), (0, 0, 0, 0))
        go.paste(grad, (0, 0), mascara)
        img.alpha_composite(go, (round(x + larg_play), 0))
    else:
        d.text((x + larg_play, y), "Go", font=fonte, fill=LIMA + (255,), anchor="ls")
    return img


def og_png() -> Image.Image:
    """Imagem de compartilhamento (1200×630): fundo da marca com a logo em versão para fundo escuro."""
    img = _degrade(1200).crop((0, 285, 1200, 915)).convert("RGBA")
    logo = logo_horizontal_png(900, escuro_sobre_claro=False)
    img.alpha_composite(logo, ((1200 - logo.width) // 2, (630 - logo.height) // 2 - 30))
    d = ImageDraw.Draw(img)
    fonte = _fonte(34)
    texto = "Encontre onde jogar. Encontre com quem jogar."
    d.text((600, 500), texto, font=fonte, fill=BRANCO + (255,), anchor="mm")
    return img.convert("RGB")


def main() -> None:
    MARCA.mkdir(parents=True, exist_ok=True)
    (MARCA / "icone.svg").write_text(svg_icone(True), encoding="utf-8")
    (MARCA / "favicon.svg").write_text(svg_icone(True), encoding="utf-8")
    (MARCA / "logo-horizontal.svg").write_text(svg_horizontal(True), encoding="utf-8")
    (MARCA / "logo-horizontal-branco.svg").write_text(svg_horizontal(False), encoding="utf-8")

    icone_png(512, True).save(MARCA / "icone-512.png")
    for t in (16, 32, 48):
        icone_png(t, True).save(MARCA / f"favicon-{t}.png")
    icone_png(64, True).save(MARCA / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    icone_png(180, False).convert("RGB").save(MARCA / "apple-touch-icon.png")
    for t in (192, 512):  # ícones do app (maskable): quadrados, a plataforma aplica a máscara
        icone_app_png(t).save(APP / f"icon-{t}.png")
    logo_horizontal_png(1920, True).save(MARCA / "logo-horizontal.png")
    logo_horizontal_png(1920, False).save(MARCA / "logo-horizontal-branco.png")
    og_png().save(MARCA / "og-playgo.png")
    for p in sorted(MARCA.iterdir()):
        print("marca/" + p.name)
    print("app/icon-192.png, app/icon-512.png")


if __name__ == "__main__":
    main()
