# Revisão de segurança do PlayGo (nov/2026)

Revisão do código inteiro (site, app/PWA, API, serviços, publicação em Docker/Caddy) e das dependências. O que foi corrigido está marcado
com ✅; o que segue como risco aceito ou recomendação está em "Pendências".

## Como foi revisto

- Leitura dos módulos de autenticação (`seguranca.py`, `deps.py`, `contas.py`, `google.py`), sessão, cookies e cabeçalhos.
- Varredura por padrões perigosos: SQL montado com texto, `eval/exec`, `|safe`, `innerHTML` com dados de pessoas, `subprocess`, `pickle`.
- Mapa de todas as **375 rotas**: só 30 não exigem login (cadastro, entrada, webhooks, links secretos, mídia pública por nome aleatório) e todas são esperadas.
- Verificação de permissão por recurso (gestor de arena, administração, mesário, titular do documento) nos serviços.
- Upload e armazenamento (tipo pelos bytes, tamanho, caminho dentro de `uploads/`, URLs assinadas).
- Segredos (histórico do git, logs, `.env`), Docker, Caddy e backup.
- `pip-audit` nas dependências instaladas.

## O que já estava bem

- Senhas com scrypt e sal; tempo de resposta igual para e-mail que não existe; cookie `HttpOnly`, `SameSite=Lax` e `Secure` em produção.
- Comparação de tokens de webhook e do cron em tempo constante; webhooks idempotentes; saque debita antes de transferir.
- Permissões dentro dos serviços (não só na tela): `exigir_gestor`, `_exigir_admin`, `planos.exigir`.
- Mídia do mural por URL assinada, por pessoa e com validade; documentos de profissionais privados e com acesso do admin auditado.
- Nenhum segredo no histórico do git; `.env`, `uploads/` e `.chave_sessao` fora do repositório; credenciais geradas com `secrets`.
- Contêiner sem root, banco sem porta publicada, HTTPS automático com HSTS, corpo de requisição limitado no Caddy.
- Apiks do Asaas e chaves Pix cifradas (Fernet); CPF/CNPJ nunca gravado; chave RTMP nunca gravada.

## Achados e correções

