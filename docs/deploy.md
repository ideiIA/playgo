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

## Recebimentos de parceiros — Arena recebe pelo app (Asaas BaaS, subconta white label)

A Arena cobra as reservas pelo PlayGo e o dinheiro cai na **subconta Asaas dela**, criada e controlada por nós. A Arena **não acessa o painel do Asaas** nem recebe e-mail dele:
cadastro, pendências de documentos, saldo, extrato, saque e link de cobrança acontecem dentro do PlayGo (site e app). A conta-mãe atual (planos e eventos avulsos, seção acima) não muda.
Código: `playgo/recebimentos.py`, `playgo/api/recebimentos.py`, `static/recebimentos.js|css`. Controles de segurança: `docs/seguranca-recebimentos.md`.

### Fases (`PLAYGO_RECEBIMENTOS_FASE`)

| Fase | O que vale |
|---|---|
| `desligado` (padrão) | Nenhuma rota de recebimento chama o Asaas; as telas e a API respondem "recurso ainda não disponível". Os botões "Receber pelo app" e "Cobrar" não aparecem. |
| `avaliacao` | Só Arenas **liberadas pelo admin** (Administração → Recebimentos de parceiros) criam subconta. Painel com nº de subcontas (máx. 10), maior total cobrado por subconta (máx. R$ 2.000), data da 1ª subconta de **produção** e dias restantes dos 60. **Bloqueio local preventivo a 80%** de cada limite (8 subcontas, R$ 1.600 por subconta, 48 dias), com mensagem clara. Só subcontas de produção contam (`PLAYGO_ASAAS_AMBIENTE=producao`). |
| `baas` | Sem os bloqueios locais da avaliação. **Só ligue depois que contrato e Segurança da Informação forem concluídos** com o Asaas. |

### Variáveis (`.env.producao` / `deploy/.env`)

