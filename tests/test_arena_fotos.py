"""Fotos da arena: capa (16:9) e galeria "Conheça nosso espaço": só o gestor mexe, limite de fotos, remoção apaga o arquivo."""

import io

import pytest
from PIL import Image

from playgo import arenas, armazenamento, detalhes, serializadores as ser
from playgo.erros import ErroNegocio, NaoEncontrado, SemPermissao

from test_planos import PERTO, _admin


def _jpeg(tamanho=(900, 600)):
    buf = io.BytesIO()
    Image.new("RGB", tamanho, (30, 140, 90)).save(buf, "JPEG")
    return buf.getvalue()


def test_capa_e_galeria_da_arena(s, fabrica):
    gestor = _admin(fabrica, s)
    a = arenas.criar(s, gestor, "Arena Fotos", *PERTO)
    arenas.definir_capa(s, a.id, gestor, _jpeg())
    s.refresh(a)
    assert a.capa and ser.arena(a)["capa_url"] == f"/capa/{a.capa}"
    capa_antiga = a.capa
    arenas.definir_capa(s, a.id, gestor, _jpeg((1200, 400)))  # trocar apaga a anterior
    s.refresh(a)
    assert a.capa != capa_antiga and not armazenamento.existe_local(f"capas/{capa_antiga}")
    with Image.open(io.BytesIO(armazenamento.ler(f"capas/{a.capa}"))) as img:
        assert img.size == (1600, 900)  # capa sempre 16:9

    arenas.adicionar_foto(s, a.id, gestor, _jpeg((1000, 2000)))
    s.refresh(a)
    foto = a.fotos[0]
    with Image.open(io.BytesIO(armazenamento.ler(f"capas/{foto}"))) as img:
        assert img.size[1] == 1600 and img.size[0] == 800  # galeria mantém a proporção
    assert detalhes.arena_publica(s, a, gestor)["fotos"] == [f"/capa/{foto}"]

    arenas.remover_foto(s, a.id, gestor, foto)
    s.refresh(a)
    assert a.fotos == [] and not armazenamento.existe_local(f"capas/{foto}")
    with pytest.raises(NaoEncontrado):
        arenas.remover_foto(s, a.id, gestor, foto)
    arenas.remover_capa(s, a.id, gestor)
    s.refresh(a)
    assert a.capa is None


def test_limite_permissao_e_arquivo_invalido(s, fabrica):
    gestor = _admin(fabrica, s)
    a = arenas.criar(s, gestor, "Arena Limite", *PERTO)
    for _ in range(arenas.MAX_FOTOS):
        arenas.adicionar_foto(s, a.id, gestor, _jpeg((200, 150)))
    with pytest.raises(ErroNegocio):
        arenas.adicionar_foto(s, a.id, gestor, _jpeg((200, 150)))
    with pytest.raises(ErroNegocio):
        arenas.definir_capa(s, a.id, gestor, b"isto nao e imagem")
    intruso = fabrica.atleta("Intruso")
    with pytest.raises(SemPermissao):
        arenas.definir_capa(s, a.id, intruso, _jpeg())
    with pytest.raises(SemPermissao):
        arenas.adicionar_foto(s, a.id, intruso, _jpeg())
    s.refresh(a)
    for f in list(a.fotos):
        arenas.remover_foto(s, a.id, gestor, f)


