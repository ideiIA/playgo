# Segurança dos recebimentos de parceiros (Asaas BaaS)

Controles implementados no PlayGo para receber pagamentos de parceiros (Arenas) por **subconta Asaas** (modelo BaaS, white label), prontos para anexar ao questionário de
Segurança da Informação do Asaas. Código: `playgo/recebimentos.py`, `playgo/api/recebimentos.py`, `playgo/db.py` (gatilho do livro-razão). Testes: `tests/test_recebimentos.py`.

## 1. Credenciais

- **apiKey da subconta**: guardada **criptografada** (Fernet, AES-128-CBC com HMAC-SHA256) em `recebedores.api_key_cripto`. A chave de criptografia vem de `PLAYGO_RECEBIMENTOS_CHAVE` (variável de ambiente do servidor, fora do banco e do repositório).
  A apiKey nunca vai a log, resposta de API, tela, auditoria ou aviso (teste: `test_a_apikey_nunca_aparece_em_respostas_nem_em_log`). É decifrada só em memória, no momento da chamada.
- **Chave Pix de saque**: também cifrada; as telas e a API mostram só a máscara.
- **Rotação**: o administrador emite nova chave (`POST /v3/accounts/{id}/accessTokens`), o sistema confere que ela funciona antes de trocar e desativa a antiga.
- A chave da conta-mãe (`PLAYGO_ASAAS_API_KEY`) segue só no ambiente do servidor e só é usada para criar subcontas e gerir suas chaves; cobranças e saques usam sempre a chave da própria subconta.

## 2. Dados pessoais (LGPD)

- **CPF/CNPJ completo, data de nascimento, endereço e demais dados do cadastro não são gravados**: vão ao Asaas no `POST /v3/accounts` (e na atualização anual) e são descartados. Fica só o necessário: ids do Asaas e os 4 últimos dígitos do documento, para exibição e para conferir a titularidade do destino de saque.
- **Documentos de identificação** (frente/verso, selfie, ata) só trafegam em memória até o Asaas (`POST /v3/myAccount/documents/{id}`): não são gravados em disco, banco ou log, e a trilha de auditoria registra apenas o tipo (nunca nome ou conteúdo do arquivo). Documentos com `onboardingUrl` são enviados diretamente ao Asaas pelo link seguro, sem passar pelo PlayGo.
- O pagador de uma cobrança informa CPF/CNPJ que também só segue ao Asaas.

## 3. Autorização e isolamento entre Arenas

- Toda rota exige login e a verificação `exigir_gestor` da **própria Arena** (gestor cadastrado ou administrador). Uma Arena nunca lê saldo, extrato, saques, destino, situação ou reservas de outra (teste de acesso cruzado).
- Cobrança de reserva: a reserva precisa pertencer à Arena do gestor; só com cadastro **aprovado** (KYC) pelo Asaas.
- Rotação de chave, liberação de Arenas na fase de avaliação e painel geral: somente administradores.
- Respostas de dinheiro com `Cache-Control: no-store`.

## 4. Webhooks

- **Token próprio dos parceiros** (`PLAYGO_ASAAS_WEBHOOK_TOKEN_PARCEIROS`, 32–255 caracteres) no cabeçalho `asaas-access-token`, comparado em tempo constante; token ausente ou errado = 401. Separado do token do webhook dos planos.
- **Idempotência**: cada evento do Asaas é registrado (`eventos_recebimento`) e um reenvio retorna `duplicado` sem lançar nada; além disso, cada lançamento do livro-razão tem chave única (`recebedor_id` + fato).
- **Verificação cruzada**: o evento de cobrança só altera uma reserva se o **id da cobrança foi criado por nós** (`reservas.asaas_payment_id`) e, quando o payload traz a conta, se ela é a da Arena dona da reserva. Eventos de outras contas ou sistemas são ignorados.
- Depois de validar o token a rota responde 200 (erros internos ficam no log e não geram reenvio em loop).

## 5. Validação de saque (mecanismo de segurança do Asaas)

O Asaas chama `POST /api/v1/recebimentos/asaas/validar-saque` ~5 s depois de cada transferência e na liquidação de cada split. O PlayGo:

- **Aprova** apenas transferências que **ele mesmo registrou** (`saques_recebedor`), conferindo identificador, valor, tipo (Pix) e destino (chave Pix cadastrada e confirmada); saque já finalizado é recusado.
- **Aprova** splits somente de cobranças criadas por nós e somente para a carteira da conta-mãe.
- **Recusa** qualquer outra operação (transferência não registrada, boleto, recarga, Pix QR Code, estorno Pix etc.) com `{"status":"REFUSED","refuseReason":…}`.
- Exige o mesmo token (401 sem ele).

## 6. Saque e livro-razão

- O saque só vai para o **destino cadastrado**, de mesma titularidade da subconta (para chave CPF/CNPJ conferimos os 4 últimos dígitos do documento da conta; nos demais tipos a Arena declara a titularidade e o Asaas valida no saque). Valor mínimo configurável, saldo conferido sob **trava de linha** e **idempotência** por chave (o duplo clique não saca duas vezes).
- O pedido é gravado e debitado **antes** de chamar o Asaas; falha da transferência (na criação ou por `TRANSFER_FAILED`/`CANCELLED`) devolve o valor uma única vez.
- **Livro-razão** (`lancamentos_recebedor`): recebimento, taxa do Asaas, comissão, estorno, saque e saque devolvido, com valores em `Decimal`. É **somente de inserção**, protegido por **gatilho no Postgres** (UPDATE e DELETE são recusados). O saldo interno é a soma dos lançamentos e é **conferido** com `GET /v3/finance/balance` da subconta, sinalizando divergência.
- Ações de usuário que mudam estado entram na trilha de auditoria (`registros`, também só de inserção).

## 7. Limites do período de avaliação

Na fase `avaliacao`: só Arenas liberadas pelo administrador criam subconta; contadores de subcontas, valor cobrado por subconta e dias; **bloqueio local a 80%** de cada limite do Asaas (10 subcontas, R$ 2.000, 60 dias), para não esbarrar no bloqueio do Asaas no meio de uma reserva.

## 8. Operação

- Fase `desligado` por padrão: nenhuma rota de recebimento chama o Asaas.
- Atualização anual dos dados cadastrais: lembrete ao gestor e tela de atualização (`POST /v3/myAccount/commercialInfo`); eventos `ACCOUNT_STATUS_COMMERCIAL_INFO_EXPIRING_SOON/EXPIRED` tratados.
- Transporte HTTP único (`cobranca._chamar`) com User-Agent identificado, tempo limite e mensagens de erro sem dados sensíveis.
- Tabelas do Supabase com RLS (`python -m playgo supabase`).
