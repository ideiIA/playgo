# Publicar o PlayGo: GitHub + Supabase + Vercel

Conta responsável: **ideiia@ideiia.com.br**. Repositório: https://github.com/ideiIA/playgo

```
GitHub (código) ──► Vercel (site + app + API, Python serverless)
                         │  PLAYGO_DATABASE_URL (pooler, porta 6543)
                         ├──► Supabase Postgres  (tabelas com RLS ligado)
                         └──► Supabase Storage   (bucket privado + URL assinada de 5 min)
```

## 1. GitHub

No terminal, na pasta do projeto (o Git abre o navegador: entre como **ideiIA**):

```powershell
git push -u origin main
```

O repositório é **público**. Ficam de fora (`.gitignore`): `PlayGO.docx`, o protótipo HTML, `.env`, `.chave_sessao`, `uploads/`.

## 2. Supabase (na conta da ideiia)

1. Em https://supabase.com/dashboard crie um projeto (nome sugerido: `playgo`, região **South America (São Paulo)**). Anote a senha do banco.
2. **Settings → Data API**: copie a *Project URL* (`https://<ref>.supabase.co`).
3. **Settings → API Keys**: copie a chave **service_role** (secreta; nunca vai para o navegador nem para o git).
4. **Connect → Transaction pooler**: copie a string de conexão e ajuste o início para `postgresql+psycopg://`.
5. Crie o arquivo `.env.producao` (gitignored) na raiz — **separado do `.env`**, para o seu servidor local nunca gravar no banco de produção:

```
PLAYGO_DATABASE_URL=postgresql+psycopg://postgres.<ref>:<SENHA>@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
PLAYGO_SUPABASE_URL=https://<ref>.supabase.co
PLAYGO_SUPABASE_SERVICE_KEY=<service_role>
```

6. Prepare o banco e o Storage (uma vez; repita ao atualizar o esquema):

```powershell
$env:PLAYGO_ENV_FILE = ".env.producao"; .venv\Scripts\python -m playgo supabase
```

Isso cria as tabelas, o gatilho da trilha de auditoria, as modalidades, **liga o RLS em todas as tabelas sem política** (a API pública do
Supabase não enxerga nada; só o nosso servidor) e cria o bucket **privado** `playgo-midia`.

> Os dados de demonstração (`python -m playgo demo`) **não** devem ir para produção.
> O primeiro usuário que se cadastrar vira administrador; faça isso você mesmo antes de divulgar o endereço.

## 3. Vercel

1. https://vercel.com/new → entre com a conta da ideiia → **Import** `ideiIA/playgo` (o Vercel precisa ter acesso ao repositório pelo app GitHub dele).
2. Framework Preset: **Other**. Não precisa de Build Command. A entrada é `api/index.py` e o roteamento está em `vercel.json`.
3. Gere as variáveis de produção e cole em **Settings → Environment Variables → Import .env**:

```powershell
.venv\Scripts\python scripts\gerar_env_vercel.py
```

   Copie o conteúdo de `.env.vercel` (o arquivo é ignorado pelo git). Ele inclui `PLAYGO_SERVERLESS=true`, uma `PLAYGO_CHAVE_SESSAO` nova e o `CRON_SECRET`.
   Acrescente, quando for o caso: `PLAYGO_ANTHROPIC_API_KEY` ou `PLAYGO_GEMINI_API_KEY` (moderação por IA), `PLAYGO_CONTROLADOR_NOME`, `PLAYGO_CONTATO_PRIVACIDADE`.
4. **Deploy**. Abra a URL, cadastre-se (vira administrador) e teste: publicar com foto, foto de perfil, moderação.

## Como o PlayGo se comporta no Vercel

