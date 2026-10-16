# Transmissão ao vivo no YouTube do PlayGo

Os jogos de um campeonato podem ser transmitidos ao vivo no **canal oficial do PlayGo no YouTube**. O vídeo vai **direto do celular
ou computador para o YouTube**: o servidor do PlayGo não recebe nem processa vídeo (só faz algumas chamadas à API), e quem assiste
puxa do YouTube. Isso mostra no **Ao vivo**, no **telão**, na faixa **🔴 Ao vivo agora** do início e, depois, no **book**.

## Regras

- **Quem transmite:** só a organização do campeonato e os **mesários** indicados para ele (os mesmos que conduzem o placar).
- **Campeonato liberado:** a organização precisa liberar a transmissão (aba **📺 Telão → 3 · Transmissão**) marcando a declaração de
  que os participantes (e os responsáveis pelos menores) foram avisados de que serão filmados. O campeonato passa a mostrar o aviso
  "🎥 Pode ter transmissão". Os Termos de Uso têm a cláusula 4-A (imagem e transmissões).
- **Uma transmissão e uma chave por jogo.** A chave de envio é um segredo: só aparece para quem conduz o campeonato, **nunca é gravada
  no banco** (é lida do YouTube a cada pedido), não vai para log nem auditoria e as respostas saem com `Cache-Control: no-store`.
  **Nova chave** troca a do jogo se vazar (só antes de entrar no ar).
- **Privacidade:** padrão **não listada** (só quem tem o link do PlayGo assiste). O administrador pode escolher **pública**.
- **Fim da transmissão:** ao encerrar o jogo no PlayGo (ou no botão), a transmissão é finalizada e a gravação fica no canal e no book.
  O agendador finaliza as esquecidas (jogo encerrado ou mais de 10 h).
- **Limite:** `PLAYGO_YOUTUBE_MAX_SIMULTANEAS` (padrão 4) transmissões preparadas ou no ar ao mesmo tempo (protege a cota da API e a internet do local).
- Todas as ações (conectar, liberar, preparar, entrar no ar, encerrar, nova chave) ficam na trilha de auditoria.

## Preparar o canal e a API (uma vez)

1. **Canal:** crie o canal **PlayGo Sports** (conta de marca) em youtube.com, **verifique o telefone** (YouTube Studio → Configurações →
   Canal → Elegibilidade de recursos) e ative **Transmitir ao vivo** (pode levar até 24 h). Permita **incorporar** transmissões.
2. **Google Cloud** (o mesmo projeto do login com Google): ative a **YouTube Data API v3**.
3. **Credenciais → ID do cliente OAuth → Aplicativo da Web.** URI de redirecionamento autorizada:
   `https://SEU-DOMINIO/youtube/retorno` (ex.: `https://playgo.ideiiapp.com.br/youtube/retorno`).
4. **Tela de permissão OAuth:** adicione o escopo `https://www.googleapis.com/auth/youtube` e, em **Teste**, a conta dona do canal como
   usuário de teste. Em **Teste** a autorização expira a cada **7 dias** (reconectar pelo botão); para ficar permanente é preciso
   **publicar** o app (o Google pede verificação do escopo sensível, com vídeo de demonstração).
5. No `deploy/.env` do servidor:
   ```
   PLAYGO_YOUTUBE_CLIENT_ID=...
   PLAYGO_YOUTUBE_CLIENT_SECRET=...
   PLAYGO_URL_PUBLICA=https://SEU-DOMINIO
   # PLAYGO_YOUTUBE_MAX_SIMULTANEAS=4
   ```
   e reinicie (`docker compose --env-file .env up -d`).
6. Como administrador: **Administração → 🎥 YouTube do PlayGo → Conectar YouTube**, entrando com a conta dona do canal.

## Transmitir um jogo (organização ou mesário)

1. **Campeonato → 📺 Telão → 3 · Transmissão:** escolha o jogo e **Preparar transmissão**.
2. No celular ou computador, abra um aplicativo de transmissão com **RTMP** (**Larix Broadcaster**, Streamlabs, Prism; **OBS** no
   computador) e use o **endereço** e a **chave** mostrados.
3. Inicie o envio. Quando o sinal aparecer como **ativo** (cerca de 15 s), clique em **▶ Entrar no ar**.
4. Ao fim do jogo, encerre o jogo no PlayGo (a transmissão é finalizada sozinha) ou use **⏹ Encerrar transmissão**.

Referências para quem transmite: 720p usa ~3 a 5 Mbps de envio (≈1,5 a 2 GB por hora); o vídeo chega ao público com ~10 a 30 s de
atraso em relação ao placar; evite música protegida por direitos autorais.

## Limites e cuidados

- Cota da API do YouTube: 10.000 unidades por dia por padrão; cada jogo gasta algumas dezenas de unidades por chamada.
- O YouTube só libera transmissões depois da verificação do telefone e da espera de até 24 h; antes disso a API devolve
  `liveStreamingNotEnabled` (o PlayGo mostra uma mensagem em português).
- A API não desliga o chat do YouTube: ajuste o canal em YouTube Studio se quiser comentários desativados.
- Quem tem o link de uma transmissão **não listada** consegue assistir; o PlayGo só mostra o vídeo a quem entra no Ao vivo/telão.
