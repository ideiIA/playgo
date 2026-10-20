/* Ícones Lucide no lugar dos emojis de interface (site e app).
   Troca, só dentro de botões, etiquetas, abas, menus e títulos de seção, cada emoji de interface pelo ícone do sprite /static/lucide.svg.
   Não mexe em: conteúdo de pessoas (mural, nomes em cartões), emojis de esporte (dado de cada modalidade), campos de texto e avisos temporários.
   Também expõe window.ic(nome, classe) para montar ícones em HTML/JS. Para um ícone novo: scripts/gerar_icones.py. */
(function () {
  const SPRITE = '/static/lucide.svg#';
  const svg = (nome, cls) => '<svg class="i ' + (cls || '') + '" aria-hidden="true"><use href="' + SPRITE + nome + '"/></svg>';
  window.ic = svg;

  // emoji → [ícone, classe extra]. '@vivo' vira a bolinha vermelha pulsante de "ao vivo".
  const MAPA = {
    '📍': ['map-pin'], '🏆': ['trophy'], '🔴': ['@vivo'], '🏟': ['building-2'], '🔥': ['flame'], '👥': ['users'], '🎥': ['video'], '✅': ['circle-check', 'ok'],
    '✓': ['check'], '📺': ['tv'], '📸': ['camera'], '📷': ['camera'], '✉': ['mail'], '⏳': ['hourglass'], '💬': ['message-circle'], '🕒': ['clock'],
    '📘': ['book-open'], '🏁': ['flag'], '⏱': ['timer'], '★': ['star', 'cheio'], '💳': ['credit-card'], '🔒': ['lock'], '🎯': ['target'], '✏': ['pencil'],
    '🎲': ['dices'], '🎟': ['ticket'], '⚠': ['triangle-alert', 'aviso'], '👑': ['crown', 'coroa'], '📞': ['phone'], '📅': ['calendar-days'], '🛡': ['shield-check'],
    '📄': ['file-text'], '✕': ['x'], '🧭': ['compass'], '⚡': ['zap'], '📱': ['smartphone'], '💻': ['laptop'], '🔑': ['key-round'], '🔄': ['refresh-cw'],
    '🔗': ['link'], '👁': ['eye'], '🤖': ['bot'], '🎁': ['gift'], '🧾': ['receipt'], '✨': ['sparkles'], '🚀': ['rocket'], '🌳': ['trees'], '🏋': ['dumbbell'],
    '🛍': ['shopping-bag'], '🎉': ['party-popper'], '📌': ['pin'], '📖': ['book-open'], '👍': ['thumbs-up'], '❤': ['heart', 'cheio'], '🥇': ['medal', 'ouro'],
    '🥈': ['medal', 'prata'], '🥉': ['medal', 'bronze'], '🔔': ['bell'], '🗺': ['map'], '🛰': ['satellite'], '⛶': ['maximize'], '🎬': ['clapperboard'],
    '🖼': ['image'], '🚫': ['ban'], '⚙': ['settings'], '📢': ['megaphone'], '🧑‍🤝‍🧑': ['handshake'], '🔓': ['lock-open'], '📝': ['file-pen'], '⭐': ['star', 'cheio'],
  };
  const chaves = Object.keys(MAPA).sort((a, b) => b.length - a.length);
  const RE = new RegExp('(' + chaves.map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\uFE0F?', 'g');

  // onde vale trocar (interface) e onde não (conteúdo de pessoas, campos, avisos temporários)
  const ALVO = '.btn, .tag, .chip, .nav, #abas, .topo-acoes, .ch-abas, .tut-abas, .alternar, .abas-int, .abas, .section-title, .secao, .panel > h2, .painel > h3, .painel > h2, ' +
    'summary, .item-menu, #menu-pop, .tut-selos, .tut-cap > header, .tut-cartao h3, .tut-hero h1, .aviso, .stat, th, .fato small, .livebar, .ch-btn, .pgm-botao, label, .legenda, .tl-vivo';
  const FORA = '.mu, .mu-dialogo, #toast, textarea, input, select, option, script, style, svg, .card h3, .card .meta, [data-sem-icones], .nome';

  function trocar(no) {
    const el = no.parentElement;
    if (!el || !RE.test(no.nodeValue)) { RE.lastIndex = 0; return; }
    RE.lastIndex = 0;
    if (el.closest(FORA) || !el.closest(ALVO)) return;
    const frag = document.createDocumentFragment();
    let ultimo = 0;
    const txt = no.nodeValue;
    txt.replace(RE, (m, g, pos) => {
      if (pos > ultimo) frag.appendChild(document.createTextNode(txt.slice(ultimo, pos)));
      const [nome, cls] = MAPA[g];
      const t = document.createElement('template');
      t.innerHTML = nome === '@vivo' ? '<span class="ponto-vivo" aria-hidden="true"></span>' : svg(nome, cls);
      frag.appendChild(t.content.firstChild);
      ultimo = pos + m.length;
      return m;
    });
    if (ultimo < txt.length) frag.appendChild(document.createTextNode(txt.slice(ultimo)));
    no.replaceWith(frag);
  }

  function varrer(raiz) {
    if (raiz.nodeType === 3) return trocar(raiz);
    if (raiz.nodeType !== 1 || raiz.closest('svg')) return;
    const w = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    const nos = [];
    while (w.nextNode()) nos.push(w.currentNode);
    nos.forEach(trocar);
  }

  function iniciar() {
    varrer(document.body);
    new MutationObserver((muts) => {
      for (const m of muts) {
        m.addedNodes.forEach((n) => varrer(n));
        if (m.type === 'characterData') trocar(m.target);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  if (document.body) iniciar(); else document.addEventListener('DOMContentLoaded', iniciar);
})();
