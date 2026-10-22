# Especificação técnica — Personal / Instrutor, Escolinhas e Central de Aulas

Base: documento de requisitos "PlayGO - requisitos personal" (RF-01 a RF-06, RN-01 a RN-09) + decisões do responsável pelo produto
(seção 1). Este arquivo é o contrato de desenvolvimento: define conceitos, dados, regras, telas, fases e critérios de aceite.
Padrões do projeto continuam valendo (ver `CLAUDE.md`): serviços concentram regra, `create_all` + `_COLUNAS_NOVAS`, auditoria em toda ação,
site (Jinja) e app (PWA) sobre a mesma API, testes com banco próprio.

---

## 1. Decisões tomadas

| # | Tema | Decisão |
|---|---|---|
| D1 | Cobrança | Dentro da plataforma, para ajudar os profissionais. Cada profissional tem **conta de recebimento própria** (subconta Asaas) e **extrato interno**; ver seção 9. |
| D2 | Academia | Tratada como **estabelecimento com espaços**: a arena passa a ter *tipo de estabelecimento* e a quadra passa a ser um *espaço* (quadra, sala, piscina, campo…). |
| D3 | Quem pode atuar | **Com aprovação.** O profissional envia os documentos legais e só publica ofertas depois de aprovado pela administração. |
| D4 | Planos | **Família de planos exclusiva do profissional**, com limites, comissão e **promoções próprias** (cupons) para engajamento. |
| D5 | Conflito de espaço | Validado **somente** em espaços que usam a agenda do PlayGo. Local externo ou sem agenda no PlayGo não é validado. |
| D6 | Fronteira entre conceitos | Ver seção 2. |

## 2. Conceitos e fronteiras (não misturar)

| Conceito | Quem cria | Para quê | Já existe? |
|---|---|---|---|
| **Atividade** | Usuário (plano Pro+) | Achar gente para jogar junto, em data e local definidos | Sim |
| **Grupo** | Usuário (plano Pro+) | Quem pratica junto troca informação (conversa, jogos do grupo) | Sim |
| **Comunidade** | **Arena, escolinha e profissional** (e Organizador) | Canal de aviso e conversa com seus **afiliados** | Sim — passa a aceitar escolinhas e profissionais (seção 7) |
| **Campeonato** | Organizador/Arena | Torneio com chaves e placar ao vivo | Sim |
| **Oferta** (nova) | Profissional / escolinha | O que se vende ou divulga: **aula avulsa**, **pacote de aulas** ou **turma** | Nova |
| **Escolinha** (nova) | Profissional ou estabelecimento | Organização com **um responsável**, **turmas** e **alunos por turma** | Nova |

Regras de fronteira:

- A **escolinha pertence ao responsável**, não à arena (RN-05). A arena só *vê* as escolinhas vinculadas e pode divulgá-las no seu perfil (RN-08).
- **Oferta** e **Atividade** não se misturam: atividade é peer-to-peer e gratuita de gestão; oferta é serviço de um profissional aprovado.
- **Comunidade** da escolinha/profissional conversa com os afiliados (alunos e interessados). **Grupo** continua sendo dos usuários.

## 3. Perfis e permissões (RF-04)

Não há "papel exclusivo": o PlayGo já trabalha com **capacidades da conta** (`planos.permissoes`). O mesmo usuário continua atleta, pode ser personal e administrar escolinha (RF-04.3).

Capacidades novas em `planos.permissoes(s, u)`:

| Capacidade | Quando é verdadeira |
|---|---|
| `ensino` | Perfil profissional **aprovado** e plano de profissional vigente (ou administrador) |
| `ensino_cobranca` | `ensino` + conta de recebimento ativa + plano que permite cobrar |
| `escolinha` | `ensino` + plano que permite escolinha (limite de turmas) |

Matriz resumida:

