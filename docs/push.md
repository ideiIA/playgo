# Avisos no celular (Web Push)

O aviso chega no celular com a logo do PlayGo, mesmo com o app fechado, para quem **ativar** em **Notificações > Avisos no celular** dentro do app.
Cada aviso novo da caixa de entrada (vaga aberta, lembrete de jogo, grupo, campeonato, reserva...) vira um aviso no aparelho em até 1 minuto.

## Ligar no servidor (uma vez)

1. Gere as chaves (a pública vai ao navegador; a privada fica só no servidor):

```bash
cd /root/stack/playgo/deploy && docker compose --env-file .env run --rm --no-deps app python -m playgo vapid
```

2. Cole as três linhas no `deploy/.env` (`nano .env`), trocando o e-mail do assunto pelo seu:

```
PLAYGO_VAPID_PUBLICA=...
PLAYGO_VAPID_PRIVADA=...
PLAYGO_VAPID_ASSUNTO=mailto:contato@seudominio.com.br
```

3. Reinicie o app: `docker compose --env-file .env up -d`.

Guarde a chave privada. **Trocar o par desliga os aparelhos já inscritos** (cada pessoa precisa ativar de novo).
Sem as chaves o recurso fica desligado e o cartão nem aparece no app.

## Como a pessoa ativa

- **Android (Chrome)**: abrir o app, tocar no sino, **Ativar avisos neste aparelho** e permitir. Funciona até sem instalar, mas instalado é melhor.
- **iPhone (iOS 16.4 ou mais novo)**: só funciona com o app **instalado**: Safari, Compartilhar, **Adicionar à Tela de Início**, abrir pelo ícone e ativar.
- O botão **Enviar um aviso de teste** confirma que está tudo certo. **Desativar neste aparelho** cancela.

## O que aparece no aviso

- Ícone grande: `app/icone-192.png` (logo colorida). Ícone da barra de status no Android: `app/badge-push-96.png` (silhueta branca da bola).
  Para refazê-los: `python scripts/gerar_marca.py`.
- Título e texto vêm da própria notificação; ao tocar, o app abre no assunto (`link` da notificação).

## Limites e segurança

- Só vira aviso o que foi criado nos últimos 20 minutos (ao ligar o recurso, avisos antigos não chegam de uma vez).
- No máximo 3 avisos por pessoa por minuto; o que passar disso vira um resumo ("Você tem mais N novos avisos").
- A mensagem é cifrada para o aparelho (RFC 8291): nem o serviço de push do Google/Apple/Mozilla lê o texto.
- O endereço de entrega vem do aparelho; por segurança só aceitamos os serviços de push dos navegadores (Google FCM, Mozilla, Apple, Windows).
- Até 8 aparelhos por pessoa. Aparelho que desinstalou ou cancelou é esquecido sozinho.

## Testar no seu celular

1. Chaves colocadas e app reiniciado (acima).
2. No celular, abrir `https://SEU-DOMINIO/app/`, entrar, tocar no sino, **Ativar avisos neste aparelho**.
3. Tocar em **Enviar um aviso de teste**. Deve chegar em segundos, com a logo do PlayGo.
