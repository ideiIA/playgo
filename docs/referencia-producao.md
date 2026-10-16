# Referência de produção — variáveis, banco e comandos (servidor próprio)

Complementa o roteiro [publicar-do-zero.md](publicar-do-zero.md). Tudo aqui vale para o `deploy/.env` (Docker) ou para o
ambiente do processo (`python -m playgo web`).

## 1. Variáveis de ambiente

As do compose (`DOMINIO`, `EMAIL_ACME`, `POSTGRES_PASSWORD`) são lidas pelo `docker-compose.yml`; as `PLAYGO_*` pelo aplicativo.
`deploy/gerar_credenciais.py` cria o arquivo com as obrigatórias já preenchidas e **novas**.

### Obrigatórias

| Variável | Exemplo | O que é |
|---|---|---|
| `DOMINIO` | `playgo.ideiiaapp.com.br` | endereço público, sem `https://` (Caddy e HTTPS) |
| `EMAIL_ACME` | `voce@ideiiaapp.com.br` | e-mail dos avisos do certificado Let's Encrypt |
| `POSTGRES_PASSWORD` | *(gerada)* | senha do usuário `playgo` do Postgres do compose |
| `PLAYGO_CHAVE_SESSAO` | *(gerada, 48+ caracteres)* | assina cookies de sessão e tokens do app. **Trocar desloga todo mundo** |
| `PLAYGO_CRON_SECRET` | *(gerada)* | protege `/api/v1/cron/ciclo` (usado se agendar por fora) |

O compose define sozinho: `PLAYGO_DATABASE_URL`, `PLAYGO_URL_PUBLICA`, `PLAYGO_CONFIAR_PROXY=true`, `PLAYGO_COOKIE_SEGURO=true`,
`PLAYGO_ARMAZENAMENTO=local` e `PLAYGO_PASTA_UPLOADS=/dados/uploads` (no `Dockerfile`).

### Banco (só no modo "banco externo")

| Variável | Exemplo |
|---|---|
| `PLAYGO_DATABASE_URL` | `postgresql+psycopg://playgo:SENHA@HOST:5432/playgo` (acrescente `?sslmode=require` se o provedor exigir TLS) |

### Identidade e LGPD (preencher antes de abrir ao público)

| Variável | Padrão | O que é |
|---|---|---|
| `PLAYGO_CONTROLADOR_NOME` | `PlayGo (razão social a definir)` | razão social que aparece na Política de Privacidade |
| `PLAYGO_CONTATO_PRIVACIDADE` | `privacidade@playgo.invalid` | e-mail do encarregado/contato de privacidade |
| `PLAYGO_RETENCAO_REGISTROS_DIAS` | `365` | prazo de guarda dos registros de interação (confirmar com o jurídico) |
| `PLAYGO_FUSO` | `America/Campo_Grande` | fuso do "hoje" e dos horários |

### Integrações (opcionais, cada uma liga um recurso)

| Variável | Para quê |
|---|---|
| `PLAYGO_GOOGLE_CLIENT_ID`, `PLAYGO_GOOGLE_CLIENT_SECRET` | login com Google. Redirecionamento: `https://DOMINIO/auth/google/retorno` |
| `PLAYGO_ASAAS_API_KEY`, `PLAYGO_ASAAS_AMBIENTE` (`sandbox`/`producao`), `PLAYGO_ASAAS_WEBHOOK_TOKEN` | cobrança dos planos. Webhook: `https://DOMINIO/api/v1/cobranca/asaas` |
| `PLAYGO_TESTE_DIAS` (30), `PLAYGO_TOLERANCIA_DIAS` (7) | período de teste e de tolerância dos planos |
| `PLAYGO_INSTAGRAM_APP_ID`, `PLAYGO_INSTAGRAM_APP_SECRET`, `PLAYGO_INSTAGRAM_VERSAO` (`v21.0`) | Instagram do PlayGo. Retorno: `https://DOMINIO/instagram/retorno`. Ver [instagram.md](instagram.md) |
| `PLAYGO_YOUTUBE_CLIENT_ID`, `PLAYGO_YOUTUBE_CLIENT_SECRET`, `PLAYGO_YOUTUBE_MAX_SIMULTANEAS` (4) | transmissão ao vivo dos jogos no YouTube oficial. Retorno: `https://DOMINIO/youtube/retorno`. Ver [youtube.md](youtube.md) |
| `PLAYGO_ANTHROPIC_API_KEY` **ou** `PLAYGO_GEMINI_API_KEY`, `PLAYGO_IA_PROVEDOR`, `PLAYGO_MODELO_MODERACAO` | moderação por IA. Sem chave, foto/vídeo vão para a análise da equipe |
| `PLAYGO_MODERACAO_MIDIA_SEM_IA` (`revisar`/`liberar`) | o que fazer com mídia quando nenhuma IA analisa. **Mantenha `revisar`** |

### Arquivos, limites e comportamento

