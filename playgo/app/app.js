/* PlayGo — aplicativo (PWA). Consome a API em /api/v1 com token Bearer.
   Telas: Início, Explorar, Criar, Grupos, Perfil + atividade, grupo, campeonato, arena, gestão, notificações. */
(() => {
  'use strict';
  const API = '/api/v1';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
  const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* modo privado */ } };
  const ler = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };
  const NIVEIS = { iniciante: 'Iniciante', intermediario: 'Intermediário', avancado: 'Avançado' };
  const PERIODOS = { manha: 'Manhã', tarde: 'Tarde', noite: 'Noite', fim_de_semana: 'Fim de semana' };

  const estado = {
    token: ler('playgo_token'),
    usuario: null,
    pos: ler('playgo_pos'),
    mods: [],
    filtros: { raio: 10, quando: '', modalidades: '', com_vagas: false, q: '', modo: 'lista' },
    arena: ler('playgo_arena'),
    dia: null,
    temporizador: null,
  };

  // ------------------------------------------------------------ infraestrutura
  function toast(t) {
    const e = $('#toast');
    e.textContent = t; e.style.display = 'block';
    clearTimeout(e._t); e._t = setTimeout(() => (e.style.display = 'none'), 3000);
  }

  async function api(caminho, { metodo = 'GET', corpo, form } = {}) {
    const cab = estado.token ? { Authorization: 'Bearer ' + estado.token } : {};
    if (!form) cab['Content-Type'] = 'application/json';
    const r = await fetch(API + caminho, { method: metodo, headers: cab, body: form || (corpo !== undefined ? JSON.stringify(corpo) : undefined) });
    const dados = await r.json().catch(() => ({}));
    if (r.status === 401 && estado.token) { sair(false); throw new Error('Sua sessão expirou. Entre novamente.'); }
    if (r.status === 403 && dados.detail && dados.detail.codigo === 'pendencia') {
      // Falta escolher o @usuario ou aceitar a versão atual dos termos
      if (estado.usuario) estado.usuario.pendencia = dados.detail.pendencia;
      location.hash = dados.detail.pendencia === 'usuario' ? '#/completar' : '#/aceite';
      throw new Error(dados.detail.mensagem);
    }
    if (r.status === 402 && dados.detail && dados.detail.codigo === 'plano_necessario') {
      // O recurso faz parte de um plano pago: explica e leva a pessoa para Meu plano
      toast(dados.detail.mensagem); setTimeout(() => { location.hash = '#/planos'; }, 900);
      throw new Error(dados.detail.mensagem);
    }
    if (!r.ok) throw new Error(typeof dados.detail === 'string' ? dados.detail : (dados.detail && dados.detail.mensagem) || (Array.isArray(dados.detail) ? 'Confira os campos informados.' : 'Não foi possível concluir. Confira os dados.'));
    return dados;
  }
  const post = (c, corpo = {}) => api(c, { metodo: 'POST', corpo });
  const qs = (o) => Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== false).map(([k, v]) => k + '=' + encodeURIComponent(v === true ? 1 : v)).join('&');
  const comPos = (o = {}) => qs({ ...o, lat: estado.pos && estado.pos.lat, lng: estado.pos && estado.pos.lng });

  function fecharAbertura() {
    const a = document.getElementById('abertura');
    if (!a) return;
    a.classList.add('sai');
    setTimeout(() => { a.remove(); document.documentElement.classList.remove('abrindo'); }, 350);
  }

  setTimeout(fecharAbertura, 8000);  // rede ruim ou erro: a abertura nunca fica por cima do app

  function montar(html, depois, manterAbertura) {
    if (!manterAbertura) fecharAbertura();
    clearInterval(estado.temporizador); estado.temporizador = null;
    if (estado.mapa) { estado.mapa.remove(); estado.mapa = null; }
    $('#view').innerHTML = html;
    window.scrollTo(0, 0);
    if (depois) depois();
  }
  const carregando = () => montar('<p class="carregando">Carregando…</p>', null, true);  // a abertura (logo) só some quando a tela de verdade aparece
  const erroTela = (e) => montar(`<div class="vazio">${esc(e.message)}<br><br><a class="btn suave" href="#/">Voltar ao início</a></div>`);

  function folha(html) {
    const f = $('#folha');
    f.innerHTML = `<div>${html}</div>`; f.hidden = false;
    f.onclick = (ev) => { if (ev.target === f) fecharFolha(); };
  }
  const fecharFolha = () => { $('#folha').hidden = true; $('#folha').innerHTML = ''; };

  function dadosForm(form) {
    const o = {};
    new FormData(form).forEach((v, k) => { o[k] = k in o ? [].concat(o[k], v) : v; });
    return o;
  }
  const num = (v, f = Number) => (v === '' || v === undefined || v === null ? null : f(String(v).replace(',', '.')));

  function pegarLocalizacao(silencioso) {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) { if (!silencioso) toast('Seu aparelho não informa a localização.'); return reject(); }
      navigator.geolocation.getCurrentPosition(
        (p) => {
          estado.pos = { lat: p.coords.latitude, lng: p.coords.longitude };
          guardar('playgo_pos', estado.pos);
          if (estado.token) {
            // LGPD: guardar a localização no perfil exige consentimento. Sem ele, a posição vale só neste aparelho.
            const ok = (estado.usuario && estado.usuario.consent_localizacao) || confirm('O PlayGo pode guardar a localização do seu perfil para mostrar atividades perto de você. Você pode revogar quando quiser em Perfil > Privacidade.\n\nAutoriza? (Se recusar, usamos a localização só neste aparelho, sem guardar.)');
            if (ok) api('/me', { metodo: 'PATCH', corpo: { latitude: estado.pos.lat, longitude: estado.pos.lng, consent_localizacao: true } }).then((u) => { estado.usuario = u; }).catch(() => {});
          }
          resolve(estado.pos);
        },
        () => { if (!silencioso) toast('Não consegui pegar sua localização. Permita o acesso nas configurações.'); reject(); },
        { timeout: 10000 }
      );
    });
  }

  // ------------------------------------------------------------ componentes
  const modIcone = (m) => esc((m && m.icone) || '🏅');
  const botaoEntrar = (a, rotulo = 'Quero jogar') => {
    if (a.minha_participacao === 'confirmado') return '<span class="btn ok pequeno">✓ Confirmado</span>';
    if (a.minha_participacao === 'espera') return '<span class="btn suave pequeno">Lista de espera</span>';
    if (a.minha_participacao === 'pendente') return '<span class="btn suave pequeno">Aguardando</span>';
    return `<button class="btn roxo pequeno" data-acao="entrar" data-id="${a.id}">${esc(rotulo)}</button>`;
  };
  const onde = (a) => `📍 ${esc(a.arena_nome || a.local_nome)}${a.distancia ? ' • ' + esc(a.distancia) : ''}`;

  const cardUrg = (a) => `
    <a class="card urgente" href="#/atividade/${a.id}">
      <span class="tag ${esc(a.modalidade.cor)}">${modIcone(a.modalidade)} ${esc(a.modalidade.nome)}</span>
      <div class="precisa">${esc(a.rotulo_falta || 'RESTAM ' + a.vagas + ' VAGAS')}</div>
      <h3>${esc(a.nome)}</h3>
      <div class="meta">${esc(a.quando)}<br>${onde(a)}<br>${esc(a.nivel_nome)} • ${esc(a.valor_texto)}</div>
      <span class="contagem">⏱ ${esc(a.contagem)}</span>
      <div class="rodape"><strong data-conf>${a.confirmados}/${a.max_participantes} confirmados</strong>${botaoEntrar(a, 'EU VOU')}</div>
    </a>`;
  const cardAtv = (a, rotulo) => `
    <a class="card" href="#/atividade/${a.id}">
      <span class="tag ${esc(a.modalidade.cor)}">${modIcone(a.modalidade)} ${esc(a.modalidade.nome)}</span>
      <h3>${esc(a.nome)}</h3>
      <div class="meta">${esc(a.quando)}<br>${onde(a)}<br>${esc(a.nivel_nome)} • ${esc(a.valor_texto)}</div>
      <div class="rodape">${a.vagas > 0 ? `<strong data-conf>${a.vagas} vaga${a.vagas !== 1 ? 's' : ''}</strong>` : '<strong style="color:var(--muted)">Lotado</strong>'}${botaoEntrar(a, rotulo)}</div>
    </a>`;
  const cardCamp = (c, destaque) => `
    <a class="card ${destaque ? 'champ' : ''}" href="#/campeonato/${c.id}">
      <span class="tag ${destaque ? '' : esc(c.modalidade.cor)}">${esc(c.situacao)}</span>
      <h3>${esc(c.nome)}</h3>
      <div class="meta">${modIcone(c.modalidade)} ${c.max_equipes} ${c.atletas_por_equipe === 2 ? 'duplas' : 'equipes'} • ${esc(c.data_inicio_texto)}${c.premiacao ? '<br>Premiação: ' + esc(c.premiacao) : ''}<br>${esc(c.local_nome)}${c.distancia ? ' • ' + esc(c.distancia) : ''}</div>
      <div class="rodape"><strong>${esc(c.valor_inscricao_texto)}</strong><span class="btn ${destaque ? 'lima' : 'roxo'} pequeno">${c.inscricoes_abertas ? 'Inscrever' : 'Ver'}</span></div>
    </a>`;
  const cardGrupo = (g) => `
    <a class="card" href="#/grupo/${g.id}">
      <span class="tag ${esc(g.modalidade.cor)}">${modIcone(g.modalidade)} ${g.membros} membros</span>
      <h3>${esc(g.nome)}</h3>
      <div class="meta">${esc(g.modalidade.nome)}${g.cidade ? ' • ' + esc(g.cidade) : ''}${g.distancia ? ' • ' + esc(g.distancia) : ''}<br>${g.proxima ? 'Próxima: ' + esc(g.proxima.quando.toLowerCase()) : esc(g.local_habitual || '')}</div>
    </a>`;
  const cardHorario = (h) => `
    <a class="card" href="#/arena/${h.arena_id}">
      <span class="tag verde">🏟️ Quadra disponível</span>
      <h3>${modIcone(h.modalidade)} ${esc(h.modalidade ? h.modalidade.nome : 'Quadra')} • ${esc(h.quando)}</h3>
      <div class="meta">${esc(h.arena_nome)} — ${esc(h.quadra_nome)}${h.distancia ? ' • ' + esc(h.distancia) : ''}<br><b>${esc(h.valor_texto)}</b></div>
    </a>`;
  const cardArena = (a) => `
    <a class="card" href="#/arena/${a.id}">
      <span class="tag verde">🏟️ ${a.quadras} quadra${a.quadras !== 1 ? 's' : ''}</span>${a.premium ? ' <span class="tag laranja">👑 Parceira</span>' : ''}
      <h3>${esc(a.nome)}</h3>
      <div class="meta">${esc(a.endereco || a.cidade || '')}${a.distancia ? ' • ' + esc(a.distancia) : ''}${a.horarios_disponiveis ? `<br><b style="color:var(--green)">${a.horarios_disponiveis} horário(s) livre(s) hoje</b>` : ''}</div>
    </a>`;
  const cardVivo = (j) => `
    <a class="card" style="border:2px solid #ff3d81" href="#/campeonato/${j.campeonato_id}/ao-vivo">
      <span class="tag rosa">🔴 AO VIVO</span> ${j.video ? '<span class="tag rosa">🎥 com vídeo</span> ' : ''}<span class="tag">${esc(j.icone)} ${esc(j.campeonato)}</span>
      <h3 style="margin-top:8px">${esc(j.a)} <b style="color:#ff3d81">${j.placar_a}</b> × <b style="color:#ff3d81">${j.placar_b}</b> ${esc(j.b)}</h3>
      <div class="meta">${esc([j.rodada, j.local].filter(Boolean).join(' · '))}${j.sets_a != null ? ` · sets ${j.sets_a} × ${j.sets_b}` : ''}</div>
    </a>`;
  const cardLocal = (l) => `
    <a class="card" href="#/local/${l.id}">
      <span class="tag verde">${esc(l.icone)} ${esc(l.categoria_nome)}</span>
      <h3>${esc(l.nome)}</h3>
      <div class="meta">${esc(l.endereco || '')}${l.distancia ? ' • ' + esc(l.distancia) : ''}${l.horario_funcionamento ? '<br>🕒 ' + esc(l.horario_funcionamento.split('\n')[0]) : ''}${l.telefone ? '<br>📞 ' + esc(l.telefone) : ''}${l.avaliacao ? '<br><b style="color:#f5b301">★ ' + String(l.avaliacao.toFixed(1)).replace('.', ',') + '</b>' : ''}</div>
    </a>`;
  const lista = (itens, fn, vazio) => (itens && itens.length ? `<div class="lista">${itens.map(fn).join('')}</div>` : `<p class="vazio">${esc(vazio)}</p>`);
  const secao = (titulo, link) => `<div class="secao"><h2>${titulo}</h2>${link || ''}</div>`;
  const pessoa = (p, extra = '') => `<div class="pessoa"><span class="avatar">${esc(p.iniciais)}</span><b>${esc(p.arroba || p.nome)}</b>${extra}</div>`;

  function iniciarMapa(id, centro, pontos) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!window.L) { el.innerHTML = '<p class="vazio">Mapa indisponível sem conexão.</p>'; return; }
    if (estado.mapa && estado.mapa.getContainer && estado.mapa.getContainer() === el) estado.mapa.remove();
    const m = (estado.mapa = L.map(el).setView([centro.latitude, centro.longitude], 13));
    if (window.PlayGoMapa) PlayGoMapa.preparar(m); else L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(m);
    L.circleMarker([centro.latitude, centro.longitude], { radius: 8, color: '#fff', weight: 3, fillColor: '#6c4cff', fillOpacity: 1 }).addTo(m);
    const cores = { atividade: '#6c4cff', campeonato: '#ff8a00', arena: '#16c784', grupo: '#22c7f2', horario: '#16c784' };
    const rotas = { atividade: '#/atividade/', campeonato: '#/campeonato/', arena: '#/arena/', grupo: '#/grupo/', local: '#/local/', profissional: '#/profissional/', oferta: '#/oferta/' };
    const lim = [[centro.latitude, centro.longitude]];
    (pontos || []).forEach((p) => {
      const alvo = p.tipo === 'horario' ? '#/arena/' + p.arena_id : rotas[p.tipo] + p.id;
      (p.tipo === 'arena' && p.premium
        ? L.marker([p.latitude, p.longitude], { icon: L.divIcon({ className: 'pino-local', iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -18],
            html: '<span style="position:relative;display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#6c4cff;border:3px solid #f5b301;box-shadow:0 3px 10px rgba(0,0,0,.35);font-size:17px;line-height:1">🏟️<b style="position:absolute;top:-19px;left:50%;transform:translateX(-50%);font-size:17px;line-height:1;filter:drop-shadow(0 1px 1px rgba(0,0,0,.4))">👑</b></span>' }) })
        : (p.tipo === 'local' || p.tipo === 'profissional' || p.tipo === 'oferta') && p.icone
        ? L.marker([p.latitude, p.longitude], { icon: L.divIcon({ className: 'pino-local', iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -16],
            html: `<span style="display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:${p.cor || '#0ea5e9'};border:3px solid #fff;box-shadow:0 3px 10px rgba(0,0,0,.35);font-size:17px;line-height:1">${p.icone}</span>` }) })
        : L.circleMarker([p.latitude, p.longitude], { radius: p.urgente ? 11 : 9, color: '#fff', weight: 2, fillColor: p.urgente ? '#ff3d81' : cores[p.tipo], fillOpacity: 0.95 }))
        .addTo(m).bindPopup(`<b>${esc(p.rotulo)}</b><br>${esc(p.distancia || '')}<br><a href="${alvo}">Ver detalhes</a>`);
      lim.push([p.latitude, p.longitude]);
    });
    if (lim.length > 1) m.fitBounds(lim, { padding: [30, 30], maxZoom: 15 });
  }

  // ------------------------------------------------------------ acesso
  async function vEntrar(modo) {
    const cad = modo === 'cadastro';
    document.body.classList.add('sem-menu');
    $('#topo').hidden = true; $('#abas').hidden = true;
    const T = cad ? await fetch(API + '/termos').then((r) => r.json()).catch(() => null) : null;
    const prov = await fetch(API + '/auth/provedores').then((r) => r.json()).catch(() => ({}));
    const falhouGoogle = /erro=google/.test(location.hash);
    const google = prov.google ? `<a class="btn suave bloco" href="/auth/google/iniciar?destino=app" style="margin-bottom:10px">Continuar com Google</a><p class="suave" style="text-align:center;margin:0 0 12px">ou ${cad ? 'crie a conta' : 'use seu e-mail'}</p>` : '';
    const dec = (T && T.declaracoes) || { localizacao: 'Autorizo guardar a localização do meu perfil (opcional).' };
    montar(`<div class="entrada"><form class="cartao" id="f-entrar">
      <span class="marca"><img src="/static/marca/icone.svg" alt="" width="40" height="40" class="marca-ico">Play<span>Go</span></span>
      <h1>${cad ? 'Crie sua conta' : 'Entrar'}</h1>
      <p class="suave" style="margin:6px 0 16px">Encontre onde jogar. Encontre com quem jogar.</p>
      <div id="erro">${falhouGoogle ? '<p class="aviso erro">Não foi possível entrar com o Google. Tente de novo.</p>' : ''}</div>
      ${google}
      ${cad ? '<div class="campo"><label>Seu nome</label><input name="nome" required autocomplete="name"></div><div class="campo"><label>Nome de usuário</label><input name="usuario" required minlength="3" maxlength="20" pattern="[A-Za-z0-9_.@]{3,21}" placeholder="ex.: carlos.cg" autocomplete="username"><span class="suave">Aparece nas suas publicações; é como te encontram para convites.</span></div>' : ''}
      <div class="campo"><label>E-mail</label><input type="email" name="email" required autocomplete="${cad ? 'email' : 'username'}"></div>
      <div class="campo"><label>Senha</label><input type="password" name="senha" required minlength="${cad ? 8 : 1}" autocomplete="${cad ? 'new-password' : 'current-password'}"></div>
      ${cad ? `<label class="suave" style="display:flex;gap:8px;align-items:flex-start;margin-bottom:10px"><input type="checkbox" name="aceito_termos" required style="margin-top:3px"><span>Li e aceito os <a href="#/documento/termos">Termos de Uso</a> e a <a href="#/documento/politica">Política de Privacidade</a>, inclusive as regras de publicação e moderação.</span></label>
        <label class="suave" style="display:flex;gap:8px;align-items:flex-start;margin-bottom:14px"><input type="checkbox" name="consent_localizacao" style="margin-top:3px"><span>${esc(dec.localizacao)}</span></label>` : ''}
      <button class="btn roxo bloco">${cad ? 'Criar conta' : 'Entrar'}</button>
      <p class="suave" style="text-align:center;margin-top:14px">${cad ? 'Já tem conta? <a href="#/entrar">Entrar</a>' : 'Ainda não tem conta? <a href="#/cadastro">Criar conta</a>'}</p>
    </form></div>`, () => {
      $('#f-entrar').onsubmit = async (ev) => {
        ev.preventDefault();
        const d = dadosForm(ev.target);
        try {
          const corpo = cad ? { nome: d.nome, usuario: d.usuario, email: d.email, senha: d.senha, aceito_termos: !!d.aceito_termos, consent_localizacao: !!d.consent_localizacao } : d;
          const r = await post(cad ? '/auth/cadastro' : '/auth/entrar', corpo);
          estado.token = r.token; estado.usuario = r.usuario; guardar('playgo_token', r.token);
          location.hash = cad ? '#/perfil' : '#/';
        } catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
      };
    });
  }

  function sair(ir = true) {
    estado.token = null; estado.usuario = null; guardar('playgo_token', null);
    if (ir) location.hash = '#/entrar';
  }

  // ------------------------------------------------------------ início
  // Menu da conta (foto ao lado do sino): todas as opções do antigo menu do perfil, num menu suspenso
  function itensMenuEu(u) {
    return [
      ['#/perfil', 'user-round', 'Meu perfil'], ['#/campeonatos', 'trophy', 'Campeonatos'], ['#/aulas', 'graduation-cap', 'Aulas e Treinamentos'], ['#/ensino', 'clipboard-list', (u.permissoes && u.permissoes.perfil_prof) ? 'Minhas aulas' : 'Dar aulas'], ['#/planos', 'credit-card', 'Meu plano'], ['#/tutoriais', 'book-open', 'Tutoriais'],
      ['#/notificacoes', 'bell', 'Notificações'], ['#/comunidades', 'users-round', 'Comunidades'], ['#/convites', 'mail', 'Convites'],
      ...(pode('arena') ? [['#/gestao', 'building-2', 'Minha Arena']] : []),
      ...(u.equipe_moderacao ? [['#/moderacao', 'shield-check', 'Moderação']] : []),
      ...(u.admin ? [['#/admin', 'settings', 'Administração']] : []),
      ['#/privacidade', 'lock', 'Meus dados e privacidade'],
    ];
  }

  function atualizarMenuEu() {
    const u = estado.usuario;
    if (!u) return;
    $('#eu-avatar').innerHTML = u.foto_url ? `<img src="${esc(u.foto_url)}" alt="Menu da conta">` : esc(u.iniciais);
    $('#menu-pop').innerHTML = `<a class="m-cab" href="#/perfil"><span class="avatar grande">${u.foto_url ? `<img src="${esc(u.foto_url)}" alt="">` : esc(u.iniciais)}</span><span class="m-nome"><b>${esc(u.arroba)}</b><small>${esc(u.nome)}</small></span></a>
      ${itensMenuEu(u).map(([h, i, n]) => `<a class="m-item" role="menuitem" href="${h}"><i>${ic(i)}</i>${esc(n)}</a>`).join('')}`;
  }

  function fecharMenuEu() { $('#menu-pop').hidden = true; $('#eu-btn').setAttribute('aria-expanded', 'false'); }

  function ligarMenuEu() {
    $('#eu-btn').addEventListener('click', (ev) => {
      ev.stopPropagation();
      const abrir = $('#menu-pop').hidden;
      if (abrir) atualizarMenuEu();
      $('#menu-pop').hidden = !abrir;
      $('#eu-btn').setAttribute('aria-expanded', abrir ? 'true' : 'false');
    });
    document.addEventListener('click', (ev) => { if (!$('#menu-pop').hidden && !ev.target.closest('#eu-btn')) fecharMenuEu(); });  // clicou fora ou numa opção
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') fecharMenuEu(); });
  }

  async function vInicio() {
    carregando();
    try {
      const f = await api('/feed?' + comPos());
      const u = estado.usuario;
      montar(`
        <div class="hero"><h1>Olá, ${esc(u.nome.split(' ')[0])}! Bora completar o time?</h1><p>Jogos, grupos, campeonatos e arenas com vagas em tempo real.</p></div>
        ${u.aviso_plano ? `<a class="aviso erro" href="#/planos" style="display:block">⏳ ${esc(u.aviso_plano.texto)} <b>Ver planos</b></a>` : ''}
        ${!f.centro.informada ? '<p class="aviso alerta">📍 Não sabemos onde você está — mostrando Campo Grande. <button class="btn suave pequeno" data-acao="localizar">Usar minha localização</button></p>' : ''}
        <div class="livebar"><div><span class="pulse"></span><strong>${f.oportunidades_agora} oportunidade${f.oportunidades_agora !== 1 ? 's' : ''} agora</strong> em até 5 km</div><a class="btn lima pequeno" href="#/explorar?mapa=1">Mapa</a></div>
        ${(f.ao_vivo || []).length ? secao('🔴 Ao vivo agora') + lista(f.ao_vivo, cardVivo) : ''}
        ${f.urgentes.length ? secao('🔥 Precisam de jogadores agora') + `<div class="carrossel">${f.urgentes.map(cardUrg).join('')}</div>` : ''}
        ${secao('O que você quer praticar?', '<a href="#/explorar">Ver todos</a>')}
        <div class="esportes">${estado.mods.slice(0, 6).map((m) => `<a class="esporte" href="#/explorar?modalidades=${m.codigo}"><span>${esc(m.icone)}</span>${esc(m.nome)}</a>`).join('')}</div>
        ${secao('Rolando perto de você 🔥', '<a href="#/explorar">Ver no mapa</a>')}
        ${lista(f.perto, (a) => cardAtv(a), 'Nada marcado por perto. Que tal criar a primeira atividade?')}
        ${f.campeonatos.length ? secao('🏆 Campeonatos abertos', '<a href="#/campeonatos">Ver todos</a>') + `<div class="carrossel">${f.campeonatos.map((c, i) => cardCamp(c, i === 0)).join('')}</div>` : ''}
        ${f.grupos.length ? secao('👥 Seus grupos', '<a href="#/grupos">Ver todos</a>') + lista(f.grupos, cardGrupo) : ''}
        ${f.recomendados.length ? secao('🎯 Recomendados para você') + lista(f.recomendados, (a) => cardAtv(a)) : (!u.esportes.length ? secao('🎯 Recomendados para você') + '<p class="vazio">Conte quais esportes você pratica para receber indicações.<br><br><a class="btn suave" href="#/perfil">Completar perfil</a></p>' : '')}
        ${f.arenas.length ? secao('🏟️ Arenas próximas', '<a href="#/explorar">Ver todas</a>') + lista(f.arenas, cardArena) : ''}`);
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ explorar
  async function vExplorar(parametros) {
    const p = new URLSearchParams(parametros || '');
    const F = estado.filtros;
    if (p.get('modalidades') !== null) F.modalidades = p.get('modalidades');
    if (p.get('mapa')) F.modo = 'mapa';
    if (p.get('q') !== null) F.q = p.get('q');
    carregando();
    try {
      const r = await api('/explorar?' + comPos({ raio: F.raio, quando: F.quando, modalidades: F.modalidades, com_vagas: F.com_vagas, q: F.q }));
      const chip = (rot, ativo, acao, valor) => `<button class="chip ${ativo ? 'on' : ''}" data-acao="${acao}" data-v="${valor}">${rot}</button>`;
      montar(`
        <form class="busca" id="f-busca"><span>⌕</span><input name="q" value="${esc(F.q)}" placeholder="Futebol, vôlei, corrida, arena…" enterkeyhint="search"><button class="btn roxo pequeno">Buscar</button></form>
        <div class="chips">${[['', 'Qualquer dia'], ['hoje', 'Hoje'], ['amanha', 'Amanhã'], ['fim_de_semana', 'Fim de semana']].map(([v, r2]) => chip(r2, F.quando === v, 'f-quando', v)).join('')}</div>
        <div class="chips">${[2, 5, 10, 20, 0].map((v) => chip(v ? 'Até ' + v + ' km' : 'Toda a cidade', F.raio === v, 'f-raio', v)).join('')}${chip('🔥 Com vagas', F.com_vagas, 'f-vagas', '')}</div>
        <div class="chips">${chip('Todos', !F.modalidades, 'f-mod', '')}${estado.mods.map((m) => chip(esc(m.icone) + ' ' + esc(m.nome), F.modalidades === m.codigo, 'f-mod', m.codigo)).join('')}</div>
        <div class="alternar"><button class="${F.modo === 'lista' ? 'on' : ''}" data-acao="f-modo" data-v="lista">Lista</button><button class="${F.modo === 'mapa' ? 'on' : ''}" data-acao="f-modo" data-v="mapa">Mapa</button></div>
        ${!r.centro.informada ? '<p class="aviso alerta">📍 Informe sua localização para ver as distâncias reais. <button class="btn suave pequeno" data-acao="localizar">Usar minha localização</button></p>' : ''}
        ${F.modo === 'mapa' ? '<div class="mapa" id="mapa"></div><p class="suave" style="margin-top:8px">' + r.total + ' resultado(s) no mapa</p>' : ''}
        ${secao('Atividades (' + r.atividades.length + ')')}
        ${lista(r.atividades, (a) => (a.falta_gente ? cardUrg(a) : cardAtv(a)), 'Nenhuma atividade com esses filtros. Tente aumentar a distância.')}
        ${r.horarios.length ? secao('🏟️ Quadras disponíveis') + lista(r.horarios, cardHorario) : ''}
        ${r.campeonatos.length ? secao('🏆 Campeonatos') + lista(r.campeonatos, (c) => cardCamp(c)) : ''}
        ${r.grupos.length ? secao('👥 Grupos') + lista(r.grupos, cardGrupo) : ''}
        ${r.arenas.length ? secao('🏟️ Arenas') + lista(r.arenas, cardArena) : ''}
        ${(r.locais || []).length ? secao('📍 Locais e lojas') + lista(r.locais, cardLocal) : ''}`,
      () => {
        $('#f-busca').onsubmit = (ev) => { ev.preventDefault(); F.q = dadosForm(ev.target).q || ''; vExplorar(); };
        if (F.modo === 'mapa') iniciarMapa('mapa', r.centro, r.pontos);
      });
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ atividade
  function barra(conf, max) { return `<div class="barra"><i style="width:${Math.round((100 * conf) / max)}%"></i></div>`; }

  async function vAtividade(id) {
    carregando();
    try {
      const a = await api('/atividades/' + id);
      const minha = a.minha_participacao;
      const euOrg = a.organizador.id === estado.usuario.id;
      let acao = '';
      if (!a.encerrada) {
        if (!minha) acao = `<button class="btn roxo bloco" data-acao="entrar-det" data-id="${a.id}">${a.visibilidade === 'autorizados' ? 'Pedir para participar' : a.vagas === 0 ? 'Entrar na lista de espera' : 'EU VOU'}</button>`;
        else if (!euOrg) acao = `<button class="btn perigo bloco" data-acao="sair-atv" data-id="${a.id}">${minha === 'espera' ? 'Sair da lista de espera' : minha === 'pendente' ? 'Cancelar pedido' : 'Desistir'}</button>`;
      }
      const espera = Array.isArray(a.espera) ? a.espera.length : a.espera;
      montar(`
        <a href="javascript:history.back()" class="suave">← Voltar</a>
        <div style="margin-top:10px"><span class="tag ${esc(a.modalidade.cor)}">${modIcone(a.modalidade)} ${esc(a.modalidade.nome)}</span>
          ${a.status === 'cancelada' ? '<span class="tag rosa">Cancelada</span>' : a.encerrada ? '<span class="tag cinza">Encerrada</span>' : a.falta_gente ? `<span class="tag rosa">🔥 ${esc(a.rotulo_falta)}</span>` : ''}
          <span class="tag cinza">${{ publica: 'Pública', autorizados: 'Só autorizados', link: 'Só por link' }[a.visibilidade]}</span></div>
        <h1 style="margin:10px 0 4px">${esc(a.nome)}</h1>
        <p class="suave">Organizado por <b>${esc(a.organizador.arroba || a.organizador.nome)}</b>${a.grupo ? ` • grupo <a href="#/grupo/${a.grupo.id}">${esc(a.grupo.nome)}</a>` : ''}</p>
        ${minha === 'confirmado' ? '<p class="aviso ok">✓ Você está confirmado nesta atividade.</p>' : minha === 'espera' ? `<p class="aviso alerta">Você está na lista de espera${a.posicao_espera ? ' (posição ' + a.posicao_espera + ')' : ''}. Se alguém desistir, você entra e avisamos.</p>` : minha === 'pendente' ? '<p class="aviso alerta">Pedido enviado ao organizador. Aguardando aprovação.</p>' : ''}
        <div class="fatos">
          <div class="fato"><small>Quando</small><b>${esc(a.quando)}</b>${a.encerrada ? '' : `<div class="suave">${esc(a.contagem)}</div>`}</div>
          <div class="fato"><small>Onde</small><b>${esc(a.local_nome)}</b>${a.distancia ? `<div class="suave">a ${esc(a.distancia)}</div>` : ''}</div>
          <div class="fato"><small>Nível</small><b>${esc(a.nivel_nome)}</b></div>
          <div class="fato"><small>Valor</small><b>${esc(a.valor_texto)}</b></div>
        </div>
        <b>${a.confirmados}/${a.max_participantes} confirmados • ${a.vagas} vaga${a.vagas !== 1 ? 's' : ''}</b>${barra(a.confirmados, a.max_participantes)}
        ${acao}
        ${a.descricao ? `<div class="painel" style="margin-top:14px"><h3>Sobre</h3><p style="margin-top:6px">${esc(a.descricao)}</p></div>` : ''}
        ${a.regras ? `<div class="painel"><h3>Regras</h3><p style="margin-top:6px">${esc(a.regras)}</p></div>` : ''}
        ${a.percurso ? `<div class="painel"><h3>Percurso</h3><p style="margin-top:6px">${esc(a.percurso)}</p></div>` : ''}
        <div class="mapa pequeno" id="mapa" style="margin-top:14px"></div>
        <p style="margin-top:8px"><a href="https://www.openstreetmap.org/directions?to=${a.latitude}%2C${a.longitude}" target="_blank" rel="noopener">Como chegar →</a></p>
        ${a.pendentes.length ? `<div class="painel" style="margin-top:14px"><h3>Aguardando aprovação (${a.pendentes.length})</h3>${a.pendentes.map((p) => pessoa(p, `<span class="acoes"><button class="btn roxo pequeno" data-acao="decidir" data-id="${a.id}" data-p="${p.participacao_id}" data-v="aprovar">Aprovar</button><button class="btn perigo pequeno" data-acao="decidir" data-id="${a.id}" data-p="${p.participacao_id}" data-v="recusar">Recusar</button></span>`)).join('')}</div>` : ''}
        <div class="painel" style="margin-top:14px"><h3>Confirmados (${a.participantes.length})</h3>
          ${a.participantes.map((p) => pessoa(p, p.organizador ? '<span class="tag">Organizador</span>' : p.nivel ? `<span class="suave">${NIVEIS[p.nivel]}</span>` : '')).join('')}
          ${espera ? `<p class="suave" style="margin-top:10px">${espera} na lista de espera</p>` : ''}</div>
        ${a.sou_organizador && !a.encerrada ? `<div class="painel" style="margin-top:14px"><h3>Organização</h3><div style="display:grid;gap:8px;margin-top:10px">
          ${a.falta_gente ? `<button class="btn suave bloco" data-acao="falta" data-id="${a.id}" data-v="0">Tirar destaque “falta gente”</button>` : a.vagas > 0 ? `<button class="btn bloco" style="background:var(--pink);color:#fff" data-acao="falta" data-id="${a.id}" data-v="1">🔥 Falta gente</button>` : ''}
          <button class="btn perigo bloco" data-acao="cancelar-atv" data-id="${a.id}">Cancelar atividade</button></div></div>` : ''}
        ${a.sou_organizador && !a.encerrada ? organizacaoExtra('atividade', a) : ''}
        ${muralBloco('Mural da atividade', a.mural_acesso, 'Só quem participa vê e publica aqui. O local das publicações é sempre o da atividade.' + (a.visibilidade !== 'publica' ? ' Atividade fechada: as publicações ficam só aqui.' : ''))}`,
      () => { iniciarMapa('mapa', { latitude: a.latitude, longitude: a.longitude }, [{ tipo: 'atividade', id: a.id, latitude: a.latitude, longitude: a.longitude, rotulo: a.local_nome, distancia: a.distancia, urgente: a.falta_gente }]); if (a.mural_acesso) montarMural('mural', { escopo: 'atividade', escopoId: a.id }); });
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ criar
  const pode = (r) => !!(estado.usuario && estado.usuario.permissoes && estado.usuario.permissoes[r]);

  function vCriar() {
    const item = (href, ic, t, d) => `<a class="item-menu" href="${href}"><i>${ic}</i><div>${t}<div class="suave">${d}</div></div></a>`;
    montar(`<h1>Criar</h1><p class="suave" style="margin:4px 0 14px">O que você quer organizar?</p><div class="painel">
      ${pode('atividade') ? item('#/nova-atividade?tipo=jogo', '⚽', 'Criar jogo', 'Futebol, vôlei, beach tennis… em quadra ou arena') : ''}
      ${pode('atividade') ? item('#/nova-atividade?tipo=atividade', '🏃', 'Criar atividade', 'Corrida, ciclismo, caminhada, trilha… com ponto de encontro') : ''}
      ${pode('grupo') ? item('#/novo-grupo', '👥', 'Criar grupo', 'Grupo do seu esporte, com aceite e remoção de participantes') : ''}
      ${pode('campeonato') ? item('#/novo-campeonato', '🏆', 'Criar campeonato', 'Torneios com equipes e inscrição') : ''}
      ${!pode('atividade') || !pode('campeonato') ? item('#/planos', '💳', 'Quer organizar?', 'Veja os planos que liberam atividades, campeonatos e arenas') : ''}
    </div>`);
  }

  async function vNovaAtividade(parametros) {
    const p = new URLSearchParams(parametros || '');
    const tipo = p.get('tipo') || 'jogo';
    carregando();
    try {
      const [arenas, grupos] = await Promise.all([api('/explorar?' + comPos({ raio: 30, tipos: 'arena' })), api('/grupos?' + comPos())]);
      const mods = estado.mods.filter((m) => (tipo === 'jogo' ? m.usa_quadra : !m.usa_quadra));
      const sel = p.get('modalidade_id');
      const hoje = new Date(); const ymd = hoje.getFullYear() + '-' + String(hoje.getMonth() + 1).padStart(2, '0') + '-' + String(hoje.getDate()).padStart(2, '0');
      montar(`<a href="#/criar" class="suave">← Voltar</a><h1 style="margin:8px 0 14px">${tipo === 'jogo' ? 'Criar jogo' : 'Criar atividade'}</h1>
        <form id="f-atv" class="painel"><div id="erro"></div>
        <div class="campo"><label>Modalidade</label><select name="modalidade_id" id="sel-mod">${mods.map((m) => `<option value="${m.id}" data-vagas="${m.vagas_padrao}" ${String(m.id) === sel ? 'selected' : ''}>${esc(m.icone)} ${esc(m.nome)}</option>`).join('')}</select></div>
        <div class="campo"><label>Nome</label><input name="nome" required maxlength="150" placeholder="Ex.: ${tipo === 'jogo' ? 'Futebol de quarta' : 'Corrida no parque'}"></div>
        <div class="duas"><div class="campo"><label>Data</label><input type="date" name="data" min="${ymd}" required></div><div class="campo"><label>Horário</label><input type="time" name="hora" required></div></div>
        ${tipo === 'jogo' ? `<div class="campo"><label>Arena <span class="suave">(opcional)</span></label><select name="arena_id" id="sel-arena"><option value="">Sem arena — usar minha localização</option>${arenas.arenas.map((a) => `<option value="${a.id}">${esc(a.nome)}${a.distancia ? ' — ' + esc(a.distancia) : ''}</option>`).join('')}</select></div>` : ''}
        <div class="campo"><label>${tipo === 'jogo' ? 'Local' : 'Ponto de encontro'}</label><input name="local_nome" placeholder="${tipo === 'jogo' ? 'Arena, quadra ou endereço' : 'Ex.: Portão 2 do parque'}"></div>
        <div class="campo"><button type="button" class="btn suave bloco" data-acao="marcar-local" id="btn-local">📍 ${estado.pos ? 'Usar minha localização atual' : 'Usar minha localização'}</button><span class="suave" id="info-local">${estado.pos ? 'Usaremos o último ponto conhecido.' : 'Necessário para marcar onde será.'}</span></div>
        ${tipo === 'atividade' ? '<div class="campo"><label>Percurso <span class="suave">(opcional)</span></label><input name="percurso" placeholder="Ex.: 2 voltas, 7 km"></div>' : ''}
        <div class="duas"><div class="campo"><label>Participantes</label><input type="number" name="max_participantes" min="2" value="12" id="in-max" required></div>
          <div class="campo"><label>Valor (R$)</label><input name="valor" inputmode="decimal" placeholder="0,00"></div></div>
        <div class="duas"><div class="campo"><label>Nível</label><select name="nivel"><option value="todos">Todos</option>${Object.entries(NIVEIS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
          <div class="campo"><label>Quem participa</label><select name="visibilidade"><option value="publica">Pública</option><option value="autorizados">Só autorizados</option><option value="link">Só por link</option></select></div></div>
        <p class="suave" style="margin:-4px 0 12px">Pública: aparece na busca, entrada automática. Só autorizados: aparece, mas você aprova cada pessoa (ou convida pelo @usuario). Só por link: não aparece em lugar nenhum — entra quem receber o link.</p>
        ${grupos.meus.length ? `<div class="campo"><label>Grupo <span class="suave">(avisa os membros)</span></label><select name="grupo_id"><option value="">Sem grupo</option>${grupos.meus.map((g) => `<option value="${g.id}">${esc(g.nome)}</option>`).join('')}</select></div>` : ''}
        <div class="campo"><label>Descrição</label><textarea name="descricao"></textarea></div>
        <div class="campo"><label class="marcas" style="border:0;padding:0"><span style="display:flex;gap:8px;align-items:center;font-weight:700"><input type="checkbox" name="falta_gente" value="1" style="width:auto"> 🔥 Falta gente — avisar atletas por perto</span></label></div>
        <button class="btn roxo bloco">Publicar</button></form>`,
      () => {
        const modSel = $('#sel-mod'); const max = $('#in-max');
        const ajusta = () => { if (!max.dataset.mexido) max.value = modSel.selectedOptions[0].dataset.vagas; };
        max.oninput = () => (max.dataset.mexido = 1); modSel.onchange = ajusta; ajusta();
        $('#f-atv').onsubmit = async (ev) => {
          ev.preventDefault();
          const d = dadosForm(ev.target);
          if (!d.arena_id && !estado.pos) { $('#erro').innerHTML = '<p class="aviso erro">Toque em “Usar minha localização” para marcar onde será, ou escolha uma arena.</p>'; return; }
          const corpo = {
            modalidade_id: Number(d.modalidade_id), nome: d.nome, inicio: d.data + 'T' + d.hora, max_participantes: Number(d.max_participantes),
            local_nome: d.local_nome || '', arena_id: num(d.arena_id), grupo_id: num(d.grupo_id), nivel: d.nivel, valor: num(d.valor) || 0,
            descricao: d.descricao || null, percurso: d.percurso || null, visibilidade: d.visibilidade || 'publica', falta_gente: !!d.falta_gente,
          };
          if (!d.arena_id) { corpo.latitude = estado.pos.lat; corpo.longitude = estado.pos.lng; }
          try { const a = await post('/atividades', corpo); toast('Atividade publicada! 🎉'); location.hash = '#/atividade/' + a.id; }
          catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; window.scrollTo(0, 0); }
        };
      });
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ grupos
  async function vGrupos() {
    carregando();
    try {
      const g = await api('/grupos?' + comPos());
      montar(`${segGrupos('grupos')}<div class="secao" style="margin-top:0"><h1>Meus grupos</h1>${pode('grupo') ? '<a class="btn roxo pequeno" href="#/novo-grupo">+ Criar</a>' : ''}</div>
        ${lista(g.meus, cardGrupo, 'Você ainda não participa de nenhum grupo.')}
        ${g.descobrir.length ? secao('Descubra grupos') + lista(g.descobrir, (x) => cardGrupo(x)) : ''}`);
    } catch (e) { erroTela(e); }
  }

  async function vGrupo(id, aba = 'jogos') {
    carregando();
    try {
      const g = await api('/grupos/' + id);
      let corpo = '';
      if (aba === 'jogos') corpo = secao('Próximos jogos') + lista(g.agenda, (a) => cardAtv(a), 'Nenhuma atividade marcada.') + (g.historico.length ? secao('Histórico') + `<div class="painel">${g.historico.map((a) => `<div class="pessoa"><a href="#/atividade/${a.id}">${esc(a.nome)}</a><span class="suave" style="margin-left:auto">${esc(a.quando)}</span></div>`).join('')}</div>` : '');
      if (aba === 'membros') corpo = (g.sou_admin && g.pedidos && g.pedidos.length ? `<div class="painel" style="border:1px solid #ffd9a1;background:#fffaf0"><h3>Pedidos para entrar (${g.pedidos.length})</h3>${g.pedidos.map((m) => pessoa(m, `<span class="acoes"><button class="btn roxo pequeno" data-acao="aceitar-pedido" data-id="${g.id}" data-v="${m.usuario_id}">Aceitar</button><button class="btn perigo pequeno" data-acao="recusar-pedido" data-id="${g.id}" data-v="${m.usuario_id}">Recusar</button></span>`)).join('')}</div>` : '') +
        `<div class="painel">${g.membros_lista.map((m) => pessoa(m, m.papel === 'admin' ? '<span class="tag">Admin</span>' : g.sou_admin ? `<span class="acoes"><button class="btn suave pequeno" data-acao="promover" data-id="${g.id}" data-v="${m.usuario_id}">Tornar admin</button><button class="btn perigo pequeno" data-acao="remover-membro" data-id="${g.id}" data-v="${m.usuario_id}">Remover</button></span>` : '')).join('')}</div>`;
      if (aba === 'conversa') corpo = g.sou_membro ? '<div class="chat" id="chat"></div><form class="enviar" id="f-msg"><input name="texto" placeholder="Escreva uma mensagem…" required autocomplete="off" maxlength="2000">' + (g.sou_admin ? '<label class="suave" style="display:flex;align-items:center;gap:4px"><input type="checkbox" name="aviso" value="1">aviso</label>' : '') + '<button class="btn roxo pequeno">Enviar</button></form>' : '<p class="vazio">Entre no grupo para ver a conversa.</p>';
      montar(`<a href="#/grupos" class="suave">← Grupos</a>
        <span class="tag ${esc(g.modalidade.cor)}" style="margin-top:10px">${modIcone(g.modalidade)} ${esc(g.modalidade.nome)}</span>
        <h1 style="margin:8px 0 4px">${esc(g.nome)}</h1>
        <p class="suave">${g.membros} membros${g.cidade ? ' • ' + esc(g.cidade) : ''}${g.local_habitual ? ' • 📍 ' + esc(g.local_habitual) : ''}</p>
        ${g.descricao ? `<p style="margin-top:8px">${esc(g.descricao)}</p>` : ''}
        ${g.avisos.map((a) => `<p class="aviso alerta">📌 <b>${esc(a.autor)}:</b> ${esc(a.texto)}</p>`).join('')}
        <div style="display:flex;gap:8px;margin-top:12px">${g.sou_membro ? `${pode('atividade') ? `<a class="btn roxo pequeno" href="#/nova-atividade?tipo=${g.modalidade.usa_quadra ? 'jogo' : 'atividade'}&modalidade_id=${g.modalidade.id}">+ Atividade</a>` : ''}<button class="btn perigo pequeno" data-acao="sair-grupo" data-id="${g.id}">Sair</button>` : `${g.pedido_pendente ? `<span class="tag">⏳ Pedido enviado</span><button class="btn perigo pequeno" data-acao="cancelar-pedido-grupo" data-id="${g.id}">Cancelar pedido</button>` : `<button class="btn roxo bloco" data-acao="entrar-grupo" data-id="${g.id}">${g.entrada === 'aprovacao' ? 'Pedir para entrar' : 'Entrar no grupo'}</button>`}`}</div>
        <div class="abas-int">${[['jogos', 'Jogos'], ['conversa', 'Conversa'], ['membros', 'Membros']].map(([k, n]) => `<button class="${aba === k ? 'on' : ''}" data-acao="aba-grupo" data-id="${g.id}" data-v="${k}">${n}</button>`).join('')}</div>${corpo}`,
      () => { if (aba === 'conversa' && g.sou_membro) conversa(g); });
    } catch (e) { erroTela(e); }
  }

  function conversa(g) {
    let ultimo = 0;
    const chat = $('#chat');
    const pintar = (msgs) => {
      msgs.forEach((m) => {
        ultimo = Math.max(ultimo, m.id);
        chat.insertAdjacentHTML('beforeend', `<div class="msg ${m.aviso ? 'aviso' : m.autor_id === estado.usuario.id ? 'minha' : ''}"><small>${esc(m.autor)} • ${esc(m.hora)}</small>${esc(m.texto)}</div>`);
      });
      if (msgs.length) window.scrollTo(0, document.body.scrollHeight);
    };
    const buscar = async () => { try { pintar(await api(`/grupos/${g.id}/mensagens?depois_de=${ultimo}`)); } catch (e) { /* tenta de novo */ } };
    buscar(); estado.temporizador = setInterval(buscar, 5000);
    $('#f-msg').onsubmit = async (ev) => {
      ev.preventDefault();
      const d = dadosForm(ev.target);
      try { await post(`/grupos/${g.id}/mensagens`, { texto: d.texto, aviso: !!d.aviso }); ev.target.reset(); buscar(); } catch (e) { toast(e.message); }
    };
  }

  async function vNovoGrupo() {
    montar(`<a href="#/grupos" class="suave">← Grupos</a><h1 style="margin:8px 0 14px">Criar grupo</h1>
      <form id="f-grupo" class="painel"><div id="erro"></div>
      <div class="campo"><label>Nome do grupo</label><input name="nome" required placeholder="Ex.: Corre CG"></div>
      <div class="campo"><label>Modalidade</label><select name="modalidade_id">${estado.mods.map((m) => `<option value="${m.id}">${esc(m.icone)} ${esc(m.nome)}</option>`).join('')}</select></div>
      <div class="campo"><label>Cidade</label><input name="cidade" value="${esc(estado.usuario.cidade || '')}"></div>
      <div class="campo"><label>Local habitual</label><input name="local_habitual" placeholder="Ex.: Parque das Nações"></div>
      <div class="campo"><label>Descrição</label><textarea name="descricao"></textarea></div>
      <button class="btn roxo bloco">Criar grupo</button></form>`, () => {
      $('#f-grupo').onsubmit = async (ev) => {
        ev.preventDefault(); const d = dadosForm(ev.target);
        try {
          const g = await post('/grupos', { nome: d.nome, modalidade_id: Number(d.modalidade_id), cidade: d.cidade || null, local_habitual: d.local_habitual || null, descricao: d.descricao || null, latitude: estado.pos && estado.pos.lat, longitude: estado.pos && estado.pos.lng });
          toast('Grupo criado!'); location.hash = '#/grupo/' + g.id;
        } catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
      };
    });
  }

  // ------------------------------------------------------------ campeonatos
  async function vCampeonatos() {
    carregando();
    try {
      const r = await api('/explorar?' + comPos({ raio: 0, tipos: 'campeonato' }));
      const encerrados = pode('campeonato') ? `<div class="secao"><h2>🏁 Encerrados</h2></div>
        <form class="filtros-enc" id="f-enc"><input name="q" placeholder="Buscar por nome"><div style="display:flex;gap:6px"><input type="date" name="de" aria-label="De"><input type="date" name="ate" aria-label="Até"></div>
        ${estado.usuario.admin ? '<input name="organizador" placeholder="Organizador (@usuario)">' : ''}<button class="btn roxo pequeno">Filtrar</button></form><div id="lista-enc"><p class="suave">Carregando…</p></div>` : '';
      montar(`<div class="secao" style="margin-top:0"><h1>Campeonatos 🏆</h1>${pode('campeonato') ? '<a class="btn roxo pequeno" href="#/novo-campeonato">+ Criar</a>' : ''}</div>${lista(r.campeonatos, (c) => cardCamp(c), 'Nenhum campeonato por perto no momento.')}${encerrados}`, () => {
        const f = $('#f-enc');
        if (!f) return;
        const carregar = async () => {
          const d = Object.fromEntries(new FormData(f));
          const qs = new URLSearchParams(Object.entries(d).filter(([, v]) => v)).toString();
          try {
            const e = await api('/campeonatos/encerrados' + (qs ? '?' + qs : ''));
            $('#lista-enc').innerHTML = e.itens.length ? e.itens.map((x) => `<a class="card" href="#/campeonato/${x.id}"><span class="tag">${esc(x.icone)} ${esc(x.modalidade)}</span><h3>${esc(x.nome)}</h3><div class="meta">Encerrou em ${esc(x.data_fim.slice(8))}/${esc(x.data_fim.slice(5, 7))}/${esc(x.data_fim.slice(0, 4))} • ${x.equipes} equipe(s)${e.todos ? ' • ' + esc(x.organizador) : ''}<br>${esc(x.arena || x.local)}</div></a>`).join('') : '<p class="vazio">Nenhum campeonato encerrado com esses filtros.</p>';
          } catch (err) { $('#lista-enc').innerHTML = `<p class="vazio">${esc(err.message)}</p>`; }
        };
        f.addEventListener('submit', (ev) => { ev.preventDefault(); carregar(); });
        carregar();
      });
    } catch (e) { erroTela(e); }
  }

  async function vCampeonato(id) {
    carregando();
    try {
      const k = await api('/campeonatos/' + id);
      const ms = k.minha_situacao;
      montar(`<a href="#/campeonatos" class="suave">← Campeonatos</a>
        <div style="margin-top:10px"><span class="tag ${esc(k.modalidade.cor)}">${modIcone(k.modalidade)} ${esc(k.modalidade.nome)}</span> <span class="tag ${k.inscricoes_abertas ? 'verde' : 'cinza'}">${esc(k.situacao)}</span></div>
        <h1 style="margin:10px 0 4px">🏆 ${esc(k.nome)}</h1><p class="suave">Organizado por <b>${esc(k.organizador.arroba || k.organizador.nome)}</b>${k.categoria ? ' • ' + esc(k.categoria) : ''}</p>
        ${(ms.convites || []).map((cv) => `<p class="aviso alerta">Você foi convidado para a equipe <b>${esc(cv.equipe)}</b>.<br><br><button class="btn roxo pequeno" data-acao="convite" data-id="${cv.equipe_id}" data-v="1">Aceitar</button> <button class="btn perigo pequeno" data-acao="convite" data-id="${cv.equipe_id}" data-v="0">Recusar</button></p>`).join('')}
        <div class="fatos">
          <div class="fato"><small>Início</small><b>${esc(k.data_inicio_texto)}</b></div><div class="fato"><small>Inscrições até</small><b>${esc(k.inscricao_ate_texto)}</b></div>
          <div class="fato"><small>Local</small><b>${esc(k.local_nome)}</b>${k.distancia ? `<div class="suave">a ${esc(k.distancia)}</div>` : ''}</div><div class="fato"><small>Inscrição</small><b>${esc(k.valor_inscricao_texto)}</b></div>
          <div class="fato"><small>Equipe</small><b>${k.atletas_por_equipe} atletas</b></div>${k.premiacao ? `<div class="fato"><small>Premiação</small><b>${esc(k.premiacao)}</b></div>` : ''}</div>
        ${abasCamp(k, 'geral')}
        <b>${k.equipes}/${k.max_equipes} equipes • ${k.vagas} vaga${k.vagas !== 1 ? 's' : ''}</b>${barra(k.equipes, k.max_equipes)}
        ${k.inscricoes_abertas ? `<form id="f-eq" class="painel" style="margin-top:12px"><h3>${(ms.equipes || []).length ? 'Inscrever outra equipe' : 'Inscrever minha equipe'}</h3>${(ms.equipes || []).length ? `<p class="suave">Você está em: ${ms.equipes.map((e) => esc(e.nome)).join(', ')}.</p>` : ''}<div class="campo" style="margin-top:10px"><input name="nome" required maxlength="100" placeholder="Nome da equipe"></div><button class="btn roxo bloco">Inscrever equipe</button><p class="suave" style="margin-top:8px">Você será o capitão e convida os jogadores depois. O organizador confirma a inscrição.</p></form>` : ''}
        ${k.descricao ? `<div class="painel" style="margin-top:12px"><h3>Sobre</h3><p style="margin-top:6px">${esc(k.descricao)}</p></div>` : ''}
        ${k.regulamento_pdf ? `<div class="painel"><h3>Regulamento</h3><a class="btn roxo bloco" style="margin-top:8px" href="${esc(k.regulamento_pdf.url)}" target="_blank" rel="noopener">📄 Abrir o regulamento em PDF</a><p class="suave" style="margin-top:6px">${esc(k.regulamento_pdf.nome)}</p></div>` : ''}
        ${k.regulamento ? `<div class="painel"><h3>${k.regulamento_pdf ? 'Resumo do regulamento' : 'Regulamento'}</h3><p style="margin-top:6px;white-space:pre-line">${esc(k.regulamento)}</p></div>` : ''}
        <div class="painel" style="margin-top:12px"><h3>Equipes (${k.equipes_lista.length})</h3>
        ${k.equipes_lista.length ? k.equipes_lista.map((e) => `<div style="padding:10px 0;border-top:1px solid var(--border)"><div style="display:flex;justify-content:space-between;align-items:center"><b>${esc(e.nome)}</b><span class="tag ${e.status === 'confirmada' ? 'verde' : 'laranja'}">${e.status === 'confirmada' ? 'Confirmada' : 'Pendente'}</span></div>
          <div class="suave">Capitão: ${esc(e.capitao)} • ${e.membros.filter((m) => m.status === 'confirmado').length}/${k.atletas_por_equipe}${k.cadastro_elenco && e.componentes ? ' • ' + e.componentes + ' no elenco' : ''}</div>
          ${e.elenco_acesso ? `<div class="elenco"><details><summary>👥 Elenco (nome e RG), técnico e capitão</summary><div class="elenco-corpo" data-equipe="${e.id}"></div></details></div>` : ''}
          ${e.membros.map((m) => `<div class="suave">• ${esc(m.arroba || m.nome)}${m.status === 'convidado' ? ' (convidado)' : ''}</div>`).join('')}
          ${e.capitao_id === estado.usuario.id ? `<div style="margin-top:8px;display:flex;gap:6px"><button class="btn suave pequeno" data-acao="convidar" data-id="${e.id}">+ Convidar atleta</button><button class="btn perigo pequeno" data-acao="cancelar-eq" data-id="${e.id}">Cancelar</button></div>` : ''}
          ${k.sou_gestor && e.status === 'pendente' ? `<div style="margin-top:8px;display:flex;gap:6px"><button class="btn roxo pequeno" data-acao="decidir-eq" data-id="${e.id}" data-v="1">Confirmar</button><button class="btn perigo pequeno" data-acao="decidir-eq" data-id="${e.id}" data-v="0">Recusar</button></div>` : ''}</div>`).join('') : '<p class="suave">Nenhuma equipe inscrita ainda.</p>'}</div>
        ${k.sou_gestor ? `<div class="painel" style="margin-top:12px"><h3>Gestão</h3><div class="campo" style="margin-top:10px"><select id="sel-status">${[['aberto', 'Inscrições abertas'], ['em_andamento', 'Em andamento'], ['encerrado', 'Encerrado'], ['cancelado', 'Cancelado']].map(([v, n]) => `<option value="${v}" ${k.status === v ? 'selected' : ''}>${n}</option>`).join('')}</select></div><button class="btn suave bloco" data-acao="status-camp" data-id="${k.id}">Atualizar situação</button><a class="btn suave bloco" style="margin-top:8px" href="#/campeonato/${k.id}/editar">✏️ Editar campeonato</a></div>` : ''}
        ${k.sou_gestor ? organizacaoExtra('campeonato', k) : ''}
        ${muralBloco('Mural do campeonato', k.mural_acesso, 'Para equipes inscritas e organização. O local das publicações é sempre o do campeonato.')}`,
      () => {
        if (k.mural_acesso) montarMural('mural', { escopo: 'campeonato', escopoId: k.id });
        $$('.elenco-corpo').forEach((el) => {
          el.closest('details').addEventListener('toggle', function once(ev) {
            if (!ev.target.open) return;
            ev.target.removeEventListener('toggle', once);
            Elenco.montar(el, { get: (c) => api(c), post, put: (c, b) => api(c, { metodo: 'PUT', corpo: b }), apagar: (c) => api(c, { metodo: 'DELETE' }), equipeId: Number(el.dataset.equipe), toast });
          });
        });
        const f = $('#f-eq');
        if (f) f.onsubmit = async (ev) => { ev.preventDefault(); try { await post(`/campeonatos/${k.id}/equipes`, { nome: dadosForm(ev.target).nome }); toast('Equipe inscrita!'); vCampeonato(k.id); } catch (e) { toast(e.message); } };
      });
    } catch (e) { erroTela(e); }
  }

  // Abas do campeonato: visão geral, chaves, ao vivo e (organização) sorteio
  function abasCamp(k, ativa) {
    const a = (id, href, nome) => `<a href="${href}" class="${ativa === id ? 'ativa' : ''}">${nome}</a>`;
    return `<nav class="ch-abas">${a('geral', '#/campeonato/' + k.id, 'Visão geral')}${a('chaves', '#/campeonato/' + k.id + '/chaves', '🏆 Chaves')}${a('ao_vivo', '#/campeonato/' + k.id + '/ao-vivo', '🔴 Ao vivo')}${a('telao', '#/campeonato/' + k.id + '/telao', '📺 Telão')}${k.sou_gestor ? a('sorteio', '#/campeonato/' + k.id + '/sorteio', '🎲 Sorteio') + a('editar', '#/campeonato/' + k.id + '/editar', '✏️ Editar') : ''}</nav>`;
  }

  // Sub-páginas do campeonato (chaves, ao vivo, jogo e sorteio): a tela é o chaves.js, igual no site
  async function vCampSub(id, modo, jogo) {
    carregando();
    try {
      const k = await api('/campeonatos/' + id);
      if (modo === 'sorteio' && !k.sou_gestor) { location.hash = '#/campeonato/' + id + '/chaves'; return; }
      montar(`<a href="#/campeonato/${id}" class="suave">← ${esc(k.nome)}</a><h1 style="margin:8px 0 0">🏆 ${esc(k.nome)}</h1>${abasCamp(k, modo === 'jogo' ? 'ao_vivo' : modo === 'acompanhar' ? 'telao' : modo)}
        ${modo === 'jogo' ? `<p><a href="#/campeonato/${id}/chaves">← Voltar às chaves</a></p>` : ''}<div id="ch-conteudo"></div>`, () => {
        const el = $('#ch-conteudo');
        const get = (c) => api(c);
        const href = (j) => '#/campeonato/' + id + '/jogo/' + j.id;
        if (modo === 'chaves') Chaves.montarChaves(el, { get, href, id });
        else if (modo === 'ao-vivo' || modo === 'ao_vivo') Chaves.montarAoVivo(el, { get, post, href, id, toast });
        else if (modo === 'jogo') Chaves.montarJogo(el, { get, post, id, jogo, toast });
        else if (modo === 'acompanhar' || (modo === 'telao' && !k.pode_telao)) Telao.montarAcompanhar(el, { get, id });
        else if (modo === 'telao') Telao.montarControle(el, { get, post, id, k, toast, hrefAcompanhar: '#/campeonato/' + id + '/acompanhar' });
        else Chaves.montarSorteio(el, { get, post, apagar: (c) => api(c, { metodo: 'DELETE' }), id, toast, aoSortear: () => { location.hash = '#/campeonato/' + id + '/chaves'; } });
      });
    } catch (e) { erroTela(e); }
  }

  async function vEditarCampeonato(id) {
    carregando();
    try {
      const k = await api('/campeonatos/' + id);
      if (!k.sou_gestor) { location.hash = '#/campeonato/' + id; return; }
      const e = k.edicao;
      const v = (x) => esc(x == null ? '' : x);
      montar(`<a href="#/campeonato/${id}" class="suave">← ${esc(k.nome)}</a><h1 style="margin:8px 0 0">✏️ Editar campeonato</h1>${abasCamp(k, 'editar')}
        <form id="f-editar" class="painel"><div id="erro"></div>
        <div class="campo"><label>Nome do campeonato</label><input name="nome" required maxlength="150" value="${v(e.nome)}"></div>
        <div class="campo"><label>Categoria</label><input name="categoria" maxlength="120" value="${v(e.categoria)}"></div>
        <div class="campo"><label>Local</label><input name="local_nome" required maxlength="200" value="${v(e.local_nome)}"></div>
        <div class="duas"><div class="campo"><label>Início</label><input type="date" name="data_inicio" required value="${v(e.data_inicio)}"></div><div class="campo"><label>Fim (opcional)</label><input type="date" name="data_fim" value="${v(e.data_fim)}"></div></div>
        <div class="campo"><label>Inscrições até</label><input type="date" name="inscricao_ate" required value="${v(e.inscricao_ate)}"></div>
        <div class="duas"><div class="campo"><label>Máximo de equipes</label><input type="number" name="max_equipes" min="2" required value="${v(e.max_equipes)}"></div><div class="campo"><label>Atletas por equipe</label><input type="number" name="atletas_por_equipe" min="1" required value="${v(e.atletas_por_equipe)}"></div></div>
        <div class="campo"><label>Inscrição por equipe (R$)</label><input type="number" name="valor_inscricao" min="0" step="0.01" value="${v(e.valor_inscricao)}"></div>
        <div class="campo"><label>Premiação</label><input name="premiacao" maxlength="200" value="${v(e.premiacao)}"></div>
        <div class="campo"><label>Sobre</label><textarea name="descricao" rows="3">${v(e.descricao)}</textarea></div>
        <div class="campo"><label>Regulamento</label><textarea name="regulamento" rows="6">${v(e.regulamento)}</textarea></div>
        <label class="suave" style="display:flex;gap:8px;align-items:flex-start;margin-bottom:14px"><input type="checkbox" name="cadastro_elenco" ${e.cadastro_elenco ? 'checked' : ''} style="margin-top:3px"><span>Liberar o cadastro do elenco: o capitão de cada equipe informa nome e RG dos componentes, o técnico e quem é o capitão. O RG só aparece para a organização e para o capitão.</span></label>
        <button class="btn roxo bloco">Salvar alterações</button></form>
        <div class="painel" style="margin-top:12px"><h3>📄 Regulamento em PDF</h3>
          <p class="suave" style="margin:6px 0 10px">Suba o regulamento definido nas reuniões. Todos podem abrir o arquivo na página do campeonato.</p>
          ${k.regulamento_pdf ? `<p><a href="${esc(k.regulamento_pdf.url)}" target="_blank" rel="noopener"><b>${esc(k.regulamento_pdf.nome)}</b></a> <button type="button" class="btn perigo pequeno" id="bt-pdf-rm">Remover</button></p>` : ''}
          <div class="campo"><input type="file" id="arq-pdf" accept="application/pdf,.pdf"></div>
          <button type="button" class="btn suave bloco" id="bt-pdf">${k.regulamento_pdf ? 'Trocar o PDF' : 'Enviar PDF'}</button></div>`, () => {
        $('#bt-pdf').onclick = async () => {
          const arq = $('#arq-pdf').files[0];
          if (!arq) { toast('Escolha o arquivo PDF.'); return; }
          const form = new FormData();
          form.append('arquivo', arq);
          try { await api('/campeonatos/' + id + '/regulamento', { metodo: 'POST', form }); toast('PDF enviado.'); vEditarCampeonato(id); } catch (er) { toast(er.message); }
        };
        const rm = $('#bt-pdf-rm');
        if (rm) rm.onclick = async () => { if (!confirm('Remover o PDF do regulamento?')) return; try { await api('/campeonatos/' + id + '/regulamento', { metodo: 'DELETE' }); vEditarCampeonato(id); } catch (er) { toast(er.message); } };
        $('#f-editar').onsubmit = async (ev) => {
          ev.preventDefault();
          const d = dadosForm(ev.target);
          try {
            await api('/campeonatos/' + id, { metodo: 'PUT', corpo: {
              nome: d.nome, categoria: d.categoria, local_nome: d.local_nome, data_inicio: d.data_inicio, data_fim: d.data_fim || null, inscricao_ate: d.inscricao_ate,
              max_equipes: Number(d.max_equipes), atletas_por_equipe: Number(d.atletas_por_equipe), valor_inscricao: d.valor_inscricao || '0', premiacao: d.premiacao,
              descricao: d.descricao, regulamento: d.regulamento, cadastro_elenco: !!d.cadastro_elenco,
            } });
            toast('Campeonato atualizado.');
            location.hash = '#/campeonato/' + id;
          } catch (er) { $('#erro').innerHTML = `<p class="aviso erro">${esc(er.message)}</p>`; }
        };
      });
    } catch (er) { erroTela(er); }
  }

  async function vNovoCampeonato() {
    carregando();
    try {
      const minhas = await api('/gestao/arenas');
      const h = new Date().toISOString().slice(0, 10);
      montar(`<a href="#/criar" class="suave">← Voltar</a><h1 style="margin:8px 0 14px">Criar campeonato</h1>
        <form id="f-camp" class="painel"><div id="erro"></div>
        <div class="campo"><label>Nome</label><input name="nome" required></div>
        <div class="campo"><label>Modalidade</label><select name="modalidade_id">${estado.mods.map((m) => `<option value="${m.id}">${esc(m.icone)} ${esc(m.nome)}</option>`).join('')}</select></div>
        <div class="campo"><label>Arena</label><select name="arena_id"><option value="">Outro local (minha localização)</option>${minhas.map((a) => `<option value="${a.id}">${esc(a.nome)}</option>`).join('')}</select></div>
        <div class="campo"><label>Local</label><input name="local_nome" placeholder="Se não for uma arena"></div>
        <div class="duas"><div class="campo"><label>Início</label><input type="date" name="data_inicio" min="${h}" required></div><div class="campo"><label>Inscrições até</label><input type="date" name="inscricao_ate" min="${h}" required></div></div>
        <div class="duas"><div class="campo"><label>Equipes</label><input type="number" name="max_equipes" value="16" min="2" required></div><div class="campo"><label>Atletas/equipe</label><input type="number" name="atletas_por_equipe" value="5" min="1" required></div></div>
        <div class="duas"><div class="campo"><label>Inscrição (R$)</label><input name="valor_inscricao" inputmode="decimal"></div><div class="campo"><label>Premiação</label><input name="premiacao"></div></div>
        <div class="campo"><label>Categoria</label><input name="categoria"></div>
        <div class="campo"><label>Regulamento</label><textarea name="regulamento" placeholder="Resumo ou texto (opcional se enviar o PDF)"></textarea></div>
        <div class="campo"><label>Regulamento em PDF (opcional)</label><input type="file" id="arq-pdf-novo" accept="application/pdf,.pdf"><span class="suave">Se já tem o regulamento definido em reunião, envie agora. Dá para trocar depois em Editar.</span></div>
        <button class="btn roxo bloco">Publicar campeonato</button></form>`, () => {
        $('#f-camp').onsubmit = async (ev) => {
          ev.preventDefault(); const d = dadosForm(ev.target);
          if (!d.arena_id && !estado.pos) { $('#erro').innerHTML = '<p class="aviso erro">Escolha uma arena ou toque no 📍 do topo para informar sua localização.</p>'; return; }
          const corpo = { nome: d.nome, modalidade_id: Number(d.modalidade_id), arena_id: num(d.arena_id), local_nome: d.local_nome || '', data_inicio: d.data_inicio, inscricao_ate: d.inscricao_ate, max_equipes: Number(d.max_equipes), atletas_por_equipe: Number(d.atletas_por_equipe), valor_inscricao: num(d.valor_inscricao) || 0, premiacao: d.premiacao || null, categoria: d.categoria || null, regulamento: d.regulamento || null };
          if (!d.arena_id) { corpo.latitude = estado.pos.lat; corpo.longitude = estado.pos.lng; }
          const pdf = $('#arq-pdf-novo').files[0];
          if (pdf && !/\.pdf$/i.test(pdf.name)) { $('#erro').innerHTML = '<p class="aviso erro">Envie um arquivo PDF.</p>'; return; }
          try {
            const c = await post('/campeonatos', corpo);
            if (pdf) {
              const form = new FormData();
              form.append('arquivo', pdf);
              try { await api('/campeonatos/' + c.id + '/regulamento', { metodo: 'POST', form }); toast('Campeonato publicado com o regulamento em PDF! 🏆'); location.hash = '#/campeonato/' + c.id; }
              catch (er) { toast('Campeonato publicado, mas o PDF não foi enviado: ' + er.message); location.hash = '#/campeonato/' + c.id + '/editar'; }
            } else { toast('Campeonato publicado! 🏆'); location.hash = '#/campeonato/' + c.id; }
          } catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
        };
      });
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ arena (pública)
  async function vArena(id) {
    carregando();
    try {
      const a = await api('/arenas/' + id);
      montar(`<a href="javascript:history.back()" class="suave">← Voltar</a>
        <span class="tag verde" style="margin-top:10px">🏟️ Arena</span><h1 style="margin:8px 0 4px">${esc(a.nome)}</h1>
        <p class="suave">${esc(a.endereco || '')}${a.cidade ? ' • ' + esc(a.cidade) : ''}${a.distancia ? ' • a ' + esc(a.distancia) : ''}<br>Aberta das ${esc(a.abre)} às ${esc(a.fecha)}</p>
        ${a.sou_gestor ? `<a class="btn roxo pequeno" style="margin-top:10px" href="#/gestao">Gerenciar</a>` : ''}
        ${a.descricao ? `<p style="margin:12px 0">${esc(a.descricao)}</p>` : ''}
        <div class="mapa pequeno" id="mapa" style="margin:12px 0"></div>
        <div class="painel"><h3>Quadras</h3>${a.quadras_lista.map((q) => `<div class="pessoa"><b>${esc(q.nome)}</b><span class="suave">${esc(q.modalidades.join(', ').replace(/_/g, ' '))}</span><span class="acoes"><b>${esc(q.valor_texto)}</b></span></div>`).join('') || '<p class="suave">Nenhuma quadra cadastrada.</p>'}</div>
        ${secao('Horários disponíveis')}${lista(a.horarios, (h) => `<div class="card"><span class="tag verde">🏟️ Livre</span><h3>${modIcone(h.modalidade)} ${esc(h.modalidade ? h.modalidade.nome : 'Quadra')} • ${esc(h.quando)}</h3><div class="meta">${esc(h.quadra_nome)} • <b>${esc(h.valor_texto)}</b></div></div>`, 'Nenhum horário divulgado no momento.')}`,
      () => iniciarMapa('mapa', { latitude: a.latitude, longitude: a.longitude }, [{ tipo: 'arena', id: a.id, latitude: a.latitude, longitude: a.longitude, rotulo: a.nome, distancia: a.distancia }]));
    } catch (e) { erroTela(e); }
  }

  async function vLocal(id) {
    carregando();
    try {
      const l = await api('/locais/' + id);
      const linha = (rotulo, v) => (v ? `<p style="margin:10px 0"><b>${rotulo}</b><br>${v}</p>` : '');
      montar(`<a href="javascript:history.back()" class="suave">← Voltar</a>
        <span class="tag verde" style="margin-top:10px">${esc(l.icone)} ${esc(l.categoria_nome)}</span><h1 style="margin:8px 0 4px">${esc(l.nome)}</h1>
        ${l.descricao ? `<p>${esc(l.descricao)}</p>` : ''}
        ${linha('📍 Endereço', esc(l.endereco) + (l.cidade ? ' — ' + esc(l.cidade) : '') + (l.distancia ? ' (' + esc(l.distancia) + ')' : ''))}
        ${l.avaliacao ? `<p style="margin:6px 0"><b style="color:#f5b301">★ ${String(l.avaliacao.toFixed(1)).replace('.', ',')}</b>${l.avaliacoes ? ` <span class="suave">(${l.avaliacoes} avaliações no Google)</span>` : ''}</p>` : ''}
        ${l.incompleto ? '<p class="aviso alerta">Horário ou telefone ainda não informados. Confirme com o local antes de ir.</p>' : ''}
        ${linha('🕒 Funcionamento', l.horario_funcionamento ? `<span style="white-space:pre-line">${esc(l.horario_funcionamento)}</span>` : '<span class="suave">Não informado</span>')}
        ${linha('📞 Telefone', l.telefone ? `<a href="tel:${esc(l.telefone)}">${esc(l.telefone)}</a>` : '<span class="suave">Não informado</span>')}
        ${linha('✉️ E-mail', l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` : '')}
        ${l.instagram || l.facebook || l.site ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0">${l.instagram ? `<a class="btn suave pequeno" href="${esc(l.instagram)}" target="_blank" rel="noopener nofollow">📸 Instagram</a>` : ''}${l.facebook ? `<a class="btn suave pequeno" href="${esc(l.facebook)}" target="_blank" rel="noopener nofollow">👍 Facebook</a>` : ''}${l.site ? `<a class="btn suave pequeno" href="${esc(l.site)}" target="_blank" rel="noopener nofollow">🌐 Site</a>` : ''}</div>` : ''}
        <p><a class="btn roxo pequeno" href="${esc(l.maps_url)}" target="_blank" rel="noopener">📍 Abrir no Google Maps</a></p>
        ${l.regras ? `<div class="painel"><h3>Regras de utilização</h3><p style="white-space:pre-line">${esc(l.regras)}</p></div>` : ''}
        <div class="mapa pequeno" id="mapa" style="margin:12px 0"></div>
        <p class="suave" style="font-size:12px">Informações cadastradas pelo PlayGo; confirme com o local antes de ir.</p>`,
      () => iniciarMapa('mapa', { latitude: l.latitude, longitude: l.longitude }, [{ tipo: 'arena', id: l.id, latitude: l.latitude, longitude: l.longitude, rotulo: l.nome, distancia: l.distancia }]));
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ gestão (arena)
  async function vGestao() {
    carregando();
    try {
      const arenas = await api('/gestao/arenas');
      if (!arenas.length) {
        return montar(`<h1>Minha Arena</h1><div class="vazio" style="margin-top:14px">Cadastre sua arena, organize a agenda das quadras e transforme horários vagos em oportunidades para atletas próximos.<br><br><a class="btn roxo" href="#/nova-arena">Cadastrar minha arena</a></div>`);
      }
      const arena = arenas.find((a) => a.id === estado.arena) || arenas[0];
      estado.arena = arena.id; guardar('playgo_arena', arena.id);
      const dia = estado.dia || new Date().toISOString().slice(0, 10);
      const [p, ag] = await Promise.all([api(`/gestao/arenas/${arena.id}/painel?dia=${dia}`), api(`/gestao/arenas/${arena.id}/agenda?dia=${dia}`)]);
      const d = new Date(dia + 'T12:00:00'); const mover = (n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); };
      const max = Math.max(1, ...p.demanda_por_dia.map((x) => x.total));
      montar(`<div class="secao" style="margin-top:0"><h1>${esc(arena.nome)}</h1>${arenas.length > 1 ? `<select id="sel-arena" class="chip">${arenas.map((a) => `<option value="${a.id}" ${a.id === arena.id ? 'selected' : ''}>${esc(a.nome)}</option>`).join('')}</select>` : ''}</div>
        <div class="stats"><div class="stat"><b>${p.reservas_hoje}</b><span>Reservas</span></div><div class="stat"><b>${p.ocupacao_pct}%</b><span>Ocupação</span></div><div class="stat"><b>${p.eventos_ativos}</b><span>Eventos ativos</span></div><div class="stat"><b>${p.atletas_alcancados}</b><span>Atletas alcançados</span></div></div>
        <div class="livebar" style="flex-direction:column;align-items:stretch">${p.horarios_ociosos ? `<div><span class="pulse"></span><strong>${p.horarios_ociosos} horário(s) ocioso(s).</strong> Publique para atletas próximos.</div><button class="btn lima" data-acao="divulgar-todos" data-id="${arena.id}">Divulgar horário</button>` : `<div><span class="pulse"></span><strong>Sem horários ociosos no restante do dia${p.horarios_divulgados ? ' — ' + p.horarios_divulgados + ' já divulgado(s)' : ''}.</strong></div>`}</div>
        <div class="secao"><h2>Agenda</h2><div><button class="btn suave pequeno" data-acao="dia" data-v="${mover(-1)}">←</button> <span class="suave">${d.toLocaleDateString('pt-BR')}</span> <button class="btn suave pequeno" data-acao="dia" data-v="${mover(1)}">→</button></div></div>
        <div class="painel" style="overflow-x:auto">${ag.quadras.length ? `<table class="agenda"><thead><tr><th>Hora</th>${ag.quadras.map((q) => `<th>${esc(q.nome)}</th>`).join('')}</tr></thead><tbody>${ag.linhas.map((l) => `<tr><td>${esc(l.hora)}</td>${l.celulas.map((c) => `<td><button class="cel ${c.estado} ${c.passado ? 'passado' : ''}" data-acao="celula" data-c='${esc(JSON.stringify({ ...c, inicio: l.inicio }))}'>${esc(c.rotulo)}</button></td>`).join('')}</tr>`).join('')}</tbody></table>` : '<p class="vazio">Cadastre a primeira quadra.</p>'}</div>
        <div style="display:flex;gap:8px;margin-top:12px"><button class="btn suave" data-acao="nova-quadra" data-id="${arena.id}">+ Nova quadra</button><a class="btn suave" href="#/nova-arena">+ Nova arena</a></div>
        ${secao('Ocupação por horário')}<div class="painel barras">${p.ocupacao_por_hora.map((h) => `<div class="l"><span>${esc(h.hora)}</span><div class="barra"><i style="width:${h.pct}%"></i></div><span>${h.pct}%</span></div>`).join('')}</div>
        ${secao('Dias com maior demanda')}<div class="painel barras">${p.demanda_por_dia.map((x) => `<div class="l"><span>${esc(x.dia)}</span><div class="barra"><i style="width:${Math.round((100 * x.total) / max)}%"></i></div><span>${x.total}</span></div>`).join('')}</div>
        ${secao('Mais procurados')}<div class="painel">${p.modalidades_procuradas.map((m) => `<div class="pessoa"><span style="font-size:22px">${esc(m.icone)}</span><b>${esc(m.nome)}</b><span class="suave" style="margin-left:auto">${m.total}</span></div>`).join('') || '<p class="suave">Sem atividades ainda.</p>'}<p class="suave" style="margin-top:10px">${p.clientes_recorrentes} clientes recorrentes</p></div>`,
      () => { const s = $('#sel-arena'); if (s) s.onchange = () => { estado.arena = Number(s.value); guardar('playgo_arena', estado.arena); vGestao(); }; });
    } catch (e) { erroTela(e); }
  }

  function vNovaArena() {
    montar(`<a href="#/gestao" class="suave">← Voltar</a><h1 style="margin:8px 0 14px">Cadastrar arena</h1>
      <form id="f-arena" class="painel"><div id="erro"></div>
      <div class="campo"><label>Nome</label><input name="nome" required></div><div class="campo"><label>Tipo de estabelecimento</label><select name="tipo_estabelecimento"><option value="arena">Arena</option><option value="academia">Academia</option><option value="clube">Clube</option><option value="escola">Escola</option></select></div><div class="campo"><label>Endereço</label><input name="endereco"></div>
      <div class="duas"><div class="campo"><label>Abre</label><input type="time" name="abre" value="06:00" required></div><div class="campo"><label>Fecha</label><input type="time" name="fecha" value="23:00" required></div></div>
      <div class="campo"><label>Estrutura</label><input name="estrutura" placeholder="Vestiário, bar, estacionamento"></div>
      <div class="campo"><label>Onde fica a arena?</label>
        <div class="mapa pequeno" id="mapa-arena" style="height:300px;margin-top:6px"></div>
        <span class="suave" id="info-local" style="display:block;margin-top:6px">Busque o endereço na lupa do mapa ou toque no ponto exato. Dá para arrastar o pino.</span>
        <button type="button" class="btn suave bloco" data-acao="local-arena" style="margin-top:8px">📍 Usar minha localização atual</button></div>
      <button class="btn roxo bloco">Cadastrar</button></form>`, () => {
      let local = null;
      const info = $('#info-local');
      const centro = estado.pos ? [estado.pos.lat, estado.pos.lng] : (estado.usuario && estado.usuario.latitude ? [estado.usuario.latitude, estado.usuario.longitude] : [-20.4697, -54.6201]);
      if (window.L) {
        const mapa = L.map('mapa-arena').setView(centro, 14);
        let pino = null;
        const definir = (la, ln, nome) => {
          local = { lat: la, lng: ln };
          if (!pino) { pino = L.marker([la, ln], { draggable: true }).addTo(mapa); pino.on('dragend', () => { const q = pino.getLatLng(); local = { lat: q.lat, lng: q.lng }; info.textContent = 'Local marcado ✓ (arrastado)'; }); } else pino.setLatLng([la, ln]);
          info.textContent = 'Local marcado ✓';
          const campo = $('#f-arena [name=endereco]');
          if (nome && campo && !campo.value.trim()) campo.value = nome.split(',').slice(0, 3).join(',').trim();  // a busca já preenche o endereço, se estiver vazio
        };
        if (window.PlayGoMapa) PlayGoMapa.preparar(mapa, { aoEscolher: (la, ln, nome) => { definir(la, ln, nome); }, marcar: false });
        else L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(mapa);
        mapa.on('click', (e) => definir(e.latlng.lat, e.latlng.lng));
        estado.seletorArena = { definir, mapa };
      } else { $('#mapa-arena').innerHTML = '<p class="vazio">Mapa indisponível sem conexão. Use “Usar minha localização atual”.</p>'; estado.seletorArena = { definir: (la, ln) => { local = { lat: la, lng: ln }; info.textContent = 'Local marcado ✓'; } }; }
      $('#f-arena').onsubmit = async (ev) => {
        ev.preventDefault(); const d = dadosForm(ev.target);
        if (!local) { $('#erro').innerHTML = '<p class="aviso erro">Marque o local da arena no mapa (busque o endereço ou toque no ponto).</p>'; return; }
        try { await post('/gestao/arenas', { nome: d.nome, tipo_estabelecimento: d.tipo_estabelecimento || 'arena', endereco: d.endereco || '', abre: d.abre, fecha: d.fecha, estrutura: d.estrutura || null, latitude: local.lat, longitude: local.lng, cidade: estado.usuario.cidade }); toast('Arena cadastrada!'); estado.arena = null; location.hash = '#/gestao'; }
        catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
      };
    });
  }

  // ------------------------------------------------------------ perfil e notificações
  async function vPerfil() {
    carregando();
    try {
      estado.usuario = await api('/me');
      atualizarMenuEu();
      const u = estado.usuario;
      const mine = await api('/minhas-atividades');
      const nivelDe = (id) => (u.esportes.find((e) => e.modalidade_id === id) || {}).nivel || '';
      montar(`<div style="display:flex;gap:14px;align-items:center;margin-bottom:14px"><span class="avatar grande">${u.foto_url ? `<img src="${esc(u.foto_url)}" alt="Sua foto">` : esc(u.iniciais)}</span><div><h1>${esc(u.arroba)}</h1><p class="suave">${esc(u.nome)} · ${esc(u.email)}</p><button class="btn suave pequeno" data-acao="trocar-usuario" style="margin-top:6px">Trocar @usuario</button> <button class="btn suave pequeno" data-acao="foto" style="margin-top:6px">📷 ${u.foto_url ? 'Trocar foto' : 'Adicionar foto'}</button>${u.foto_url ? ' <button class="btn perigo pequeno" data-acao="remover-foto" style="margin-top:6px">Remover</button>' : ''}</div></div>
        ${mine.proximas.length ? secao('Meus próximos jogos') + lista(mine.proximas, (a) => cardAtv(a)) : ''}
        <form id="f-perfil" style="margin-top:18px"><div class="painel"><h3 style="margin-bottom:12px">Esportes que pratico</h3>
          ${estado.mods.map((m) => `<div class="pessoa"><span style="font-size:22px">${esc(m.icone)}</span><b>${esc(m.nome)}</b><span class="acoes"><select name="esporte_${m.id}" style="border:1px solid var(--border);border-radius:10px;padding:7px"><option value="">—</option>${Object.entries(NIVEIS).map(([k, v]) => `<option value="${k}" ${nivelDe(m.id) === k ? 'selected' : ''}>${v}</option>`).join('')}</select></span></div>`).join('')}</div>
        <div class="painel"><h3 style="margin-bottom:12px">Disponibilidade e distância</h3>
          <div class="marcas">${Object.entries(PERIODOS).map(([k, v]) => `<label><input type="checkbox" name="disponibilidade" value="${k}" ${u.disponibilidade.includes(k) ? 'checked' : ''}> ${v}</label>`).join('')}</div>
          <div class="campo" style="margin-top:14px"><label>Distância máxima</label><select name="raio_km">${[2, 5, 10, 20, 50].map((r) => `<option value="${r}" ${u.raio_km === r ? 'selected' : ''}>Até ${r} km</option>`).join('')}</select></div>
          <div class="campo"><label>Cidade</label><input name="cidade" value="${esc(u.cidade || '')}"></div>
          <button type="button" class="btn suave bloco" data-acao="localizar">📍 ${u.latitude ? 'Atualizar' : 'Informar'} minha localização</button></div>
        <div class="painel"><h3 style="margin-bottom:12px">Notificações</h3><div class="marcas">
          ${[['notif_vagas', '🔥 Vagas por perto', u.notif.vagas], ['notif_lembretes', '⏱ Lembretes', u.notif.lembretes], ['notif_campeonatos', '🏆 Campeonatos', u.notif.campeonatos], ['notif_grupos', '👥 Grupos', u.notif.grupos]].map(([n, r, v]) => `<label><input type="checkbox" name="${n}" value="1" ${v ? 'checked' : ''}> ${r}</label>`).join('')}</div>
          <div class="campo" style="margin-top:14px"><label>Avisar sobre vagas até</label><select name="notif_raio_km">${[1, 2, 3, 5, 10, 20].map((r) => `<option value="${r}" ${u.notif.raio_km === r ? 'selected' : ''}>${r} km de mim</option>`).join('')}</select></div></div>
        <button class="btn roxo bloco" style="margin-top:12px">Salvar perfil</button></form>
        <button class="btn perigo bloco" data-acao="sair" style="margin-top:12px">Sair</button>
        <p class="suave" style="text-align:center;margin-top:14px"><a href="/">Abrir versão web</a></p>`,
      () => {
        $('#f-perfil').onsubmit = async (ev) => {
          ev.preventDefault();
          const f = new FormData(ev.target);
          try {
            const esportes = [...f.entries()].filter(([k, v]) => k.startsWith('esporte_') && v).map(([k, v]) => ({ modalidade_id: Number(k.slice(8)), nivel: v }));
            await api('/me', { metodo: 'PATCH', corpo: { cidade: f.get('cidade') || '', raio_km: Number(f.get('raio_km')), disponibilidade: f.getAll('disponibilidade'), notif_vagas: f.has('notif_vagas'), notif_lembretes: f.has('notif_lembretes'), notif_campeonatos: f.has('notif_campeonatos'), notif_grupos: f.has('notif_grupos'), notif_raio_km: Number(f.get('notif_raio_km')) } });
            estado.usuario = await api('/me/esportes', { metodo: 'PUT', corpo: esportes });
            toast('Perfil salvo!');
          } catch (e) { toast(e.message); }
        };
      });
    } catch (e) { erroTela(e); }
  }

  async function vNotificacoes() {
    carregando();
    try {
      const r = await api('/notificacoes');
      montar(`<div class="secao" style="margin-top:0"><h1>Notificações</h1>${r.nao_lidas ? '<button class="btn suave pequeno" data-acao="lidas">Marcar lidas</button>' : ''}</div>
        <div class="painel" style="padding:0;overflow:hidden">${r.itens.map((n) => `<a class="notif ${n.lida ? '' : 'nova'}" href="${n.link ? '#' + esc(n.link) : '#/notificacoes'}"><b>${esc(n.titulo)}</b>${n.corpo ? `<span>${esc(n.corpo)}</span>` : ''}<small>${esc(n.quando)}</small></a>`).join('') || '<p class="vazio" style="border:0">Nada por aqui ainda. Quando surgirem vagas perto de você, avisamos.</p>'}</div>`);
      atualizarSino(0);
    } catch (e) { erroTela(e); }
  }

  async function atualizarSino(n) {
    const s = $('#sino');
    if (n === undefined) { try { n = (await api('/notificacoes/contagem')).nao_lidas; } catch (e) { return; } }
    s.hidden = !n; s.textContent = n > 99 ? '99+' : n;
    atualizarEquipe();
  }

  // Ícones fixos de Moderação (administrador e moderador geral) e Administração (só administrador), com a fila pendente
  async function atualizarEquipe() {
    const u = estado.usuario || {};
    $('#mod').hidden = !u.equipe_moderacao;
    if (!u.equipe_moderacao) return;
    try { const n = (await api('/moderacao/contagem')).total; const b = $('#modn'); b.hidden = !n; b.textContent = n > 99 ? '99+' : n; } catch (e) { /* sem rede */ }
  }

  // ------------------------------------------------------------ mural, comunidades, convites, moderação, termos e privacidade (LGPD)
  function montarMural(id, extra) {
    const el = document.getElementById(id);
    if (!el || !window.Mural) return null;
    const u = estado.usuario;
    return Mural.montar(el, Object.assign({
      api, posicao: estado.pos, eu: { id: u.id, arroba: u.arroba, admin: u.equipe_moderacao }, toast,
      consulta: estado.pos ? 'lat=' + estado.pos.lat + '&lng=' + estado.pos.lng : '',
      link: (escopo, id2) => ({ atividade: '#/atividade/', campeonato: '#/campeonato/', comunidade: '#/comunidade/' }[escopo] || '#/feed') + id2,
    }, extra));
  }
  const muralBloco = (titulo, acesso, nota) => `<div class="secao"><h2>💬 ${esc(titulo)}</h2></div>` + (acesso
    ? `<p class="suave" style="margin:-4px 0 10px">${esc(nota)}</p><div id="mural"></div>`
    : '<p class="vazio">O mural é exclusivo de quem participa. Entre para ver e publicar.</p>');
  const segGrupos = (ativa) => `<div class="alternar"><a class="${ativa === 'grupos' ? 'on' : ''}" href="#/grupos">Grupos</a><a class="${ativa === 'comunidades' ? 'on' : ''}" href="#/comunidades">Comunidades</a></div>`;
  const VISIB = { publica: 'Pública', autorizados: 'Só autorizados', link: 'Só por link' };

  function buscaArroba(rotulo, aoEscolher) {
    folha(`<h3>${esc(rotulo)}</h3><div class="campo"><input id="busca-arroba" placeholder="@usuario (mín. 2 letras)" autocomplete="off"></div><div id="achados-arroba"></div>`);
    estado.aoEscolherArroba = aoEscolher;
    let t;
    $('#busca-arroba').oninput = (ev) => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const q = ev.target.value.trim().replace(/^@/, '');
        if (q.length < 2) return;
        try {
          const r = await api('/atletas?q=' + encodeURIComponent(q));
          $('#achados-arroba').innerHTML = r.map((a) => pessoa(a, `<span class="acoes"><button class="btn roxo pequeno" data-acao="escolher-arroba" data-u="${esc(a.usuario)}">Escolher</button></span>`)).join('') || '<p class="suave">Ninguém encontrado com esse @.</p>';
        } catch (e) { /* aviso já mostrado */ }
      }, 300);
    };
  }

  // Convidar pelo @usuario, link de convite e moderadores delegados (organizador/gestor)
  function organizacaoExtra(escopo, o) {
    const mods = (o.moderadores || []).map((m) => `<span class="tag">${esc(m.arroba)} <button data-acao="rem-mod" data-escopo="${escopo}" data-id="${o.id}" data-u="${esc(m.arroba.replace('@', ''))}" style="border:0;background:none;cursor:pointer" aria-label="Remover">✕</button></span>`).join(' ') || '<span class="suave">Ninguém além de você.</span>';
    const url = o.link ? location.origin + o.link : '';
    return `<div class="painel" style="margin-top:14px"><h3>${escopo === 'atividade' ? 'Convites e mural' : 'Mural'}</h3>
      ${escopo === 'atividade' ? `<button class="btn suave bloco" style="margin-top:10px" data-acao="convidar-arroba" data-escopo="atividade" data-id="${o.id}">✉️ Convidar pelo @usuario</button>` : ''}
      ${url ? `<p class="suave" style="margin-top:12px">Link de convite — quem receber entra direto, sem aprovação:</p><div style="display:flex;gap:6px"><input readonly value="${esc(url)}" style="flex:1;border:1px solid var(--border);border-radius:10px;padding:8px"><button class="btn suave pequeno" data-acao="copiar" data-v="${esc(url)}">Copiar</button></div><button class="btn perigo pequeno" style="margin-top:8px" data-acao="renovar-link" data-escopo="${escopo}" data-id="${o.id}">Gerar novo link (derruba o atual)</button>` : ''}
      <p class="suave" style="margin-top:12px">Quem cuida do mural (além de você):</p><div>${mods}</div>
      <button class="btn suave pequeno" style="margin-top:8px" data-acao="add-mod" data-escopo="${escopo}" data-id="${o.id}">+ Indicar moderador</button></div>`;
  }

  async function vFeed(parametros) {
    const raio = new URLSearchParams(parametros || '').get('raio') || '';
    const base = estado.pos ? 'lat=' + estado.pos.lat + '&lng=' + estado.pos.lng : '';
    const chip = (r, rot) => `<a class="chip ${raio === r ? 'on' : ''}" href="#/feed${r ? '?raio=' + r : ''}">${rot}</a>`;
    montar(`<div class="secao" style="margin-top:0"><h1>Feed</h1></div>
      <div class="chips">${chip('', 'Todos')}${chip('10', 'Perto de mim (10 km)')}${chip('3', 'Bem perto (3 km)')}</div>
      <p class="suave" style="margin:4px 0 12px">Publicações de todo o app e as que organizadores e participantes quiseram mostrar para todo mundo. Toda publicação mostra o local.</p>
      ${raio && !estado.pos ? '<p class="aviso alerta">Informe sua localização (📍 no topo) para filtrar por distância.</p>' : ''}
      <div id="mural"></div>`, () => montarMural('mural', { geral: true, consulta: [base, raio && estado.pos ? 'raio=' + raio : ''].filter(Boolean).join('&') }));
  }

  // ---- comunidades
  const cardComunidade = (c) => `<a class="card" href="#/comunidade/${c.id}"><span class="tag ${c.visibilidade === 'publica' ? 'verde' : 'laranja'}">${VISIB[c.visibilidade]} · ${c.membros} membro${c.membros !== 1 ? 's' : ''}</span>
    <h3>${esc(c.nome)}</h3><div class="meta">${esc((c.descricao || '').slice(0, 110))}${c.cidade ? '<br>📍 ' + esc(c.cidade) : ''}${c.meu_status === 'pendente' ? '<br><b>Aguardando aprovação</b>' : ''}</div></a>`;

  async function vComunidades() {
    carregando();
    try {
      const c = await api('/comunidades');
      montar(`${segGrupos('comunidades')}<div class="secao" style="margin-top:0"><h1>Comunidades</h1>${pode('comunidade') ? '<a class="btn roxo pequeno" href="#/nova-comunidade">+ Criar</a>' : ''}</div>
        <p class="suave" style="margin:-4px 0 10px">Espaços de relacionamento por interesse, bairro ou jeito de jogar — cada um com seu mural.</p>
        ${lista(c.minhas, cardComunidade, 'Você ainda não participa de nenhuma comunidade.')}
        ${c.descobrir.length ? secao('Descubra comunidades') + lista(c.descobrir, cardComunidade) : ''}`);
    } catch (e) { erroTela(e); }
  }

  async function vComunidade(id) {
    carregando();
    try {
      const c = await api('/comunidades/' + id);
      const url = c.link ? location.origin + c.link : '';
      montar(`<a href="#/comunidades" class="suave">← Comunidades</a>
        <div style="margin-top:10px"><span class="tag ${c.visibilidade === 'publica' ? 'verde' : 'laranja'}">${VISIB[c.visibilidade]}</span></div>
        <h1 style="margin:8px 0 4px">${esc(c.nome)}</h1><p class="suave">${c.membros} membro${c.membros !== 1 ? 's' : ''}${c.cidade ? ' • 📍 ' + esc(c.cidade) : ''}</p>
        ${c.descricao ? `<p style="margin-top:8px">${esc(c.descricao)}</p>` : ''}
        <div style="margin-top:12px">${c.meu_status === 'ativo' ? (c.sou_dono ? '' : `<button class="btn perigo pequeno" data-acao="sair-com" data-id="${c.id}">Sair</button>`) : c.meu_status === 'pendente' ? '<span class="btn suave">Pedido enviado — aguardando aprovação</span>' : c.visibilidade !== 'link' ? `<button class="btn roxo bloco" data-acao="entrar-com" data-id="${c.id}">${c.visibilidade === 'autorizados' ? 'Pedir para entrar' : 'Entrar na comunidade'}</button>` : ''}</div>
        ${c.regras ? `<div class="painel" style="margin-top:14px"><h3>Regras</h3><p style="margin-top:6px;white-space:pre-line">${esc(c.regras)}</p></div>` : ''}
        ${c.pendentes.length ? `<div class="painel" style="margin-top:14px"><h3>Pedidos para entrar (${c.pendentes.length})</h3>${c.pendentes.map((m) => pessoa(m, `<span class="acoes"><button class="btn roxo pequeno" data-acao="pedido" data-id="${c.id}" data-u="${m.usuario_id}" data-v="1">Aprovar</button><button class="btn perigo pequeno" data-acao="pedido" data-id="${c.id}" data-u="${m.usuario_id}" data-v="0">Recusar</button></span>`)).join('')}</div>` : ''}
        ${c.sou_moderador ? `<div class="painel" style="margin-top:14px"><h3>Convites</h3><button class="btn suave bloco" style="margin-top:10px" data-acao="convidar-arroba" data-escopo="comunidade" data-id="${c.id}">✉️ Convidar pelo @usuario</button>
          ${url ? `<p class="suave" style="margin-top:12px">Link de convite:</p><div style="display:flex;gap:6px"><input readonly value="${esc(url)}" style="flex:1;border:1px solid var(--border);border-radius:10px;padding:8px"><button class="btn suave pequeno" data-acao="copiar" data-v="${esc(url)}">Copiar</button></div><button class="btn perigo pequeno" style="margin-top:8px" data-acao="renovar-link" data-escopo="comunidade" data-id="${c.id}">Gerar novo link</button>` : ''}</div>` : ''}
        ${muralBloco('Mural da comunidade', c.pode_ler, 'Os membros publicam; o local de cada publicação fica sempre marcado.')}
        ${c.membros_lista.length ? `<div class="painel" style="margin-top:14px"><h3>Membros</h3>${c.membros_lista.map((m) => pessoa(m, (m.papel !== 'membro' ? `<span class="tag">${m.papel === 'dono' ? 'Dono' : 'Moderador'}</span>` : '') + (m.papel !== 'dono' && c.sou_moderador ? `<span class="acoes">${c.sou_dono ? `<button class="btn suave pequeno" data-acao="papel-com" data-id="${c.id}" data-u="${m.usuario_id}" data-v="${m.papel === 'moderador' ? 'membro' : 'moderador'}">${m.papel === 'moderador' ? 'Tirar moderação' : 'Moderador'}</button>` : ''}${m.papel === 'membro' ? `<button class="btn perigo pequeno" data-acao="papel-com" data-id="${c.id}" data-u="${m.usuario_id}" data-v="banido">Banir</button>` : ''}</span>` : ''))).join('')}</div>` : ''}`,
      () => { if (c.pode_ler) montarMural('mural', { escopo: 'comunidade', escopoId: c.id }); });
    } catch (e) { erroTela(e); }
  }

  function vNovaComunidade() {
    montar(`<a href="#/comunidades" class="suave">← Comunidades</a><h1 style="margin:8px 0 14px">Criar comunidade</h1>
      <form id="f-com" class="painel"><div id="erro"></div>
      <div class="campo"><label>Nome</label><input name="nome" required maxlength="120"></div>
      <div class="campo"><label>Cidade</label><input name="cidade" value="${esc(estado.usuario.cidade || '')}"></div>
      <div class="campo"><label>Quem pode entrar</label><select name="visibilidade"><option value="publica">Pública — qualquer um entra e ela aparece na busca</option><option value="autorizados">Só autorizados — você aprova cada pedido</option><option value="link">Só por link — não aparece na busca; entra quem receber o link ou convite</option></select></div>
      <div class="campo"><label>Descrição</label><textarea name="descricao"></textarea></div>
      <div class="campo"><label>Regras</label><textarea name="regras" placeholder="Ex.: respeito sempre; nada de propaganda"></textarea></div>
      <p class="suave">Você será o dono: indica moderadores, aprova entradas e cuida do mural.</p>
      <button class="btn roxo bloco">Criar comunidade</button></form>`, () => {
      $('#f-com').onsubmit = async (ev) => {
        ev.preventDefault(); const d = dadosForm(ev.target);
        try { const c = await post('/comunidades', { nome: d.nome, cidade: d.cidade || null, visibilidade: d.visibilidade, descricao: d.descricao || null, regras: d.regras || null }); toast('Comunidade criada!'); location.hash = '#/comunidade/' + c.id; }
        catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
      };
    });
  }

  // ---- convites
  async function vConvites() {
    carregando();
    try {
      const l = await api('/convites');
      montar(`<h1>✉️ Convites</h1><div style="margin-top:12px">${l.length ? `<div class="painel">${l.map((c) => `<div class="pessoa" style="flex-wrap:wrap"><div><b>${esc(c.titulo)}</b><div class="suave">${c.escopo === 'atividade' ? 'Atividade' : 'Comunidade'} · de ${esc(c.de)}</div></div>
        <span class="acoes"><button class="btn roxo pequeno" data-acao="responder-convite" data-id="${c.id}" data-v="1">Aceitar</button><button class="btn perigo pequeno" data-acao="responder-convite" data-id="${c.id}" data-v="0">Recusar</button></span></div>`).join('')}</div>` : '<p class="vazio">Nenhum convite pendente.</p>'}</div>`);
    } catch (e) { erroTela(e); }
  }

  async function vConviteLink(token) {
    carregando();
    try {
      const r = await api('/convites/link/' + encodeURIComponent(token));
      montar(`<div class="painel" style="text-align:center;margin-top:20px;padding:26px 18px"><span class="tag">✉️ Você foi convidado</span>
        <h1 style="margin:12px 0 6px">${esc(r.titulo)}</h1>
        ${r.tipo === 'atividade' ? `<p class="meta">${modIcone(r.modalidade)} ${esc(r.modalidade.nome)} • ${esc(r.quando)}<br>📍 ${esc(r.local)}<br>Organizador: ${esc(r.organizador)} • ${r.vagas} vaga${r.vagas !== 1 ? 's' : ''}</p>` : `<p class="meta">${esc(r.descricao || '')}<br>${r.membros} membro${r.membros !== 1 ? 's' : ''}</p>`}
        <button class="btn roxo bloco" style="margin-top:14px" data-acao="entrar-link" data-token="${esc(token)}">${r.tipo === 'atividade' ? 'EU VOU' : 'Entrar na comunidade'}</button>
        <p class="suave" style="margin-top:12px">Ao entrar, valem os <a href="#/documento/termos">Termos de Uso</a>.</p></div>`);
    } catch (e) { erroTela(e); }
  }

  // ---- fila de moderação (administrador)
  async function vModeracao() {
    carregando();
    try {
      const f = await api('/moderacao/fila');
      montar(`<h1>🛡 Moderação</h1><p class="suave" style="margin:4px 0 12px">Conteúdo que a análise automática segurou ou que juntou várias denúncias. Só o autor vê até você decidir. Você aprova ou rejeita — nunca edita o texto de outra pessoa.</p>
        ${f.itens.length ? f.itens.map((i) => `<article class="painel" style="margin-bottom:12px"><div style="display:flex;justify-content:space-between;gap:8px"><b>${i.tipo === 'publicacao' ? 'Publicação' : 'Comentário'} de ${esc(i.autor.arroba)}</b><span class="suave">${esc(i.quando)}</span></div>
          <p class="aviso alerta" style="margin:8px 0"><b>Motivo:</b> ${esc(i.motivo_analise)}</p>
          ${i.denuncias && i.denuncias.length ? `<p class="suave">Denúncias: ${i.denuncias.map((d) => esc(d.motivo) + (d.detalhe ? ' — “' + esc(d.detalhe) + '”' : '')).join('; ')}</p>` : ''}
          <div style="white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0">${esc(i.texto)}</div>
          ${i.midias && i.midias.length ? i.midias.map((m) => m.tipo === 'video' ? `<video controls preload="metadata" src="${esc(m.url)}" style="max-width:100%;border-radius:10px;margin-top:6px"></video>` : `<img src="${esc(m.miniatura || m.url)}" style="max-width:100%;border-radius:10px;margin-top:6px">`).join('') : ''}
          ${i.local ? `<p class="suave">📍 ${esc(i.local.nome)}</p>` : ''}
          <div style="display:flex;gap:8px;margin-top:10px"><button class="btn roxo" data-acao="decidir-fila" data-tipo="${i.tipo}" data-id="${i.id}" data-v="1">Aprovar</button><button class="btn perigo" data-acao="decidir-fila" data-tipo="${i.tipo}" data-id="${i.id}" data-v="0">Rejeitar</button></div></article>`).join('') : '<p class="vazio">🎉 Nada aguardando análise.</p>'}<div id="instagram-fila"></div>`, () => {
        InstagramFila.montar($('#instagram-fila'), { get: (c) => api(c), post, toast });
      });
    } catch (e) { erroTela(e); }
  }

  // ---- meu plano e mensalidades
  const NOME_PLANO = { gratuito: 'Usuário', pro: 'Pro', organizador: 'Organizador', arena: 'Arena' };
  const brl = (v) => 'R$ ' + Number(v || 0).toFixed(2).replace('.', ',') + '/mês';
  const dataBr = (iso) => (iso ? iso.split('-').reverse().join('/') : '');
  const reais = (v) => Number(v || 0).toFixed(2).replace('.', ',');
  let cicloPlano = 'mensal';

  async function vPlanos() {
    carregando();
    try {
      const p = await api('/planos');
      const f = dataBr(p.fim), t = dataBr(p.tolerancia_ate);
      const aviso = p.aviso_vencimento ? `<p class="aviso erro" style="margin-top:12px">⏳ ${esc(p.aviso_vencimento.texto)}</p>` : '';
      const faixa = p.status === 'administrador' ? ['ok', 'Você é administrador: acesso completo, sem cobrança.']
        : p.status === 'teste' ? ['ok', `🎁 Teste do plano ${NOME_PLANO[p.plano]} até ${f} (${p.dias_restantes} dia${p.dias_restantes !== 1 ? 's' : ''}).`]
        : p.status === 'agendada' ? ['ok', `📅 Plano ${NOME_PLANO[p.plano_contratado]} liberado de ${dataBr(p.inicio)} até ${f}. Até lá você segue no plano Usuário.`]
        : p.status === 'ativa' ? ['ok', `✅ Plano ${NOME_PLANO[p.plano]} ${p.ciclo === 'anual' ? '(anual) ' : ''}ativo até ${f}.`]
        : p.status === 'cancelada' ? ['ok', `Plano ${NOME_PLANO[p.plano]} cancelado: vale até ${f}, sem renovação.`]
        : p.status === 'tolerancia' ? ['erro', `⚠️ O plano ${NOME_PLANO[p.plano]} venceu em ${f}. Tolerância até ${t}; depois, não dá para criar nem divulgar nada novo (o que já existe continua).`]
        : p.status === 'vencida' ? ['erro', `O acesso ao plano ${NOME_PLANO[p.plano_contratado]} terminou em ${f}. Criar e divulgar coisas novas exige assinar de novo.`]
        : !p.acesso_basico ? ['erro', 'Para publicar e participar, assine o plano Usuário.']
        : ['alerta', 'Você está no plano Usuário: vê tudo, publica no feed e participa de atividades. Para organizar atividades, campeonatos ou uma arena, escolha um plano.'];
      const cartao = (pl) => {
        const atual = p.plano_contratado === pl.codigo || (pl.codigo === 'gratuito' && p.plano === 'gratuito');
        const lim = pl.codigo === 'gratuito' ? 'Ver tudo, publicar e comentar no feed e participar de atividades. Não organiza atividades.' : pl.codigo === 'pro' ? `Organiza atividades: até ${pl.max_participantes || '∞'} participantes por atividade e ${pl.max_atividades_abertas || '∞'} abertas ao mesmo tempo. Sem campeonatos.` : pl.codigo === 'organizador' ? 'Atividades sem limite e campeonatos.' : 'Tudo do Organizador, mais arenas, quadras, agenda e divulgação de horários.';
        const cic = cicloPlano === 'anual' && pl.anual_disponivel ? 'anual' : 'mensal';
        const preco = cic === 'anual' ? `R$ ${reais(pl.valor_anual)}/ano<div class="suave" style="font-size:13px;font-weight:700">≈ R$ ${reais(pl.valor_anual / 12)}/mês${pl.desconto_anual ? ' · economize ' + pl.desconto_anual + '%' : ''}</div>` : brl(pl.valor_mensal);
        let acao = '';
        if ((pl.codigo !== 'gratuito' || pl.assinavel) && p.status !== 'administrador') {
          if (p.assinatura_asaas && p.plano_contratado === pl.codigo) acao = '<button class="btn perigo bloco" data-acao="plano-cancelar">Cancelar assinatura</button>';
          else {
            if (p.teste_disponivel.includes(pl.codigo)) acao += `<button class="btn suave bloco" data-acao="plano-teste" data-p="${pl.codigo}">🎁 Começar teste de ${p.teste_dias} dias</button>`;
            acao += pl.assinavel && p.cobranca_ativa ? `<button class="btn roxo bloco" style="margin-top:8px" data-acao="plano-assinar" data-p="${pl.codigo}" data-c="${cic}">Assinar${cic === 'anual' ? ' o anual' : ''}</button>` : `<p class="suave" style="margin-top:8px">${pl.assinavel ? 'Cobrança ainda não ativada.' : 'Valor ainda não definido.'}</p>`;
          }
        }
        return `<div class="card ${atual ? 'champ' : ''}"><span class="tag ${atual ? '' : pl.codigo === 'arena' ? 'verde' : pl.codigo === 'organizador' ? 'laranja' : ''}">${atual ? 'Seu plano' : esc(pl.nome)}</span><h3>${esc(pl.nome)}</h3>
          <div style="font-size:22px;font-weight:900;margin:4px 0">${preco}</div><div class="meta">${esc(lim)}</div><div style="margin-top:12px">${acao}</div></div>`;
      };
      const pr = p.profissional;
      const cartaoProf = (pl) => {
        const atual = pr.plano_contratado === pl.codigo;
        const itens = [pl.max_ofertas ? `Até ${pl.max_ofertas} ofertas publicadas` : 'Ofertas ilimitadas', pl.pode_cobrar ? 'Recebimento online dos alunos' : 'Divulgação e contato direto', pl.pode_escolinha ? 'Escolinha com turmas' : null].filter(Boolean);
        let acao = '';
        if (p.status !== 'administrador') {
          if (pr.assinatura_asaas && atual) acao = '<button class="btn perigo bloco" data-acao="plano-cancelar" data-f="profissional">Cancelar assinatura</button>';
          else {
            if (pr.teste_disponivel.includes(pl.codigo)) acao += `<button class="btn suave bloco" data-acao="plano-teste" data-p="${pl.codigo}">🎁 Começar teste de ${p.teste_dias} dias</button>`;
            acao += pl.assinavel && p.cobranca_ativa ? `<button class="btn roxo bloco" style="margin-top:8px" data-acao="plano-assinar" data-p="${pl.codigo}" data-nome="${esc(pl.nome)}" data-c="mensal">Assinar</button>` : `<p class="suave" style="margin-top:8px">${pl.assinavel ? 'Cobrança ainda não ativada.' : 'Valor ainda não definido.'}</p>`;
          }
        }
        return `<div class="card ${atual ? 'champ' : ''}"><span class="tag ${atual ? '' : 'azul'}">${atual ? 'Seu plano' : '🎓 Profissional'}</span><h3>${esc(pl.nome)}</h3><div style="font-size:22px;font-weight:900;margin:4px 0">${brl(pl.valor_mensal)}</div><div class="meta">${itens.map(esc).join(' · ')}</div><div style="margin-top:12px">${acao}</div></div>`;
      };
      const profissional = pr ? `<h2 style="margin-top:18px">🎓 Para quem dá aulas</h2><p class="suave" style="margin:4px 0 10px">Personal, professores e escolinhas: plano exclusivo para divulgar aulas, pacotes e turmas.${pr.perfil_aprovado ? '' : ' <a href="#/ensino">Crie seu perfil e envie os documentos</a> para ser aprovado.'}</p><div class="lista">${pr.planos.map(cartaoProf).join('')}</div>` : '';
      const STATUS_PG = { PENDING: 'Aguardando pagamento', OVERDUE: 'Vencida', CONFIRMED: 'Paga', RECEIVED: 'Paga', RECEIVED_IN_CASH: 'Paga', REFUNDED: 'Estornada', DELETED: 'Cancelada' };
      const cobrancas = (p.cobrancas || []).length ? `<h2 style="margin-top:18px">🧾 Minhas cobranças</h2><div class="lista">${p.cobrancas.map((c) => `<div class="card"><b>${dataBr(c.vencimento)}</b> · R$ ${c.valor.toFixed(2).replace('.', ',')} · <span class="tag ${c.pago_em ? 'verde' : 'laranja'}">${esc(STATUS_PG[c.status] || c.status)}</span>${c.pago_em ? `<div class="suave">paga em ${dataBr(c.pago_em)}</div>` : ''}${c.link ? `<a class="btn suave bloco" style="margin-top:8px" href="${esc(c.link)}" target="_blank" rel="noopener">${c.pago_em ? 'Ver recibo' : 'Abrir fatura / 2ª via'}</a>` : ''}</div>`).join('')}</div>` : '';
      const temAnual = p.planos.some((x) => x.anual_disponivel) && p.status !== 'administrador';
      const seletor = temAnual ? `<div style="display:flex;gap:8px;margin-top:12px"><button class="btn ${cicloPlano === 'mensal' ? 'roxo' : 'suave'} pequeno" data-acao="plano-ciclo" data-c="mensal">Mensal</button><button class="btn ${cicloPlano === 'anual' ? 'roxo' : 'suave'} pequeno" data-acao="plano-ciclo" data-c="anual">Anual${Math.max(...p.planos.map((x) => x.desconto_anual || 0)) ? ' · até ' + Math.max(...p.planos.map((x) => x.desconto_anual || 0)) + '% off' : ''}</button></div>` : '';
      const org = p.planos.find((x) => x.codigo === 'organizador');
      const ev = p.eventos || { saldo: 0, compras: [] };
      const pend = (ev.compras || []).filter((c) => c.status === 'pendente' && c.link);
      const eventos = p.status === 'administrador' ? '' :
        (ev.saldo ? `<p class="aviso ok" style="margin-top:14px">🎟 Você tem <b>${ev.saldo}</b> evento${ev.saldo > 1 ? 's' : ''} para criar campeonatos, até ${dataBr(ev.valida_ate)}.</p>` : '') +
        (org && org.evento_disponivel && p.cobranca_ativa ? `<div class="card" style="margin-top:14px"><h3>🎟 Vai fazer só alguns campeonatos?</h3><p class="suave" style="margin:6px 0">Pague só pelos eventos que vai realizar: <b>R$ ${reais(org.valor_evento)} por evento</b>. Cada campeonato criado usa 1 evento, válidos por 12 meses depois do pagamento. Sem mensalidade.</p><button class="btn roxo bloco" data-acao="eventos-comprar" data-v="${org.valor_evento}">Comprar eventos</button></div>` : '') +
        (pend.length ? pend.map((c) => `<div class="card" style="margin-top:10px"><b>${c.quantidade} evento${c.quantidade > 1 ? 's' : ''}</b> · R$ ${reais(c.valor)} · <span class="tag laranja">Aguardando pagamento</span><a class="btn suave bloco" style="margin-top:8px" href="${esc(c.link)}" target="_blank" rel="noopener">Abrir fatura</a></div>`).join('') : '');
      montar(`<h1>💳 Meu plano</h1>${aviso}<p class="aviso ${faixa[0]}" style="margin-top:12px">${esc(faixa[1])}</p>${seletor}<div class="lista">${p.planos.map(cartao).join('')}</div>${profissional}${eventos}${cobrancas}
        <p class="suave" style="margin-top:14px">A cobrança é mensal ou anual pelo Asaas (Pix, boleto ou cartão); você cancela quando quiser e o acesso segue até o fim do período pago. Quando termina, você volta ao plano Usuário. <a href="#/documento/termos">Termos de Uso</a></p>`);
    } catch (e) { erroTela(e); }
  }

  // ---- administração de perfis (administrador)
  const PERFIS = { usuario: 'Usuário', moderador: 'Moderador geral', admin: 'Administrador' };
  async function vAdmin(q = '', perfil = '') {
    carregando();
    try {
      const r = await api('/admin/usuarios?' + qs({ q, perfil }));
      montar(`<h1>⚙️ Administração</h1><p class="suave" style="margin:4px 0 12px"><b>Administrador</b>: tudo, inclusive promover perfis. <b>Moderador geral</b>: aprova a fila e oculta em qualquer mural. <b>Usuário</b>: comum.</p>
        <a class="btn suave bloco" href="#/admin-profissionais" style="margin-bottom:10px">🧑‍🏫 Aprovação de profissionais</a>
        <form class="busca" id="f-admin"><span>⌕</span><input name="q" value="${esc(q)}" placeholder="@usuario, nome ou e-mail"><button class="btn roxo pequeno">Buscar</button></form>
        <div class="chips">${[['', 'Todos'], ['admin', 'Administradores'], ['moderador', 'Moderadores']].map(([v, n]) => `<button class="chip ${perfil === v ? 'on' : ''}" data-acao="admin-filtro" data-v="${v}">${n}</button>`).join('')}</div>
        <div class="painel">${r.itens.map((i) => `<div class="pessoa" style="flex-wrap:wrap"><span class="avatar">${i.foto_url ? `<img src="${esc(i.foto_url)}" alt="">` : esc(i.iniciais)}</span><div><b>${esc(i.arroba)}</b><div class="suave">${esc(i.nome)} · ${esc(i.email)}</div></div>
          <span class="acoes"><select data-admin-perfil="${i.id}" style="border:1px solid var(--border);border-radius:10px;padding:7px">${Object.entries(PERFIS).map(([k, n]) => `<option value="${k}" ${i.perfil === k ? 'selected' : ''}>${n}</option>`).join('')}</select></span></div>`).join('') || '<p class="suave">Ninguém encontrado.</p>'}</div>`,
      () => {
        $('#f-admin').onsubmit = (ev) => { ev.preventDefault(); vAdmin(dadosForm(ev.target).q || '', perfil); };
        $$('[data-admin-perfil]').forEach((sel) => {
          sel.onchange = async () => {
            if (sel.value === 'admin' && !confirm('Tornar esta pessoa ADMINISTRADORA? Ela poderá gerir usuários e perfis.')) { vAdmin(q, perfil); return; }
            try { await post(`/admin/usuarios/${sel.dataset.adminPerfil}/perfil`, { perfil: sel.value }); toast('Perfil atualizado.'); vAdmin(q, perfil); } catch (e) { toast(e.message); vAdmin(q, perfil); }
          };
        });
      });
    } catch (e) { erroTela(e); }
  }

  // ---- termos, política, aceite e @usuario
  async function vDocumento(doc) {
    carregando();
    try {
      const T = await api('/termos');
      const secoes = doc === 'politica' ? T.politica_de_privacidade : T.termos_de_uso;
      montar(`<a href="javascript:history.back()" class="suave">← Voltar</a>
        <div class="alternar" style="margin-top:10px"><a class="${doc !== 'politica' ? 'on' : ''}" href="#/documento/termos">Termos de Uso</a><a class="${doc === 'politica' ? 'on' : ''}" href="#/documento/politica">Privacidade</a></div>
        <h1>${doc === 'politica' ? 'Política de Privacidade' : 'Termos de Uso'}</h1><p class="suave">Versão ${esc(T.versao)}</p>
        ${secoes.map((s) => `<div class="painel" style="margin-top:12px"><h3>${esc(s.titulo)}</h3>${s.paragrafos.map((p) => `<p style="margin-top:8px;line-height:1.55">${esc(p)}</p>`).join('')}</div>`).join('')}`);
    } catch (e) { erroTela(e); }
  }

  // ---- tutoriais (o mesmo conteúdo do site, vindo da API)
  async function vTutoriais() {
    carregando();
    try {
      const lista = await api('/tutoriais');
      montar(`<h1>📘 Tutoriais</h1><p class="suave" style="margin:4px 0 12px">Guias passo a passo, em linguagem simples.</p><div class="lista">${lista.map((t) => `<a class="card" href="#/tutorial/${esc(t.codigo)}" style="text-decoration:none;color:inherit;display:block"><h3>${esc(t.icone)} ${esc(t.titulo)}</h3><p class="suave" style="margin-top:4px">Toque para abrir ›</p></a>`).join('')}</div>`);
    } catch (e) { erroTela(e); }
  }

  async function vTutorial(codigo) {
    carregando();
    try {
      const [lista, t] = await Promise.all([api('/tutoriais'), api('/tutoriais/' + encodeURIComponent(codigo))]);
      montar(`<a href="#/tutoriais" class="suave">← Tutoriais</a>
        <div class="alternar" style="margin:10px 0">${lista.map((x) => `<a class="${x.codigo === codigo ? 'on' : ''}" href="#/tutorial/${esc(x.codigo)}">${esc(x.icone)} ${esc(x.codigo === 'campeonatos' ? 'Campeonatos' : 'Transmissão')}</a>`).join('')}</div>
        <div class="tut-app">${t.html}</div>`);
    } catch (e) { erroTela(e); }
  }

  // ------------------------------------------------------------ ensino: aulas, profissionais, ofertas (módulo compartilhado com o site)
  function ensinoApp(extra) {
    return Object.assign({
      api: (c, op) => api(c, op), toast, cls: { primario: 'btn roxo', suave: 'btn suave', perigo: 'btn perigo' },
      modalidades: estado.mods, pos: estado.pos, eu: estado.usuario,
      mapa: (id, centro, pontos) => iniciarMapa(id, centro, pontos),
      abrir: async (caminho) => {
        const r = await fetch(API + caminho, { headers: { Authorization: 'Bearer ' + estado.token } });
        if (!r.ok) { toast('Não foi possível abrir o documento.'); return; }
        window.open(URL.createObjectURL(await r.blob()), '_blank');
      },
      href: (t, id) => ({ aulas: '#/aulas', ensino: '#/ensino', profissional: '#/profissional/' + id, oferta: '#/oferta/' + id })[t] || '#/',
      ir: (d) => { location.hash = ({ ensino: '#/ensino', planos: '#/planos', aulas: '#/aulas' })[d] || '#/'; },
    }, extra || {});
  }
  const montarEnsino = (fn, extra) => { montar('<div id="ensino"></div>'); window.Ensino[fn]($('#ensino'), ensinoApp(extra)); };
  const vAulas = () => montarEnsino('montarCentral');
  const vProfissional = (id) => montarEnsino('montarProfissional', { id });
  const vOfertaEnsino = (id) => montarEnsino('montarOferta', { id });
  const vEnsino = () => montarEnsino('montarMinhas');
  const vAdminProfissionais = () => { montar('<a href="#/admin" class="suave">← Administração</a><h1 style="margin:8px 0 12px">🧑‍🏫 Aprovação de profissionais</h1><div id="ensino"></div>'); window.Ensino.montarAdmin($('#ensino'), ensinoApp()); };

  async function vAceite() {
    const T = await fetch(API + '/termos').then((r) => r.json()).catch(() => null);
    montar(`<div class="entrada"><form class="cartao" id="f-aceite"><span class="marca"><img src="/static/marca/icone.svg" alt="" width="40" height="40" class="marca-ico">Play<span>Go</span></span>
      <h1>Atualizamos nossos termos</h1>
      <p class="suave" style="margin:6px 0 12px">A versão <b>${esc(T ? T.versao : '')}</b> dos Termos de Uso e da Política de Privacidade traz regras de publicação, moderação e proteção de dados. Para continuar, leia e aceite.</p>
      <p style="margin-bottom:12px"><a href="#/documento/termos">Ler os Termos de Uso</a> · <a href="#/documento/politica">Ler a Política de Privacidade</a></p><div id="erro"></div>
      <label class="suave" style="display:flex;gap:8px;align-items:flex-start;margin-bottom:10px"><input type="checkbox" name="aceito_termos" required style="margin-top:3px"><span>${esc((T && T.declaracoes.termos) || 'Li e aceito os Termos de Uso e a Política de Privacidade.')}</span></label>
      <button class="btn roxo bloco">Aceitar e continuar</button>
      <button type="button" class="btn suave bloco" style="margin-top:8px" data-acao="sair">Sair</button></form></div>`, () => {
      $('#f-aceite').onsubmit = async (ev) => {
        ev.preventDefault(); const d = dadosForm(ev.target);
        try { estado.usuario = await post('/me/aceitar-termos', { aceito_termos: !!d.aceito_termos }); location.hash = '#/'; rotear(); }
        catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
      };
    });
  }

  function vCompletar() {
    montar(`<div class="entrada"><form class="cartao" id="f-usuario"><span class="marca"><img src="/static/marca/icone.svg" alt="" width="40" height="40" class="marca-ico">Play<span>Go</span></span>
      <h1>Escolha seu nome de usuário</h1>
      <p class="suave" style="margin:6px 0 12px">Ele aparece nas suas publicações e comentários, e é como as pessoas vão te encontrar para convites. Seu nome real não aparece nas publicações.</p><div id="erro"></div>
      <div class="campo"><input name="usuario" required minlength="3" maxlength="20" pattern="[A-Za-z0-9_.@]{3,21}" placeholder="ex.: carlos.cg" autocomplete="username"></div>
      <p class="suave" style="margin-bottom:12px">De 3 a 20 caracteres: letras, números, _ ou ponto.</p>
      <button class="btn roxo bloco">Continuar</button></form></div>`, () => {
      $('#f-usuario').onsubmit = async (ev) => {
        ev.preventDefault();
        try { estado.usuario = await api('/me/usuario', { metodo: 'PUT', corpo: { usuario: dadosForm(ev.target).usuario } }); location.hash = '#/'; rotear(); }
        catch (e) { $('#erro').innerHTML = `<p class="aviso erro">${esc(e.message)}</p>`; }
      };
    });
  }

  // ---- privacidade (LGPD art. 18)
  async function vPrivacidade() {
    const u = estado.usuario;
    montar(`<a href="#/perfil" class="suave">← Perfil</a><h1 style="margin:8px 0 4px">🔒 Meus dados e privacidade</h1>
      <p class="suave" style="margin-bottom:12px">Seus direitos como titular (LGPD, art. 18). <a href="#/documento/politica">Política de Privacidade</a> · <a href="#/documento/termos">Termos de Uso</a></p>
      <div class="painel"><h3>Termos aceitos</h3><p class="suave" style="margin-top:6px">Versão ${esc(u.termos_versao)}. Cada aceite fica registrado com data, hora e IP.</p></div>
      <div class="painel"><h3>Cópia dos meus dados</h3><p class="suave" style="margin:6px 0 10px">Perfil, publicações (com versões anteriores), comentários, participações e registros de acesso, em um arquivo legível.</p><button class="btn roxo bloco" data-acao="baixar-dados">Baixar meus dados (.json)</button></div>
      <div class="painel"><h3>Localização</h3><p class="suave" style="margin:6px 0 10px">${u.consent_localizacao ? 'Você autorizou guardar a localização do seu perfil.' : 'Você não autorizou guardar a localização no perfil. Ainda pode publicar marcando o local.'}</p>${u.consent_localizacao ? '<button class="btn suave bloco" data-acao="revogar-loc">Revogar e apagar minha localização</button>' : ''}</div>
      <div class="painel" style="border-color:#ffd0d0"><h3>Excluir minha conta</h3><p class="suave" style="margin:6px 0 10px">Seus dados pessoais são anonimizados, suas publicações e comentários saem do ar, o que você organiza é cancelado e suas vagas são liberadas. Registros de interação e conteúdo excluído ficam guardados de forma restrita pelo prazo legal e depois são eliminados. Não dá para desfazer.</p><button class="btn perigo bloco" data-acao="excluir-conta">Excluir minha conta</button></div>
      <div class="painel"><h3>Outros pedidos</h3><p class="suave" style="margin-top:6px">Correção de dados, revisão de decisão de moderação e demais direitos: escreva ao encarregado de dados (contato na Política de Privacidade).</p></div>`);
  }

  const acoesNovas = {
    'admin-filtro'(el) { vAdmin('', el.dataset.v); },
    'plano-ciclo'(el) { cicloPlano = el.dataset.c; vPlanos(); },
    'eventos-comprar'(el) {
      const v = Number(el.dataset.v);
      folha(`<h3>Comprar eventos</h3><p class="suave" style="margin-bottom:10px">R$ ${reais(v)} por evento. Informe quantos e seu CPF ou CNPJ (vai direto ao Asaas e não é guardado pelo PlayGo).</p><form id="f-eventos"><div class="campo"><label>Quantos eventos?</label><input name="quantidade" type="number" min="1" max="50" value="1" required></div><p class="suave" id="ev-total" style="margin:6px 0 10px"><b>Total: R$ ${reais(v)}</b></p><div class="campo"><input name="cpf_cnpj" inputmode="numeric" required placeholder="CPF ou CNPJ (só números)"></div><button class="btn roxo bloco">Ir para o pagamento</button></form>`);
      $('#f-eventos').querySelector('[name=quantidade]').oninput = (ev) => { const n = Math.max(1, Math.min(50, Number(ev.target.value) || 1)); $('#ev-total').innerHTML = `<b>Total: R$ ${reais(v * n)}</b>`; };
      $('#f-eventos').onsubmit = async (ev) => {
        ev.preventDefault();
        const d = dadosForm(ev.target);
        try { const r = await post('/planos/eventos', { quantidade: Number(d.quantidade), cpf_cnpj: d.cpf_cnpj }); fecharFolha(); if (r.link_pagamento) window.open(r.link_pagamento, '_blank', 'noopener'); else toast('Cobrança criada. O link chega no seu e-mail.'); vPlanos(); } catch (e) { toast(e.message); }
      };
    },
    async 'plano-teste'(el) { try { await post('/planos/teste', { plano: el.dataset.p }); toast('Teste começou!'); vPlanos(); } catch (e) { toast(e.message); } },
    async 'plano-cancelar'(el) { if (!confirm('Cancelar a assinatura? O acesso segue até o fim do período já pago.')) return; try { await post('/planos/cancelar', { familia: el.dataset.f || 'geral' }); toast('Assinatura cancelada.'); vPlanos(); } catch (e) { toast(e.message); } },
    'plano-assinar'(el) {
      folha(`<h3>Assinar plano ${esc(NOME_PLANO[el.dataset.p] || el.dataset.nome)}</h3><p class="suave" style="margin-bottom:10px">Informe seu CPF ou CNPJ para emitir a cobrança. Ele vai direto ao Asaas e não é guardado pelo PlayGo.</p><form id="f-assinar"><div class="campo"><input name="cpf_cnpj" inputmode="numeric" required placeholder="CPF ou CNPJ (só números)"></div><button class="btn roxo bloco">Ir para o pagamento</button></form>`);
      $('#f-assinar').onsubmit = async (ev) => {
        ev.preventDefault();
        try { const r = await post('/planos/assinar', { plano: el.dataset.p, cpf_cnpj: dadosForm(ev.target).cpf_cnpj, ciclo: el.dataset.c || 'mensal' }); fecharFolha(); if (r.link_pagamento) window.open(r.link_pagamento, '_blank', 'noopener'); else toast('Assinatura criada. A cobrança chega no seu e-mail.'); vPlanos(); } catch (e) { toast(e.message); }
      };
    },
    'escolher-arroba'(el) { if (estado.aoEscolherArroba) Promise.resolve(estado.aoEscolherArroba(el.dataset.u)).catch((e) => toast(e.message)); },
    'convidar-arroba'(el) {
      const { escopo, id } = el.dataset;
      buscaArroba('Convidar pelo @usuario', async (u) => { await post(`/escopos/${escopo}/${id}/convites`, { usuario: u }); toast('Convite enviado a @' + u + '.'); fecharFolha(); });
    },
    'add-mod'(el) {
      const { escopo, id } = el.dataset;
      buscaArroba('Indicar moderador do mural', async (u) => { await post(`/escopos/${escopo}/${id}/moderadores`, { usuario: u }); toast('@' + u + ' agora cuida do mural.'); fecharFolha(); rotear(); });
    },
    async 'rem-mod'(el) { try { await api(`/escopos/${el.dataset.escopo}/${el.dataset.id}/moderadores/${el.dataset.u}`, { metodo: 'DELETE' }); toast('Moderador removido.'); rotear(); } catch (e) { toast(e.message); } },
    copiar(el) { (navigator.clipboard ? navigator.clipboard.writeText(el.dataset.v) : Promise.reject()).then(() => toast('Link copiado!')).catch(() => toast('Copie o link manualmente.')); },
    async 'renovar-link'(el) { if (!confirm('Gerar um novo link? O atual deixa de funcionar.')) return; try { await post(`/escopos/${el.dataset.escopo}/${el.dataset.id}/link/renovar`); toast('Novo link gerado.'); rotear(); } catch (e) { toast(e.message); } },
    async 'entrar-com'(el) { try { await post(`/comunidades/${el.dataset.id}/entrar`); rotear(); } catch (e) { toast(e.message); } },
    async 'sair-com'(el) { if (!confirm('Sair da comunidade?')) return; try { await post(`/comunidades/${el.dataset.id}/sair`); location.hash = '#/comunidades'; } catch (e) { toast(e.message); } },
    async pedido(el) { try { await post(`/comunidades/${el.dataset.id}/pedidos/${el.dataset.u}`, { aprovar: el.dataset.v === '1' }); rotear(); } catch (e) { toast(e.message); } },
    async 'papel-com'(el) { if (el.dataset.v === 'banido' && !confirm('Banir esta pessoa da comunidade?')) return; try { await post(`/comunidades/${el.dataset.id}/membros/${el.dataset.u}/papel`, { papel: el.dataset.v }); rotear(); } catch (e) { toast(e.message); } },
    async 'responder-convite'(el) {
      try { const r = await post(`/convites/${el.dataset.id}/responder`, { aceitar: el.dataset.v === '1' }); if (r.escopo === 'atividade') location.hash = '#/atividade/' + r.escopo_id; else if (r.escopo === 'comunidade') location.hash = '#/comunidade/' + r.escopo_id; else rotear(); }
      catch (e) { toast(e.message); }
    },
    async 'entrar-link'(el) { try { const r = await post(`/convites/link/${encodeURIComponent(el.dataset.token)}/entrar`); toast(r.situacao === 'pendente' ? 'Pedido enviado.' : 'Você entrou!'); location.hash = (r.tipo === 'atividade' ? '#/atividade/' : '#/comunidade/') + r.id; } catch (e) { toast(e.message); } },
    async 'decidir-fila'(el) {
      if (el.dataset.v === '0' && !confirm('Rejeitar? O conteúdo não será exibido e o autor será avisado.')) return;
      try { await post(`/moderacao/${el.dataset.tipo}/${el.dataset.id}/decidir`, { aprovar: el.dataset.v === '1' }); toast('Decisão registrada.'); vModeracao(); } catch (e) { toast(e.message); }
    },
    foto() {
      const i = document.createElement('input');
      i.type = 'file'; i.accept = 'image/jpeg,image/png,image/webp';
      i.onchange = async () => {
        if (!i.files[0]) return;
        const fd = new FormData(); fd.append('arquivo', window.Mural ? await Mural.reduzir(i.files[0], 800, 0.88) : i.files[0]);
        try { estado.usuario = await api('/me/foto', { metodo: 'POST', form: fd }); toast('Foto atualizada!'); rotear(); } catch (e) { toast(e.message); }
      };
      i.click();
    },
    async 'remover-foto'() { if (!confirm('Remover sua foto de perfil?')) return; try { estado.usuario = await api('/me/foto', { metodo: 'DELETE' }); toast('Foto removida.'); rotear(); } catch (e) { toast(e.message); } },
    'trocar-usuario'() {
      folha(`<h3>Trocar nome de usuário</h3><form id="f-trocar"><div class="campo"><input name="usuario" required minlength="3" maxlength="20" pattern="[A-Za-z0-9_.@]{3,21}" placeholder="novo.usuario"></div><p class="suave" style="margin-bottom:10px">As publicações antigas passam a mostrar o novo nome. A troca fica registrada.</p><button class="btn roxo bloco">Salvar</button></form>`);
      $('#f-trocar').onsubmit = async (ev) => { ev.preventDefault(); try { estado.usuario = await api('/me/usuario', { metodo: 'PUT', corpo: { usuario: dadosForm(ev.target).usuario } }); toast('Nome de usuário atualizado.'); fecharFolha(); rotear(); } catch (e) { toast(e.message); } };
    },
    async 'baixar-dados'() {
      try {
        const r = await fetch(API + '/privacidade/meus-dados', { headers: { Authorization: 'Bearer ' + estado.token } });
        if (!r.ok) throw new Error('Não foi possível gerar o arquivo.');
        const url = URL.createObjectURL(new Blob([JSON.stringify(await r.json(), null, 2)], { type: 'application/json' }));
        const a = document.createElement('a'); a.href = url; a.download = 'meus-dados-playgo.json'; a.click(); URL.revokeObjectURL(url);
      } catch (e) { toast(e.message); }
    },
    async 'revogar-loc'() { if (!confirm('Apagar a localização do perfil e revogar a autorização?')) return; try { estado.usuario = await post('/privacidade/revogar-localizacao'); guardar('playgo_pos', null); estado.pos = null; toast('Localização apagada.'); vPrivacidade(); } catch (e) { toast(e.message); } },
    'excluir-conta'() {
      folha(`<h3>Excluir minha conta</h3><p class="suave" style="margin-bottom:10px">Isso não pode ser desfeito. Confirme com sua senha.</p><form id="f-excluir"><div class="campo"><input type="password" name="senha" required placeholder="Sua senha" autocomplete="current-password"></div><button class="btn perigo bloco">Excluir definitivamente</button></form>`);
      $('#f-excluir').onsubmit = async (ev) => { ev.preventDefault(); try { await post('/privacidade/excluir-conta', { senha: dadosForm(ev.target).senha }); fecharFolha(); sair(); toast('Conta excluída.'); } catch (e) { toast(e.message); } };
    },
  };

  // ------------------------------------------------------------ ações (delegação de eventos)
  const trocaFiltro = (k, v) => { estado.filtros[k] = v; vExplorar(); };
  const ROT = { confirmado: ['✓ Confirmado', 'Você está confirmado!'], espera: ['Lista de espera', 'Lotado: você entrou na lista de espera.'], pendente: ['Aguardando', 'Pedido enviado ao organizador.'] };
  const acoes = {
    async entrar(el, ev) {
      ev.preventDefault(); ev.stopPropagation(); el.disabled = true;
      try {
        const a = await post(`/atividades/${el.dataset.id}/entrar`);
        const [rot, msg] = ROT[a.minha_participacao] || ['✓ Pronto', 'Feito!'];
        el.outerHTML = `<span class="btn ${a.minha_participacao === 'confirmado' ? 'ok' : 'suave'} pequeno">${rot}</span>`; toast(msg);
      } catch (e) { el.disabled = false; toast(e.message); }
    },
    async 'entrar-det'(el) { try { await post(`/atividades/${el.dataset.id}/entrar`); vAtividade(el.dataset.id); } catch (e) { toast(e.message); } },
    async 'sair-atv'(el) { try { await post(`/atividades/${el.dataset.id}/sair`); toast('Você saiu da atividade.'); vAtividade(el.dataset.id); } catch (e) { toast(e.message); } },
    async falta(el) { try { await post(`/atividades/${el.dataset.id}/falta-gente`, { ligado: el.dataset.v === '1' }); toast(el.dataset.v === '1' ? 'Em destaque! Avisando atletas compatíveis.' : 'Destaque removido.'); vAtividade(el.dataset.id); } catch (e) { toast(e.message); } },
    async 'cancelar-atv'(el) { if (!confirm('Cancelar a atividade? Os participantes serão avisados.')) return; try { await post(`/atividades/${el.dataset.id}/cancelar`); toast('Atividade cancelada.'); vAtividade(el.dataset.id); } catch (e) { toast(e.message); } },
    async decidir(el) { try { await post(`/atividades/${el.dataset.id}/participacoes/${el.dataset.p}/${el.dataset.v}`); vAtividade(el.dataset.id); } catch (e) { toast(e.message); } },
    async 'entrar-grupo'(el) { try { await post(`/grupos/${el.dataset.id}/entrar`); vGrupo(el.dataset.id); } catch (e) { toast(e.message); } },
    async 'cancelar-pedido-grupo'(el) { try { await post(`/grupos/${el.dataset.id}/sair`); vGrupo(el.dataset.id); } catch (e) { toast(e.message); } },
    async 'aceitar-pedido'(el) { try { await post(`/grupos/${el.dataset.id}/pedidos/${el.dataset.v}/aceitar`); toast('Pedido aceito.'); vGrupo(el.dataset.id, 'membros'); } catch (e) { toast(e.message); } },
    async 'recusar-pedido'(el) { try { await post(`/grupos/${el.dataset.id}/pedidos/${el.dataset.v}/recusar`); vGrupo(el.dataset.id, 'membros'); } catch (e) { toast(e.message); } },
    async 'remover-membro'(el) { if (!confirm('Remover esta pessoa do grupo?')) return; try { await post(`/grupos/${el.dataset.id}/membros/${el.dataset.v}/remover`); toast('Pessoa removida.'); vGrupo(el.dataset.id, 'membros'); } catch (e) { toast(e.message); } },
    async 'sair-grupo'(el) { if (!confirm('Sair do grupo?')) return; try { await post(`/grupos/${el.dataset.id}/sair`); location.hash = '#/grupos'; } catch (e) { toast(e.message); } },
    'aba-grupo'(el) { vGrupo(el.dataset.id, el.dataset.v); },
    async promover(el) { try { await post(`/grupos/${el.dataset.id}/promover/${el.dataset.v}`); vGrupo(el.dataset.id, 'membros'); } catch (e) { toast(e.message); } },
    async convite(el) { try { await post(`/equipes/${el.dataset.id}/responder`, { aceitar: el.dataset.v === '1' }); location.reload(); } catch (e) { toast(e.message); } },
    async 'decidir-eq'(el) { try { await post(`/equipes/${el.dataset.id}/decidir`, { confirmar: el.dataset.v === '1' }); location.reload(); } catch (e) { toast(e.message); } },
    async 'cancelar-eq'(el) { if (!confirm('Cancelar a inscrição da equipe?')) return; try { await post(`/equipes/${el.dataset.id}/cancelar`); location.reload(); } catch (e) { toast(e.message); } },
    async 'status-camp'(el) { try { await post(`/campeonatos/${el.dataset.id}/status`, { status: $('#sel-status').value }); toast('Situação atualizada.'); vCampeonato(el.dataset.id); } catch (e) { toast(e.message); } },
    convidar(el) {
      folha(`<h3>Convidar atleta</h3><div class="campo"><input id="busca-atl" placeholder="Digite o nome (mín. 2 letras)" autocomplete="off"></div><div id="achados"></div>`);
      let t; $('#busca-atl').oninput = (ev) => {
        clearTimeout(t);
        t = setTimeout(async () => {
          if (ev.target.value.trim().length < 2) return;
          try { const r = await api('/atletas?q=' + encodeURIComponent(ev.target.value.trim())); $('#achados').innerHTML = r.map((a) => pessoa(a, `<span class="acoes"><button class="btn roxo pequeno" data-acao="convidar-1" data-id="${el.dataset.id}" data-v="${a.id}">Convidar</button></span>`)).join('') || '<p class="suave">Ninguém encontrado.</p>'; } catch (e) { /* ignora */ }
        }, 300);
      };
    },
    async 'convidar-1'(el) { try { await post(`/equipes/${el.dataset.id}/convidar`, { usuario_id: Number(el.dataset.v) }); toast('Convite enviado.'); fecharFolha(); } catch (e) { toast(e.message); } },
    async localizar() { try { await pegarLocalizacao(); toast('Localização atualizada.'); rotear(); } catch (e) { /* aviso já mostrado */ } },
    async 'local-arena'() { try { await pegarLocalizacao(); if (estado.seletorArena) { estado.seletorArena.definir(estado.pos.lat, estado.pos.lng); if (estado.seletorArena.mapa) estado.seletorArena.mapa.setView([estado.pos.lat, estado.pos.lng], 17); } } catch (e) { /* aviso já mostrado */ } },
    async 'marcar-local'() { try { await pegarLocalizacao(); toast('Localização marcada.'); const i = $('#info-local'); if (i) i.textContent = 'Local marcado ✓'; } catch (e) { /* aviso já mostrado */ } },
    'f-quando'(el) { trocaFiltro('quando', el.dataset.v); },
    'f-raio'(el) { trocaFiltro('raio', Number(el.dataset.v)); },
    'f-vagas'() { trocaFiltro('com_vagas', !estado.filtros.com_vagas); },
    'f-mod'(el) { trocaFiltro('modalidades', el.dataset.v); },
    'f-modo'(el) { trocaFiltro('modo', el.dataset.v); },
    async 'divulgar-todos'(el) { try { const r = await post(`/gestao/arenas/${el.dataset.id}/divulgar-ociosos`); toast(`${r.divulgados} horário(s) publicado(s)! 🚀`); vGestao(); } catch (e) { toast(e.message); } },
    dia(el) { estado.dia = el.dataset.v; vGestao(); },
    celula(el) {
      const c = JSON.parse(el.dataset.c); const fim = (iso) => { const d = new Date(iso); d.setHours(d.getHours() + 1); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
      let corpo = `<h3>${esc(c.rotulo)} — ${c.inicio.slice(11, 16)}</h3>`;
      if (c.estado === 'livre') corpo += `<button class="btn roxo bloco" data-acao="g-divulgar" data-q="${c.quadra_id}" data-i="${c.inicio}" data-f="${fim(c.inicio)}">Divulgar para atletas próximos</button><button class="btn suave bloco" style="margin-top:8px" data-acao="g-reservar" data-q="${c.quadra_id}" data-i="${c.inicio}" data-f="${fim(c.inicio)}">Reservar</button>`;
      else if (c.estado === 'divulgado') corpo += `<button class="btn perigo bloco" data-acao="g-retirar" data-id="${c.horario_id}">Retirar divulgação</button>`;
      else if (c.atividade_id) corpo += `<a class="btn suave bloco" href="#/atividade/${c.atividade_id}" onclick="document.getElementById('folha').hidden=true">Abrir atividade</a>`;
      else corpo += `<button class="btn perigo bloco" data-acao="g-liberar" data-id="${c.reserva_id}">Liberar horário</button>`;
      folha(corpo);
    },
    async 'g-divulgar'(el) { try { await post(`/gestao/quadras/${el.dataset.q}/divulgar`, { inicio: el.dataset.i, fim: el.dataset.f }); toast('Horário divulgado! 🚀'); fecharFolha(); vGestao(); } catch (e) { toast(e.message); } },
    async 'g-reservar'(el) { const r = prompt('Nome da reserva (ex.: Futebol do João)'); if (r === null) return; try { await post(`/gestao/quadras/${el.dataset.q}/reservas`, { inicio: el.dataset.i, fim: el.dataset.f, rotulo: r || 'Reservada' }); fecharFolha(); vGestao(); } catch (e) { toast(e.message); } },
    async 'g-retirar'(el) { try { await api(`/gestao/horarios/${el.dataset.id}`, { metodo: 'DELETE' }); fecharFolha(); vGestao(); } catch (e) { toast(e.message); } },
    async 'g-liberar'(el) { try { await api(`/gestao/reservas/${el.dataset.id}`, { metodo: 'DELETE' }); fecharFolha(); vGestao(); } catch (e) { toast(e.message); } },
    'nova-quadra'(el) {
      folha(`<h3>Nova quadra ou espaço</h3><form id="f-quadra"><div class="campo"><label>Nome</label><input name="nome" required placeholder="Ex.: Quadra 04, Sala de spinning"></div><div class="campo"><label>Tipo de espaço</label><select name="tipo"><option value="quadra">Quadra</option><option value="sala">Sala</option><option value="piscina">Piscina</option><option value="campo">Campo</option><option value="outro">Outro</option></select></div><div class="duas"><div class="campo"><label>Valor da hora (R$)</label><input name="valor_hora" inputmode="decimal"></div><div class="campo"><label>Capacidade</label><input type="number" name="capacidade" min="1"></div></div><div class="campo"><label>Modalidades</label><div class="marcas">${estado.mods.map((m) => `<label><input type="checkbox" name="modalidades" value="${m.codigo}"> ${esc(m.icone)} ${esc(m.nome)}</label>`).join('')}</div></div><button class="btn roxo bloco">Cadastrar quadra</button></form>`);
      $('#f-quadra').onsubmit = async (ev) => {
        ev.preventDefault(); const f = new FormData(ev.target);
        try { await post(`/gestao/arenas/${el.dataset.id}/quadras`, { nome: f.get('nome'), tipo: f.get('tipo') || 'quadra', valor_hora: num(f.get('valor_hora')) || 0, capacidade: num(f.get('capacidade')), modalidades: f.getAll('modalidades') }); toast('Quadra cadastrada.'); fecharFolha(); vGestao(); } catch (e) { toast(e.message); }
      };
    },
    async lidas() { await post('/notificacoes/lidas', {}); vNotificacoes(); },
    ...acoesNovas,
    sair() { sair(); },
  };

  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-acao]');
    if (el && acoes[el.dataset.acao]) {
      if (el.tagName === 'A' || el.closest('a')) ev.preventDefault();
      acoes[el.dataset.acao](el, ev);
    }
  });
  $('#loc').onclick = () => acoes.localizar();

  // ------------------------------------------------------------ rotas
  function destacarAba(aba) { $$('#abas a').forEach((a) => a.classList.toggle('ativa', a.dataset.aba === aba)); }

  async function garantirSessao() {
    if (!estado.token) return false;
    if (estado.usuario && estado.mods.length) return true;
    try {
      [estado.usuario, estado.mods] = await Promise.all([api('/me'), api('/modalidades')]);
      return true;
    } catch (e) { return false; }
  }

  async function rotear() {
    fecharFolha();
    const [caminho, parametros] = (location.hash || '#/').split('?');
    const partes = caminho.replace(/^#\/?/, '').split('/');
    const [rota, arg, arg2, arg3] = partes;
    if (rota === 'google' && arg) {  // volta do login com Google: o servidor entrega o token no fragmento
      estado.token = arg; estado.usuario = null; guardar('playgo_token', arg);
      location.replace(location.pathname + '#/'); return;  // troca a entrada do histórico: o token não fica no "voltar"
    }
    if (rota === 'entrar' || rota === 'cadastro') return vEntrar(rota);
    if (rota === 'documento') { $('#topo').hidden = true; $('#abas').hidden = true; return vDocumento(arg); }
    if (!(await garantirSessao())) { location.hash = '#/entrar'; return; }
    // Portão: sem @usuario ou sem o aceite da versão atual dos termos, só estas duas telas funcionam
    const pendencia = estado.usuario.pendencia;
    if (pendencia) {
      $('#topo').hidden = true; $('#abas').hidden = true;
      if (pendencia === 'usuario') return rota === 'completar' ? vCompletar() : void (location.hash = '#/completar');
      return rota === 'aceite' ? vAceite() : void (location.hash = '#/aceite');
    }
    if (rota === 'completar' || rota === 'aceite') { location.hash = '#/'; return; }
    $('#topo').hidden = false; $('#abas').hidden = false;
    fecharMenuEu(); atualizarMenuEu();
    atualizarSino();
    destacarAba({ '': 'inicio', explorar: 'explorar', criar: 'criar', 'nova-atividade': 'criar', 'novo-grupo': 'grupos', grupos: 'grupos', grupo: 'grupos', comunidades: 'grupos', comunidade: 'grupos', 'nova-comunidade': 'grupos', campeonatos: 'campeonatos', campeonato: 'campeonatos', 'novo-campeonato': 'campeonatos' }[rota] || '');
    switch (rota) {
      case '': return vInicio();
      case 'explorar': return vExplorar(parametros);
      case 'criar': return vCriar();
      case 'nova-atividade': return vNovaAtividade(parametros);
      case 'atividade': return vAtividade(arg);
      case 'grupos': return vGrupos();
      case 'grupo': return vGrupo(arg, arg2);
      case 'novo-grupo': return vNovoGrupo();
      case 'campeonatos': return vCampeonatos();
      case 'campeonato': return arg2 === 'editar' ? vEditarCampeonato(arg) : ['chaves', 'ao-vivo', 'sorteio', 'jogo', 'telao', 'acompanhar'].includes(arg2) ? vCampSub(arg, arg2, arg3) : vCampeonato(arg);
      case 'novo-campeonato': return vNovoCampeonato();
      case 'arena': return vArena(arg);
      case 'local': return vLocal(arg);
      case 'gestao': return vGestao();
      case 'nova-arena': return vNovaArena();
      case 'perfil': return vPerfil();
      case 'notificacoes': return vNotificacoes();
      case 'feed': return vFeed(parametros);
      case 'planos': return vPlanos();
      case 'aulas': return vAulas();
      case 'profissional': return vProfissional(arg);
      case 'oferta': return vOfertaEnsino(arg);
      case 'ensino': return vEnsino();
      case 'admin-profissionais': return vAdminProfissionais();
      case 'tutoriais': return vTutoriais();
      case 'tutorial': return vTutorial(arg);
      case 'comunidades': return vComunidades();
      case 'comunidade': return vComunidade(arg);
      case 'nova-comunidade': return vNovaComunidade();
      case 'convites': return vConvites();
      case 'convite': return vConviteLink(arg);
      case 'moderacao': return vModeracao();
      case 'admin': return vAdmin();
      case 'privacidade': return vPrivacidade();
      default: return vInicio();
    }
  }

  ligarMenuEu();
  window.addEventListener('hashchange', rotear);
  setInterval(() => { if (estado.token && !document.hidden) atualizarSino(); }, 30000);
  if ('serviceWorker' in navigator && location.protocol !== 'file:') navigator.serviceWorker.register('sw.js').catch(() => {});
  rotear();
})();
