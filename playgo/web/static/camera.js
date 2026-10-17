/* Câmera do navegador para a transmissão do jogo (site e app): publica no LiveKit Cloud, que repassa ao YouTube (docs/youtube.md).
   PlayGoCamera.montar(raiz, { postar(caminho, corpo), base(), aviso(texto), depois(), cls: {primario, suave, perigo} }) devolve { mostrar(painel) }. */
(function () {
  const CDN = 'https://cdn.jsdelivr.net/npm/livekit-client@2.22.1/dist/livekit-client.umd.js';
  let carregando = null;

  function carregar() {
    if (window.LivekitClient) return Promise.resolve();
    if (!carregando) {
      carregando = new Promise((ok, erro) => {
        const s = document.createElement('script');
        s.src = CDN; s.onload = ok;
        s.onerror = () => { carregando = null; erro(new Error('Não foi possível carregar o módulo da câmera. Verifique a internet e tente de novo.')); };
        document.head.appendChild(s);
      });
    }
    return carregando;
  }

  function montar(raiz, o) {
    const c = Object.assign({ primario: 'btn', suave: 'btn', perigo: 'btn' }, o.cls || {});
    raiz.innerHTML =
      '<div style="margin-top:14px;padding-top:12px;border-top:1px dashed #e5c4c4">' +
      '<p style="margin:0 0 6px"><b>📷 Ou transmita pela câmera deste aparelho</b> <span style="opacity:.7">(sem instalar nada)</span></p>' +
      '<p style="margin:0 0 8px;font-size:.9em;opacity:.8">Deixe esta tela <b>aberta e acesa</b> durante o jogo. Depois que o sinal aparecer como <b>ativo</b>, toque em <b>Entrar no ar</b>.</p>' +
      '<video data-c="video" muted playsinline autoplay hidden style="width:100%;max-width:420px;aspect-ratio:16/9;background:#000;border-radius:12px;margin-bottom:8px"></video>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
      '<button type="button" class="' + c.primario + '" data-c="ligar">📷 Ligar câmera</button>' +
      '<button type="button" class="' + c.suave + ' pequeno" data-c="virar" hidden>🔄 Virar câmera</button>' +
      '<button type="button" class="' + c.perigo + ' pequeno" data-c="parar" hidden>⏹ Parar câmera</button>' +
      '<span data-c="status" style="font-size:.9em;opacity:.8"></span></div></div>';
    raiz.hidden = true;
    const q = (n) => raiz.querySelector('[data-c="' + n + '"]');
    let sala = null, luz = null, frente = false, ocupado = false;

    const status = (t) => { q('status').textContent = t || ''; };
    const botoes = (ligada, remota) => {
      q('ligar').hidden = ligada || remota;
      q('virar').hidden = !ligada;
      q('parar').hidden = !(ligada || remota);
      q('video').hidden = !ligada;
    };
    async function acender() { try { if (navigator.wakeLock && !document.hidden) luz = await navigator.wakeLock.request('screen'); } catch (e) { /* sem suporte */ } }
    function apagar() { try { if (luz) luz.release(); } catch (e) { /* já liberada */ } luz = null; }

    async function ligar() {
      if (ocupado) return;
      ocupado = true; q('ligar').disabled = true; status('Abrindo a câmera…');
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('Este navegador não deixa abrir a câmera. Use o Chrome ou o Safari atualizados, em https.');
        const t = await o.postar(o.base() + '/camera', {});
        await carregar();
        const L = window.LivekitClient;
        sala = new L.Room({ adaptiveStream: false, dynacast: false });
        sala.on(L.RoomEvent.Disconnected, () => { sala = null; apagar(); botoes(false, false); status('Câmera desconectada.'); });
        await sala.connect(t.url, t.token);
        await sala.localParticipant.setCameraEnabled(true, { resolution: { width: 1280, height: 720, frameRate: 30 }, facingMode: frente ? 'user' : 'environment' });
        await sala.localParticipant.setMicrophoneEnabled(true);
        const pub = sala.localParticipant.getTrackPublication(L.Track.Source.Camera);
        if (pub && pub.track) pub.track.attach(q('video'));
        status('Enviando para o YouTube…');
        await o.postar(o.base() + '/camera/iniciar', {});
        botoes(true, false); acender();
        status('Câmera ligada. Espere o sinal ficar ativo.');
        if (o.depois) o.depois();
      } catch (e) {
        if (sala) { try { await sala.disconnect(); } catch (x) { /* nada */ } sala = null; }
        botoes(false, false); status('');
        o.aviso(e.message || 'Não foi possível ligar a câmera.');
      } finally { ocupado = false; q('ligar').disabled = false; }
    }

    async function parar() {
      status('Parando…');
      try { if (sala) { const s = sala; sala = null; await s.disconnect(); } } catch (e) { /* nada */ }
      apagar();
      try { await o.postar(o.base() + '/camera/parar', {}); } catch (e) { o.aviso(e.message); }
      botoes(false, false); status('Câmera parada.');
      if (o.depois) o.depois();
    }

    async function virar() {
      frente = !frente;
      try { if (sala) await sala.localParticipant.setCameraEnabled(true, { resolution: { width: 1280, height: 720, frameRate: 30 }, facingMode: frente ? 'user' : 'environment' }); } catch (e) { o.aviso('Não foi possível virar a câmera.'); }
    }

    q('ligar').onclick = ligar;
    q('parar').onclick = parar;
    q('virar').onclick = virar;

    return {
      mostrar(d) {
        const cam = d.camera || {};
        raiz.hidden = !(cam.disponivel && ['preparada', 'ao_vivo'].includes(d.estado));
        if (raiz.hidden) return;
        const sd = cam.saida;
        if (sd && ['FAILED', 'ABORTED', 'LIMIT_REACHED', 'COMPLETE', 'NAO_ENCONTRADA'].includes(sd.estado)) {
          status('⚠ O LiveKit não está enviando ao YouTube (' + sd.estado.toLowerCase() + (sd.erro ? ': ' + sd.erro : '') + '). Toque em Parar câmera e ligue de novo.');
          if (sala) botoes(true, false);
          return;
        }
        if (sd && sala) status('✅ LiveKit enviando ao YouTube (' + sd.estado.toLowerCase() + '). Espere o sinal ficar ativo.');
        if (!sala) { botoes(false, !!cam.ativa); if (cam.ativa) status('A câmera já está enviando (outro aparelho ou outra aba). Use Parar para encerrar.'); }
      },
    };
  }

  window.PlayGoCamera = { montar };
})();