| Variável | Padrão | O que é |
|---|---|---|
| `PLAYGO_MAX_FOTO_MB` / `_VIDEO_MB` / `_PDF_MB` | 10 / 50 / 15 | tamanho máximo de foto, vídeo e PDF do regulamento. O Caddy aceita corpo de até 60 MB |
| `PLAYGO_MAX_MIDIAS_POST` | 4 | fotos/vídeos por publicação |
| `PLAYGO_MAX_TEXTO_POST` | 2000 | caracteres por publicação |
| `PLAYGO_JANELA_EXCLUSAO_MIN` | 60 | o autor só exclui na primeira hora |
| `PLAYGO_LIMITE_POSTS_10MIN` | 10 | freio de publicações em 10 min |
| `PLAYGO_DENUNCIAS_PARA_ANALISE` | 3 | denúncias que mandam o conteúdo para a análise |
| `PLAYGO_DIAS_TOKEN_APP` | 30 | validade do token do app |
| `PLAYGO_RAIO_PADRAO_KM` / `PLAYGO_LIMITE_RESULTADOS` | 10 / 60 | busca por proximidade |
| `PLAYGO_LATITUDE_PADRAO` / `_LONGITUDE_PADRAO` | Campo Grande | onde o mapa abre sem localização |
| `PLAYGO_AGENDADOR_NA_WEB` | `true` | rotinas (lembretes, reposição, planos, Instagram) dentro do aplicativo |
| `PLAYGO_INTERVALO_AGENDADOR_S` | 60 | de quanto em quanto tempo o agendador roda |
| `PLAYGO_AUTO_MIGRAR` | `true` | cria/atualiza tabelas ao subir (deixe ligado no servidor próprio) |

### Só para Vercel/Supabase (não usar no servidor próprio)

`PLAYGO_SERVERLESS`, `PLAYGO_SUPABASE_URL`, `PLAYGO_SUPABASE_SERVICE_KEY`, `PLAYGO_SUPABASE_BUCKET`, `CRON_SECRET`.

## 2. Banco de dados

| Item | Valor |
|---|---|
| Servidor | PostgreSQL **16** (qualquer 13+ serve). Sem extensões (nada de PostGIS) |
| Banco / usuário | `playgo` / `playgo` (donos das tabelas). Codificação **UTF8** |
| Tabelas | ~39, criadas sozinhas na primeira subida (`create_all` + colunas novas por `ALTER … IF NOT EXISTS`) |
| Gatilho | `tg_registros_somente_insercao`: a trilha de auditoria (`registros`) só aceita INSERT |
| Dados iniciais | 14 esportes (`modalidades`) |
| Permissões do usuário | precisa criar tabelas, funções e gatilhos (ser dono do banco basta) |
| Datas | `timestamp` sem fuso, no horário do servidor (`PLAYGO_FUSO`) |
| Acesso | só pela rede interna do Docker; a porta 5432 **não** é publicada |

### Criar o banco e o usuário (em qualquer Postgres)

```bash
pip install "psycopg[binary]"
python3 deploy/criar_banco.py --admin-url "postgresql://postgres:SENHA_ADMIN@HOST:5432/postgres" --nome playgo --usuario playgo
# imprime: PLAYGO_DATABASE_URL=postgresql+psycopg://playgo:<senha forte nova>@HOST:5432/playgo
```

Equivalente em SQL (como superusuário):

```sql
CREATE ROLE playgo WITH LOGIN PASSWORD 'SENHA-FORTE-NOVA';
CREATE DATABASE playgo OWNER playgo ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE playgo FROM PUBLIC;
GRANT ALL PRIVILEGES ON DATABASE playgo TO playgo;
```

### Criar as tabelas sem subir o site (opcional)

```bash
docker compose --env-file .env run --rm app python -m playgo init-db
```

### Conferir

```bash
docker compose --env-file .env exec db psql -U playgo -d playgo -c "\dt" | head
docker compose --env-file .env exec db psql -U playgo -d playgo -tc "select count(*) from modalidades;"
```

### Backup e restauração

```bash
./deploy/backup.sh                       # banco (pg_dump -Fc) + uploads, com rotação de 14 dias
# restaurar o banco:
docker compose --env-file .env exec -T db pg_restore -U playgo -d playgo --clean --if-exists --no-owner < banco-AAAAMMDD-HHMMSS.dump
# restaurar os arquivos:
docker run --rm -v deploy_dados_uploads:/dados -v /var/backups/playgo:/backup alpine tar -C /dados -xzf /backup/uploads-AAAAMMDD-HHMMSS.tar.gz
```

## 3. Comandos do dia a dia

```bash
cd /opt/playgo/deploy
docker compose --env-file .env up -d --build        # subir/atualizar
docker compose --env-file .env ps                   # estado
docker compose --env-file .env logs -f app          # logs do aplicativo
docker compose --env-file .env logs -f caddy        # HTTPS / proxy
docker compose --env-file .env restart app          # reiniciar o aplicativo
docker compose --env-file .env exec app python -m playgo purgar   # purga LGPD
docker compose --env-file .env down                 # parar (os dados ficam nos volumes)
curl https://DOMINIO/saude                          # {"ok": true}
```

## 4. Portas e redes

| Porta | Quem | Aberta na internet? |
|---|---|---|
| 80, 443 | Caddy (HTTPS) | **sim** |
| 8010 | aplicativo (interna) | não |
| 5432 | Postgres (interna) | **não** |
| 22 | SSH | sim (restrinja por chave e, se possível, por IP) |
