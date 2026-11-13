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

  // ---- pinos do Explorar: parceira em destaque, o resto discreto, e filtro de camadas
  const CAMADAS = [['base', 'Arenas e locais públicos'], ['clubes', 'Clubes e academias'], ['lojas', 'Lojas'], ['eventos', 'Jogos e eventos']];
  const CHAVE_CAMADAS = 'pg-mapa-camadas';
  const escapar = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function lerCamadas() {
    try { const v = JSON.parse(localStorage.getItem(CHAVE_CAMADAS) || 'null'); if (Array.isArray(v)) return v.filter((k) => CAMADAS.some((c) => c[0] === k)); } catch (e) { /* sem armazenamento */ }
    return ['base'];
  }
  function guardarCamadas(v) { try { localStorage.setItem(CHAVE_CAMADAS, JSON.stringify(v)); } catch (e) { /* sem armazenamento */ } }

  function pino(p, cores) {
    const popup = (p.premium ? '<span style="font-size:12px;font-weight:700;color:#8a5f00">👑 Parceira PlayGo</span><br>' : '') + '<b>' + escapar(p.rotulo) + '</b><br>' + escapar(p.distancia || '') + '<br><a href="' + escapar(p._alvo) + '">Ver detalhes</a>';
    let m;
    if (p.tipo === 'arena' && p.premium) {  // parceira: grande, coroa, borda dourada, sempre por cima
      m = L.marker([p.latitude, p.longitude], { zIndexOffset: 1000, icon: L.divIcon({ className: 'pino-local', iconSize: [42, 42], iconAnchor: [21, 21], popupAnchor: [0, -22],
        html: '<span style="position:relative;display:grid;place-items:center;width:42px;height:42px;border-radius:50%;background:#6c4cff;border:4px solid #f5b301;box-shadow:0 4px 14px rgba(0,0,0,.45);font-size:21px;line-height:1">🏟️<b style="position:absolute;top:-23px;left:50%;transform:translateX(-50%);font-size:21px;line-height:1;filter:drop-shadow(0 1px 1px rgba(0,0,0,.4))">👑</b></span>' }) });
    } else if (p.tipo === 'arena' || (p.tipo === 'local' && p.categoria === 'arena')) {  // arena que não assina: pontinho cinza
      m = L.circleMarker([p.latitude, p.longitude], { radius: 6, color: '#fff', weight: 1.5, fillColor: '#9aa3b2', fillOpacity: 0.9 });
    } else if (p.tipo === 'local' && p.icone) {  // local público, clube, loja: pino pequeno com o ícone do tipo
      const t = p.camada === 'base' ? 24 : 28;
      m = L.marker([p.latitude, p.longitude], { icon: L.divIcon({ className: 'pino-local', iconSize: [t, t], iconAnchor: [t / 2, t / 2], popupAnchor: [0, -t / 2],
        html: '<span style="display:grid;place-items:center;width:' + t + 'px;height:' + t + 'px;border-radius:50%;background:' + (p.cor || '#0ea5e9') + ';border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3);font-size:' + Math.round(t * 0.55) + 'px;line-height:1;opacity:.92">' + p.icone + '</span>' }) });
    } else if ((p.tipo === 'profissional' || p.tipo === 'oferta') && p.icone) {
      m = L.marker([p.latitude, p.longitude], { icon: L.divIcon({ className: 'pino-local', iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -14],
        html: '<span style="display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:' + (p.cor || '#0ea5e9') + ';border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.3);font-size:15px;line-height:1">' + p.icone + '</span>' }) });
    } else {
      m = L.circleMarker([p.latitude, p.longitude], { radius: p.urgente ? 10 : 8, color: '#fff', weight: 2, fillColor: p.urgente ? '#ff3d81' : (cores[p.tipo] || '#6c4cff'), fillOpacity: 0.95 });
    }
    return m.bindPopup(popup);
  }

  /* PlayGoMapa.pontos(mapa, pontos, { alvo(p) -> link, aoAjustar }): desenha os pontos do Explorar.
     Parceira (arena com plano) fica sempre visível e em destaque; as outras arenas viram um ponto cinza; o filtro no canto do mapa liga/desliga
     "Clubes e academias", "Lojas" e "Jogos e eventos" (abre só com "Arenas e locais públicos"). A escolha fica guardada no navegador. */
  const CSS_FILTRO = '.pgm-filtro{background:#fff;border-radius:16px;padding:8px 10px;box-shadow:0 4px 14px rgba(20,33,61,.25);max-width:min(330px,calc(100vw - 32px));font-family:inherit}' +
    '.pgm-titulo{font-size:12px;font-weight:700;color:#667085;margin:0 0 6px}' +
    '.pgm-chip{border:1px solid #e4e7f0;background:#fff;color:#14213d;border-radius:999px;padding:5px 10px;margin:0 4px 4px 0;font:600 12px/1.2 inherit;cursor:pointer;min-height:30px}' +
    '.pgm-chip small{opacity:.7;font-weight:600}.pgm-chip.on{background:#6c4cff;border-color:#6c4cff;color:#fff}.pgm-chip.tudo.on{background:#14213d;border-color:#14213d}' +
    '.pgm-chip:focus-visible{outline:2px solid #6c4cff;outline-offset:2px}';
  function pontosDoExplorar(mapa, pontos, op) {
    estilo();
    if (!document.getElementById('pgm-filtro-estilo')) { const s = document.createElement('style'); s.id = 'pgm-filtro-estilo'; s.textContent = CSS_FILTRO; document.head.appendChild(s); }
    const cores = { atividade: '#6c4cff', campeonato: '#ff8a00', grupo: '#22c7f2', horario: '#16c784' };
    const grupos = {};
    CAMADAS.forEach(([k]) => { grupos[k] = L.layerGroup(); });
    const parceiras = L.layerGroup().addTo(mapa);
    const porCamada = {};
    pontos.forEach((p) => {
      p._alvo = op.alvo(p);
      const m = pino(p, cores);
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
        const desenhar = () => {
          const todas = CAMADAS.every(([k]) => ligadas.has(k));
          caixa.innerHTML = '<div class="pgm-titulo">Mostrar no mapa</div>' + CAMADAS.map(([k, nome]) => '<button type="button" class="pgm-chip' + (ligadas.has(k) ? ' on' : '') + '" data-k="' + k + '" aria-pressed="' + ligadas.has(k) + '">' + escapar(nome) + (porCamada[k] ? ' <small>' + porCamada[k] + '</small>' : '') + '</button>').join('') +
            '<button type="button" class="pgm-chip tudo' + (todas ? ' on' : '') + '" data-k="tudo" aria-pressed="' + todas + '">Tudo</button>';
        };
        caixa.addEventListener('click', (e) => {
          const b = e.target.closest('[data-k]');
          if (!b) return;
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

  window.PlayGoMapa = {
    preparar(mapa, op) {
      estilo();
      camadas(mapa);
      telaCheia(mapa);
      busca(mapa, op || {});
    },
    pontos: pontosDoExplorar,
  };
})();
