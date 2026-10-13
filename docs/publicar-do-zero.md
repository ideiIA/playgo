# Publicar o PlayGo do zero — servidor próprio, sem Supabase e sem Vercel

> Todas as variáveis, o banco e os comandos do dia a dia estão em [referencia-producao.md](referencia-producao.md).

Este roteiro coloca o site, o app (PWA) e a API no ar em **um servidor seu**, com o **Postgres em produção criado por você** e
**credenciais novas**. Tudo foi testado em contêineres (Docker): o banco sobe vazio, o PlayGo cria as tabelas, os gatilhos de
auditoria e os esportes iniciais sozinho, e o primeiro cadastro vira o administrador.

```
Internet ──► Caddy (HTTPS automático, 80/443) ──► PlayGo (uvicorn, porta 8010 interna)
                                                      ├──► Postgres 16 (rede interna, nunca exposto)
                                                      └──► volume de arquivos (fotos, vídeos, PDFs)
```

Os arquivos de `deploy/` e o `Dockerfile` fazem tudo. Você só roda comandos e preenche chaves de terceiros.

---

## 0. O que você precisa

| Item | Detalhe |
|---|---|
| Servidor (VPS) | Linux Ubuntu 22.04/24.04, **2 vCPU, 2 GB de RAM (4 GB melhor), 40 GB de SSD**. Serve Hetzner, DigitalOcean, Contabo, Hostinger, Locaweb, AWS Lightsail, Oracle Cloud… |
| Domínio | ex.: `playgo.ideiiaapp.com.br`, com acesso ao painel de DNS |
| E-mail | para os avisos do certificado HTTPS (Let's Encrypt) |
| Opcional | Google OAuth (login com Google), Asaas (cobrança), chave de IA (moderação) |

## 1. Servidor e DNS

1. Crie o VPS e anote o **IP público**.
2. No DNS do domínio, crie um registro **A**: `playgo` → IP do servidor. (Se o servidor tiver IPv6, crie também o **AAAA**.)
3. Entre por SSH e prepare o servidor:

```bash
# como root, no servidor
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh            # instala Docker e o plugin compose
apt -y install git ufw python3

# firewall: só SSH, HTTP e HTTPS (o banco NÃO é aberto para a internet)
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

> Dica de segurança: use chave SSH, desative o login por senha e, se quiser, instale o `fail2ban`.

## 2. Baixar o código

```bash
git clone https://github.com/ideiIA/playgo.git /opt/playgo
cd /opt/playgo
```

## 3. Gerar as credenciais novas

```bash
python3 deploy/gerar_credenciais.py --dominio playgo.ideiiaapp.com.br --email voce@ideiiaapp.com.br
```

Isso cria `deploy/.env` (fora do git, permissão 600) com **senha do Postgres, chave de sessão, segredo do cron e token do
webhook do Asaas, todos novos**. Depois edite o arquivo e preencha o que for usar:

```bash
nano deploy/.env
```

| Variável | O que é |
|---|---|
| `PLAYGO_CONTROLADOR_NOME`, `PLAYGO_CONTATO_PRIVACIDADE` | dados da empresa para a Política de Privacidade (LGPD) |
| `PLAYGO_GOOGLE_CLIENT_ID` / `_SECRET` | login com Google (passo 6) |
| `PLAYGO_ASAAS_API_KEY`, `PLAYGO_ASAAS_AMBIENTE` | cobrança (passo 7); comece em `sandbox` |
| `PLAYGO_ANTHROPIC_API_KEY` ou `PLAYGO_GEMINI_API_KEY` | moderação por IA (sem chave, fotos/vídeos vão para a análise da equipe) |

## 4. Subir

```bash
cd /opt/playgo/deploy
docker compose --env-file .env up -d --build
docker compose --env-file .env ps          # app e db devem aparecer "healthy"
curl https://playgo.ideiiaapp.com.br/saude # {"ok":true}
```

Na primeira subida o Caddy obtém o certificado HTTPS (leva um minuto; o DNS precisa já estar apontando).
Se algo falhar: `docker compose --env-file .env logs -f app` e `... logs caddy`.

## 5. Primeiro acesso

1. Abra `https://playgo.ideiiaapp.com.br` → **Criar conta**. **A primeira conta criada vira administradora.** Crie a sua antes
   de divulgar o endereço.
2. Entre em **Administração → Planos** e defina os preços (Usuário, Pro, Organizador, Arena) e os limites.
3. Teste: criar atividade, campeonato, subir uma foto e um PDF de regulamento.

## 6. Login com Google

No Google Cloud (projeto do PlayGo) → **Clientes → seu cliente OAuth**:

- **Origens JavaScript autorizadas:** `https://playgo.ideiiaapp.com.br`
- **URIs de redirecionamento:** `https://playgo.ideiiaapp.com.br/auth/google/retorno`
- **Branding:** domínio autorizado `ideiiaapp.com.br`, links da política (`/politica-de-privacidade`) e dos termos (`/termos`).

Coloque o ID e a chave no `deploy/.env` e aplique: `docker compose --env-file .env up -d`.

## 7. Cobrança (Asaas)

1. No Asaas (comece pelo **sandbox**): gere a chave de API e coloque em `PLAYGO_ASAAS_API_KEY`.
2. Em **Integrações → Webhooks**: URL `https://playgo.ideiiaapp.com.br/api/v1/cobranca/asaas`, **token = `PLAYGO_ASAAS_WEBHOOK_TOKEN`
   do seu `.env`** (o gerador já criou e mostrou), eventos de Cobranças e Assinaturas.
3. Faça uma assinatura de teste. Para produção, troque `PLAYGO_ASAAS_AMBIENTE=producao` e a chave.

## 7b. Instagram do PlayGo (opcional)

Preencha `PLAYGO_INSTAGRAM_APP_ID` e `PLAYGO_INSTAGRAM_APP_SECRET` no `deploy/.env`, cadastre no app da Meta a URI de retorno
`https://playgo.ideiiaapp.com.br/instagram/retorno`, aplique (`docker compose --env-file .env up -d`) e, como administrador, use
**Administração → Conectar Instagram**. Detalhes em [instagram.md](instagram.md).

## 8. Backups (não pule)

O banco e os arquivos enviados vivem em volumes do Docker. Agende o backup diário:

```bash
chmod +x /opt/playgo/deploy/backup.sh
crontab -e
# todo dia às 3h
0 3 * * *  cd /opt/playgo/deploy && ./backup.sh >> /var/log/playgo-backup.log 2>&1
```

Gera `banco-*.dump` e `uploads-*.tar.gz` em `/var/backups/playgo` e apaga o que passa de 14 dias. **Copie esses arquivos
para fora do servidor** (outro provedor, S3/R2, `rclone`) — backup no mesmo disco não protege de perda do servidor.
Para restaurar o banco: comando no fim do `backup.sh`. **Teste uma restauração** antes de depender dela.

## 9. Atualizar a versão

```bash
cd /opt/playgo && git pull
cd deploy && docker compose --env-file .env up -d --build
```

O esquema do banco se atualiza sozinho ao subir (`PLAYGO_AUTO_MIGRAR`). Os dados e os arquivos ficam nos volumes.

## 10. Rotinas e monitoramento

- **Lembretes, reposição de vagas, encerramentos e vencimento de planos:** rodam sozinhos dentro do aplicativo (agendador interno).
- **Saúde:** `https://seu-dominio/saude` (use em um monitor externo, como UptimeRobot).
- **Logs:** `docker compose --env-file .env logs -f app`.
- **Purga LGPD** (registros e publicações excluídas além do prazo): `docker compose --env-file .env exec app python -m playgo purgar`
  (agende mensalmente, se a política de retenção exigir).

---

## Alternativa: banco de dados em outro lugar (gerenciado ou outro servidor)

Se preferir o Postgres fora do servidor do aplicativo (RDS, Neon, DigitalOcean Managed, Railway…):

1. Crie o banco e o usuário da aplicação **com senha nova** (precisa de um usuário administrador do Postgres):

```bash
pip install "psycopg[binary]"
python3 deploy/criar_banco.py --admin-url "postgresql://admin:SENHA_ADMIN@HOST:5432/postgres" --nome playgo --usuario playgo
```

   O script cria o usuário e o banco (idempotente), gera uma senha forte e imprime a linha pronta:
   `PLAYGO_DATABASE_URL=postgresql+psycopg://playgo:...@HOST:5432/playgo`.

2. Cole essa linha no `deploy/.env` (acrescente `?sslmode=require` no fim se o provedor exigir TLS) e suba **sem** o Postgres local:

```bash
cd /opt/playgo/deploy
docker compose -f docker-compose.banco-externo.yml --env-file .env up -d --build
```

3. O PlayGo cria tabelas, gatilhos e esportes na primeira subida. Faça o backup no próprio provedor do banco (snapshots/PITR)
   além do `uploads` do `backup.sh`.

> Equivalente em SQL, caso prefira rodar à mão com `psql`:
> ```sql
> CREATE ROLE playgo WITH LOGIN PASSWORD 'SENHA-FORTE';
> CREATE DATABASE playgo OWNER playgo ENCODING 'UTF8' TEMPLATE template0;
> REVOKE ALL ON DATABASE playgo FROM PUBLIC;
> ```

## Levar os dados do Supabase para o novo banco (opcional)

Só se você já tiver usuários e conteúdo no Supabase. Faça com o site em manutenção:

```bash
# 1) com o PlayGo novo já no ar (tabelas criadas), exporte só os dados do Supabase:
pg_dump "postgresql://postgres.<ref>:<SENHA>@aws-0-sa-east-1.pooler.supabase.com:5432/postgres" \
  --data-only --schema=public --no-owner --disable-triggers -Fc -f dados-supabase.dump
# 2) importe no novo banco:
pg_restore --data-only --no-owner --disable-triggers -d "postgresql://playgo:SENHA@HOST:5432/playgo" dados-supabase.dump
```

As fotos, vídeos e PDFs ficam no **Supabase Storage** (bucket `playgo-midia`) e precisam ser baixados e copiados para o
volume `uploads` do servidor novo, mantendo os nomes dos arquivos. Se o site está no início, é mais simples recomeçar do zero.

## Segurança — checklist final

- [ ] `ufw status`: só 22, 80 e 443 abertos; a porta 5432 **não** aparece.
- [ ] `deploy/.env` com permissão 600 e **fora do git** (o `.gitignore` já cobre).
- [ ] Senha do banco, chave de sessão e segredos **diferentes** dos de qualquer ambiente anterior (o gerador cria novos).
- [ ] Backups agendados **e copiados para fora** do servidor, com uma restauração testada.
- [ ] Atualizações do sistema (`unattended-upgrades`) e do PlayGo (passo 9) em dia.
- [ ] Dados da empresa (controlador e contato de privacidade) preenchidos e Termos revisados pelo jurídico (`docs/lgpd.md`).
- [ ] O projeto antigo no Vercel/Supabase pausado ou removido quando o novo estiver validado, e as chaves antigas revogadas.
