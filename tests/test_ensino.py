"""Ensino, fase 1: perfil profissional com documentos e aprovação, ofertas (aula, pacote, turma), limites do plano e busca."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from playgo import contas, ofertas, planos, profissionais
from playgo.db import Session, agora
from playgo.erros import ErroNegocio, NaoEncontrado, PlanoNecessario, SemPermissao
from playgo.models import (
    DOC_APROVADO,
    DOC_RECUSADO,
    PP_APROVADO,
    PP_EM_ANALISE,
    PP_REPROVADO,
    PP_SUSPENSO,
    Notificacao,
    PerfilProfissional,
    Usuario,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64
PERTO = (-20.4535, -54.6201)


@pytest.fixture(autouse=True)
def _ja_existe_um_admin(banco):
    with Session() as s:
        if not s.scalar(select(Usuario.id).where(Usuario.admin).limit(1)):
            contas.cadastrar(s, "Primeiro", f"{uuid.uuid4().hex[:8]}@t.local", "senha-de-teste-1", f"pri{uuid.uuid4().hex[:10]}", True, False)


@pytest.fixture()
def admin(s, fabrica):
    a = fabrica.atleta("Admin Ensino")
    a.admin = True
    s.commit()
    return a


def _perfil_completo(s, u, **extra):
    dados = {"nome_profissional": "Prof. Ana", "apresentacao": "Personal e professora de beach tennis.", "cidade": "Campo Grande", "telefone": "67999990000",
             "latitude": PERTO[0], "longitude": PERTO[1], "modalidades": ["futebol", "volei"], "registro_tipo": "CREF", "registro_numero": "012345-G/MS"} | extra
    return profissionais.salvar(s, u, dados)


def _aprovado(s, fabrica, admin, nome="Instrutor"):
    u = fabrica.atleta(nome)
    p = _perfil_completo(s, u)
    profissionais.aceitar_termo(s, u)
    profissionais.enviar_documento(s, u, "identidade", PNG, "rg.png")
    profissionais.enviar_documento(s, u, "registro_profissional", PDF, "cref.pdf")
    profissionais.enviar_para_analise(s, u)
    for d in profissionais.documentos_de(p):
        profissionais.decidir_documento(s, admin, d.id, True)
    profissionais.decidir(s, admin, p.id, True)
    return u, p


# ---------------------------------------------------------------- perfil e aprovação


def test_perfil_nasce_rascunho_e_so_vai_para_analise_completo(s, fabrica):
    u = fabrica.atleta("Quer Ensinar")
    p = profissionais.salvar(s, u, {"nome_profissional": "Prof. Beto", "cidade": "Campo Grande"})
    assert p.status == "rascunho" and p.modalidades == []
    with pytest.raises(ErroNegocio, match="Antes de enviar"):
        profissionais.enviar_para_analise(s, u)
    falta = profissionais.pendencias(p)
    assert "documento de identidade" in falta and "ponto de atendimento no mapa" in falta and "aceite do Termo de Adesão do Profissional" in falta
    _perfil_completo(s, u)
    profissionais.aceitar_termo(s, u)
    profissionais.enviar_documento(s, u, "identidade", PNG, "rg.png")
    assert "comprovante do registro profissional" in profissionais.pendencias(profissionais.meu(s, u))  # informou o número do registro
    profissionais.enviar_documento(s, u, "registro_profissional", PDF, "cref.pdf")
    p = profissionais.enviar_para_analise(s, u)
    assert p.status == PP_EM_ANALISE
    with pytest.raises(ErroNegocio, match="já está em análise"):
        profissionais.enviar_para_analise(s, u)
    # modalidades inexistentes são ignoradas
    assert profissionais.salvar(s, u, {"modalidades": ["futebol", "esporte-que-nao-existe"]}).modalidades == ["futebol"]


def test_documento_so_pdf_ou_foto_e_so_titular_e_admin_leem(s, fabrica, admin):
    u = fabrica.atleta("Titular")
    _perfil_completo(s, u)
    with pytest.raises(ErroNegocio, match="PDF ou como foto"):
        profissionais.enviar_documento(s, u, "identidade", b"texto qualquer", "x.txt")
    with pytest.raises(ErroNegocio):
        profissionais.enviar_documento(s, u, "tipo-invalido", PNG)
    doc = profissionais.enviar_documento(s, u, "identidade", PNG, "rg.png")
    assert profissionais.arquivo_documento(s, u, doc.id)[1] == "image/png"
    assert profissionais.arquivo_documento(s, admin, doc.id)[0] == PNG
    estranho = fabrica.atleta("Estranho")
    with pytest.raises(SemPermissao):
        profissionais.arquivo_documento(s, estranho, doc.id)
    assert "arquivo" not in str(profissionais.dict_privado(profissionais.meu(s, u))["documentos"])  # o caminho nunca sai na resposta
    assert "documentos" not in profissionais.dict_publico(profissionais.meu(s, u))  # visão pública não traz documentos


def test_aprovacao_exige_documentos_aprovados_e_recusa_pede_motivo(s, fabrica, admin):
    u = fabrica.atleta("Aguardando")
    p = _perfil_completo(s, u)
    profissionais.aceitar_termo(s, u)
    profissionais.enviar_documento(s, u, "identidade", PNG, "rg.png")
    profissionais.enviar_documento(s, u, "registro_profissional", PDF, "cref.pdf")
    profissionais.enviar_para_analise(s, u)
    with pytest.raises(SemPermissao):
        profissionais.decidir(s, u, p.id, True)  # só a administração
    with pytest.raises(ErroNegocio, match="Aprove antes os documentos"):
        profissionais.decidir(s, admin, p.id, True)
    ident, reg = profissionais.documentos_de(p)
    with pytest.raises(ErroNegocio, match="motivo"):
        profissionais.decidir_documento(s, admin, reg.id, False)
    profissionais.decidir_documento(s, admin, reg.id, False, "Foto ilegível")
    assert reg.status == DOC_RECUSADO
    profissionais.decidir_documento(s, admin, ident.id, True)
    with pytest.raises(ErroNegocio, match="Aprove antes"):
        profissionais.decidir(s, admin, p.id, True)  # o registro ainda não foi aprovado
    with pytest.raises(ErroNegocio, match="motivo"):
        profissionais.decidir(s, admin, p.id, False)
    profissionais.decidir(s, admin, p.id, False, "Registro profissional não confere.")
    assert p.status == PP_REPROVADO
    assert s.query(Notificacao).filter(Notificacao.usuario_id == u.id, Notificacao.titulo.like("%recusado%")).count() == 2  # documento e perfil
    # corrige, reenvia e é aprovado
    profissionais.remover_documento(s, u, reg.id)
    novo = profissionais.enviar_documento(s, u, "registro_profissional", PDF, "cref2.pdf")
    profissionais.enviar_para_analise(s, u)
    profissionais.decidir_documento(s, admin, novo.id, True)
    profissionais.decidir(s, admin, p.id, True)
    assert p.status == PP_APROVADO and planos.permissoes(s, u)["perfil_prof"] is True
    assert s.query(Notificacao).filter(Notificacao.usuario_id == u.id, Notificacao.titulo.like("%aprovado%")).count() == 1


def test_mudar_o_registro_depois_de_aprovado_volta_para_analise_e_suspensao(s, fabrica, admin):
    u, p = _aprovado(s, fabrica, admin)
    profissionais.salvar(s, u, {"apresentacao": "Texto novo."})
    assert p.status == PP_APROVADO  # texto não exige nova análise
    profissionais.salvar(s, u, {"registro_numero": "999999-G/MS"})
    assert p.status == PP_EM_ANALISE
    profissionais.decidir(s, admin, p.id, True)
    profissionais.suspender(s, admin, p.id, "Denúncia em apuração")
    assert p.status == PP_SUSPENSO
    with pytest.raises(ErroNegocio, match="suspenso"):
        profissionais.enviar_para_analise(s, u)
    profissionais.suspender(s, admin, p.id, "", reativar=True)
    assert p.status == PP_APROVADO


# ---------------------------------------------------------------- ofertas


def test_oferta_so_publica_com_perfil_aprovado_e_plano(s, fabrica, admin):
    u = fabrica.atleta("Sem Aprovacao")
    _perfil_completo(s, u)
    o = ofertas.criar(s, u, {"tipo": "aula", "titulo": "Aula de beach tennis", "modalidade": "futebol", "valor_aula": "80", "local_texto": "Parque das Nações"})
    assert o.status == "rascunho" and o.latitude == PERTO[0]  # herda o ponto do perfil
    with pytest.raises(ErroNegocio, match="não foi aprovado"):
        ofertas.publicar(s, u, o.id)
    u2, p2 = _aprovado(s, fabrica, admin, "Aprovado")
    o2 = ofertas.criar(s, u2, {"tipo": "aula", "titulo": "Aula particular", "modalidade": "futebol", "valor_aula": "90", "local_texto": "Quadra do condomínio"})
    assert planos.situacao_prof(s, u2)["plano"] is None
    ofertas.publicar(s, u2, o2.id)  # abre o teste grátis do plano básico do profissional
    assert o2.status == "publicada" and planos.situacao_prof(s, u2)["status"] == "teste"
    ofertas.pausar(s, u2, o2.id)
    assert o2.status == "pausada"
    outro = fabrica.atleta("Outro")
    with pytest.raises(SemPermissao):
        ofertas.editar(s, outro, o2.id, {"titulo": "Invadida"})
    ofertas.encerrar(s, u2, o2.id)
    with pytest.raises(ErroNegocio, match="encerrada"):
        ofertas.publicar(s, u2, o2.id)


def test_publicar_exige_os_dados_do_tipo_e_respeita_o_limite_do_plano(s, fabrica, admin):
    u, p = _aprovado(s, fabrica, admin, "Limitado")
    planos.salvar_regras(s, admin, "prof_basico", max_ofertas=2)
    turma = ofertas.criar(s, u, {"tipo": "turma", "titulo": "Turma de vôlei", "modalidade": "volei", "valor_mensal": "150", "local_texto": "Ginásio"})
    with pytest.raises(ErroNegocio, match="horário semanal"):
        ofertas.publicar(s, u, turma.id)
    ofertas.definir_horarios(s, u, turma.id, [{"dia_semana": 1, "hora_inicio": "19:00", "duracao_min": 60}, {"dia_semana": 3, "hora_inicio": "19:00"}])
    with pytest.raises(ErroNegocio, match="dois horários iguais"):
        ofertas.definir_horarios(s, u, turma.id, [{"dia_semana": 1, "hora_inicio": "19:00"}, {"dia_semana": 1, "hora_inicio": "19:00"}])
    assert [h.dia_semana for h in turma.horarios] == [1, 3]  # a lista inválida não apagou a anterior
    ofertas.publicar(s, u, turma.id)
    pacote = ofertas.criar(s, u, {"tipo": "pacote", "titulo": "Pacote 8 aulas", "modalidade": "futebol", "local_texto": "Arena X"})
    with pytest.raises(ErroNegocio, match="pacote"):
        ofertas.publicar(s, u, pacote.id)
    ofertas.editar(s, u, pacote.id, {"valor_pacote": "560", "aulas_pacote": 8})
    ofertas.publicar(s, u, pacote.id)
    terceira = ofertas.criar(s, u, {"tipo": "aula", "titulo": "Aula 3", "modalidade": "futebol", "valor_aula": "70", "local_texto": "Arena X"})
    with pytest.raises(PlanoNecessario):
        ofertas.publicar(s, u, terceira.id)  # passou do limite de 2 ofertas publicadas
    ofertas.pausar(s, u, pacote.id)
    ofertas.publicar(s, u, terceira.id)
    assert {x.titulo for x in ofertas.minhas(s, u)} == {"Turma de vôlei", "Pacote 8 aulas", "Aula 3"}
    d = ofertas.dict_oferta(turma, completo=True)
    assert d["preco_texto"] == "R$ 150,00/mês" and d["horarios_texto"] == "Terça 19:00, Quinta 19:00" and d["profissional"]["verificado"] is True
    planos.salvar_regras(s, admin, "prof_basico", max_ofertas=3)


def test_validacoes_de_oferta(s, fabrica):
    u = fabrica.atleta("Valida")
    _perfil_completo(s, u)
    with pytest.raises(ErroNegocio):
        ofertas.criar(s, u, {"tipo": "aula"})  # sem título
    with pytest.raises(ErroNegocio):
        ofertas.criar(s, u, {"tipo": "outro", "titulo": "Aula"})
    with pytest.raises(ErroNegocio, match="negativo"):
        ofertas.criar(s, u, {"tipo": "aula", "titulo": "Aula", "valor_aula": "-5"})
    with pytest.raises(ErroNegocio, match="idade mínima"):
        ofertas.criar(s, u, {"tipo": "turma", "titulo": "Turma", "faixa_etaria_min": 12, "faixa_etaria_max": 8})
    with pytest.raises(NaoEncontrado):
        ofertas.criar(s, fabrica.atleta("Sem Perfil"), {"tipo": "aula", "titulo": "Aula"})