| Ação | Aluno/Atleta | Profissional aprovado | Gestor de arena | Admin PlayGo |
|---|---|---|---|---|
| Buscar, favoritar, solicitar aula/matrícula, avaliar | ✔ | ✔ | ✔ | ✔ |
| Cadastrar perfil profissional (rascunho) | ✔ | ✔ | ✔ | ✔ |
| Publicar ofertas, agenda, escolinha | ✖ | ✔ | ✔ (se aprovado) | ✔ |
| Autorizar vínculo com o estabelecimento | ✖ | ✖ | ✔ (da própria arena) | ✔ |
| Aprovar/reprovar/suspender profissionais, documentos | ✖ | ✖ | ✖ | ✔ |
| Ver dados de menores | só o responsável | só nas turmas em que leciona | ✖ | ✔ (auditado) |

Gestor de arena que também ensina precisa do **seu próprio** perfil profissional aprovado (o cadastro da arena não dá `ensino`).

## 4. Modelo de dados

Convenções: nomes em português, datas naïve no fuso do servidor, colunas novas em `_COLUNAS_NOVAS`, tabelas novas por `create_all`.

### 4.1 Mudanças em tabelas existentes (Fase 0)

| Tabela | Mudança | Motivo |
|---|---|---|
| `arenas` | `tipo_estabelecimento` (`arena` \| `academia` \| `clube` \| `escola`, padrão `arena`) | D2 |
| `quadras` | `tipo` (`quadra` \| `sala` \| `piscina` \| `campo` \| `outro`, padrão `quadra`) — na tela passa a se chamar **Espaço** | D2 |
| `reservas` | `tipo` aceita `aula` + coluna `sessao_id` (FK, `ON DELETE CASCADE`) | Sessão de aula ocupa o espaço |
| `planos` | `familia` (`geral` \| `profissional`, padrão `geral`) + `max_ofertas`, `max_turmas`, `max_professores`, `comissao_pct`, `pode_cobrar`, `pode_escolinha` | D4 |
| `assinaturas` | Deixa de ser única por usuário: **única por (usuário, família)** | Quem é Organizador também pode assinar plano de profissional |
| `comunidades` | `escolinha_id`, `perfil_profissional_id` (dono opcional) | Fronteira das comunidades |

### 4.2 Tabelas novas

**Profissional**

- `perfis_profissionais` — `usuario_id` único; `status` (`rascunho` \| `em_analise` \| `aprovado` \| `reprovado` \| `suspenso`); `nome_profissional`, `apresentacao`, `foto`;
  `registro_tipo`, `registro_numero` (CREF, federação, etc.); `telefone`, `whatsapp`, `email_contato`; `cidade`, `regiao`, `latitude`, `longitude`, `raio_atendimento_km`;
  `modalidades` (array de códigos), `especialidades` (texto); `aprovado_por_id`, `aprovado_em`, `motivo_recusa`; `nota_media`, `n_avaliacoes` (derivados, atualizados na avaliação).
- `documentos_profissionais` — `perfil_id`, `tipo` (`identidade`, `registro_profissional`, `comprovante_endereco`, `certificado`, `termo_adesao`), `arquivo` (via `armazenamento.py`, acesso restrito),
  `status` (`pendente` \| `aprovado` \| `recusado`), `motivo`, `enviado_em`, `validade` (opcional). **Nunca público**; só o titular e a administração.
- `bloqueios_profissionais` — `perfil_id`, `inicio`, `fim`, `motivo` (férias, indisponível).

**Oferta e agenda**

- `escolinhas` — `responsavel_id` (usuário), `nome`, `descricao`, `capa`, `contato`, `estabelecimento_id` (arena de origem, opcional), `ativa`, `publicada`, `inscricoes_abertas`.
- `ofertas` — `perfil_id` (dono), `escolinha_id` (opcional), `tipo` (`aula` \| `pacote` \| `turma`), `titulo`, `descricao`, `modalidade_id`, `nivel`, `faixa_etaria_min/max`,
  `formato` (`individual` \| `coletiva`), `duracao_min`, `capacidade`, `aula_experimental` (bool) e `valor_experimental`, `valor_aula`, `valor_pacote` + `aulas_pacote` + `validade_pacote_dias`, `valor_mensal`,
  `espaco_id` (quadra/espaço, opcional) ou `local_texto` + `latitude/longitude` (local externo), `status` (`rascunho` \| `publicada` \| `pausada` \| `encerrada`), `inscricoes_abertas`.
