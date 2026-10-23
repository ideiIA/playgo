"""Gera o sprite de ícones Lucide usado no site e no app (playgo/web/static/lucide.svg).

Lucide é ISC (https://lucide.dev). O sprite é local: nada de CDN em tempo de uso e funciona offline no PWA.
Uso:  .venv\\Scripts\\python scripts\\gerar_icones.py
Para adicionar um ícone novo: acrescente o nome em ICONES (nomes do Lucide) e rode o script.
No HTML:  <svg class="i"><use href="/static/lucide.svg#trophy"/></svg>   (Jinja: macro `ic` em _ico.html; JS: função `ic`)."""

import re
import sys
import urllib.request
from pathlib import Path

VERSAO = "1.50.0"
BASE = f"https://cdn.jsdelivr.net/npm/lucide-static@{VERSAO}/icons/"
DESTINO = Path(__file__).resolve().parent.parent / "playgo" / "web" / "static" / "lucide.svg"

ICONES = """
home pen-line compass circle-plus plus users users-round trophy building-2 credit-card book-open map-pin map settings shield-check shield bell mail user
lock video tv radio dices pencil calendar calendar-days clock ticket crown camera search share-2 heart message-circle message-square play square
key-round circle-check circle-alert triangle-alert info chevron-right chevron-down chevron-left arrow-left arrow-right log-out x check flag medal swords store
trees dumbbell layers locate-fixed navigation gift sparkles zap flame eye eye-off upload download external-link link copy trash-2 refresh-cw send image
scroll-text clipboard-list star list-checks qr-code wifi smartphone monitor hourglass timer rotate-ccw ellipsis user-round-plus user-check handshake
megaphone book-marked graduation-cap target award coins banknote wallet receipt landmark badge-check circle-play circle-stop activity bike footprints
volleyball goal party-popper bookmark filter sliders-horizontal image-plus file-text file-down mic mic-off video-off power maximize minimize
phone laptop bot rocket shopping-bag pin thumbs-up satellite clapperboard ban lock-open file-pen
school calendar-check user-round-check
""".split()


def baixar(nome: str) -> str:
    with urllib.request.urlopen(BASE + nome + ".svg", timeout=30) as r:
        return r.read().decode("utf-8")


def main() -> int:
    simbolos, faltou = [], []
    for nome in sorted(set(ICONES)):
        try:
            svg = baixar(nome)
        except Exception:  # noqa: BLE001 - nome que não existe nesta versão do Lucide
            faltou.append(nome)
            continue
        miolo = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
        miolo = re.sub(r"\s+", " ", miolo).strip()
        simbolos.append(f'<symbol id="{nome}" viewBox="0 0 24 24">{miolo}</symbol>')
    sprite = (
        f"<!-- Lucide v{VERSAO} (ISC, https://lucide.dev). Gerado por scripts/gerar_icones.py — não edite à mão. -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n' + "\n".join(simbolos) + "\n</svg>\n"
    )
    DESTINO.write_text(sprite, encoding="utf-8")
    print(f"{len(simbolos)} ícones em {DESTINO} ({len(sprite) // 1024} KB)")
    if faltou:
        print("não existem nesta versão:", ", ".join(faltou))
    return 0


if __name__ == "__main__":
    sys.exit(main())
