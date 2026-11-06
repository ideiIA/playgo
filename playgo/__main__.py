"""Linha de comando: python -m playgo <comando>."""

import argparse
import logging

from . import agendador, modalidades
from .db import Base, Session, criar_tabelas, engine


def main() -> None:
    parser = argparse.ArgumentParser(prog="playgo", description="PlayGo: encontre onde jogar, encontre com quem jogar")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("init-db", help="cria as tabelas e as modalidades iniciais")
    p = sub.add_parser("demo", help="popula dados de demonstração (Campo Grande)")
    p.add_argument("--refazer", action="store_true", help="APAGA todas as tabelas e recria (só desenvolvimento)")
    sub.add_parser("ciclo", help="roda uma vez as rotinas do agendador (lembretes, reforço, encerramento)")
    sub.add_parser("purgar", help="elimina registros e publicações excluídas que passaram do prazo legal de guarda (LGPD)")
    sub.add_parser("supabase", help="prepara o banco/Storage do Supabase: tabelas, RLS e bucket privado")
    sub.add_parser("agendador", help="fica rodando as rotinas periódicas sem a interface")
    p = sub.add_parser("web", help="sobe o site, o app (/app) e a API (/api/v1)")
    p.add_argument("--porta", type=int, default=8010)
    p.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.comando == "demo" and args.refazer:
        from . import models  # noqa: F401

        Base.metadata.drop_all(engine)
    criar_tabelas()
    with Session() as s:
        modalidades.semear(s)

    if args.comando == "demo":
        from . import demo

        with Session() as s:
            print(demo.popular(s))
            print(f"Entrar como {demo.DEMO_EMAIL} (senha em playgo/demo.py).")
    elif args.comando == "ciclo":
        print(agendador.ciclo())
    elif args.comando == "supabase":
        from . import supabase_setup

        for linha in supabase_setup.preparar():
            print(linha)
    elif args.comando == "purgar":
        from . import privacidade

        with Session() as s:
            print(privacidade.purgar(s))
    elif args.comando == "agendador":
        import threading

        print("Agendador rodando. Ctrl+C para parar.")
        try:
            agendador.laco(threading.Event())
        except KeyboardInterrupt:
            pass
    elif args.comando == "web":
        import uvicorn

        uvicorn.run("playgo.web.app:app", host=args.host, port=args.porta)


if __name__ == "__main__":
    main()
