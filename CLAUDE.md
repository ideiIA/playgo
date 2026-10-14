# CLAUDE.md

Orientação para trabalhar neste repositório. Leia o `README.md` para o funcionamento; aqui ficam as regras.

## Padrão do projeto

PlayGo segue o padrão do **Licitações** (`C:\Projetos\Licitações`): pacote Python único (`playgo/`), FastAPI + Jinja,
SQLAlchemy 2 + psycopg 3, pydantic-settings, CLI `python -m playgo`, Postgres de desenvolvimento isolado
(`scripts\dev_db.ps1`, porta **5434**, não use 5432/5433), testes com banco próprio. Não introduza Alembic, Celery,
ORM alternativo ou framework de front sem pedido explícito.

- **Idioma:** código, tabelas, rotas e mensagens em português do Brasil (como no Licitações). Mensagens de `ErroNegocio` já vão
  prontas para a tela.
- **Regra de negócio fica em serviços** (`vagas.py`, `arenas.py`, …), nunca em rota ou template. `web/app.py` e `api/v1.py`
  só traduzem HTTP ↔ serviço. Serializadores (`serializadores.py`, `detalhes.py`) são compartilhados pelo site e pela API.
- **Datas** são naïve no fuso do servidor (`db.agora()`, `PLAYGO_FUSO`), como no Licitações.
- **Esquema:** `create_all` + `_COLUNAS_NOVAS` em `db.py` (`ALTER TABLE … IF NOT EXISTS`). Coluna nova = linha ali.
- **Esporte novo** = linha em `modalidades` (RF-012), sem mudar código. `usa_quadra=False` leva a ponto de encontro.

## Armadilhas já resolvidas — não desfaça

- `Atividade.confirmados` só muda dentro de `vagas.py`, com a atividade travada por `vagas.travar()` (usa `with_for_update(of=Atividade)`
  porque os joins eager são externos). Mexer nele fora disso reabre a corrida da última vaga.
- Reserva de quadra trava a linha da quadra (`arenas.reservar`). Atividade em quadra só por gestor ou por horário divulgado.
- A sessão usa `expire_on_commit=False`: coleções relacionadas (`campeonato.equipes`) podem estar velhas dentro da mesma sessão.
  Para decidir regra (vagas, nome repetido), **consulte o banco**, como `campeonatos.equipes_ativas`.
- Aviso duplicado é evitado por `Notificacao.chave` (UNIQUE por usuário) com `begin_nested`; sempre passe `chave` em avisos recorrentes.
- A API aceita token Bearer **ou** cookie de sessão; POSTs por cookie dependem de `SameSite=lax`.

- **Mural/moderação** (`publicacoes.py`, `escopos.py`, `moderacao.py`, `midia.py`): só o autor edita; exclusão na 1ª hora; ocultar vale em
  todos os lugares e só admin restaura o que admin ocultou; tudo entra `em_analise`; falha da IA **nunca** libera. Permissão de mural
  (ler/postar/moderar) vive em `escopos.py` — não replique `if` de permissão em rota ou template.
- **Perfis**: `Usuario.admin` e `Usuario.moderador`; use `u.equipe_moderacao` (admin ou moderador geral) para permissões de moderação e `u.admin` só para
  gerir usuários/perfis. Quem oculta fica em `oculta_por_papel` (`admin` > `moderacao` > `moderador`) e só o mesmo nível ou acima restaura.
- **Planos**: todo recurso pago passa por `planos.exigir(s, usuario, "atividade"|"campeonato"|"arena")` dentro do serviço (nunca só na tela); `PlanoNecessario` vira 402 na API e leva a `/planos`.
  Planos: `gratuito` (= **Usuário**: vê, publica no feed e participa; **não** organiza), `pro` (organiza atividades com limites), `organizador` (+ campeonatos), `arena` (tudo). `Plano.pode_atividade` liga/desliga organizar. O plano Usuário também tem preço do admin: em R$ 0 nada é cobrado; com valor, `planos.exigir(..., "basico")` (publicar, comentar, participar) exige acesso em dia (qualquer plano vigente serve). O CPF/CNPJ nunca é gravado (`cobranca.assinar` só repassa ao Asaas).
- **Auditoria** (`auditoria.registrar`): toda ação de usuário que mude estado grava um `Registro` (a tabela é só de inserção, com gatilho no
  Postgres). Ação nova de serviço = uma linha de `registrar`. Nunca UPDATE/DELETE em `registros`; a purga é `privacidade.purgar`.
- **Pessoas aparecem pelo `@usuario`** (nunca nome real/e-mail) em murais, comunidades, buscas e convites.
- **Localização do perfil só com consentimento** (`consent_localizacao`); sem ele a API recusa guardar lat/lng.
- **Termos**: mudar `termos.VERSAO` obriga todos a aceitar de novo (portão em `deps.py`).
- O `mural.js`/`mural.css` são compartilhados pelo site e pelo PWA; o PWA carrega os dois de `/static/`.