- `PLAYGO_RECEBIMENTOS_FASE` (acima) e `PLAYGO_ASAAS_AMBIENTE` (`sandbox` | `producao`; a chave da conta-mãe segue em `PLAYGO_ASAAS_API_KEY`).
- `PLAYGO_ASAAS_WEBHOOK_TOKEN_PARCEIROS`: token **próprio** dos parceiros (32 a 255 caracteres; gere um texto longo e secreto). Vale para o webhook das subcontas **e** para a validação de saque.
- `PLAYGO_URL_PUBLICA` (ex.: `https://playgo.ideiiapp.com.br`): o webhook é cadastrado junto com a subconta usando este endereço.
- `PLAYGO_ASAAS_WALLET_ID`: `walletId` da **conta-mãe** (recebe a comissão do split; a validação de saque só aceita esta carteira).
- `PLAYGO_RECEBIMENTOS_COMISSAO_PERCENTUAL` (padrão 0) e `PLAYGO_RECEBIMENTOS_COMISSAO_FIXA` (padrão 0): parte do PlayGo em cada cobrança, por split. **Nada fixo no código**: as condições comerciais com o Asaas ainda não estão definidas.
- `PLAYGO_RECEBIMENTOS_TAXA_POR` (`arena` | `plataforma`, padrão `arena`): quem arca com a taxa do Asaas; fica registrado no livro-razão (`taxa_asaas`). Com `plataforma` a taxa é registrada sem descontar do saldo da Arena — o repasse real depende das condições comerciais.
- `PLAYGO_RECEBIMENTOS_SAQUE_MINIMO` (padrão 10).
- `PLAYGO_RECEBIMENTOS_CHAVE`: chave Fernet (base64 de 32 bytes; `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) que cifra a apiKey das subcontas e a chave Pix de saque. **Faça backup**: sem ela as credenciais guardadas não abrem. Em branco, é derivada da chave de sessão (trocar a chave de sessão invalidaria as credenciais).
- `PLAYGO_RECEBIMENTOS_EMAIL_ALERTAS`: destinatário dos avisos do webhook das subcontas.
- `PLAYGO_RECEBIMENTOS_TEXTO_ASAAS` e `PLAYGO_RECEBIMENTOS_LINKS_ASAAS` (`Texto|https://url;Outro|https://url`): rodapé obrigatório do Asaas (veja abaixo).

Depois de atualizar o código, rode `python -m playgo supabase` (com `PLAYGO_ENV_FILE=.env.producao`) para criar as tabelas novas (`recebedores`, `lancamentos_recebedor`, `saques_recebedor`, `eventos_recebimento`) **com RLS**. O banco próprio recebe as colunas novas sozinho (`ALTER TABLE … IF NOT EXISTS`).

### Webhooks e validação de saque (cadastrar no Asaas)

1. **Webhook das subcontas**: já vai junto com cada subconta criada (campo `webhooks` do `POST /v3/accounts`): `https://SEU-DOMINIO/api/v1/recebimentos/asaas`, token `PLAYGO_ASAAS_WEBHOOK_TOKEN_PARCEIROS` no cabeçalho `asaas-access-token`, eventos de cobrança (`PAYMENT_CONFIRMED/RECEIVED/REFUNDED/DELETED/OVERDUE/SPLIT_DONE`), transferência (`TRANSFER_DONE/FAILED/CANCELLED`) e situação da conta (`ACCOUNT_STATUS_*`). Responde 200 depois de validar o token (401 se o token estiver errado), é idempotente e não interfere no webhook dos planos (`/api/v1/cobranca/asaas`).
2. **Validação de saque** (o Asaas chama o nosso servidor ~5 s depois de cada transferência e na liquidação de cada split; resposta inválida ou 3 falhas = operação cancelada): no Asaas, **Menu do usuário → Integrações → Mecanismos de segurança** (validação de saque via webhook), informe a URL `https://SEU-DOMINIO/api/v1/recebimentos/asaas/validar-saque` e o **mesmo token**. O PlayGo confere com o saque que registrou (valor, destino) e responde `{"status":"APPROVED"}` ou `{"status":"REFUSED","refuseReason":"…"}`; **recusa toda transferência que não registrou** (inclui PIX_REFUND, BILL, recarga e Pix QR Code) e só aprova split de cobranças criadas por nós para a carteira da conta-mãe. Faça um saque de teste no sandbox antes de ligar em produção.
3. Rotação da apiKey de uma subconta (Administração → Recebimentos → "Trocar chave"): exige antes, no Asaas, **Integrações → Chaves de API → Gerenciamento de chaves de API de subcontas** (liberação de 2 horas) e o IP do servidor na whitelist.

### Aviso regulatório obrigatório (Resolução Conjunta nº 16/17)

O Asaas deve aparecer como instituição prestadora do serviço em **todas** as telas e mensagens de recebimento/pagamento (cadastro, status, saldo, extrato, saque, link de cobrança, avisos). O rodapé sai de `PLAYGO_RECEBIMENTOS_TEXTO_ASAAS` e `PLAYGO_RECEBIMENTOS_LINKS_ASAAS`.

> **TODO (Playbook do Asaas):** o texto provisório é "Pagamentos processados por Asaas Gestão Financeira Instituição de Pagamento S.A.". O texto oficial e os links (marca, termos, política) vêm do **Playbook** do Asaas, que ainda não recebemos: trocar as duas variáveis (e o padrão em `config.py`) assim que chegar.

### Checklist do que falta fazer com o Asaas

O processo do Asaas (BaaS): **(1)** conta-mãe PJ com análise cadastral aprovada; **(2)** criar a primeira subconta via API — isso **inicia o Período de Avaliação** (até 60 dias corridos, no máximo 10 subcontas e R$ 2.000 em cobranças por subconta; ao atingir qualquer limite o Asaas bloqueia novas subcontas e cobranças até concluir a avaliação); **(3)** pedir a habilitação do BaaS e preencher o checklist do modelo de negócio (análise em até ~3 dias úteis); **(4)** após aprovação: contrato, etapas de Segurança da Informação e liberação técnica.

- [ ] Conta-mãe PJ aprovada (etapa 1).
- [ ] Validar tudo no **sandbox** (fase `avaliacao` ou `baas`, `PLAYGO_ASAAS_AMBIENTE=sandbox`): ativação, pendências com e sem `onboardingUrl`, aprovação, cobrança com split, webhook, saque e validação de saque.
- [ ] Criar a **primeira subconta de produção somente com o código pronto** (isso inicia os 60 dias).
- [ ] Enviar o checklist do modelo de negócio BaaS (etapa 3) e anexar `docs/seguranca-recebimentos.md` ao questionário de Segurança da Informação.
- [ ] Obter o **Playbook** (marca e textos) e atualizar o rodapé.
- [ ] Fechar **condições comerciais** (taxas) e o **contrato**; então ajustar comissão e `PLAYGO_RECEBIMENTOS_TAXA_POR`.
- [ ] Ativar a **validação de saque** (URL + token acima) e conferir o `PLAYGO_ASAAS_WALLET_ID`.
- [ ] Confirmar que a conta-mãe aceita o webhook por subconta (ou cadastrar o webhook manualmente) e liberar o gerenciamento de chaves de subcontas se for rotacionar.
- [ ] Só então mudar para `PLAYGO_RECEBIMENTOS_FASE=baas`.

A integração foi testada apenas com transporte simulado: valide cada passo no sandbox antes de cobrar de verdade. Campos da documentação oficial usados: criar subconta (`POST /v3/accounts`), documentos (`GET/POST /v3/myAccount/documents`), situação (`GET /v3/myAccount/status`), dados comerciais (`POST /v3/myAccount/commercialInfo`), saldo (`GET /v3/finance/balance`), transferência Pix (`POST /v3/transfers`), split e chaves de API de subcontas.

## Pendências conhecidas

- **Vídeos acima de 4 MB**: exigem upload direto do navegador para o Storage (URL assinada de envio). É a próxima etapa técnica.
- **Domínio próprio**, e-mail transacional e push no aparelho: não configurados.
- Os textos jurídicos continuam sendo minuta (ver `docs/lgpd.md`); como o repositório é público, o código da moderação e as listas de regras também ficam visíveis.
- Se o Vercel recusar o `includeFiles` ou o tamanho do pacote, o primeiro log de build indica o ajuste (as dependências somam bem menos que o limite de 250 MB).