- `oferta_professores` — `oferta_id`, `perfil_id`, `papel` (`titular` \| `auxiliar`) (RN-03).
- `oferta_horarios` — regra semanal: `oferta_id`, `dia_semana` (0–6), `hora_inicio`, `duracao_min`, `vigencia_inicio`, `vigencia_fim` (nula = sem fim), `espaco_id`/`local_texto`.
- `sessoes` — ocorrência concreta (materializada): `oferta_id`, `horario_id` (nulo se avulsa), `inicio`, `fim`, `espaco_id`/`local_texto`, `capacidade`,
  `status` (`agendada` \| `realizada` \| `cancelada`), `motivo_cancelamento`, `conflito` (bool).
- `oferta_excecoes` — cancelar/alterar uma data de uma regra: `horario_id`, `data`, `tipo`, `novo_inicio`, `motivo`.

**Aluno**

- `dependentes` — `responsavel_id` (usuário), `nome`, `nascimento`, `parentesco`, `observacoes` (opcional; evitar dados de saúde além do estritamente necessário).
- `matriculas` — turma/escolinha: `oferta_id`, `aluno_id` (usuário) **ou** `dependente_id`, `tipo` (`regular` \| `experimental`), `status` (`solicitada` \| `ativa` \| `lista_espera` \| `recusada` \| `cancelada` \| `encerrada`),
  `solicitada_em`, `decidida_por_id`, `decidida_em`, `aceite_responsavel_em`.
- `agendamentos_aula` — aula avulsa/pacote: `sessao_id`, `aluno_id`/`dependente_id`, `pacote_aluno_id` (opcional), `status` (`solicitado` \| `confirmado` \| `recusado` \| `cancelado` \| `realizado` \| `faltou`).
- `presencas` — `sessao_id`, `matricula_id`/`agendamento_id`, `presente` (bool).
- `favoritos_profissionais` — `usuario_id`, `perfil_id` (e `escolinha_id`), único por par.
- `avaliacoes_aula` — `autor_id`, `perfil_id` (e `escolinha_id` opcional), `agendamento_id`/`matricula_id` (único), `nota` (1–5), `comentario`, `status` (moderação), `resposta` (do profissional, 1), `criado_em`.

**Vínculo com estabelecimento (RF-01.4, RF-02.4)**

- `vinculos_espaco` — `arena_id`, `perfil_id` e/ou `escolinha_id`, `status` (`solicitado` \| `autorizado` \| `recusado` \| `revogado`), `solicitado_por_id`, `decidido_por_id`, `decidido_em`, `observacao`.
  Um profissional/escolinha pode ter vários vínculos; uma arena recebe vários (RN-04, RN-05).

**Financeiro (seção 9)**

- `contas_recebimento` — `perfil_id`, `asaas_account_id`, `asaas_wallet_id`, `status_kyc` (`nao_iniciada` \| `pendente` \| `aprovada` \| `recusada`), `chave_pix` (opcional, só rótulo). **Não guarda a chave de API da subconta.**
- `cobrancas_aula` — `aluno_id`, `perfil_id`, `referencia` (`matricula` \| `agendamento` \| `pacote`), `referencia_id`, `valor_bruto`, `taxa_plataforma`, `valor_profissional`, `cupom_id`,
  `asaas_payment_id`, `status` (espelho do Asaas), `vencimento`, `pago_em`.
- `lancamentos` — **extrato do profissional** (só inserção): `perfil_id`, `tipo` (`venda` \| `comissao` \| `estorno` \| `repasse` \| `ajuste`), `valor` (positivo/negativo), `cobranca_id`, `descricao`, `criado_em`.
- `pacotes_aluno` — **créditos de aulas do aluno com um profissional**: `aluno_id`, `oferta_id`, `perfil_id`, `aulas_total`, `aulas_usadas`, `valido_ate`, `status`.
- `cupons` / `cupom_usos` — seção 8.