def _foto_com_detalhe_a_direita():
    """Panorama 3200x900: metade esquerda lisa, metade direita cheia de listras (é onde está o "assunto")."""
    img = Image.new("RGB", (3200, 900), (120, 120, 120))
    for x in range(1700, 3200, 20):
        for y in range(0, 900, 20):
            if (x // 20 + y // 20) % 2 == 0:
                img.paste((250, 250, 250), (x, y, x + 20, y + 20))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def test_analise_e_ajustes_da_capa(s, fabrica):
    from playgo import midia

    ok = midia.analisar_capa(_jpeg((1600, 900)))
    assert ok["dentro_do_padrao"] and ok["avisos"] == []
    vertical = midia.analisar_capa(_jpeg((900, 1600)))
    assert not vertical["dentro_do_padrao"] and any("vertical" in a for a in vertical["avisos"])
    pequena = midia.analisar_capa(_jpeg((640, 360)))
    assert not pequena["dentro_do_padrao"] and any("pequena" in a for a in pequena["avisos"])

    previas = midia.previas_capa(_jpeg((900, 1600)))
    assert set(previas) == {"recortar", "auto", "inteira"} and all(v.startswith("data:image/jpeg;base64,") for v in previas.values())
    for ajuste in midia.AJUSTES_CAPA:
        with Image.open(io.BytesIO(midia.processar_capa(_jpeg((900, 1600)), ajuste=ajuste))) as img:
            assert img.size == (1600, 900)
    with pytest.raises(ErroNegocio):
        midia.processar_capa(_jpeg(), ajuste="mágico")

    # "auto" corta onde há detalhe (direita); "recortar" fica no centro. O resultado do auto é mais "rico" que o do centro.
    pano = _foto_com_detalhe_a_direita()
    def riqueza(ajuste):
        with Image.open(io.BytesIO(midia.processar_capa(pano, ajuste=ajuste, largura=640, altura=360))) as im:
            tons = list(im.convert("L").getdata())
            return sum(1 for t in tons if t > 200) / len(tons)
    assert riqueza("auto") > riqueza("recortar")


def test_definir_capa_com_ajuste_e_api(s, fabrica):
    gestor = _admin(fabrica, s)
    a = arenas.criar(s, gestor, "Arena Ajuste", *PERTO)
    arenas.definir_capa(s, a.id, gestor, _jpeg((900, 1600)), "inteira")
    s.refresh(a)
    with Image.open(io.BytesIO(armazenamento.ler(f"capas/{a.capa}"))) as img:
        assert img.size == (1600, 900)
    analise = arenas.analisar_capa(s, a.id, gestor, _jpeg((900, 1600)))
    assert analise["previas"]["auto"] and not analise["dentro_do_padrao"]
    with pytest.raises(SemPermissao):
        arenas.analisar_capa(s, a.id, fabrica.atleta("Intruso"), _jpeg())
    with pytest.raises(ErroNegocio):
        arenas.definir_capa(s, a.id, gestor, _jpeg(), "xyz")


def _png_transparente(tamanho=(600, 300)):
    buf = io.BytesIO()
    img = Image.new("RGBA", tamanho, (0, 0, 0, 0))
    img.paste((200, 30, 90, 255), (tamanho[0] // 4, 0, tamanho[0] * 3 // 4, tamanho[1]))
    img.save(buf, "PNG")
    return buf.getvalue()


def test_logo_da_arena_inteiro_quadrado_e_transparente(s, fabrica):
    gestor = _admin(fabrica, s)
    a = arenas.criar(s, gestor, "Arena Logo", *PERTO)
    arenas.definir_logo(s, a.id, gestor, _png_transparente())
    s.refresh(a)
    assert a.logo and a.logo.endswith(".png") and ser.arena(a)["logo_url"] == f"/logo/{a.logo}"
    with Image.open(io.BytesIO(armazenamento.ler(f"logos/{a.logo}"))) as img:
        assert img.size == (400, 400) and img.mode == "RGBA"
        assert img.getpixel((2, 2))[3] == 0  # canto continua transparente
        assert img.getpixel((200, 200))[3] == 255  # a imagem inteira cabe, sem cortar (600x300 viram 400x200 no meio)
        assert img.getpixel((200, 20))[3] == 0  # faixa de cima é só margem: o logo largo não foi esticado nem cortado
    antigo = a.logo
    arenas.definir_logo(s, a.id, gestor, _jpeg((300, 300)))  # JPG também vale
    s.refresh(a)
    assert a.logo != antigo and not armazenamento.existe_local(f"logos/{antigo}")
    with pytest.raises(ErroNegocio):
        arenas.definir_logo(s, a.id, gestor, b"nao e imagem")
    with pytest.raises(SemPermissao):
        arenas.definir_logo(s, a.id, fabrica.atleta("Intruso"), _png_transparente())
    arenas.remover_logo(s, a.id, gestor)
    s.refresh(a)
    assert a.logo is None
    rel = __import__("playgo.relatorios", fromlist=["x"]).arena(s, gestor, a.id)
    assert "logo_url" in rel["arena"]


def test_divulgar_e_retirar_por_dia_e_por_quadra(s, fabrica):
    from datetime import timedelta

    from sqlalchemy import func, select

    from playgo.db import agora
    from playgo.models import HorarioDivulgado

    gestor = _admin(fabrica, s)
    a = arenas.criar(s, gestor, "Arena Retirar", *PERTO)
    q1 = arenas.criar_quadra(s, a, gestor, "Quadra 1", [], 4, __import__("decimal").Decimal(50))
    q2 = arenas.criar_quadra(s, a, gestor, "Quadra 2", [], 4, __import__("decimal").Decimal(50))
    amanha = agora().date() + timedelta(days=1)
    qtd = lambda qid=None: s.scalar(select(func.count()).select_from(HorarioDivulgado).where(HorarioDivulgado.quadra_id == qid) if qid else select(func.count()).select_from(HorarioDivulgado).where(HorarioDivulgado.quadra_id.in_([q1.id, q2.id])))

    so_q1 = arenas.divulgar_ociosos(s, a, gestor, amanha, q1.id)
    assert so_q1 > 0 and qtd(q1.id) == so_q1 and qtd(q2.id) == 0  # divulgar só uma quadra
    todas = arenas.divulgar_ociosos(s, a, gestor, amanha)
    assert qtd(q2.id) > 0 and qtd(q1.id) == so_q1 and todas == qtd(q2.id)  # o resto, sem duplicar a q1
    ag = arenas.agenda_do_dia(s, a, amanha)
    assert {q["id"]: q["divulgados"] for q in ag["quadras"]} == {q1.id: so_q1, q2.id: qtd(q2.id)} and all(q["livres"] == 0 for q in ag["quadras"])

    retirados = arenas.retirar_divulgados(s, a, gestor, amanha, q1.id)  # retirar só de uma quadra
    assert retirados == so_q1 and qtd(q1.id) == 0 and qtd(q2.id) > 0
    assert arenas.retirar_divulgados(s, a, gestor, amanha) == qtd(q2.id) or qtd(q2.id) == 0  # e o resto do dia
    assert qtd() == 0
    assert arenas.retirar_divulgados(s, a, gestor, amanha) == 0  # nada mais a retirar
    ontem = agora().date() - timedelta(days=1)
    assert arenas.retirar_divulgados(s, a, gestor, ontem) == 0
    with pytest.raises(ErroNegocio):
        arenas.divulgar_ociosos(s, a, gestor, ontem)  # dia que já passou
    outra = arenas.criar(s, gestor, "Outra Arena", *PERTO)
    qo = arenas.criar_quadra(s, outra, gestor, "Q", [], 4, __import__("decimal").Decimal(10))
    with pytest.raises(NaoEncontrado):
        arenas.retirar_divulgados(s, a, gestor, amanha, qo.id)  # quadra de outra arena
    arenas.divulgar_ociosos(s, a, gestor, amanha)
    with pytest.raises(SemPermissao):
        arenas.retirar_divulgados(s, a, fabrica.atleta("Intruso"), amanha)
    assert qtd() > 0  # nada foi retirado pelo intruso
