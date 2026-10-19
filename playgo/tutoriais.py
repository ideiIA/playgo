"""Tutoriais em HTML (site e app). O conteúdo vive em `web/templates/_tut_*.html`; o app busca o trecho pela API e o mostra na própria tela.
Quem pode ver: o de campeonatos, só quem cria campeonatos (como no site); o de transmissão, qualquer usuário logado (mesários também precisam)."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import planos
from .erros import NaoEncontrado, PlanoNecessario
from .models import Usuario

PASTA = Path(__file__).parent / "web" / "templates"
_env = Environment(loader=FileSystemLoader(PASTA), autoescape=False)

TUTORIAIS = {
    "campeonatos": {"titulo": "Como organizar um campeonato", "icone": "🏆", "arquivo": "_tut_campeonatos.html", "recurso": "campeonato"},
    "transmissao": {"titulo": "Como transmitir os jogos ao vivo", "icone": "🎥", "arquivo": "_tut_transmissao.html", "recurso": None},
}


def lista(s, u: Usuario) -> list[dict]:
    pode = planos.permissoes(s, u)
    return [{"codigo": k, "titulo": v["titulo"], "icone": v["icone"]} for k, v in TUTORIAIS.items() if not v["recurso"] or pode[v["recurso"]]]


def html(s, u: Usuario, codigo: str, embutido: bool = False) -> dict:
    t = TUTORIAIS.get(codigo)
    if t is None:
        raise NaoEncontrado("Tutorial não encontrado.")
    if t["recurso"] and not planos.permissoes(s, u)[t["recurso"]]:
        raise PlanoNecessario("organizador", "O tutorial de campeonatos é para quem cria campeonatos (plano Organizador).")
    return {"codigo": codigo, "titulo": t["titulo"], "html": _env.get_template(t["arquivo"]).render(embutido=embutido)}