## 5. Regras de negócio

### 5.1 Aprovação do profissional (D3)

1. A pessoa preenche o perfil (rascunho) e **anexa os documentos** exigidos; envia para análise (`em_analise`).
2. Fila **"Aprovação de profissionais"** na Administração: aprova, recusa (com motivo) ou pede documento novo; cada documento tem estado próprio.
3. Aprovado = selo **Verificado**, libera publicar ofertas e vincular a espaços. Reprovado/suspenso = ofertas saem de circulação; matrículas e aulas já agendadas notificam as partes.
4. Documento com validade vencida volta o perfil para `em_analise` (aviso 30 dias antes, no padrão de `avisar_vencimentos`).
5. Toda decisão grava auditoria e notifica o profissional.

### 5.2 Ofertas e recorrência

- **Aula avulsa:** uma ou mais sessões em datas específicas. **Pacote:** N aulas com validade (cria `pacotes_aluno` na compra). **Turma:** regra semanal + matrículas.
- Recorrência **semanal** (`oferta_horarios`). O sistema **materializa `sessoes` numa janela móvel de 8 semanas** (rotina diária, mesmo padrão do agendador existente; em produção Vercel, pelo endpoint de cron).
  Motivo: conflito, reserva de espaço, presença e agenda do aluno trabalham com datas reais.
- Alterar uma regra só afeta sessões **futuras sem presença registrada**; cancelar uma data cria exceção e notifica os inscritos.
- **Bloqueios** do profissional impedem novas sessões/agendamentos no período e sinalizam as já existentes como `conflito`.

### 5.3 Vagas e solicitações

- Vaga é contada **no banco**, com a oferta/sessão **travada** (`with_for_update`), igual a `vagas.py` — nunca por coleção carregada (a sessão não expira ao salvar).
- Turma: `matriculas` com `status='ativa'` ≤ `capacidade`. Passou da capacidade → `lista_espera`; ao abrir vaga, o primeiro da fila é notificado e tem prazo (configurável, 24 h) para confirmar.
- Aula avulsa: `agendamentos_aula` confirmados ≤ `sessao.capacidade` (individual = 1).
- **Aula experimental:** uma por aluno/oferta (se `aula_experimental`), gratuita ou com `valor_experimental`; vira `matricula(tipo='experimental')` ou agendamento especial.
- O profissional **confirma ou recusa**; opção por oferta de **confirmação automática** enquanto houver vaga.

### 5.4 Conflitos (RN-07, D5)

| Situação | Regra |
|---|---|
| **Professor** | Um perfil não pode ter duas sessões **confirmadas** que se sobreponham, em qualquer oferta/escolinha. Verificado ao confirmar, ao publicar horário e ao materializar. Conflito ao publicar = aviso; ao confirmar = bloqueio. |
| **Espaço com agenda no PlayGo** | Sessão com `espaco_id` cria `Reserva(tipo='aula')`, usando o mesmo bloqueio de linha da quadra de `arenas.reservar`. Sobreposição com reserva/bloqueio/atividade = recusada; ao materializar, a sessão fica `conflito=true` e o responsável é avisado. |
| **Local externo** ou espaço sem agenda | **Não validado** (D5). O sistema só avisa que o local não é controlado pelo PlayGo. |
| **Divulgar ≠ reservar** (RN-06) | Publicar oferta com um espaço **não reserva** o espaço. A reserva só nasce com **vínculo autorizado** e sessão confirmada. |

### 5.5 Vínculo com estabelecimento

1. Profissional/escolinha **solicita** vínculo a uma arena (informando a oferta e o espaço desejado).
2. O gestor da arena **autoriza ou recusa** (pode ter regra "autorizar automaticamente profissionais verificados", desligada por padrão).
3. Vínculo `autorizado` libera escolher os espaços daquela arena nas ofertas e reservar pela agenda. `revogado` cancela as reservas futuras e avisa.
4. A arena **vê** as escolinhas e profissionais vinculados e pode **divulgá-los** no seu perfil (RN-08), sem poder de administração (RN-05).