| # | Gravidade | Achado | Correção |
|---|---|---|---|
| 1 | Alta | **Apagar arquivo de outra pessoa.** `PATCH /me` aceitava qualquer `foto_url`; com `/foto/../capas/<nome>.jpg` a troca da foto apagava a capa/logo de qualquer arena (os nomes são públicos). Também permitia apontar a foto para um site de fora (rastreia quem vê o perfil). | ✅ A foto só entra pelo envio de imagem; o endereço é validado por expressão exata e o caminho nunca é montado com `..`. |
| 2 | Alta | **Dependências vulneráveis.** Pillow 12.3.0 com 42 avisos conhecidos (todo upload passa por ele) e `certifi` desatualizado. Como `requirements.txt` não fixa versões e o Docker reaproveita a camada de `pip install`, a imagem de produção pode ter versões antigas. | ✅ `pillow>=13.0.0` e `certifi>=2026.10.18`; testes passam com Pillow 13. **Na VPS, reconstruir sem cache** (ver abaixo). |
| 3 | Média | **Sem freio contra senha adivinhada nem cadastro em massa.** | ✅ 5 falhas por conta e endereço, 30 por endereço, a cada 15 min; 10 cadastros por endereço por hora (HTTP 429). Conta as falhas pela trilha de auditoria (vale em qualquer hospedagem); o e-mail digitado não é gravado, só uma impressão. |
| 4 | Média | **Comando de demonstração perigoso.** `python -m playgo demo --refazer` apaga todas as tabelas e `demo` cria um admin com senha pública. | ✅ Recusa quando há `PLAYGO_COOKIE_SEGURO`, `PLAYGO_URL_PUBLICA`, serverless ou banco que não seja local. |
| 5 | Média | **Bomba de descompressão de imagem.** Um PNG pequeno podia expandir para centenas de MB de memória. | ✅ Teto de 40 megapixels checado antes de decodificar (capa, avatar, mural). Também no `.xlsx` da importação de locais (30 MB descompactados). |
| 6 | Média | **Reserva paga podia ser apagada pelo gestor** ("liberar") sem devolver o valor. | ✅ Recusa se há pagamento pelo app (pago ou pendente). |
| 7 | Média | **Injeção de fórmula no CSV** do relatório (nomes e rótulos começando com `=`, `+`, `-`, `@`). | ✅ Células de texto recebem um apóstrofo. |
| 8 | Baixa | **Cabeçalhos de segurança ausentes** (clickjacking, MIME, permissões do navegador). | ✅ `X-Frame-Options`, `frame-ancestors`, `object-src 'none'`, `form-action 'self'`, `Permissions-Policy`, `Referrer-Policy`, `nosniff`, HSTS. |
| 9 | Baixa | **Documentação da API pública** (`/docs`, `/openapi.json`) em produção lista todas as rotas. | ✅ Desligada quando há HTTPS/serverless. |
| 10 | Baixa | **CSRF só dependia do cookie `SameSite=Lax`.** | ✅ Pedido que muda dados com `Origin` de outro site (ou `null`) é recusado. App nativo e webhooks (sem `Origin`) seguem normais. |
| 11 | Baixa | **Redirecionamento aberto** pelo cabeçalho `Referer` após erro ou ação. | ✅ Só volta para o mesmo site. |
| 12 | Baixa | Parâmetros do scrypt abaixo do recomendado (N=2^14). | ✅ Novos hashes usam N=2^15; hashes antigos valem e são refeitos no próximo login. |
| 13 | Baixa | Nome do arquivo do documento de profissional ia direto no cabeçalho `Content-Disposition`. | ✅ Nome higienizado. |
| 14 | Baixa | `<option value>` do campeonato com `@usuario` sem escape. | ✅ Escapado (o nome já era restrito, é defesa em profundidade). |
| 15 | Baixa | Backup com permissão aberta (dump do banco e uploads). | ✅ `umask 077` no `backup.sh`. |

## Pendências e recomendações

1. **Token do app dura 30 dias e não é revogável.** Sair do app só apaga o token local; trocar a senha não invalida os tokens existentes.
   Recomendação: guardar no token uma versão derivada do hash da senha (trocar a senha invalida) e reduzir para 7 a 14 dias. Não foi
   feito agora para não deslogar todo mundo de uma vez. O token fica em `localStorage`, então qualquer XSS o expõe.
2. **Sem CSP de scripts.** As páginas usam muito JavaScript inline; por isso a política só restringe moldura, objetos e formulários.
   Migrar para scripts externos com `nonce` é o caminho para limitar o estrago de um XSS futuro.
3. **Sem recuperação de senha nem segundo fator.** Para contas de administração, vale ter TOTP e senha única forte.
4. **O primeiro usuário cadastrado vira administrador** (também pelo Google). Em banco novo, cadastre a conta do dono antes de divulgar o endereço.
5. **Dependências sem trava.** Gerar um arquivo de versões fixas (`pip-compile` ou `uv lock`), rodar `pip-audit` periodicamente e reconstruir a imagem sem cache de tempos em tempos.
6. **Limite geral de requisições.** Só login e cadastro têm freio. Considerar limite no Caddy (módulo de rate limit) ou `fail2ban` lendo o log.
7. **Backup**: copiar para fora do servidor e cifrar; manter `deploy/.env` com permissão 600.
8. **Fotos da arena entram sem análise automática.** Se virar problema de conteúdo, passar pela moderação.
9. **Contêiner**: considerar `read_only: true`, `mem_limit` e `cap_drop: [ALL]` no `docker-compose.yml`.

## Publicando esta revisão

Na VPS, reconstruir a imagem **sem cache** para trazer o Pillow corrigido:

```bash
cd /root/stack/playgo && git pull
cd /root/stack/playgo/deploy && docker compose --env-file .env build --no-cache app && docker compose --env-file .env up -d
```
