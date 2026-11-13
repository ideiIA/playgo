/* PlayGo: galeria de fotos em tela cheia (site e app). Qualquer link com `data-galeria` abre a foto num visualizador com anterior/próxima, contador e fechar.
   As fotos seguem a ordem da página (na arena: a capa primeiro, depois "Conheça nosso espaço"); um contêiner `data-galeria-grupo` limita o conjunto. Teclado: setas e Esc. Celular: arrastar para o lado troca de foto; tocar fora fecha.
   Funciona com conteúdo desenhado depois (delegação no document), então o app não precisa chamar nada. Sem o JS, o link abre a foto normalmente. */
(function () {
  const ESTILO = '.gl-fundo{position:fixed;inset:0;z-index:400;background:rgba(10,16,32,.92);display:flex;flex-direction:column;color:#fff;font-family:var(--fonte-texto,system-ui,sans-serif);touch-action:pan-y}' +
    '.gl-topo{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;flex:none}' +
    '.gl-contador{font-size:15px;font-weight:600;opacity:.9}' +
    '.gl-palco{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:0 56px}' +
    '.gl-img{max-width:100%;max-height:100%;object-fit:contain;border-radius:12px;user-select:none;-webkit-user-drag:none}' +
    '.gl-btn{border:0;background:rgba(255,255,255,.16);color:#fff;width:48px;height:48px;border-radius:50%;display:grid;place-items:center;cursor:pointer;font-size:24px;line-height:1}' +
    '.gl-btn:hover,.gl-btn:focus-visible{background:rgba(255,255,255,.3);outline:2px solid #fff;outline-offset:2px}' +
    '.gl-ant,.gl-prox{position:absolute;top:50%;transform:translateY(-50%)}.gl-ant{left:8px}.gl-prox{right:8px}' +
    '.gl-leg{flex:none;text-align:center;font-size:13px;opacity:.75;padding:10px 16px 16px}' +
    '@media (max-width:700px){.gl-palco{padding:0 8px}.gl-ant,.gl-prox{top:auto;bottom:8px;transform:none}.gl-leg{padding-bottom:64px}}';

  function estilo() {
    if (document.getElementById('gl-estilo')) return;
    const s = document.createElement('style');
    s.id = 'gl-estilo';
    s.textContent = ESTILO;
    document.head.appendChild(s);
  }

  function abrir(fotos, inicio) {
    if (!fotos.length) return;
    estilo();
    let i = Math.min(Math.max(inicio || 0, 0), fotos.length - 1);
    const anterior = document.activeElement;
    const f = document.createElement('div');
    f.className = 'gl-fundo';
    f.setAttribute('role', 'dialog');
    f.setAttribute('aria-modal', 'true');
    f.setAttribute('aria-label', 'Galeria de fotos');
    f.innerHTML = '<div class="gl-topo"><span class="gl-contador" aria-live="polite"></span><button type="button" class="gl-btn gl-fechar" aria-label="Fechar">✕</button></div>' +
      '<div class="gl-palco"><button type="button" class="gl-btn gl-ant" aria-label="Foto anterior">‹</button><img class="gl-img" alt=""><button type="button" class="gl-btn gl-prox" aria-label="Próxima foto">›</button></div>' +
      '<div class="gl-leg"></div>';
    const img = f.querySelector('.gl-img'), cont = f.querySelector('.gl-contador'), leg = f.querySelector('.gl-leg');
    const ant = f.querySelector('.gl-ant'), prox = f.querySelector('.gl-prox');
    const mostrar = (n) => {
      i = (n + fotos.length) % fotos.length;
      img.src = fotos[i].url;
      img.alt = fotos[i].alt || 'Foto ' + (i + 1);
      cont.textContent = (i + 1) + ' de ' + fotos.length;
      leg.textContent = fotos[i].alt || '';
      ant.hidden = prox.hidden = fotos.length < 2;
      [i + 1, i - 1].forEach((k) => { const p = new Image(); p.src = fotos[(k + fotos.length) % fotos.length].url; });  // já deixa as vizinhas carregando
    };
    const fechar = () => {
      document.removeEventListener('keydown', tecla);
      document.body.style.overflow = '';
      f.remove();
      if (anterior && anterior.focus) anterior.focus();
    };
    const tecla = (e) => {
      if (e.key === 'Escape') fechar();
      else if (e.key === 'ArrowRight') mostrar(i + 1);
      else if (e.key === 'ArrowLeft') mostrar(i - 1);
    };
    ant.onclick = () => mostrar(i - 1);
    prox.onclick = () => mostrar(i + 1);
    f.querySelector('.gl-fechar').onclick = fechar;
    f.addEventListener('click', (e) => { if (e.target === f || e.target.classList.contains('gl-palco') || e.target.classList.contains('gl-leg')) fechar(); });
    let x0 = null;  // arrastar para o lado (celular)
    f.addEventListener('touchstart', (e) => { x0 = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
    f.addEventListener('touchend', (e) => {
      if (x0 === null) return;
      const dx = e.changedTouches[0].clientX - x0;
      x0 = null;
      if (Math.abs(dx) > 50) mostrar(dx < 0 ? i + 1 : i - 1);
    });
    document.addEventListener('keydown', tecla);
    document.body.style.overflow = 'hidden';
    document.body.appendChild(f);
    mostrar(i);
    f.querySelector('.gl-fechar').focus();
  }

  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('[data-galeria]');
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
    const grupo = a.closest('[data-galeria-grupo]') || document;
    const itens = [...grupo.querySelectorAll('[data-galeria]')];
    ev.preventDefault();
    abrir(itens.map((el) => ({ url: el.getAttribute('href') || el.dataset.galeria, alt: el.dataset.legenda || (el.querySelector('img') && el.querySelector('img').alt) || '' })), itens.indexOf(a));
  });

  window.Galeria = { abrir };
})();