### 5.6 Menores de idade

- Quem tem menos de 18 anos **não se matricula sozinho**: o **responsável** (usuário maior de idade) cadastra o **dependente** e faz/autoriza a inscrição.
- Matrícula de dependente exige **aceite do responsável** (termo com versão e data, como `AceiteTermos`) e registra `aceite_responsavel_em`.
- Dados do dependente: visíveis ao responsável, aos professores da turma e à administração (auditado). **Nunca** em listas públicas; no mural/comunidades o dependente não aparece.
- Revisão jurídica obrigatória (LGPD, art. 14) antes da Fase 3 ir ao ar.

### 5.7 Avaliações

- Só quem **realizou** a aula (presença/`realizado`) avalia, **uma vez por matrícula/agendamento**; nota 1–5 + comentário.
- Comentário passa pela mesma análise/moderação do mural (denúncia, ocultar, restaurar); o profissional pode **responder uma vez**.
- Média e contagem ficam no perfil; só aparece a nota pública a partir de 3 avaliações.

## 6. Descoberta, mapa e Central "Aulas e Treinamentos" (RF-03)

- Novos tipos em `descoberta.TIPOS`: `profissional`, `oferta`, `escolinha`. Mesmo caminho dos tipos `local` e arena com coroa: busca por proximidade, filtros, `pontos_do_mapa`, serializador.
- **Filtros novos:** modalidade, distância, dia da semana, período, faixa etária, nível, preço máximo, nota mínima, professor, **com vagas**, aula experimental, tipo de aula (individual/coletiva/pacote/turma).
- **Mapa — 5 categorias** com pino e ícone próprios (Lucide): Jogos, Quadras e arenas, **Personal/Instrutores** (`graduation-cap`), **Aulas e treinamentos** (`calendar-check`), **Escolinhas** (`school`). Legenda e liga/desliga por categoria, respeitando os filtros e a região.
- **Central "Aulas e Treinamentos"** (item no menu do site; aba/atalho no app): seções *Profissionais perto de você*, *Aulas com horários livres*, *Escolinhas com inscrições abertas*, *Aulas experimentais*, *Por modalidade*, *Arenas e academias que oferecem treinamento*.
- **Cartão da oferta:** profissional/escolinha, modalidade, local e distância, dia e horário, valor, vagas, nota, botões *Ver detalhes* e *Solicitar*.
- **Páginas públicas** (só logados, como o resto do PlayGo): `/profissionais/{id}`, `/escolinhas/{id}`, `/ofertas/{id}` — com horários, locais, avaliações e ação de solicitar.

## 7. Comunidades de escolinhas e profissionais

- Hoje só Organizador e Arena criam comunidade. Passa a valer: `pode_campeonato or pode_arena or ensino` (`planos.exigir(..., "comunidade")`).
- Comunidade pode ter **dono vinculado** (arena, escolinha ou perfil profissional) e a página da entidade mostra "Comunidade". Afiliados = alunos matriculados (entrada automática opcional) e interessados.
- Moderação e mural seguem as regras existentes (`escopos.py` decide quem lê/posta/modera).

## 8. Planos e promoções do profissional (D4)

Família `profissional` na tabela `planos` (valores e limites editáveis na Administração, igual aos planos atuais). Proposta inicial (valores a definir):

| Plano | Para quem | Limites sugeridos | Comissão |
|---|---|---|---|
| **Profissional Essencial** | Personal autônomo | Perfil + até N ofertas, sem escolinha, recebimento online opcional | maior |
| **Profissional Pro** | Instrutor com agenda cheia | Ofertas ilimitadas, pacotes, cupons | menor |
| **Escolinha** | Responsável por escolinha | Turmas e professores vinculados, comunidade, relatórios | menor ainda |

Regras:

