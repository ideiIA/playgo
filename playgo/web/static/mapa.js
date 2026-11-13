/* Recursos comuns dos mapas do PlayGo (site e app): camada de satélite, ampliar em tela cheia e busca por endereço.
   Uso: PlayGoMapa.preparar(mapa, { aoEscolher(lat, lng, nome) }) logo depois de criar o L.map. */
(function () {
  const OSM = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
  const SATELITE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  const ROTULOS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';
  const BUSCA = 'https://nominatim.openstreetmap.org/search';

  const css = `
    .pgm-caixa{background:#fff;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.25);padding:5px;width:min(340px,56vw);font:14px/1.3 system-ui,sans-serif}
    .leaflet-control.pgm-caixa{position:absolute;top:10px;left:54px;margin:0 !important}
    .pgm-linha{display:flex;gap:6px}
    .pgm-linha input{flex:1;min-width:0;border:1px solid #d9dbe6;border-radius:8px;padding:8px 10px;font:inherit}
    .pgm-linha button{border:0;background:#6c4cff;color:#fff;border-radius:8px;padding:0 12px;font-weight:700;cursor:pointer}
    .pgm-res{margin:6px 0 0;padding:0;list-style:none;max-height:220px;overflow:auto}
    .pgm-res li{padding:8px 8px;border-radius:8px;cursor:pointer;font-size:13px}
    .pgm-res li:hover,.pgm-res li:focus{background:#f1efff;outline:none}
    .pgm-msg{padding:6px 8px;color:#667085;font-size:13px}
    .pgm-botao{width:34px;height:34px;display:grid;place-items:center;background:#fff;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.25);font-size:18px;cursor:pointer;text-decoration:none;color:#14213d}
    .pgm-tela-cheia{position:fixed !important;inset:0 !important;width:100vw !important;height:100vh !important;max-height:none !important;z-index:99999 !important;border-radius:0 !important;margin:0 !important}
    body.pgm-sem-rolagem{overflow:hidden}
    /* visual: mapa base mais suave para os pinos aparecerem, controles arredondados */
    .pgm-ruas .leaflet-tile-pane{filter:saturate(.62) brightness(1.04) contrast(.95)}
    .leaflet-container{font-family:inherit;background:#e9edf3}
    .leaflet-bar{border:0 !important;border-radius:12px !important;overflow:hidden;box-shadow:0 2px 10px rgba(20,33,61,.22) !important}
    .leaflet-bar a{width:34px !important;height:34px !important;line-height:34px !important;color:#14213d !important}
    .leaflet-control-layers{border:0 !important;border-radius:12px !important;box-shadow:0 2px 10px rgba(20,33,61,.22) !important}
    .leaflet-control-attribution{background:rgba(255,255,255,.75) !important;border-radius:8px 0 0 0;font-size:10px}
    /* pinos */
    .pgm-icone{background:none !important;border:0 !important}
    .pgm-pino{display:grid;place-items:center;border-radius:50%;border:2px solid #fff;box-shadow:0 2px 6px rgba(20,33,61,.38);transition:transform .15s ease;box-sizing:border-box}
    .pgm-pino:hover{transform:scale(1.18)}
    .pgm-pino.discreto{border-width:1.5px;box-shadow:0 1px 3px rgba(20,33,61,.25);opacity:.92}
    .pgm-parceira{position:relative;display:block;width:52px;height:74px;filter:drop-shadow(0 4px 6px rgba(20,33,61,.4))}
    .pgm-coroa{position:absolute;left:50%;top:0;transform:translateX(-50%) rotate(-6deg);z-index:2;line-height:0;margin-top:1px}
    .pgm-disco{position:absolute;left:3px;top:13px;width:46px;height:46px;border-radius:50%;background:#fff;border:3px solid #f5b301;box-sizing:border-box;padding:3px;z-index:1;transition:transform .15s ease;transform-origin:50% 100%}
    .pgm-cauda{position:absolute;left:50%;top:57px;transform:translateX(-50%);border:7px solid transparent;border-top:12px solid #f5b301;border-bottom:0;z-index:0}
    .pgm-parceira:hover .pgm-disco{transform:scale(1.08)}
    .pgm-disco::after{content:"";position:absolute;inset:-6px;border-radius:50%;border:2px solid rgba(245,179,1,.55);animation:pgm-pulso 2.6s ease-out infinite;pointer-events:none}
    @keyframes pgm-pulso{0%{transform:scale(.85);opacity:.9}70%{transform:scale(1.25);opacity:0}100%{opacity:0}}
    .pgm-voce{display:block;width:16px;height:16px;margin:3px;border-radius:50%;background:#6c4cff;border:3px solid #fff;box-shadow:0 0 0 0 rgba(108,76,255,.5);animation:pgm-voce 2s infinite}
    @keyframes pgm-voce{0%{box-shadow:0 0 0 0 rgba(108,76,255,.5)}70%{box-shadow:0 0 0 14px rgba(108,76,255,0)}100%{box-shadow:0 0 0 0 rgba(108,76,255,0)}}
    @media (prefers-reduced-motion:reduce){.pgm-disco::after,.pgm-voce{animation:none}.pgm-pino,.pgm-disco{transition:none}}
    /* balão */
    .pgm-popup .leaflet-popup-content-wrapper{border-radius:16px;box-shadow:0 8px 24px rgba(20,33,61,.28);padding:2px}
    .pgm-popup .leaflet-popup-content{margin:12px 14px;font-family:inherit}
    .pgm-pop{display:flex;flex-direction:column;gap:4px;min-width:150px;color:#14213d}
    .pgm-pop b{font-size:15px;line-height:1.25}
    .pgm-dist{font-size:13px;color:#667085}
    .pgm-selo{display:inline-flex;align-items:center;gap:4px;align-self:flex-start;background:#fff4d6;color:#8a5f00;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700}
    .pgm-ver{display:inline-block;margin-top:6px;align-self:flex-start;background:#6c4cff;color:#fff !important;text-decoration:none;border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600}
    .pgm-ver:hover{background:#5a3df0}
    /* filtro de camadas */
    .pgm-filtro{background:rgba(255,255,255,.96);border-radius:18px;padding:10px 12px;box-shadow:0 6px 20px rgba(20,33,61,.25);max-width:min(340px,calc(100vw - 32px));font-family:inherit}
    .pgm-titulo{display:flex;align-items:center;gap:6px;width:100%;border:0;background:none;padding:0 0 6px;font:700 12px/1.2 inherit;color:#667085;text-transform:uppercase;letter-spacing:.04em;cursor:pointer;text-align:left}
    .pgm-titulo small{text-transform:none;letter-spacing:0;font-weight:600}.pgm-titulo:only-child{padding-bottom:0}
    .pgm-chip{display:inline-flex;align-items:center;gap:6px;border:1px solid #e4e7f0;background:#fff;color:#14213d;border-radius:999px;padding:4px 10px 4px 5px;margin:0 4px 4px 0;font:600 12px/1.2 inherit;cursor:pointer;min-height:30px}
    .pgm-chip.tudo{padding-left:12px}
    .pgm-chip-ic{display:grid;place-items:center;width:20px;height:20px;border-radius:50%;flex:none}
    .pgm-chip small{opacity:.7;font-weight:600}
    .pgm-chip.on{background:#ece8ff;border-color:#6c4cff;color:#3d2bb8}
    .pgm-chip.tudo.on{background:#14213d;border-color:#14213d;color:#fff}
    .pgm-chip:focus-visible{outline:2px solid #6c4cff;outline-offset:2px}
    .pgm-leg{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:6px;padding-top:6px;border-top:1px solid #eef0f5;font-size:11px;color:#667085}
    .pgm-leg span{display:inline-flex;align-items:center;gap:4px}
    .pgm-leg i{display:inline-block;width:10px;height:10px;border-radius:50%}
    .pgm-leg-p{color:#8a5f00;font-weight:700}`;

  function estilo() {
    if (document.getElementById('pgm-estilo')) return;
    const s = document.createElement('style');
    s.id = 'pgm-estilo';
    s.textContent = css;
    document.head.appendChild(s);
  }

  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function camadas(mapa) {
    const ruas = L.tileLayer(OSM, { maxZoom: 19, attribution: '© OpenStreetMap' });
    const satelite = L.layerGroup([
      L.tileLayer(SATELITE, { maxZoom: 19, maxNativeZoom: 18, attribution: 'Imagens © Esri, Maxar, Earthstar Geographics' }),
      L.tileLayer(ROTULOS, { maxZoom: 19, maxNativeZoom: 18 }),
    ]);
    ruas.addTo(mapa);
    mapa.getContainer().classList.add('pgm-ruas');  // mapa de ruas com cores mais suaves; o satélite fica como está
    mapa.on('baselayerchange', (e) => mapa.getContainer().classList.toggle('pgm-ruas', e.layer === ruas));
    L.control.layers({ '🗺️ Mapa': ruas, '🛰️ Satélite': satelite }, null, { position: 'topright', collapsed: true }).addTo(mapa);
  }

  function telaCheia(mapa) {
    const Controle = L.Control.extend({
      options: { position: 'topright' },
      onAdd() {
        const a = L.DomUtil.create('a', 'pgm-botao');
        a.href = '#';
        a.title = 'Ampliar o mapa';
        a.setAttribute('role', 'button');
        a.setAttribute('aria-label', 'Ampliar o mapa em tela cheia');
        a.textContent = '⛶';
        L.DomEvent.disableClickPropagation(a);
        const alternar = (ev) => {
          if (ev) L.DomEvent.preventDefault(ev);
          const el = mapa.getContainer();
          const ligado = el.classList.toggle('pgm-tela-cheia');
          document.body.classList.toggle('pgm-sem-rolagem', ligado);
          a.textContent = ligado ? '✕' : '⛶';
          a.title = ligado ? 'Voltar ao tamanho normal' : 'Ampliar o mapa';
          setTimeout(() => mapa.invalidateSize(), 60);
        };
        L.DomEvent.on(a, 'click', alternar);
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && mapa.getContainer().classList.contains('pgm-tela-cheia')) alternar(); });
        return a;
      },
    });
    new Controle().addTo(mapa);
  }

  function busca(mapa, op) {
    let marca = null;
    const Controle = L.Control.extend({
      options: { position: 'topleft' },
      onAdd() {
        const caixa = L.DomUtil.create('div', 'pgm-caixa');
        caixa.innerHTML = '<form class="pgm-linha" role="search"><input type="search" placeholder="Buscar endereço ou lugar" aria-label="Buscar endereço" autocomplete="off"><button type="submit">Buscar</button></form><ul class="pgm-res" hidden></ul>';
        L.DomEvent.disableClickPropagation(caixa);
        L.DomEvent.disableScrollPropagation(caixa);
        const form = caixa.querySelector('form'), campo = caixa.querySelector('input'), lista = caixa.querySelector('.pgm-res');
        const msg = (t) => { lista.hidden = false; lista.innerHTML = `<li class="pgm-msg">${esc(t)}</li>`; };
        const ir = (lat, lng, nome) => {
          lista.hidden = true;
          mapa.setView([lat, lng], Math.max(mapa.getZoom(), 17));
          if (op && op.marcar === false) { if (op.aoEscolher) op.aoEscolher(lat, lng, nome); return; }  // o formulário já mostra o próprio pino
          if (marca) marca.remove();
          marca = L.marker([lat, lng]).addTo(mapa).bindPopup(esc(nome)).openPopup();
          if (op && op.aoEscolher) op.aoEscolher(lat, lng, nome);
        };
        form.addEventListener('submit', async (ev) => {
          ev.preventDefault();
          const q = campo.value.trim();
          if (q.length < 3) return msg('Digite pelo menos 3 letras.');
          msg('Buscando…');
          try {
            const c = mapa.getCenter();
            const url = `${BUSCA}?format=jsonv2&limit=6&countrycodes=br&accept-language=pt-BR&q=${encodeURIComponent(q)}&viewbox=${c.lng - 1},${c.lat + 1},${c.lng + 1},${c.lat - 1}`;
            const r = await fetch(url, { headers: { Accept: 'application/json' } });
            if (!r.ok) throw new Error('falha');
            const itens = await r.json();
            if (!itens.length) return msg('Nada encontrado. Tente com a cidade ou o bairro.');
            lista.hidden = false;
            lista.innerHTML = itens.map((i, n) => `<li tabindex="0" data-n="${n}">${esc(i.display_name)}</li>`).join('');
            lista.onclick = (e) => { const li = e.target.closest('li[data-n]'); if (li) { const i = itens[Number(li.dataset.n)]; ir(Number(i.lat), Number(i.lon), i.display_name); } };
            lista.onkeydown = (e) => { if (e.key === 'Enter') lista.onclick(e); };
            if (itens.length === 1) ir(Number(itens[0].lat), Number(itens[0].lon), itens[0].display_name);
          } catch (e) {
            msg('Não consegui buscar agora. Confira a conexão e tente de novo.');
          }
        });
        return caixa;
      },
    });
    new Controle().addTo(mapa);
  }

  // ---- pinos do Explorar: parceira em destaque (logo do PlayGo + coroa), o resto discreto com ícones Material, e filtro de camadas
  // Ícones: Material Design Icons (Google), licença Apache 2.0, trilhas 24x24 copiadas do pacote @material-design-icons/svg (nada é carregado da internet).
  const MD = {
    stadium: '<path d="M7 5 3 7V3l4 2zm11-2v4l4-2-4-2zm-7-1v4l4-2-4-2zm-6 8.04c1.38.49 3.77.96 7 .96s5.62-.47 7-.96C19 9.86 16.22 9 12 9s-7 .86-7 1.04zM15 17H9v4.88c-4.06-.39-7-1.54-7-2.88v-9c0-1.66 4.48-3 10-3s10 1.34 10 3v9c0 1.34-2.94 2.48-7 2.87V17z"/>',
    park: '<path d="M17 12h2L12 2 5.05 12H7l-3.9 6h6.92v4h3.96v-4H21z"/>',
    fitness_center: '<path d="M20.57 14.86 22 13.43 20.57 12 17 15.57 8.43 7 12 3.43 10.57 2 9.14 3.43 7.71 2 5.57 4.14 4.14 2.71 2.71 4.14l1.43 1.43L2 7.71l1.43 1.43L2 10.57 3.43 12 7 8.43 15.57 17 12 20.57 13.43 22l1.43-1.43L16.29 22l2.14-2.14 1.43 1.43 1.43-1.43-1.43-1.43L22 16.29z"/>',
    storefront: '<path d="m21.9 8.89-1.05-4.37c-.22-.9-1-1.52-1.91-1.52H5.05c-.9 0-1.69.63-1.9 1.52L2.1 8.89c-.24 1.02-.02 2.06.62 2.88.08.11.19.19.28.29V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-6.94c.09-.09.2-.18.28-.28.64-.82.87-1.87.62-2.89zm-2.99-3.9 1.05 4.37c.1.42.01.84-.25 1.17-.14.18-.44.47-.94.47-.61 0-1.14-.49-1.21-1.14L16.98 5l1.93-.01zM13 5h1.96l.54 4.52c.05.39-.07.78-.33 1.07-.22.26-.54.41-.95.41-.67 0-1.22-.59-1.22-1.31V5zM8.49 9.52 9.04 5H11v4.69c0 .72-.55 1.31-1.29 1.31-.34 0-.65-.15-.89-.41a1.42 1.42 0 0 1-.33-1.07zm-4.45-.16L5.05 5h1.97l-.58 4.86c-.08.65-.6 1.14-1.21 1.14-.49 0-.8-.29-.93-.47-.27-.32-.36-.75-.26-1.17zM5 19v-6.03c.08.01.15.03.23.03.87 0 1.66-.36 2.24-.95.6.6 1.4.95 2.31.95.87 0 1.65-.36 2.23-.93.59.57 1.39.93 2.29.93.84 0 1.64-.35 2.24-.95.58.59 1.37.95 2.24.95.08 0 .15-.02.23-.03V19H5z"/>',
    sports_soccer: '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 3.3 1.35-.95a8.01 8.01 0 0 1 4.38 3.34l-.39 1.34-1.35.46L13 6.7V5.3zm-3.35-.95L11 5.3v1.4L7.01 9.49l-1.35-.46-.39-1.34a8.103 8.103 0 0 1 4.38-3.34zM7.08 17.11l-1.14.1A7.938 7.938 0 0 1 4 12c0-.12.01-.23.02-.35l1-.73 1.38.48 1.46 4.34-.78 1.37zm7.42 2.48c-.79.26-1.63.41-2.5.41s-1.71-.15-2.5-.41l-.69-1.49.64-1.1h5.11l.64 1.11-.7 1.48zM14.27 15H9.73l-1.35-4.02L12 8.44l3.63 2.54L14.27 15zm3.79 2.21-1.14-.1-.79-1.37 1.46-4.34 1.39-.47 1 .73c.01.11.02.22.02.34 0 1.99-.73 3.81-1.94 5.21z"/>',
    emoji_events: '<path d="M19 5h-2V3H7v2H5c-1.1 0-2 .9-2 2v1c0 2.55 1.92 4.63 4.39 4.94A5.01 5.01 0 0 0 11 15.9V19H7v2h10v-2h-4v-3.1a5.01 5.01 0 0 0 3.61-2.96C19.08 12.63 21 10.55 21 8V7c0-1.1-.9-2-2-2zM5 8V7h2v3.82C5.84 10.4 5 9.3 5 8zm14 0c0 1.3-.84 2.4-2 2.82V7h2v1z"/>',
    groups: '<path d="M12 12.75c1.63 0 3.07.39 4.24.9 1.08.48 1.76 1.56 1.76 2.73V18H6v-1.61c0-1.18.68-2.26 1.76-2.73 1.17-.52 2.61-.91 4.24-.91zM4 13c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm1.13 1.1c-.37-.06-.74-.1-1.13-.1-.99 0-1.93.21-2.78.58A2.01 2.01 0 0 0 0 16.43V18h4.5v-1.61c0-.83.23-1.61.63-2.29zM20 13c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm4 3.43c0-.81-.48-1.53-1.22-1.85A6.95 6.95 0 0 0 20 14c-.39 0-.76.04-1.13.1.4.68.63 1.46.63 2.29V18H24v-1.57zM12 6c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3z"/>',
    schedule: '<path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8z"/><path d="M12.5 7H11v6l5.25 3.15.75-1.23-4.5-2.67z"/>',
    school: '<path d="M5 13.18v4L12 21l7-3.82v-4L12 17l-7-3.82zM12 3 1 9l11 6 9-4.91V17h2V9L12 3z"/>',
    event_available: '<path d="M16.53 11.06 15.47 10l-4.88 4.88-2.12-2.12-1.06 1.06L10.59 17l5.94-5.94zM19 3h-1V1h-2v2H8V1H6v2H5c-1.11 0-1.99.9-1.99 2L3 19a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11z"/>',
    local_fire_department: '<path d="m12 12.9-2.13 2.09c-.56.56-.87 1.29-.87 2.07C9 18.68 10.35 20 12 20s3-1.32 3-2.94c0-.78-.31-1.52-.87-2.07L12 12.9z"/><path d="m16 6-.44.55C14.38 8.02 12 7.19 12 5.3V2S4 6 4 13c0 2.92 1.56 5.47 3.89 6.86-.56-.79-.89-1.76-.89-2.8 0-1.32.52-2.56 1.47-3.5L12 10.1l3.53 3.47c.95.93 1.47 2.17 1.47 3.5 0 1.02-.31 1.96-.85 2.75 1.89-1.15 3.29-3.06 3.71-5.3.66-3.55-1.07-6.9-3.86-8.52z"/>',
  };
  const icone = (nome, tam, cor) => '<svg viewBox="0 0 24 24" width="' + tam + '" height="' + tam + '" fill="' + (cor || '#fff') + '" aria-hidden="true" focusable="false">' + MD[nome] + '</svg>';

  // logo do PlayGo (bola com rastro lima), mesma geometria de templates/_logo.html, recortada para caber no pino
  const BOLA = '<svg viewBox="8 6 108 108" width="100%" height="100%" aria-hidden="true" focusable="false"><defs><clipPath id="pgm-bola"><circle cx="62" cy="58" r="40"/></clipPath></defs><circle cx="62" cy="58" r="40" fill="#fff" stroke="#14213d" stroke-width="3"/><g clip-path="url(#pgm-bola)" fill="#14213d" stroke="#14213d" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"><path d="M62.0 45.2L62.0 30.2 M74.1 54.1L88.5 49.4 M69.5 68.3L78.4 80.5 M54.5 68.3L45.6 80.5 M49.9 54.1L35.5 49.4" fill="none"/><polygon points="62.0,30.2 47.1,19.4 52.8,1.9 71.2,1.9 76.9,19.4"/><polygon points="88.5,49.4 94.2,31.9 112.6,31.9 118.2,49.4 103.4,60.2"/><polygon points="78.4,80.5 96.8,80.5 102.4,98.0 87.6,108.8 72.7,98.0"/><polygon points="45.6,80.5 51.3,98.0 36.4,108.8 21.6,98.0 27.2,80.5"/><polygon points="35.5,49.4 20.6,60.2 5.8,49.4 11.4,31.9 29.8,31.9"/></g><polygon points="62.0,45.2 74.1,54.1 69.5,68.3 54.5,68.3 49.9,54.1" fill="#14213d" stroke="#14213d" stroke-width="3" stroke-linejoin="round"/><path d="M16 76A48 48 0 0 0 66 108" fill="none" stroke="#b7f34b" stroke-width="9" stroke-linecap="round"/></svg>';
  const COROA = '<svg viewBox="0 0 24 16" width="26" height="17" aria-hidden="true" focusable="false"><path d="M2.2 5.2 6.6 9l5.4-7 5.4 7 4.4-3.8-1.9 9.3H4.1z" fill="#f5b301" stroke="#9a6a00" stroke-width="1" stroke-linejoin="round"/><circle cx="2.2" cy="4.4" r="1.5" fill="#f5b301" stroke="#9a6a00" stroke-width=".7"/><circle cx="12" cy="1.8" r="1.6" fill="#f5b301" stroke="#9a6a00" stroke-width=".7"/><circle cx="21.8" cy="4.4" r="1.5" fill="#f5b301" stroke="#9a6a00" stroke-width=".7"/></svg>';

  // tipo do ponto -> [cor, ícone Material, tamanho]
  const ESTILO_PINO = {
    arena: ['#94a3b8', 'stadium', 24],       // arena que não assina: discreta
    publico: ['#22a55a', 'park', 28],        // local público de prática
    clube: ['#e5484d', 'fitness_center', 28],
    loja: ['#f97316', 'storefront', 28],
    atividade: ['#6c4cff', 'sports_soccer', 28],
    urgente: ['#ff3d81', 'local_fire_department', 30],
    campeonato: ['#d99a00', 'emoji_events', 28],
    grupo: ['#22b7e0', 'groups', 28],
    horario: ['#16c784', 'schedule', 28],
    profissional: ['#0ea5a4', 'school', 28],
    oferta: ['#f59e0b', 'event_available', 28],
  };
  const CAMADAS = [['base', 'Arenas e locais públicos', 'stadium', '#6c4cff'], ['clubes', 'Clubes e academias', 'fitness_center', '#e5484d'], ['lojas', 'Lojas', 'storefront', '#f97316'], ['eventos', 'Jogos e eventos', 'sports_soccer', '#6c4cff']];
  const CHAVE_CAMADAS = 'pg-mapa-camadas';
  const escapar = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function lerCamadas() {
    try { const v = JSON.parse(localStorage.getItem(CHAVE_CAMADAS) || 'null'); if (Array.isArray(v)) return v.filter((k) => CAMADAS.some((c) => c[0] === k)); } catch (e) { /* sem armazenamento */ }
    return ['base'];
  }
  function guardarCamadas(v) { try { localStorage.setItem(CHAVE_CAMADAS, JSON.stringify(v)); } catch (e) { /* sem armazenamento */ } }

  function chaveDoEstilo(p) {
    if (p.tipo === 'local') return p.categoria === 'arena' ? 'arena' : (p.categoria === 'clube' ? 'clube' : (p.categoria === 'loja' ? 'loja' : 'publico'));
    if (p.tipo === 'atividade' && p.urgente) return 'urgente';
    return ESTILO_PINO[p.tipo] ? p.tipo : 'atividade';
  }

  function pino(p) {
    const popup = '<div class="pgm-pop">' + (p.premium ? '<span class="pgm-selo">' + icone('stadium', 12, '#8a5f00') + ' Parceira PlayGo</span>' : '') + '<b>' + escapar(p.rotulo) + '</b>' +
      (p.distancia ? '<span class="pgm-dist">' + escapar(p.distancia) + '</span>' : '') + '<a class="pgm-ver" href="' + escapar(p._alvo) + '">Ver detalhes</a></div>';
    let m;
    if (p.tipo === 'arena' && p.premium) {  // parceira: logo do PlayGo num pino com coroa, borda dourada e pulso suave; sempre por cima
      m = L.marker([p.latitude, p.longitude], { zIndexOffset: 1000, riseOnHover: true, icon: L.divIcon({ className: 'pgm-icone', iconSize: [52, 74], iconAnchor: [26, 74], popupAnchor: [0, -70],
        html: '<span class="pgm-parceira"><span class="pgm-coroa">' + COROA + '</span><span class="pgm-disco">' + BOLA + '</span><i class="pgm-cauda"></i></span>' }) });
    } else {
      const [cor, ic, t] = ESTILO_PINO[chaveDoEstilo(p)];
      const discreto = chaveDoEstilo(p) === 'arena';
      m = L.marker([p.latitude, p.longitude], { riseOnHover: true, zIndexOffset: discreto ? -200 : 0, icon: L.divIcon({ className: 'pgm-icone', iconSize: [t, t], iconAnchor: [t / 2, t / 2], popupAnchor: [0, -t / 2],
        html: '<span class="pgm-pino' + (discreto ? ' discreto' : '') + '" style="width:' + t + 'px;height:' + t + 'px;background:' + cor + '">' + icone(ic, Math.round(t * 0.6)) + '</span>' }) });
    }
    return m.bindPopup(popup, { className: 'pgm-popup', maxWidth: 260 });
  }

  /* PlayGoMapa.pontos(mapa, pontos, { alvo(p) -> link }): desenha os pontos do Explorar.
     Parceira (arena com plano) fica sempre visível e em destaque; as outras arenas viram um pino cinza discreto; o filtro no canto do mapa liga/desliga
     "Clubes e academias", "Lojas" e "Jogos e eventos" (abre só com "Arenas e locais públicos"). A escolha fica guardada no navegador. */
  function pontosDoExplorar(mapa, pontos, op) {
    estilo();
    const grupos = {};
    CAMADAS.forEach(([k]) => { grupos[k] = L.layerGroup(); });
    const parceiras = L.layerGroup().addTo(mapa);
    const porCamada = {};
    pontos.forEach((p) => {
      p._alvo = op.alvo(p);
      const m = pino(p);
      (p.tipo === 'arena' && p.premium ? parceiras : grupos[p.camada || 'base']).addLayer(m);
      porCamada[p.camada || 'base'] = (porCamada[p.camada || 'base'] || 0) + 1;
    });
    const ligadas = new Set(lerCamadas());
    const aplicar = () => CAMADAS.forEach(([k]) => { if (ligadas.has(k)) grupos[k].addTo(mapa); else mapa.removeLayer(grupos[k]); });
    aplicar();

    const Filtro = L.Control.extend({
      options: { position: 'bottomleft' },
      onAdd() {
        const caixa = L.DomUtil.create('div', 'pgm-filtro');
        L.DomEvent.disableClickPropagation(caixa);
        let aberto = mapa.getSize().x >= 560;  // em mapa estreito (celular) o painel começa recolhido para não cobrir o mapa
        const desenhar = () => {
          const todas = CAMADAS.every(([k]) => ligadas.has(k));
          const ativas = CAMADAS.filter(([k]) => ligadas.has(k)).length;
          const cab = '<button type="button" class="pgm-titulo" data-k="abrir" aria-expanded="' + aberto + '">Mostrar no mapa <span class="pgm-seta">' + (aberto ? '▾' : '▸') + '</span>' + (aberto ? '' : ' <small>(' + ativas + ' de ' + CAMADAS.length + ')</small>') + '</button>';
          if (!aberto) { caixa.innerHTML = cab; return; }
          caixa.innerHTML = cab + CAMADAS.map(([k, nome, ic, cor]) => '<button type="button" class="pgm-chip' + (ligadas.has(k) ? ' on' : '') + '" data-k="' + k + '" aria-pressed="' + ligadas.has(k) + '"><span class="pgm-chip-ic" style="background:' + cor + '">' + icone(ic, 12) + '</span>' + escapar(nome) + (porCamada[k] ? ' <small>' + porCamada[k] + '</small>' : '') + '</button>').join('') +
            '<button type="button" class="pgm-chip tudo' + (todas ? ' on' : '') + '" data-k="tudo" aria-pressed="' + todas + '">Tudo</button>' +
            '<div class="pgm-leg"><span class="pgm-leg-p">' + icone('stadium', 11, '#8a5f00') + ' Parceira PlayGo</span><span><i style="background:#94a3b8"></i> Outras arenas</span><span><i style="background:#22a55a"></i> Local público</span></div>';
        };
        caixa.addEventListener('click', (e) => {
          const b = e.target.closest('[data-k]');
          if (!b) return;
          if (b.dataset.k === 'abrir') { aberto = !aberto; desenhar(); return; }
          if (b.dataset.k === 'tudo') { if (CAMADAS.every(([k]) => ligadas.has(k))) { ligadas.clear(); ligadas.add('base'); } else CAMADAS.forEach(([k]) => ligadas.add(k)); }
          else if (ligadas.has(b.dataset.k)) ligadas.delete(b.dataset.k); else ligadas.add(b.dataset.k);
          guardarCamadas([...ligadas]);
          aplicar();
          desenhar();
        });
        desenhar();
        return caixa;
      },
    });
    new Filtro().addTo(mapa);

    // enquadra o que está visível ao abrir (parceiras e camadas ligadas)
    const limites = [];
    pontos.forEach((p) => { if ((p.tipo === 'arena' && p.premium) || ligadas.has(p.camada || 'base')) limites.push([p.latitude, p.longitude]); });
    return limites;
  }

  // "Você está aqui": ponto roxo com pulso (no lugar do círculo simples)
  function voce(mapa, centro) {
    estilo();
    return L.marker([centro.latitude, centro.longitude], { interactive: false, keyboard: false, zIndexOffset: 900, icon: L.divIcon({ className: 'pgm-icone', iconSize: [22, 22], iconAnchor: [11, 11], html: '<span class="pgm-voce"></span>' }) }).addTo(mapa);
  }

  window.PlayGoMapa = {
    preparar(mapa, op) {
      estilo();
      camadas(mapa);
      telaCheia(mapa);
      busca(mapa, op || {});
    },
    pontos: pontosDoExplorar,
    voce,
  };
})();
