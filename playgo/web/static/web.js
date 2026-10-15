/* PlayGo web — comportamento comum: toast, ações por fetch (EU VOU), localização e mapas (Leaflet). */
(function () {
  const $ = (s, r) => (r || document).querySelector(s);

  window.toast = function (texto) {
    const e = $('#toast');
    if (!e) return;
    e.textContent = texto;
    e.style.display = 'block';
    clearTimeout(e._t);
    e._t = setTimeout(() => (e.style.display = 'none'), 2800);
  };

  async function api(url, corpo) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo || {}) });
    const dados = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof dados.detail === 'string' ? dados.detail : (dados.detail && dados.detail.mensagem) || 'Não foi possível concluir.');
    return dados;
  }
  window.api = api;

  // API com o cookie de sessão do site (multipart quando há `form`)
  window.webApi = async function (caminho, { metodo = 'GET', corpo, form } = {}) {
    const opc = { method: metodo, credentials: 'same-origin', headers: {} };
    if (form) opc.body = form;
    else if (corpo !== undefined) { opc.headers['Content-Type'] = 'application/json'; opc.body = JSON.stringify(corpo); }
    const r = await fetch('/api/v1' + caminho, opc);
    let d = {};
    try { d = await r.json(); } catch (e) { /* sem corpo */ }
    if (!r.ok) {
      const det = d.detail;
      throw new Error(typeof det === 'string' ? det : (det && det.mensagem) || (Array.isArray(det) ? 'Confira os campos informados.' : 'Não foi possível concluir.'));
    }
    return d;
  };
  // Monta um mural nesta página. extra: {geral, escopo, escopoId, titulo, consulta}
  window.montarMural = function (id, extra) {
    const el = document.getElementById(id);
    if (!el || !window.Mural) return null;
    const P = window.PLAYGO || {};
    const consulta = [P.posicao ? 'lat=' + P.posicao.lat + '&lng=' + P.posicao.lng : '', (extra && extra.consulta) || ''].filter(Boolean).join('&');
    return Mural.montar(el, Object.assign({ api: webApi, posicao: P.posicao, eu: P.eu, toast: window.toast, consulta }, extra));
  };

  const ROTULOS = {
    confirmado: ['✓ Confirmado', 'Você está confirmado nesta atividade!'],
    espera: ['Na lista de espera', 'Lotado: você entrou na lista de espera. Avisamos se abrir vaga.'],
    pendente: ['Aguardando aprovação', 'Pedido enviado ao organizador.'],
  };
  // Botão "EU VOU": confirma sem sair da tela
  document.addEventListener('click', async (ev) => {
    const b = ev.target.closest('[data-entrar]');
    if (!b || b.disabled) return;
    ev.preventDefault();
    b.disabled = true;
    try {
      const a = await api('/api/v1/atividades/' + b.dataset.entrar + '/entrar');
      const [rotulo, msg] = ROTULOS[a.minha_participacao] || ['✓ Pronto', 'Feito!'];
      b.textContent = rotulo;
      if (a.minha_participacao === 'confirmado') b.classList.add('confirmado');
      toast(msg);
      const lugar = b.closest('.card');
      const conf = lugar && lugar.querySelector('[data-confirmados]');
      if (conf) conf.textContent = a.confirmados + '/' + a.max_participantes + ' confirmados';
    } catch (e) {
      b.disabled = false;
      toast(e.message);
    }
  });

  // Localização do aparelho → perfil → recarrega
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-localizar]');
    if (!b) return;
    if (!navigator.geolocation) return toast('Seu navegador não informa a localização.');
    const P = window.PLAYGO || {};
    if (!(P.eu && P.eu.consentLocalizacao) && !confirm('O PlayGo vai guardar a localização do seu perfil para mostrar atividades perto de você. Você pode revogar quando quiser em Privacidade. Autoriza?')) return;
    toast('Buscando sua localização…');
    navigator.geolocation.getCurrentPosition(
      async (p) => {
        try {
          await webApi('/me', { metodo: 'PATCH', corpo: { latitude: p.coords.latitude, longitude: p.coords.longitude, consent_localizacao: true } });
          location.reload();
        } catch (e) { toast(e.message || 'Não foi possível salvar a localização.'); }
      },
      () => toast('Não consegui pegar sua localização. Permita o acesso no navegador.'),
      { enableHighAccuracy: false, timeout: 10000 }
    );
  });

  // Mapa de pontos (Explorar). dados: {centro, pontos}
  window.mapaPontos = function (id, centro, pontos, usuario) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!window.L) { el.innerHTML = '<p class="vazio">Mapa indisponível sem conexão. A lista ao lado continua funcionando.</p>'; return; }
    const mapa = L.map(el).setView([centro.latitude, centro.longitude], 13);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(mapa);
    L.circleMarker([centro.latitude, centro.longitude], { radius: 8, color: '#fff', weight: 3, fillColor: '#6c4cff', fillOpacity: 1 }).addTo(mapa).bindTooltip('Você');
    const rotas = { atividade: '/atividades/', campeonato: '/campeonatos/', arena: '/arenas/', grupo: '/grupos/', horario: '/arenas/', local: '/locais/' };
    const limites = [[centro.latitude, centro.longitude]];
    pontos.forEach((p) => {
      const cor = p.urgente ? '#ff3d81' : { atividade: '#6c4cff', campeonato: '#ff8a00', arena: '#16c784', grupo: '#22c7f2', horario: '#16c784', local: '#0ea5e9' }[p.tipo];
      // locais do guia: pino com o ícone e a cor do tipo (público, arena, clube, loja)
      const m = p.tipo === 'local' && p.icone
        ? L.marker([p.latitude, p.longitude], { icon: L.divIcon({ className: 'pino-local', iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -16],
            html: '<span style="display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:' + (p.cor || '#0ea5e9') + ';border:3px solid #fff;box-shadow:0 3px 10px rgba(0,0,0,.35);font-size:17px;line-height:1">' + p.icone + '</span>' }) }).addTo(mapa)
        : L.circleMarker([p.latitude, p.longitude], { radius: p.urgente ? 11 : 9, color: '#fff', weight: 2, fillColor: cor, fillOpacity: 0.95 }).addTo(mapa);
      const alvo = p.tipo === 'horario' ? '/arenas/' + (p.arena_id || p.id) : rotas[p.tipo] + p.id;
      m.bindPopup('<b>' + p.rotulo.replace(/</g, '&lt;') + '</b><br>' + (p.distancia || '') + '<br><a href="' + alvo + '">Ver detalhes</a>');
      limites.push([p.latitude, p.longitude]);
    });
    if (limites.length > 1) mapa.fitBounds(limites, { padding: [40, 40], maxZoom: 15 });
    window._mapa = mapa;
  };

  // Seletor de local nos formulários: clique no mapa define latitude/longitude
  window.mapaSeletor = function (id, inicial, campoLat, campoLng, aoMudar) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!window.L) { el.innerHTML = '<p class="vazio">Mapa indisponível. Escolha uma arena ou use sua localização.</p>'; return; }
    const lat = $(campoLat), lng = $(campoLng);
    const temPonto = lat.value && lng.value;
    const mapa = L.map(el).setView([temPonto ? +lat.value : inicial.latitude, temPonto ? +lng.value : inicial.longitude], 14);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(mapa);
    let marcador = null;
    const por = (la, ln, mover) => {
      lat.value = la.toFixed(6); lng.value = ln.toFixed(6);
      if (marcador) marcador.setLatLng([la, ln]); else marcador = L.marker([la, ln]).addTo(mapa);
      if (mover) mapa.setView([la, ln], 15);
      if (aoMudar) aoMudar(la, ln);
    };
    if (temPonto) por(+lat.value, +lng.value, false);
    mapa.on('click', (e) => por(e.latlng.lat, e.latlng.lng, false));
    window._seletor = { por, mapa };
  };
  window.usarMinhaLocalizacao = function () {
    if (!navigator.geolocation) return toast('Seu navegador não informa a localização.');
    navigator.geolocation.getCurrentPosition((p) => window._seletor && window._seletor.por(p.coords.latitude, p.coords.longitude, true), () => toast('Não consegui pegar sua localização.'));
  };

  // ---- Convidar pelo @usuario / escolher moderadores (busca por prefixo do @, sem expor nome real)
  function escHtml(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function buscaUsuarios(el, aoEscolher, rotulo) {
    el.innerHTML = '<div style="display:flex;gap:6px"><input class="bu-campo" placeholder="@usuario (mín. 2 letras)" autocomplete="off" style="flex:1;border:1px solid var(--border);border-radius:10px;padding:8px"></div><div class="bu-lista" style="display:grid;gap:4px;margin-top:6px"></div>';
    const campo = el.querySelector('.bu-campo'), lista = el.querySelector('.bu-lista');
    let t;
    campo.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        if (campo.value.trim().length < 2) { lista.innerHTML = ''; return; }
        try {
          const r = await webApi('/atletas?q=' + encodeURIComponent(campo.value.trim()));
          lista.innerHTML = r.length ? r.map((u) => '<button type="button" class="btn soft pequeno" data-u="' + escHtml(u.usuario) + '" style="text-align:left">' + escHtml(u.arroba) + ' — ' + rotulo + '</button>').join('') : '<span class="suave">Ninguém encontrado com esse @.</span>';
        } catch (e) { lista.innerHTML = ''; }
      }, 250);
    });
    lista.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-u]');
      if (!b) return;
      try { await aoEscolher(b.dataset.u); campo.value = ''; lista.innerHTML = ''; } catch (e) { toast(e.message); }
    });
  }
  function iniciarWidgets() {
    document.querySelectorAll('.convidar').forEach((el) => {
      buscaUsuarios(el, async (u) => { await webApi('/escopos/' + el.dataset.escopo + '/' + el.dataset.id + '/convites', { metodo: 'POST', corpo: { usuario: u } }); toast('Convite enviado a @' + u + '.'); }, 'convidar');
    });
    document.querySelectorAll('.moderadores').forEach((el) => {
      const base = '/escopos/' + el.dataset.escopo + '/' + el.dataset.id + '/moderadores';
      const atuais = document.createElement('div');
      const busca = document.createElement('div');
      el.append(atuais, busca);
      const pintar = (lista) => {
        atuais.innerHTML = lista.length ? lista.map((m) => '<span class="mu-chip" style="margin:0 6px 6px 0">' + escHtml(m.arroba) + ' <button type="button" data-rem="' + escHtml(m.arroba.replace('@', '')) + '" style="border:0;background:none;cursor:pointer" aria-label="Remover">✕</button></span>').join('') : '<p class="suave" style="margin:0 0 6px">Ninguém além de você.</p>';
      };
      pintar(JSON.parse(el.dataset.atual || '[]'));
      atuais.addEventListener('click', async (ev) => {
        const b = ev.target.closest('[data-rem]');
        if (!b) return;
        try { await webApi(base + '/' + b.dataset.rem, { metodo: 'DELETE' }); pintar(await webApi(base)); toast('Moderador removido.'); } catch (e) { toast(e.message); }
      });
      buscaUsuarios(busca, async (u) => { await webApi(base, { metodo: 'POST', corpo: { usuario: u } }); pintar(await webApi(base)); toast('@' + u + ' agora cuida do mural.'); }, 'tornar moderador');
    });
  }
  window.addEventListener('load', iniciarWidgets);
})();