- Assinatura mensal/anual pelo Asaas, **como o plano atual** (`cobranca.assinar` ganha o parâmetro de família); teste grátis de 30 dias por plano; aviso de vencimento nos últimos 7 dias; fim do acesso devolve ao perfil básico (ofertas pausadas, **dados preservados**).
- `Assinatura` única por **(usuário, família)** para não colidir com Organizador/Arena.
- **Promoções próprias (cupons):**
  - *PlayGo → profissional:* desconto no plano, meses grátis, teste estendido, campanha por período/público ("primeiros 100 instrutores").
  - *Profissional → aluno:* aula experimental grátis, % de desconto no pacote ou na primeira mensalidade, cupom com limite de usos e validade.
  - Tabela `cupons` (`codigo`, `escopo` plataforma/profissional, `tipo` percentual/fixo/gratuito, `valor`, `valido_de/ate`, `limite_usos`, `plano_alvo`/`oferta_id`, `perfil_id` criador, `ativo`) e `cupom_usos` (`cupom_id`, `usuario_id`, `cobranca_id`). Uso validado **dentro do serviço**, sob trava.

## 9. Financeiro dentro da plataforma (D1)

### 9.1 Modelo recomendado: subcontas Asaas + split

1. **Cada profissional aprovado ganha uma subconta** no Asaas, criada pela conta principal do PlayGo; o PlayGo guarda **só** `asaas_account_id` e `asaas_wallet_id`.
2. **Aluno paga ao PlayGo** (Pix, boleto ou cartão, pela fatura do Asaas, como já ocorre nos planos).
3. A cobrança nasce com **split**: parte vai para o `walletId` do profissional, o restante (comissão) fica com o PlayGo.
4. O **webhook** confirma o pagamento; o PlayGo cria os `lancamentos` (venda, comissão) e libera a matrícula/aula/pacote.
5. O profissional vê **"Meus recebimentos"**: a receber, disponível, pago e extrato; saca para a conta/Pix dele pela própria subconta.
6. **Estorno/chargeback** gera lançamentos negativos e reverte a matrícula, conforme a política.

Não recomendado: receber tudo na conta do PlayGo e repassar manualmente (o PlayGo vira recebedor integral, com impacto fiscal e operacional maior).

### 9.2 Como cada profissional tem os "créditos controlados"

Há **dois controles distintos**:

- **Extrato financeiro do profissional** — tabela `lancamentos` (só inserção, com gatilho no Postgres como a `registros`): todo valor que entra, comissão, estorno e repasse. O saldo é a soma; concilia com o Asaas por webhook e por uma rotina de conferência diária; divergência vira alerta para a administração.
- **Créditos de aulas do aluno** — `pacotes_aluno`: o aluno compra um pacote com um profissional e cada aula realizada consome 1 crédito; validade e saldo visíveis aos dois.

### 9.3 O que precisa ser feito fora do código (responsável pelo produto)

| # | Ação | Quem |
|---|---|---|
| 1 | **Pedir ao Asaas a habilitação de Subcontas e de Split** na conta do PlayGo; confirmar disponibilidade em produção e sandbox, taxas, **quem arca com a taxa**, prazos de liberação por meio de pagamento, limites e responsabilidade por chargeback | Produto |
| 2 | **Definir com o contador/jurídico** o modelo de intermediação: a comissão é receita da IDEIIA; cada profissional é o prestador e responde pelo seu recibo/nota; texto do **Termo de Adesão do Profissional** e política de cancelamento e reembolso | Produto + jurídico |
| 3 | **Definir comissão por plano** e **prazo de liberação** do saldo ao profissional | Produto |
| 4 | **Lista final de documentos** exigidos para aprovação (identidade, registro profissional, comprovante, termo) e prazo de guarda | Produto + jurídico |
| 5 | **Dados que a subconta exige** (CPF/CNPJ, data de nascimento, endereço, faturamento/renda, dados de recebimento): o PlayGo repassa ao Asaas **sem gravar CPF/CNPJ**, como já faz nos planos | Dev |
| 6 | **Testar tudo no sandbox** do Asaas antes da produção (criação de subconta, aprovação, split, webhook, estorno) | Dev |
| 7 | Confirmar a lista de **eventos de webhook** de subconta (aprovação/recusa de cadastro, transferências) e onde são configurados | Dev, no sandbox |

