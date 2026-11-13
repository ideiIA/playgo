/* PlayGo: escolha do enquadramento da capa da arena (site e app).
   `CapaArena.escolher(arquivo, { api, arenaId })` consulta /gestao/arenas/{id}/capa/analisar. Foto dentro do padrão (horizontal 16:9, 1200 px ou mais)
   segue direto. Fora do padrão, mostra três enquadramentos lado a lado e a arena decide; nada muda na foto além de corte, nitidez leve ou fundo desfocado.
   Resolve com "recortar", "auto" ou "inteira", ou com null se a arena desistir. Quem chama envia o arquivo com o campo `ajuste`. */
window.CapaArena = (function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DICA = 'Dica: use uma foto na horizontal, de 1600 × 900 px (proporção 16:9), com a quadra ou a fachada no centro. Fotos na vertical ou pequenas perdem qualidade.';

  const ESTILO = '.ca-fundo{position:fixed;inset:0;z-index:300;background:rgba(20,33,61,.55);display:grid;place-items:center;padding:16px}' +
    '.ca-painel{background:#fff;border-radius:24px;max-width:820px;width:100%;max-height:92vh;overflow:auto;padding:22px;box-shadow:0 16px 40px rgba(20,33,61,.3);color:#14213d;font-family:var(--fonte-texto,system-ui,sans-serif)}' +
    '.ca-painel h3{margin:0 0 6px;font:700 20px var(--fonte-titulo,inherit)}.ca-painel p{margin:6px 0;font-size:14px;line-height:1.45}' +
    '.ca-aviso{background:#fff1dc;color:#8a4f00;border-radius:12px;padding:10px 12px;font-size:14px;margin:10px 0}.ca-aviso p{margin:2px 0}' +
    '.ca-opcoes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:14px 0}' +
    '.ca-opcao{border:1.5px solid #e4e7f0;border-radius:16px;padding:10px;display:flex;flex-direction:column;gap:6px}' +
    '.ca-opcao img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px;background:#f7f8fc;display:block}' +
    '.ca-opcao b{font-size:14px}.ca-opcao span{font-size:13px;color:#667085;flex:1}' +
    '.ca-btn{border:0;border-radius:999px;padding:0 16px;min-height:44px;font:600 14px var(--fonte-texto,inherit);cursor:pointer;background:#6c4cff;color:#fff}' +
    '.ca-btn.suave{background:#ece8ff;color:#5a3df0}' +
    '@media (max-width:700px){.ca-opcoes{grid-template-columns:1fr}}';

  function estilo() {
    if (document.getElementById('ca-estilo')) return;
    const s = document.createElement('style');
    s.id = 'ca-estilo';
    s.textContent = ESTILO;
    document.head.appendChild(s);
  }

  function dialogo(r) {
    estilo();
    return new Promise((resolve) => {
      const f = document.createElement('div');
      f.className = 'ca-fundo';
      f.setAttribute('role', 'dialog');
      f.setAttribute('aria-modal', 'true');
      f.setAttribute('aria-label', 'Ajustar a foto da capa');
      const perda = r.perda_corte_pct ? ' Perde cerca de ' + r.perda_corte_pct + '% da foto.' : '';
      const op = [
        ['recortar', 'Recortar no centro', 'Corta as bordas para caber em 16:9.' + perda],
        ['auto', 'Ajuste automático', 'Corta onde há mais detalhe e dá um leve reforço de nitidez se precisar ampliar. Não muda a foto.'],
        ['inteira', 'Foto inteira', 'Mostra a foto toda, sem cortar, com um fundo desfocado dela mesma nas laterais.'],
      ];
      f.innerHTML = '<div class="ca-painel"><h3>Sua foto está fora do padrão da capa</h3>' +
        '<div class="ca-aviso">' + r.avisos.map((a) => '<p>' + esc(a) + '</p>').join('') + '</div>' +
        '<p>Escolha como quer que ela apareça. É só enquadramento: a foto continua sendo a sua.</p>' +
        '<div class="ca-opcoes">' + op.map(([k, nome, desc]) => '<div class="ca-opcao"><img src="' + esc(r.previas[k]) + '" alt="Prévia: ' + esc(nome) + '"><b>' + esc(nome) + '</b><span>' + esc(desc) + '</span><button type="button" class="ca-btn" data-ajuste="' + k + '">Usar esta</button></div>').join('') + '</div>' +
        '<p style="color:#667085;font-size:13px">' + esc(DICA) + '</p>' +
        '<button type="button" class="ca-btn suave" data-ajuste="">Escolher outra foto</button></div>';
      const fechar = (v) => { document.removeEventListener('keydown', tecla); f.remove(); resolve(v); };
      const tecla = (e) => { if (e.key === 'Escape') fechar(null); };
      document.addEventListener('keydown', tecla);
      f.addEventListener('click', (e) => {
        if (e.target === f) return fechar(null);
        const b = e.target.closest('[data-ajuste]');
        if (b) fechar(b.dataset.ajuste || null);
      });
      document.body.appendChild(f);
      const primeiro = f.querySelector('.ca-btn');
      if (primeiro) primeiro.focus();
    });
  }

  async function escolher(arquivo, o) {
    const form = new FormData();
    form.append('arquivo', arquivo);
    const r = await o.api('/gestao/arenas/' + o.arenaId + '/capa/analisar', { metodo: 'POST', form });
    if (r.dentro_do_padrao) return 'recortar';
    return dialogo(r);
  }

  return { escolher, DICA };
})();