| Ponto | Como foi resolvido |
|---|---|
| Sem disco persistente | Fotos, vídeos e fotos de perfil vão para o **Supabase Storage** (bucket privado). O servidor confere a permissão e redireciona para uma URL assinada de 5 min. |
| Sem thread em segundo plano | A análise de moderação roda **dentro da requisição**. O agendador virou `GET /api/v1/cron/ciclo`, protegido por `CRON_SECRET`. |
| Tabelas | Não são criadas a cada partida (`PLAYGO_AUTO_MIGRAR` desligado no serverless). Rode `python -m playgo supabase` ao mudar o esquema. |
| Conexões | `NullPool` + pooler do Supabase (porta 6543, sem prepared statements). |
| Limite de 4,5 MB por requisição | Fotos são reduzidas no navegador antes de enviar (2048 px). **Vídeos ficam limitados a 4 MB** por enquanto. |
| Chave de sessão | Obrigatória por variável de ambiente (não há disco para guardá-la). |

### Rotinas periódicas (lembretes, "falta gente", encerramento)

`vercel.json` agenda o cron **uma vez por dia (09:00 UTC)**, o máximo do plano Hobby. Lembretes "seu jogo começa em 2 horas" precisam de frequência maior:

- **Vercel Pro**: troque `"0 9 * * *"` por `"*/10 * * * *"` em `vercel.json`.
- **Plano grátis**: use o `pg_cron` do Supabase para chamar o endereço a cada 10 minutos (SQL Editor):

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net;
select cron.schedule('playgo-ciclo', '*/10 * * * *', $$
  select net.http_get(
    url := 'https://SEU-APP.vercel.app/api/v1/cron/ciclo',
    headers := jsonb_build_object('Authorization', 'Bearer SEU_CRON_SECRET'));
$$);
```

## Planos e mensalidades (Asaas)

Perfis: **Usuário** (grátis: vê, publica no feed e participa), **Pro** (organiza atividades com limites), **Organizador** (campeonatos e atividades sem limite) e **Arena**
(tudo do Organizador + arenas, quadras, agenda e divulgação de horários). Teste grátis de 30 dias (começa sozinho na primeira vez que a pessoa
precisa do recurso, uma vez por plano) e 7 dias de tolerância; depois, não cria nem divulga nada novo (o que existe continua). O administrador não paga.

1. **Preços e limites**: entre como administrador em **Administração → Planos e mensalidades** e defina o valor mensal de Organizador e Arena
   (enquanto for R$ 0, o plano não pode ser assinado) e, se quiser, os limites do plano gratuito.
2. **Asaas**: crie a conta em https://www.asaas.com (teste antes no sandbox: https://sandbox.asaas.com) e gere a chave de API (Integrações → Chave de API).
3. No `.env.producao` / variáveis do Vercel: `PLAYGO_ASAAS_API_KEY`, `PLAYGO_ASAAS_AMBIENTE` (`sandbox` ou `producao`) e `PLAYGO_ASAAS_WEBHOOK_TOKEN` (um texto longo e secreto que você inventa).
4. No Asaas, **Integrações → Webhooks**: URL `https://SEU-APP.vercel.app/api/v1/cobranca/asaas`, o **mesmo token** acima, versão v3, e marque os eventos de **Cobranças**
   (criada, confirmada, recebida, vencida, estornada, removida) e de **Assinaturas** (removida, inativada).
5. Sem a chave do Asaas, tudo funciona e o administrador libera planos à mão (**Conceder plano**, na lista de usuários).

O CPF/CNPJ é pedido só ao assinar, vai direto ao Asaas e **não é guardado** pelo PlayGo. **A integração foi testada apenas com transporte simulado**:
faça uma assinatura de teste no sandbox (Pix/cartão de teste) antes de cobrar de verdade.

Depois de atualizar o código, rode `python -m playgo supabase` (com `PLAYGO_ENV_FILE=.env.producao`) para criar as tabelas novas com RLS.

## Pendências conhecidas

- **Vídeos acima de 4 MB**: exigem upload direto do navegador para o Storage (URL assinada de envio). É a próxima etapa técnica.
- **Domínio próprio**, e-mail transacional e push no aparelho: não configurados.
- Os textos jurídicos continuam sendo minuta (ver `docs/lgpd.md`); como o repositório é público, o código da moderação e as listas de regras também ficam visíveis.
- Se o Vercel recusar o `includeFiles` ou o tamanho do pacote, o primeiro log de build indica o ajuste (as dependências somam bem menos que o limite de 250 MB).