**Esses itens 1 a 4 têm prazo externo** e devem começar já, em paralelo às Fases 1 a 4. O financeiro (Fase 5) só entra em produção quando estiverem resolvidos.

### 9.4 Integração técnica

- Novo módulo `cobranca_aula.py` (não mexe em `cobranca.py` dos planos): `criar_subconta`, `cobrar_matricula`, `cobrar_pacote`, `estornar`.
- Webhook existente passa a reconhecer `externalReference` com prefixos `matricula:`, `agendamento:` e `pacote:` (mesmo padrão do `evento:`).
- `externalReference` carrega só o id interno; **nenhum CPF/chave** em log, auditoria ou `lancamentos`.

## 10. Notificações e auditoria

Notificações (sempre com `chave` para não duplicar): solicitação recebida; confirmada/recusada/cancelada; lembrete 24 h e 2 h antes; alteração ou cancelamento de sessão; nova matrícula; vaga liberada (lista de espera); pagamento confirmado/atrasado; vínculo solicitado/autorizado/revogado;
perfil aprovado/recusado; documento a vencer; nova avaliação; resumo de repasse.

Auditoria (`auditoria.registrar`): aprovação/recusa de perfil e documento, publicação/pausa de oferta, matrícula e agendamento (todas as transições), vínculo, cupom criado/usado, conta de recebimento criada, lançamento manual, avaliação ocultada.

## 11. Telas

**Site e app (mesmas rotas de API):**

| Área | Telas |
|---|---|
| **Aulas e Treinamentos** (público logado) | Central; resultados com filtros e mapa; perfil do profissional; página da escolinha; página da oferta; solicitar agendamento/matrícula; **Minhas aulas** (aluno): agenda, histórico, matrículas, pacotes, favoritos, avaliar |
| **Minhas Aulas e Escolinhas** (gestão do profissional) | Perfil profissional e documentos; ofertas; agenda (dia/semana) e bloqueios; solicitações; alunos por turma e presença; escolinhas e turmas; professores vinculados; vínculos com espaços; cupons; **Meus recebimentos** |
| **Gestão da arena** | Vínculos solicitados; escolinhas e profissionais vinculados; espaços (antes "quadras") |
| **Administração** | Aprovação de profissionais e documentos; planos da família profissional; cupons da plataforma; conciliação financeira |

Tutorial: novo capítulo em **Tutoriais** ("Como oferecer aulas e criar escolinhas") nos parciais já usados pelo site e pelo app.

## 12. API (prefixo `/api/v1`)

`/profissionais` (perfil, documentos, busca) · `/ofertas` (+ horários, sessões, exceções) · `/escolinhas` (+ turmas, professores) · `/matriculas` · `/agendamentos` · `/dependentes` ·
`/vinculos` (solicitar/decidir) · `/avaliacoes` · `/favoritos` · `/cupons` · `/recebimentos` (conta, extrato, saldo) · `/admin/profissionais` (fila, decisão) · `/aulas` (Central: agregação de busca).
Toda regra fica em serviços novos (`profissionais.py`, `ofertas.py`, `escolinhas.py`, `matriculas.py`, `agenda_ensino.py`, `vinculos.py`, `avaliacoes.py`, `cupons.py`, `cobranca_aula.py`); rotas e telas só traduzem HTTP.

## 13. Fases de entrega