- **Produção = Vercel serverless + Supabase** (`docs/deploy.md`): arquivos só por `armazenamento.py` (nunca `Path.write_bytes` direto), nada de thread/estado em memória,
  tarefas periódicas só pelo endpoint de cron, limites de upload via `settings.limite_*_mb`. Tabelas do Supabase sempre com RLS ligado (`python -m playgo supabase`).

- **Chaves e jogo ao vivo** (`chaves.py`, `api/chaves.py`, `web/rotas_chaves.py`, `static/chaves.js|css`): placar, início e fim só dentro de `chaves.py`, com o jogo travado por `_travar()`
  (`populate_existing`, porque a sessão não expira ao salvar: relações como `jogo.equipe_a` ficam velhas — `_avancar` atualiza a relação e o id). Conduz quem `pode_gerir` ou é mesário
  (`CampeonatoMesario`); qualquer pessoa logada só lê. Sem websocket (Vercel): a tela consulta a API a cada poucos segundos. A semente do sorteio é bigint — sempre em texto na API. Formatos: `eliminatoria`, `pontos_corridos`, `grupos` (jogos com `fase`/`grupo`; o mata-mata `fase="mata_mata"` nasce em `_gerar_mata_mata_se_pronto` quando o último jogo de grupo termina). Regulamento em PDF: `campeonatos.anexar_regulamento` (confere `%PDF-`, `settings.limite_pdf_mb`, guarda por `armazenamento.py`); o download usa URL assinada de 10 min (`url_regulamento`), sem Bearer no link. Elenco (`elenco.py`, `EquipeComponente`): só com `Campeonato.cadastro_elenco`; o **RG só para a organização e o capitão da equipe** (listas públicas mostram apenas a contagem; o RG nunca vai para aviso nem auditoria). Edição do campeonato em `campeonatos.editar`. Pontuação por sets: `Campeonato.placar_modo` (`sets`) + `JogoSet`; `Jogo.placar_a/b` guarda os **sets** ganhos e os pontos do set ficam em `JogoSet` (preset por esporte em `campeonatos.PLACAR_POR_SETS`; regras só mudam antes do 1º jogo). Cabeças de chave e grupos ficam em `ChaveEquipe`; folgas da eliminatória nascem `encerrado` e não contam como jogo começado.
  Teste que sobe `TestClient(app)` não pode usar a sessão `s` aberta ao mesmo tempo (o startup altera tabelas e trava): monte os dados pela API.

- **Publicação própria** (`Dockerfile`, `deploy/`, `docs/publicar-do-zero.md`): o mesmo código roda fora do Vercel/Supabase com `PLAYGO_ARMAZENAMENTO=local` (volume), agendador interno e `PLAYGO_COOKIE_SEGURO`/`PLAYGO_CONFIAR_PROXY` atrás do Caddy; `/saude` é a verificação de saúde. Dependências do servidor em `requirements-servidor.txt` (o `requirements.txt` do Vercel não leva o uvicorn).

- **Instagram** (`instagram.py`, `api/instagram.py`, `web/rotas_instagram.py`, `static/instagram.js`): só vai com **consentimento do autor** (`compartilhar_instagram`, só feed geral e com mídia) **e** aprovação da equipe; nunca publicar direto. A API da Meta não exclui posts. O token fica em `ConfigExterna` (renova sozinho); o ID/chave do app vêm do ambiente e a chave **nunca** vai para o git. `instagram._http`/`_pausa` são trocados nos testes.

- **Guia de locais** (`locais.py`, tabela `locais`, `/gestao/locais`, seção "Locais e lojas" no Explorar, `descoberta.TIPOS` inclui `local`): locais públicos de prática, lojas e arenas ainda não assinantes. **Só informação** (nada de reserva/cobrança). Só `Usuario.admin` cadastra/edita/oculta (`locais._exigir_admin`); todos os logados leem. Obrigatórios: tipo, nome, endereço, ponto no mapa, horário de funcionamento e telefone; e-mail, site, regras, descrição e esportes são opcionais. Não confundir com `Arena` (esta tem quadras e agenda).

## Comandos

```powershell
.\scripts\dev_db.ps1 start
.venv\Scripts\python -m playgo demo --refazer   # apaga e recria os dados de demonstração
.venv\Scripts\python -m playgo web              # http://localhost:8010  (site /, app /app/, API /api/v1)
.venv\Scripts\python -m pytest tests
.venv\Scripts\python scripts\gerar_marca.py      # regenera logo, favicon e ícones do app (marca/)
.venv\Scripts\python -m playgo purgar         # elimina registros/publicações excluídas além do prazo de guarda
```

## Fases do projeto (docs §15)

MVP + protótipo estão feitos. **Fase 2:** reserva e agenda com reserva pelo app. **Fase 3:** pagamento, gestão completa de
campeonato (tabelas/confrontos/classificação — RF-009), ranking, avaliações/reputação (seção 11), monetização. Push no
aparelho também está pendente. Mural, comunidades e LGPD: ver `docs/lgpd.md` (inclui o que depende do jurídico). Veja `docs/rastreabilidade.md`.
