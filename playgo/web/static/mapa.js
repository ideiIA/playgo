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
    body.pgm-sem-rolagem{overflow:hidden}`;

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

  window.PlayGoMapa = {
    preparar(mapa, op) {
      estilo();
      camadas(mapa);
      telaCheia(mapa);
      busca(mapa, op || {});
    },
  };
})();