| Fase | Entrega | Requisitos cobertos |
|---|---|---|
| **0 — Preparação** | Espaços (arena com tipo, quadra com tipo), família de planos e assinatura por (usuário, família), capacidade `ensino`, comunidades abertas a profissionais | RF-04, D2, D4, base de RF-02.4 |
| **1 — Perfil e divulgação** | Perfil profissional, documentos e **fila de aprovação**, plano do profissional, ofertas (avulsa, pacote, turma) com horários semanais **informativos**, páginas públicas, **Central "Aulas e Treinamentos"**, filtros, pinos no mapa, Minhas Aulas | RF-01.1–1.2, RF-03, RF-06 (cadastro), RF-02.5 (divulgação) |
| **2 — Agenda e agendamento** | Sessões materializadas, recorrência e exceções, bloqueios, solicitação/confirmação, vagas, lista de espera, aula experimental, agenda do profissional e do aluno, notificações, **conflito de professor**, favoritos, histórico | RF-01.3, 1.5, 1.6, RF-02.6, RF-05, RN-07 (professor) |
| **3 — Escolinhas e menores** | Escolinha com responsável, turmas, professores vinculados, alunos por turma, presença, abrir/encerrar inscrições, **dependentes e aceite do responsável**, comunidade da escolinha | RF-02.1–2.3, RF-04, RN-01 a RN-03, RN-09 |
| **4 — Estabelecimentos e avaliações** | Vínculo profissional/escolinha ↔ arena, reserva de espaço pela sessão, **conflito de espaço** nas agendas do PlayGo, arena vê e divulga escolinhas, avaliações com moderação | RF-01.4, RF-02.4, RF-05, RN-04 a RN-08 |
| **5 — Financeiro** | Subconta e split, cobrança de matrícula/aula/pacote, extrato e saldo, pacotes de aulas, cupons do profissional, conciliação | D1, D4 (promoções) |

Cada fase fecha com: testes automatizados, tutorial atualizado, `CLAUDE.md` atualizado, validação visual no site e no app.

## 14. Critérios de aceite (resumo)

- **F1:** profissional sem aprovação não publica; aprovado publica; busca por proximidade e filtros devolve profissionais/ofertas/escolinhas; mapa mostra as 5 categorias; plano sem vigência pausa as ofertas sem apagar dados.
- **F2:** nunca passa de `capacidade` mesmo com pedidos simultâneos; profissional não confirma duas sessões sobrepostas; cancelar uma data notifica os inscritos; lista de espera avança sozinha.
- **F3:** menor só se matricula pelo responsável com aceite registrado; dados do dependente não aparecem publicamente; professor vinculado vê só as suas turmas.
- **F4:** sem vínculo autorizado não reserva espaço; sessão em espaço com agenda no PlayGo cria reserva e bloqueia conflito; espaço externo só gera aviso; avaliação só de quem realizou a aula.
- **F5:** pagamento confirmado cria matrícula ativa e lançamentos corretos (venda e comissão); estorno reverte; soma do extrato = valor liquidado no Asaas; nenhum CPF nem chave em log.

## 15. Riscos e dependências

| Risco | Tratamento |
|---|---|
| Recorrência e conflitos entre séries | Materialização em janela de 8 semanas; testes de borda (virada de semana, exceções, bloqueios) |
| Dois clientes (site e app) | Regra no serviço; telas finas; cada fase entrega os dois |
| Habilitação do Asaas e modelo fiscal demoram | Fase 5 por último; itens 1 a 4 da seção 9.3 começam agora |
| Dados de menores e documentos | Revisão jurídica antes da Fase 3; acesso restrito e auditado; prazo de guarda definido |
| Confusão de conceitos (grupo, comunidade, atividade, escolinha) | Seção 2 vira texto de ajuda e tutorial |
| Termos de uso | Aceite **específico** do profissional e do responsável, sem forçar novo aceite de todos os usuários (`termos.VERSAO` só muda se houver mudança que vale para todos) |

## 16. Rastreabilidade

| Requisito | Fase | | Requisito | Fase |
|---|---|---|---|---|
| RF-01.1–1.2 | 1 | | RF-03 | 1 |
| RF-01.3 | 2 | | RF-04 | 0, 3 |
| RF-01.4 | 4 | | RF-05 | 2, 4 |
| RF-01.5–1.6 | 2 | | RF-06 | 1, 2 |
| RF-02.1–2.3 | 3 | | RN-01 a RN-03, RN-09 | 3 |
| RF-02.4 | 4 | | RN-04, RN-05, RN-08 | 4 |
| RF-02.5 | 1 | | RN-06 | 2, 4 |
| RF-02.6 | 2, 3 | | RN-07 | 2 (professor), 4 (espaço) |
