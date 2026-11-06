/* PlayGo — fila do Instagram do PlayGo (equipe de moderação). Usado pelo site e pelo PWA. */
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ROTULO = { aprovada: 'Aprovada, na fila de envio', publicada: 'Publicada', erro: 'Erro ao enviar', recusada: 'Recusada' };

  function midias(i) {
    return (i.midias || []).map((m) => m.tipo === 'video'
      ? '<video controls preload="metadata" src="' + esc(m.url) + '" style="max-width:220px;border-radius:10px"></video>'
      : '<a href="' + esc(m.url) + '" target="_blank" rel="noopener"><img src="' + esc(m.miniatura || m.url) + '" style="max-height:170px;border-radius:10px" alt=""></a>').join('');
  }

  function montar(el, o) {
    const { get, post, toast } = o;
    async function carregar() {
      let f;
      try { f = await get('/instagram/fila'); } catch (e) { el.innerHTML = '<p class="vazio">' + esc(e.message) + '</p>'; return; }
      const est = f.estado;
      let h = '<h2 style="margin:22px 0 6px">📸 Instagram do PlayGo</h2>';
      if (!est.conectado) {
        h += '<p class="aviso alerta">A conta do Instagram ainda não está conectada. ' + (est.app_configurado ? 'Um administrador conecta em <b>Administração → Instagram</b>.' : 'Falta configurar o app da Meta no servidor (ver docs/instagram.md).') + '</p>';
      } else {
        h += '<p class="suave" style="margin:0 0 10px">Conectado como <b>@' + esc(est.usuario) + '</b>. Aqui chegam as publicações que o autor marcou para o Instagram e a moderação já aprovou. Nada vai sem a sua aprovação.</p>';
      }
      h += f.itens.length ? f.itens.map((i) => '<article class="panel painel" style="margin-bottom:12px" data-ig="' + i.id + '">' +
        '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><b>' + esc(i.autor) + '</b><span class="suave">📍 ' + esc(i.local) + ' · autorizou em ' + esc((i.consentimento_em || '').replace('T', ' ')) + '</span></div>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0">' + midias(i) + '</div>' +
        '<p class="suave" style="margin:0 0 4px">Legenda que irá ao Instagram:</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f8fc;border-radius:10px;padding:10px;margin:0 0 10px;font:inherit">' + esc(i.legenda) + '</pre>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn purple roxo" data-ig-a="aprovar">📸 Aprovar e publicar</button><button class="btn perigo" data-ig-a="recusar">Não enviar</button></div></article>').join('')
        : '<p class="vazio">Nenhuma publicação aguardando para o Instagram.</p>';
      if (f.recentes.length) {
        h += '<h3 style="margin:18px 0 8px">Últimas</h3>' + f.recentes.map((i) => '<div class="pessoa" style="flex-wrap:wrap;gap:8px;padding:8px 0;border-top:1px solid #e9eaf0" data-ig="' + i.id + '">' +
          '<span><b>' + esc(i.autor) + '</b> · ' + esc(ROTULO[i.status] || i.status) + (i.erro ? ': ' + esc(i.erro) : '') + '</span>' +
          (i.link ? ' <a href="' + esc(i.link) + '" target="_blank" rel="noopener">ver no Instagram ↗</a>' : '') +
          (i.status === 'erro' ? ' <button class="btn suave pequeno" data-ig-a="aprovar">Tentar de novo</button>' : '') + '</div>').join('');
      }
      el.innerHTML = h;
    }
    el.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-ig-a]');
      if (!b) return;
      const id = b.closest('[data-ig]').dataset.ig, acao = b.dataset.igA;
      if (acao === 'recusar' && !confirm('Não enviar ao Instagram? A publicação continua no PlayGo e o autor é avisado.')) return;
      if (acao === 'aprovar' && !confirm('Publicar no Instagram do PlayGo? Depois de publicada, só dá para remover pelo próprio Instagram.')) return;
      b.disabled = true;
      try { await post('/instagram/' + id + '/' + acao, {}); if (toast) toast(acao === 'aprovar' ? 'Aprovada: será publicada em instantes.' : 'Registrado.'); } catch (e) { if (toast) toast(e.message); }
      carregar();
    });
    carregar();
  }

  window.InstagramFila = { montar };
})();
