/* PlayGo — componente de mural (publicações + comentários), usado pelo site e pelo app (PWA).
   Mural.montar(elemento, opcoes)
     opcoes.api(caminho, {metodo, corpo, form})  -> Promise<json>   (cada lado sabe se autenticar)
     opcoes.geral        true para o feed geral; senão informe escopo ('atividade'|'campeonato'|'comunidade') e escopoId
     opcoes.posicao      {lat, lng} do perfil (se houver) — usada como sugestão de local
     opcoes.eu           {id, arroba, admin}
     opcoes.toast(msg)   aviso rápido
     opcoes.link(escopo, id, linkWeb)  mapeia o link do escopo (o app usa #/…)
     opcoes.consulta     querystring extra da listagem (ex.: lat=..&lng=..&raio=10)
     opcoes.titulo       texto do cabeçalho (opcional) */
(function (global) {
  'use strict';
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const MOTIVOS = { ofensa: 'Ofensa ou discriminação', ameaca: 'Ameaça ou assédio', sexual: 'Conteúdo sexual', dados: 'Expõe dados pessoais', spam: 'Spam ou golpe', outro: 'Outro motivo' };
  const av = (a, extra) => `<span class="mu-av ${extra || ''}">${a.foto_url ? `<img src="${esc(a.foto_url)}" alt="">` : esc(a.iniciais)}</span>`;
  const ACEITA = 'image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm';

  // Fotos de celular passam de 4,5 MB (limite do corpo da requisição no Vercel): reduz no navegador antes de enviar.
  // Só imagens JPEG/PNG/WebP; vídeo segue como está.
  function reduzir(arquivo, lado, qualidade) {
    lado = lado || 2048; qualidade = qualidade || 0.85;
    if (!/^image\/(jpeg|png|webp)$/.test(arquivo.type)) return Promise.resolve(arquivo);
    return new Promise((resolve) => {
      const url = URL.createObjectURL(arquivo);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const k = Math.min(1, lado / Math.max(img.width, img.height));
        if (k === 1 && arquivo.size < 1.5 * 1024 * 1024) return resolve(arquivo);
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        c.toBlob((b) => resolve(b ? new File([b], (arquivo.name || 'foto').replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' }) : arquivo), 'image/jpeg', qualidade);
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(arquivo); };
      img.src = url;
    });
  }

  function dialogo({ titulo, texto, motivos, placeholder, confirmar }) {
    return new Promise((resolve) => {
      const f = document.createElement('div');
      f.className = 'mu-fundo';
      f.innerHTML = `<form class="mu-dialogo"><h3>${esc(titulo)}</h3>${texto ? `<p>${esc(texto)}</p>` : ''}
        ${motivos ? `<select name="motivo">${Object.entries(motivos).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select>` : ''}
        ${placeholder ? `<textarea name="detalhe" rows="3" maxlength="500" placeholder="${esc(placeholder)}"></textarea>` : ''}
        <div class="mu-dialogo-acoes"><button type="button" class="mu-btn cinza" data-x>Cancelar</button><button class="mu-btn">${esc(confirmar || 'Confirmar')}</button></div></form>`;
      document.body.appendChild(f);
      const fim = (v) => { f.remove(); resolve(v); };
      f.addEventListener('click', (e) => { if (e.target === f || e.target.hasAttribute('data-x')) fim(null); });
      f.querySelector('form').addEventListener('submit', (e) => {
        e.preventDefault();
        const d = new FormData(e.target);
        fim({ motivo: d.get('motivo') || '', detalhe: d.get('detalhe') || '' });
      });
      const campo = f.querySelector('textarea, select');
      if (campo) campo.focus();
    });
  }

  function montar(el, o) {
    const S = { itens: [], proxima: null, escopo: null, anexos: [], loc: o.posicao ? { lat: o.posicao.lat, lng: o.posicao.lng, nome: '', origem: 'perfil' } : null, abertos: {} };
    const eu = o.eu || {};
    const link = o.link || ((e, i, l) => l);
    const ate = (m) => { (o.toast || alert)(m); };
    const caminhoLista = (antes) => {
      const q = [];
      if (o.consulta) q.push(o.consulta); // ex.: lat=..&lng=..&raio=10 (distâncias e filtro "perto de mim")
      if (antes) q.push('antes_de=' + antes);
      return (o.geral ? '/mural/geral' : `/mural/${o.escopo}/${o.escopoId}`) + (q.length ? '?' + q.join('&') : '');
    };

    async function chamar(caminho, opc) {
      try { return await o.api(caminho, opc); } catch (e) { ate(e.message || 'Não foi possível concluir.'); throw e; }
    }

    // ---------------------------------------------------------------- composição
    function localFixo() { return !o.geral && S.escopo && S.escopo.local_fixo; }
    function podePostar() { return o.geral || !S.escopo || S.escopo.pode_postar !== false; }

    function htmlComposer() {
      const fixo = localFixo();
      const loc = S.loc;
      return `<form class="mu-compor" data-form="post">
        <textarea name="texto" maxlength="2000" rows="3" placeholder="${o.geral ? 'Compartilhe o que está rolando perto de você…' : 'Escreva no mural…'}"></textarea>
        <div class="mu-anexos">${S.anexos.map((a, i) => `<span class="mu-anexo">${a.type.startsWith('video') ? '🎬' : '🖼️'} ${esc(a.name.slice(0, 22))} <button type="button" data-a="tirar-anexo" data-i="${i}" aria-label="Remover">✕</button></span>`).join('')}</div>
        <div class="mu-local">
          ${fixo ? `<span class="mu-chip">📍 Local do evento — ${esc(S.escopo.titulo)}</span>` : `
            <span class="mu-chip ${loc ? 'ok' : 'falta'}">📍 ${loc ? (loc.origem === 'perfil' ? 'Usando a localização do seu perfil' : 'Localização marcada') : 'Marque o local da publicação'}</span>
            <button type="button" class="mu-btn cinza peq" data-a="gps">Usar onde estou agora</button>
            <input class="mu-nome-local" name="local_nome" maxlength="200" placeholder="Nome do local (ex.: Parque das Nações)">`}
        </div>
        <div class="mu-barra">
          <label class="mu-btn cinza peq">📷 Foto ou vídeo<input type="file" data-a="arquivos" accept="${ACEITA}" multiple hidden></label>
          ${S.escopo && S.escopo.pode_replicar ? '<label class="mu-rep"><input type="checkbox" name="replicar"> Mostrar também no feed geral</label>' : ''}
          ${S.instagram ? '<label class="mu-rep mu-ig"><input type="checkbox" name="instagram"> 📸 Compartilhar no Instagram do PlayGo</label>' : ''}
          <button class="mu-btn" data-enviar>Publicar</button>
        </div>
        ${S.instagram ? '<p class="mu-nota mu-ig-nota">Para ir ao Instagram: precisa ter foto ou vídeo e ser do feed geral. Passa pela aprovação da equipe do PlayGo e, depois de publicada lá, só a equipe consegue removê-la.</p>' : ''}
        <p class="mu-nota">Toda publicação mostra o local e passa por análise automática antes de aparecer para os outros. Você pode excluir na primeira hora; depois, só um moderador oculta.</p>
      </form>`;
    }

    // ---------------------------------------------------------------- publicação
    function seloIg(p) {
      const i = p.instagram;
      if (!i) return '';
      if (i.status === 'publicada') return `<div class="mu-aviso ig">📸 ${i.link ? `<a href="${esc(i.link)}" target="_blank" rel="noopener">Publicada no Instagram do PlayGo ↗</a>` : 'Publicada no Instagram do PlayGo'}</div>`;
      if (i.status === 'aguardando') return '<div class="mu-aviso ig">📸 Aguardando a equipe do PlayGo para ir ao Instagram.</div>';
      if (i.status === 'aprovada') return '<div class="mu-aviso ig">📸 Aprovada: vai ao Instagram do PlayGo em instantes.</div>';
      if (i.status === 'recusada') return '<div class="mu-aviso ig">📸 Não foi escolhida para o Instagram (continua aqui no PlayGo).</div>';
      if (i.status === 'erro') return '<div class="mu-aviso ig">📸 Houve um problema ao enviar ao Instagram; a equipe vai tentar de novo.</div>';
      return '';
    }

    function selo(p) { return seloBase(p) + seloIg(p); }

    function seloBase(p) {
      if (p.status === 'em_analise') return `<div class="mu-aviso em">⏳ Em análise — só você vê por enquanto.${eu.admin && p.motivo_analise ? ' <b>Motivo:</b> ' + esc(p.motivo_analise) : ''}</div>`;
      if (p.status === 'oculta') return `<div class="mu-aviso oc">🚫 Oculta por ${p.oculta_por_papel === 'admin' ? 'um administrador' : 'um moderador'}${p.oculta_motivo ? ': ' + esc(p.oculta_motivo) : ''}</div>`;
      if (p.status === 'rejeitada') return '<div class="mu-aviso oc">❌ Não aprovada: viola os termos de uso e não é exibida.</div>';
      return '';
    }

    function midias(p) {
      if (!p.midias.length) return '';
      return `<div class="mu-midias n${Math.min(p.midias.length, 4)}">${p.midias.map((m) => m.tipo === 'video'
        ? `<video controls preload="metadata" playsinline src="${esc(m.url)}"></video>`
        : `<a href="${esc(m.url)}" target="_blank" rel="noopener"><img loading="lazy" alt="Foto da publicação" src="${esc(m.miniatura || m.url)}"></a>`).join('')}</div>`;
    }

    function acoes(p) {
      const b = [];
      if (p.status === 'publicada') b.push(`<button data-a="curtir" data-id="${p.id}" class="${p.curtiu ? 'mu-curtiu' : ''}" aria-pressed="${p.curtiu ? 'true' : 'false'}">${p.curtiu ? '❤️' : '🤍'} ${p.n_curtidas || 'Curtir'}</button>`);
      if (p.status === 'publicada') b.push(`<button data-a="comentarios" data-id="${p.id}">💬 ${p.n_comentarios ? p.n_comentarios + ' comentário' + (p.n_comentarios > 1 ? 's' : '') : 'Comentar'}</button>`);
      if (p.pode_editar) b.push(`<button data-a="editar" data-id="${p.id}">Editar</button>`);
      if (p.pode_excluir) b.push(`<button data-a="excluir" data-id="${p.id}" title="Você pode excluir até a primeira hora">Excluir</button>`);
      if (p.pode_ocultar) b.push(`<button data-a="ocultar" data-id="${p.id}">Ocultar</button>`);
      if (p.pode_restaurar) b.push(`<button data-a="restaurar" data-id="${p.id}">Restaurar</button>`);
      if (p.pode_denunciar) b.push(`<button data-a="denunciar" data-id="${p.id}">Denunciar</button>`);
      return b.join('');
    }

    function card(p) {
      const origem = p.escopo !== 'geral' ? `<a class="mu-origem" href="${esc(link(p.escopo, p.escopo_id, p.escopo_link))}">${p.escopo === 'atividade' ? '🏅' : p.escopo === 'campeonato' ? '🏆' : '👥'} ${esc(p.escopo_titulo)}</a>${p.replicada && o.geral ? '' : p.replicada ? ' <span class="mu-tag">também no feed geral</span>' : ''}` : '';
      const mapa = `https://www.openstreetmap.org/?mlat=${p.local.latitude}&mlon=${p.local.longitude}#map=17/${p.local.latitude}/${p.local.longitude}`;
      return `<article class="mu-post ${p.status !== 'publicada' ? 'st-' + p.status : ''}" data-pid="${p.id}">
        <header>${av(p.autor)}<div><b>${esc(p.autor.arroba)}</b>${origem ? ' · ' + origem : ''}
          <div class="mu-meta">${esc(p.quando)}${p.editado ? ` · <span class="mu-ed" title="O texto original fica guardado">${esc(p.editado)}</span>` : ''}</div></div></header>
        ${selo(p)}
        <div class="mu-texto" data-texto>${esc(p.texto).replace(/\n/g, '<br>')}</div>
        ${midias(p)}
        <a class="mu-loc" href="${mapa}" target="_blank" rel="noopener">📍 ${esc(p.local.nome)}${p.local.distancia ? ' · ' + esc(p.local.distancia) : ''}</a>
        <div class="mu-acoes">${acoes(p)}</div>
        <div class="mu-coments" data-coments hidden></div>
      </article>`;
    }

    // ---------------------------------------------------------------- comentários
    function cardComentario(c, pid) {
      const sel = c.status === 'em_analise' ? '<span class="mu-tag am">⏳ em análise</span>' : c.status === 'oculta' ? `<span class="mu-tag vm">🚫 oculto${c.oculta_motivo ? ': ' + esc(c.oculta_motivo) : ''}</span>` : c.status === 'rejeitada' ? '<span class="mu-tag vm">não aprovado</span>' : '';
      const b = [];
      if (c.pode_editar) b.push(`<button data-a="c-editar" data-id="${c.id}">Editar</button>`);
      if (c.pode_excluir) b.push(`<button data-a="c-excluir" data-id="${c.id}" data-p="${pid}">Excluir</button>`);
      if (c.pode_ocultar) b.push(`<button data-a="c-ocultar" data-id="${c.id}" data-p="${pid}">Ocultar</button>`);
      if (c.pode_restaurar) b.push(`<button data-a="c-restaurar" data-id="${c.id}" data-p="${pid}">Restaurar</button>`);
      if (c.pode_denunciar) b.push(`<button data-a="c-denunciar" data-id="${c.id}">Denunciar</button>`);
      return `<div class="mu-com" data-cid="${c.id}">${av(c.autor, "peq")}<div class="mu-com-corpo"><b>${esc(c.autor.arroba)}</b> ${sel}
        <span class="mu-com-texto" data-ctexto>${esc(c.texto).replace(/\n/g, '<br>')}</span>
        <div class="mu-meta">${esc(c.quando)}${c.editado ? ` · ${esc(c.editado)}` : ''} ${b.length ? ' · ' + b.join(' · ') : ''}</div></div></div>`;
    }

    async function carregarComentarios(pid, caixa) {
      const lista = await chamar(`/publicacoes/${pid}/comentarios`);
      const post = S.itens.find((x) => x.id === pid);
      caixa.innerHTML = `<div class="mu-com-lista">${lista.map((c) => cardComentario(c, pid)).join('') || '<p class="mu-vazio">Nenhum comentário ainda.</p>'}</div>
        ${post && post.pode_comentar ? `<form class="mu-com-form" data-form="comentar" data-id="${pid}"><input name="texto" maxlength="1000" placeholder="Escreva um comentário…" autocomplete="off" required><button class="mu-btn peq">Enviar</button></form>` : ''}`;
    }

    // ---------------------------------------------------------------- render e dados
    function desenhar() {
      const lista = el.querySelector('[data-lista]');
      lista.innerHTML = S.itens.length ? S.itens.map(card).join('') : '<p class="mu-vazio">Ainda não há publicações por aqui. Seja o primeiro!</p>';
      el.querySelector('[data-mais]').hidden = !S.proxima;
      Object.keys(S.abertos).forEach((pid) => {
        const caixa = lista.querySelector(`[data-pid="${pid}"] [data-coments]`);
        if (caixa) { caixa.hidden = false; carregarComentarios(Number(pid), caixa).catch(() => {}); }
      });
    }

    function substituir(p) {
      const i = S.itens.findIndex((x) => x.id === p.id);
      if (i >= 0) S.itens[i] = p;
      desenhar();
    }

    async function carregar(mais) {
      const r = await chamar(caminhoLista(mais ? S.proxima : null));
      if (r.escopo) S.escopo = r.escopo;
      if (r.instagram !== undefined) S.instagram = r.instagram;
      S.itens = mais ? S.itens.concat(r.itens) : r.itens;
      S.proxima = r.proxima;
    }

    async function acompanhar(id, tentativa) {
      // A análise automática roda em segundo plano: confere até virar 'publicada' (ou ficar em análise de verdade)
      if (tentativa > 8) return;
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const p = await o.api(`/publicacoes/${id}`);
        const i = S.itens.findIndex((x) => x.id === id);
        if (i < 0) return;
        const mudou = S.itens[i].status !== p.status;
        S.itens[i] = p;
        if (mudou) desenhar();
        if (p.status === 'em_analise' && !p.motivo_analise) acompanhar(id, tentativa + 1);
      } catch (e) { /* sem rede: para de acompanhar */ }
    }

    async function iniciar() {
      el.innerHTML = '<div class="mu"><p class="mu-vazio">Carregando…</p></div>';
      try { await carregar(false); } catch (e) {
        el.innerHTML = `<div class="mu"><p class="mu-vazio">${esc(e.message || 'Não foi possível abrir o mural.')}</p></div>`;
        return;
      }
      el.innerHTML = `<div class="mu">${o.titulo ? `<h2 class="mu-titulo">${esc(o.titulo)}</h2>` : ''}${podePostar() ? `<div data-composer>${htmlComposer()}</div>` : '<p class="mu-nota">Entre para publicar neste mural.</p>'}<div data-lista></div><button class="mu-btn cinza mu-mais" data-mais hidden>Ver publicações mais antigas</button></div>`;
      desenhar();
    }

    function redesenharComposer() {
      const c = el.querySelector('[data-composer]');
      if (!c) return;
      const texto = c.querySelector('textarea').value;
      const nome = (c.querySelector('[name=local_nome]') || {}).value;
      const rep = (c.querySelector('[name=replicar]') || {}).checked;
      const ig = (c.querySelector('[name=instagram]') || {}).checked;
      c.innerHTML = htmlComposer();
      if (ig && c.querySelector('[name=instagram]')) c.querySelector('[name=instagram]').checked = true;
      c.querySelector('textarea').value = texto;
      if (nome && c.querySelector('[name=local_nome]')) c.querySelector('[name=local_nome]').value = nome;
      if (rep && c.querySelector('[name=replicar]')) c.querySelector('[name=replicar]').checked = true;
    }

    // ---------------------------------------------------------------- eventos
    el.addEventListener('change', (ev) => {
      if (ev.target.matches('[data-a=arquivos]')) {
        S.anexos = S.anexos.concat([...ev.target.files]).slice(0, 4);
        redesenharComposer();
      }
    });

    el.addEventListener('submit', async (ev) => {
      const f = ev.target.closest('form');
      if (!f) return;
      ev.preventDefault();
      if (f.dataset.form === 'post') {
        const texto = f.texto.value.trim();
        if (!texto && !S.anexos.length) return ate('Escreva algo ou anexe uma foto ou vídeo.');
        const fd = new FormData();
        fd.append('escopo', o.geral ? 'geral' : o.escopo);
        if (!o.geral) fd.append('escopo_id', o.escopoId);
        fd.append('texto', texto);
        if (!localFixo()) {
          if (!S.loc) return ate('Marque o local da publicação: use sua localização.');
          fd.append('latitude', S.loc.lat); fd.append('longitude', S.loc.lng);
          fd.append('local_nome', f.local_nome ? f.local_nome.value : '');
        }
        fd.append('replicar_geral', f.replicar && f.replicar.checked ? 'true' : 'false');
        fd.append('compartilhar_instagram', f.instagram && f.instagram.checked ? 'true' : 'false');
        for (const a of S.anexos) fd.append('arquivos', await reduzir(a));
        const botao = f.querySelector('[data-enviar]');
        botao.disabled = true; botao.textContent = 'Enviando…';
        try {
          const p = await o.api('/publicacoes', { metodo: 'POST', form: fd });
          S.itens.unshift(p); S.anexos = [];
          redesenharComposer(); el.querySelector('[data-composer] textarea').value = '';
          desenhar(); ate('Publicação enviada. Ela passa por uma análise rápida antes de aparecer para os outros.');
          acompanhar(p.id, 0);
        } catch (e) { ate(e.message || 'Não foi possível publicar.'); botao.disabled = false; botao.textContent = 'Publicar'; }
      } else if (f.dataset.form === 'comentar') {
        const pid = Number(f.dataset.id);
        try {
          await o.api(`/publicacoes/${pid}/comentarios`, { metodo: 'POST', corpo: { texto: f.texto.value } });
          const post = S.itens.find((x) => x.id === pid); if (post) post.n_comentarios += 0;
          ate('Comentário enviado — passa por uma análise rápida antes de aparecer para todos.');
          carregarComentarios(pid, f.closest('[data-coments]'));
        } catch (e) { ate(e.message); }
      } else if (f.dataset.form === 'editar-post' || f.dataset.form === 'editar-comentario') {
        const ehPost = f.dataset.form === 'editar-post';
        try {
          const r = await o.api(`/${ehPost ? 'publicacoes' : 'comentarios'}/${f.dataset.id}`, { metodo: 'PATCH', corpo: { texto: f.texto.value } });
          if (ehPost) { substituir(r); acompanhar(r.id, 0); } else carregarComentarios(Number(f.dataset.p), f.closest('[data-coments]'));
        } catch (e) { ate(e.message); }
      }
    });

    el.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-a]');
      if (!b || b.tagName === 'INPUT') return;
      const id = Number(b.dataset.id);
      const a = b.dataset.a;
      try {
        if (a === 'mais') return;
        if (a === 'tirar-anexo') { S.anexos.splice(Number(b.dataset.i), 1); redesenharComposer(); }
        else if (a === 'gps') {
          if (!navigator.geolocation) return ate('Seu aparelho não informa a localização.');
          navigator.geolocation.getCurrentPosition((p) => { S.loc = { lat: p.coords.latitude, lng: p.coords.longitude, nome: '', origem: 'gps' }; redesenharComposer(); }, () => ate('Não consegui pegar sua localização. Permita o acesso no navegador.'), { timeout: 10000 });
        } else if (a === 'curtir') {
          const r = await o.api(`/publicacoes/${id}/curtir`, { metodo: 'POST', corpo: {} });
          const post = S.itens.find((x) => x.id === id);
          if (post) { post.curtiu = r.curtiu; post.n_curtidas = r.n_curtidas; desenhar(); }
        } else if (a === 'comentarios') {
          const caixa = b.closest('article').querySelector('[data-coments]');
          if (caixa.hidden) { S.abertos[id] = true; caixa.hidden = false; await carregarComentarios(id, caixa); } else { delete S.abertos[id]; caixa.hidden = true; }
        } else if (a === 'editar') {
          const art = b.closest('article'); const p = S.itens.find((x) => x.id === id);
          art.querySelector('[data-texto]').innerHTML = `<form data-form="editar-post" data-id="${id}"><textarea name="texto" rows="4" maxlength="2000">${esc(p.texto)}</textarea><div class="mu-dialogo-acoes"><button type="button" class="mu-btn cinza peq" data-a="cancelar-edicao">Cancelar</button><button class="mu-btn peq">Salvar</button></div><p class="mu-nota">A edição será marcada como “editada” e o texto novo passa pela análise de novo.</p></form>`;
        } else if (a === 'cancelar-edicao') { desenhar(); }
        else if (a === 'excluir') {
          if (!confirm('Excluir esta publicação? Você só pode excluir na primeira hora; depois, apenas um moderador pode ocultar.')) return;
          await o.api(`/publicacoes/${id}`, { metodo: 'DELETE' }); S.itens = S.itens.filter((x) => x.id !== id); desenhar(); ate('Publicação excluída.');
        } else if (a === 'ocultar') {
          const d = await dialogo({ titulo: 'Ocultar publicação', texto: 'Ela some para todos os lugares onde aparece e o autor é avisado. Fica guardada pelo prazo legal.', placeholder: 'Motivo (o autor vai ver)', confirmar: 'Ocultar' });
          if (d) { substituir(await o.api(`/publicacoes/${id}/ocultar`, { metodo: 'POST', corpo: { motivo: d.detalhe } })); if (!eu.admin) { /* o moderador continua vendo para restaurar */ } }
        } else if (a === 'restaurar') { substituir(await o.api(`/publicacoes/${id}/restaurar`, { metodo: 'POST', corpo: {} })); }
        else if (a === 'denunciar' || a === 'c-denunciar') {
          const d = await dialogo({ titulo: 'Denunciar', texto: 'Quem cuida deste mural e os administradores vão avaliar.', motivos: MOTIVOS, placeholder: 'Conte o que aconteceu (opcional)', confirmar: 'Denunciar' });
          if (d) { await o.api(`/${a === 'denunciar' ? 'publicacoes' : 'comentarios'}/${id}/denunciar`, { metodo: 'POST', corpo: { motivo: d.motivo, detalhe: d.detalhe } }); ate('Denúncia enviada. Obrigado por ajudar.'); }
        } else if (a === 'c-editar') {
          const com = b.closest('.mu-com'); const atual = com.querySelector('[data-ctexto]').innerText;
          const pid = Number(b.closest('article').dataset.pid);
          com.querySelector('[data-ctexto]').innerHTML = `<form data-form="editar-comentario" data-id="${id}" data-p="${pid}" class="mu-com-form"><input name="texto" maxlength="1000" value="${esc(atual)}" required><button class="mu-btn peq">Salvar</button></form>`;
        } else if (a === 'c-excluir') {
          if (!confirm('Excluir este comentário? Só é possível na primeira hora.')) return;
          await o.api(`/comentarios/${id}`, { metodo: 'DELETE' }); carregarComentarios(Number(b.dataset.p), b.closest('[data-coments]'));
        } else if (a === 'c-ocultar') {
          const d = await dialogo({ titulo: 'Ocultar comentário', placeholder: 'Motivo (o autor vai ver)', confirmar: 'Ocultar' });
          if (d) { await o.api(`/comentarios/${id}/ocultar`, { metodo: 'POST', corpo: { motivo: d.detalhe } }); carregarComentarios(Number(b.dataset.p), b.closest('[data-coments]')); }
        } else if (a === 'c-restaurar') { await o.api(`/comentarios/${id}/restaurar`, { metodo: 'POST', corpo: {} }); carregarComentarios(Number(b.dataset.p), b.closest('[data-coments]')); }
      } catch (e) { ate(e.message || 'Não foi possível concluir.'); }
    });
    el.addEventListener('click', async (ev) => { if (ev.target.closest('[data-mais]')) { try { await carregar(true); desenhar(); } catch (e) { /* aviso já mostrado */ } } });

    iniciar();
    return { recarregar: iniciar };
  }

  global.Mural = { montar, dialogo, esc, reduzir };
})(window);
