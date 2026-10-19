/* PlayGo — Telão e transmissão ao vivo (YouTube). Usado pelo PWA.
   `get(caminho)` e `post(caminho, corpo)` são a API de quem usa. Sem conexão aberta: a tela consulta a API de poucos em poucos
   segundos enquanto estiver aberta e para sozinha quando sai da tela (el deixa de estar no documento). */
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const quando = (iso) => (iso ? new Date(iso).toLocaleString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : 'a definir');
  const diaHora = (iso) => (iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) + ' ' + iso.slice(11, 16) : '');

  // ------------------------------------------------------------ espectador (qualquer pessoa logada)
  const pontoHtml = (j) => (j.ponto ? '<div class="tl-ponto"><small>' + esc(j.ponto.rotulo) + '</small><span>' + esc(j.ponto.a) + '</span><span class="tl-sep">–</span><span>' + esc(j.ponto.b) + '</span></div>' : '');

  function placarHtml(j, comVideo) {
    const g = j.grande;
    const vence = (l) => (g[l] > g[l === 'a' ? 'b' : 'a'] ? ' vence' : '');
    const lado = (l, eq) => '<div class="tl-lado' + vence(l) + '"><div class="tl-nome">' + esc(eq.nome) + '</div><div class="tl-num">' + esc(g[l]) + '</div>' +
      (g.sets_a != null && !comVideo ? '<div class="tl-sets">' + esc(g['sets_' + l]) + ' set' + (g['sets_' + l] === 1 ? '' : 's') + '</div>' : '') + '</div>';
    const fechados = (j.sets || []).filter((x) => x.encerrado).map((x) => x.a + '–' + x.b).join(' · ');
    return '<div class="tl-rodada">' + esc([j.rodada_nome, j.local, g.rotulo, fechados && !comVideo ? 'Sets anteriores ' + fechados : ''].filter(Boolean).join(' · ')) + '</div>' +
      '<div class="tl-placar">' + lado('a', j.a) + '<div class="tl-x">×</div>' + lado('b', j.b) + '</div>' + pontoHtml(j) +
      (comVideo && g.sets_a != null ? '<div class="tl-sets grande">Sets ' + esc(g.sets_a) + ' × ' + esc(g.sets_b) + '</div>' : '');
  }

  const linhaMini = (j, fim) => '<div class="tl-linha"><span>' + esc(j.a.nome) + ' × ' + esc(j.b.nome) + '</span><em>' + (fim ? esc(j.placar_a) + ' × ' + esc(j.placar_b) : esc(quando(j.inicio_previsto))) + '</em></div>';

  function montarAcompanhar(el, o) {
    const { get, id } = o;
    el.innerHTML = '<div class="tl" data-tl>' +
      '<div class="tl-topo"><div><h2 data-t="titulo">Carregando…</h2><small data-t="sub"></small></div><a class="tl-canal off" data-t="canal" target="_blank" rel="noopener" hidden>▶ YouTube</a><div class="tl-vivo parado" data-t="vivo"><i></i><span data-t="vivo-txt">AGUARDANDO</span></div></div>' +
      '<div class="tl-video" data-t="video" hidden></div>' +
      '<div class="tl-principal" data-t="principal"></div>' +
      '<div class="tl-pontos" data-t="pontos"></div>' +
      '<div class="tl-rodape" data-t="rodape"></div>' +
      '<div class="tl-barra"><label class="tl-escolha">Jogo <select data-t="sel"><option value="">Automático (o jogo em destaque)</option></select></label><button class="btn suave pequeno" type="button" data-t="cheia">⛶ Tela cheia</button></div>' +
      '<p class="tl-conexao" data-t="conexao"></p></div>';
    const raiz = el.querySelector('[data-tl]');
    const t = (n) => el.querySelector('[data-t="' + n + '"]');
    let D = null, idx = 0, ultimaTroca = Date.now(), escolhido = '', videoAtual = null, luz = null;

    function desenhar() {
      if (!D) return;
      const c = D.campeonato, jogos = D.jogos;
      t('titulo').textContent = c.nome;
      t('sub').textContent = c.icone + ' ' + c.modalidade;
      const yt = t('canal');
      yt.hidden = !D.canal;
      if (D.canal) { yt.classList.toggle('off', !D.canal.ao_vivo); yt.textContent = D.canal.ao_vivo ? '▶ Assistir no YouTube · 🔴 no ar' : '▶ YouTube · sem transmissão'; if (D.canal.ao_vivo) yt.href = D.canal.url; else yt.removeAttribute('href'); }
      const aoVivo = jogos.some((j) => j.status === 'ao_vivo');
      t('vivo').classList.toggle('parado', !aoVivo);
      t('vivo-txt').textContent = aoVivo ? 'AO VIVO' : (jogos.length ? 'EM BREVE' : 'AGUARDANDO');
      if (idx >= jogos.length) idx = 0;
      const j = jogos[idx];
      const v = j && j.video && j.video.estado === 'ao_vivo' ? j.video.id : null;
      // o vídeo só é recriado quando muda; senão cada atualização do placar reiniciaria a transmissão
      if (v !== videoAtual) {
        videoAtual = v;
        t('video').hidden = !v;
        t('video').innerHTML = v ? '<iframe src="https://www.youtube.com/embed/' + encodeURIComponent(v) + '?autoplay=1&mute=1&rel=0&playsinline=1" allow="autoplay; encrypted-media; fullscreen" allowfullscreen title="Transmissão ao vivo"></iframe>' : '';
      }
      const p = D.proximos[0];
      t('principal').innerHTML = j ? placarHtml(j, !!v)
        : '<div class="tl-espera"><h3>' + (D.ultimos.length || D.proximos.length ? 'Intervalo entre os jogos' : 'Em breve, o placar ao vivo') + '</h3><p>' + esc(c.nome) + '</p>' +
          (p ? '<div class="tl-proximo">Próximo jogo<br><b>' + esc(p.a.nome) + ' × ' + esc(p.b.nome) + '</b><small>' + esc([p.rodada_nome, p.local, quando(p.inicio_previsto)].filter(Boolean).join(' · ')) + '</small></div>' : '') + '</div>';
      t('pontos').innerHTML = jogos.length > 1 ? jogos.map((_, i) => '<b class="' + (i === idx ? 'on' : '') + '"></b>').join('') : '';
      t('rodape').innerHTML = '<div><h4>Próximos jogos</h4>' + (D.proximos.slice(0, 3).map((x) => linhaMini(x, false)).join('') || '<div class="tl-linha"><span>—</span></div>') + '</div>' +
        '<div><h4>Últimos resultados</h4>' + (D.ultimos.slice(0, 3).map((x) => linhaMini(x, true)).join('') || '<div class="tl-linha"><span>—</span></div>') + '</div>';
      const sel = t('sel');
      if (document.activeElement !== sel) {
        sel.innerHTML = '<option value="">Automático (o jogo em destaque)</option>' + (D.opcoes || []).map((x) => '<option value="' + x.id + '"' + (String(x.id) === String(escolhido) ? ' selected' : '') + '>' + esc(x.rotulo) + '</option>').join('');
      }
    }

    async function atualizar() {
      if (!el.isConnected) { parar(); return; }
      if (document.hidden) return;
      try {
        D = await get('/campeonatos/' + id + '/acompanhar' + (escolhido ? '?jogo=' + encodeURIComponent(escolhido) : ''));
        t('conexao').textContent = '';
        desenhar();
      } catch (e) {
        t('conexao').textContent = D ? 'Reconectando…' : e.message;
        t('conexao').className = 'tl-conexao erro';
      }
    }
    const tAtualiza = setInterval(atualizar, 3000);
    // vários jogos ao vivo no modo automático: alterna a cada poucos segundos
    const tRotaciona = setInterval(() => {
      if (D && D.modo === 'auto' && D.jogos.length > 1 && Date.now() - ultimaTroca >= D.rotacao_s * 1000) { idx = (idx + 1) % D.jogos.length; ultimaTroca = Date.now(); desenhar(); }
    }, 1000);
    async function acender() { try { if (navigator.wakeLock && !document.hidden) luz = await navigator.wakeLock.request('screen'); } catch (e) { /* sem suporte */ } }
    const aoVoltar = () => { if (!document.hidden) { acender(); atualizar(); } };
    function parar() { clearInterval(tAtualiza); clearInterval(tRotaciona); document.removeEventListener('visibilitychange', aoVoltar); try { if (luz) luz.release(); } catch (e) { /* já liberada */ } }
    document.addEventListener('visibilitychange', aoVoltar);
    acender();

    t('sel').addEventListener('change', (ev) => { escolhido = ev.target.value; idx = 0; atualizar(); });
    t('cheia').addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (raiz.requestFullscreen) raiz.requestFullscreen().catch(() => {});
    });
    atualizar();
  }

  // ------------------------------------------------------------ organização e mesários
  async function montarControle(el, o) {
    const { get, post, id, k, toast } = o;
    const origem = o.origem || location.origin;
    el.innerHTML = '<p class="carregando">Carregando…</p>';
    let p, yt;
    try {
      [p, yt] = await Promise.all([get('/campeonatos/' + id + '/telao'), get('/youtube/estado').catch(() => ({ conectado: false }))]);
    } catch (e) { el.innerHTML = '<div class="vazio">' + esc(e.message) + '</div>'; return; }

    const opcao = (j) => '<label class="tl-op"><input type="radio" name="jogo" value="' + j.id + '"' + (p.escolhido === j.id ? ' checked' : '') + '><span><b>' + esc(j.a) + ' × ' + esc(j.b) + '</b> <span class="suave">· ' + esc(j.rodada) +
      (j.local ? ' · ' + esc(j.local) : '') + (j.status === 'agendado' && j.inicio_previsto ? ' · ' + diaHora(j.inicio_previsto) : (j.status !== 'agendado' ? ' · ' + esc(j.placar) : '')) + '</span></span></label>';
    const grupo = (titulo, lista) => (lista.length ? '<h4 class="tl-gtit">' + titulo + '</h4>' + lista.map(opcao).join('') : '');
    const temJogos = p.ao_vivo.length || p.proximos.length || p.encerrados.length;

    let transmissao;
    if (!yt.conectado) {
      transmissao = '<p class="vazio">A administração ainda não conectou o canal do YouTube do PlayGo. Assim que for conectado, a transmissão aparece aqui.</p>';
    } else if (!k.transmissao_permitida) {
      transmissao = '<p class="suave">Este campeonato ainda não liberou transmissões. Os jogos só podem ser filmados e transmitidos depois que a organização declarar que os participantes foram avisados.</p>' +
        (k.sou_gestor ? '<label class="tl-op"><input type="checkbox" data-t="declaro"><span>' + esc(yt.declaracao || 'Declaro que os participantes foram avisados e autorizam a filmagem e a transmissão dos jogos.') + '</span></label>' +
          '<button class="btn roxo bloco" type="button" data-t="liberar">🎥 Liberar transmissão deste campeonato</button>' : '<p class="suave">Peça à organização do campeonato para liberar a transmissão.</p>');
    } else {
      transmissao = '<details class="tut-embutido" data-t="tut"><summary>📘 Como transmitir — passo a passo para iniciantes</summary><div class="tut-dentro" data-t="tut-corpo"><p class="carregando">Abra para ver o passo a passo.</p></div></details>' +
        '<p class="suave">O vídeo vai <b>direto do seu celular ou computador para o YouTube</b>: o PlayGo só prepara a transmissão e a chave de cada jogo. Quem assiste vê no Ao vivo, no telão e na tela inicial. A gravação fica no canal e entra no book.' +
        (yt.privacidade ? (yt.privacidade === 'unlisted' ? ' Hoje as transmissões são <b>não listadas</b> (só quem tem o link do PlayGo assiste).' : ' Hoje as transmissões são <b>públicas</b> no canal.') : '') + '</p>' +
        '<div class="campo"><label>Jogo</label><select data-t="jogo-yt">' + p.ao_vivo.concat(p.proximos).map((j) => '<option value="' + j.id + '">' + (j.status === 'ao_vivo' ? '🔴 ' : '') + esc(j.a) + ' × ' + esc(j.b) + ' — ' + esc(j.rodada) + (j.local ? ' · ' + esc(j.local) : '') + '</option>').join('') + '</select></div>' +
        '<button class="btn roxo bloco" type="button" data-t="preparar"' + (p.ao_vivo.length || p.proximos.length ? '' : ' disabled') + '>🎥 Preparar transmissão deste jogo</button>' +
        (k.sou_gestor ? '<p style="margin-top:8px"><button class="btn perigo pequeno" type="button" data-t="bloquear">Bloquear transmissões</button></p>' : '') +
        '<div class="tl-yt" data-t="cartao" hidden>' +
          '<div class="tl-yt-topo"><b data-t="yt-titulo"></b><span class="tag cinza" data-t="yt-estado"></span><span class="tag cinza" data-t="yt-sinal"></span></div>' +
          '<p class="suave"><b>1.</b> No aplicativo de transmissão (<b>Larix Broadcaster</b>, Streamlabs, Prism ou <b>OBS</b> no computador), crie uma conexão <b>RTMP</b> com estes dados:</p>' +
          '<div class="campo"><label>Endereço do servidor</label><input data-t="yt-endereco" readonly></div><button class="btn suave pequeno" type="button" data-copiar="yt-endereco">Copiar endereço</button>' +
          '<div class="campo" style="margin-top:10px"><label>Chave de transmissão</label><input data-t="yt-chave" type="password" readonly></div>' +
          '<div class="tl-botoes"><button class="btn suave pequeno" type="button" data-t="yt-ver">Mostrar</button><button class="btn suave pequeno" type="button" data-copiar="yt-chave">Copiar chave</button></div>' +
          '<p class="suave" style="margin-top:10px"><b>2.</b> Comece a enviar o vídeo no aplicativo. Quando o sinal aparecer como <b>ativo</b> (uns 15 segundos), <b>3.</b> toque em <b>Entrar no ar</b>.</p>' +
          '<div class="tl-botoes"><button class="btn roxo" type="button" data-t="no-ar">▶ Entrar no ar</button><button class="btn perigo" type="button" data-t="encerrar">⏹ Encerrar transmissão</button><button class="btn suave pequeno" type="button" data-t="nova-chave">🔑 Nova chave</button>' +
          '<a class="btn suave pequeno" data-t="yt-assistir" target="_blank" rel="noopener" href="#" hidden>Ver no YouTube</a></div>' +
          '<p class="aviso alerta" style="margin-top:12px">⚠ A <b>chave é um segredo</b>: não compartilhe nem tire print. Se vazar, use <b>Nova chave</b>. Use ambiente sem música protegida por direitos autorais e avise quem estiver em quadra que está sendo filmado. O vídeo chega ao público com ~10 a 30 segundos de atraso em relação ao placar. Para 720p, o envio usa ~3 a 5 Mbps (≈1,5 a 2 GB por hora).</p>' +
          '<div data-t="yt-previa" hidden></div><div data-t="camera"></div></div>';
    }

    el.innerHTML =
      '<div class="painel"><h3>1 · Abra o telão na TV ou no tablet</h3>' +
      '<p class="suave" style="margin:6px 0 10px">Este link abre <b>sem login</b>: cole-o no navegador da TV ou do tablet e toque na tela para ficar em tela cheia. A tela fica acesa e se atualiza sozinha.</p>' +
      '<div class="campo"><input data-t="link" readonly value="' + esc(origem + p.link) + '"></div>' +
      '<div class="tl-botoes"><button class="btn roxo pequeno" type="button" data-t="copiar">🔗 Copiar link</button><a class="btn suave pequeno" data-t="abrir" target="_blank" rel="noopener" href="' + esc(origem + p.link) + '">📺 Abrir telão</a>' +
      '<a class="btn suave pequeno" href="' + esc(o.hrefAcompanhar || '#') + '">👁 Ver como espectador</a><button class="btn perigo pequeno" type="button" data-t="renovar">Gerar novo link</button></div>' +
      '<p class="suave" style="margin-top:8px">Qualquer pessoa com o link vê o placar. Os demais usuários logados acompanham pela aba <b>📺 Telão</b> do campeonato, sem poder de transmitir. Se o link vazar ou o evento terminar, use <b>Gerar novo link</b>: o antigo para de funcionar.</p></div>' +
      '<form class="painel" data-t="f-jogo"><h3>2 · Escolha o que aparece</h3>' +
      '<p class="suave" style="margin:6px 0 10px">No <b>automático</b> o telão mostra o jogo que estiver ao vivo (e alterna entre eles se houver mais de um). Ou fixe um jogo específico, mesmo antes de começar.</p>' +
      '<label class="tl-op"><input type="radio" name="jogo" value=""' + (p.escolhido ? '' : ' checked') + '><span><b>🤖 Automático</b> — o jogo ao vivo</span></label>' +
      grupo('🔴 Ao vivo agora', p.ao_vivo) + grupo('⏭ Próximos jogos', p.proximos) + grupo('🏁 Últimos resultados', p.encerrados) +
      (temJogos ? '' : '<p class="vazio">Ainda não há jogos. Faça o sorteio na aba Sorteio.</p>') +
      '<button class="btn roxo bloco" type="submit" style="margin-top:12px">Aplicar no telão</button></form>' +
      '<div class="painel"><h3>3 · Transmissão ao vivo no YouTube do PlayGo</h3><div style="margin-top:8px">' + transmissao + '</div></div>';

    const t = (n) => el.querySelector('[data-t="' + n + '"]');
    const avisar = (m) => (toast ? toast(m) : undefined);

    t('copiar').onclick = async function () {
      try { await navigator.clipboard.writeText(t('link').value); this.textContent = '✅ Link copiado'; } catch (e) { t('link').select(); }
    };
    t('renovar').onclick = async () => {
      if (!confirm('Gerar um novo link? O link atual deixa de funcionar e a TV precisará abrir o novo.')) return;
      try { const r = await post('/campeonatos/' + id + '/telao/renovar', {}); t('link').value = origem + r.link; t('abrir').href = origem + r.link; avisar('Novo link gerado.'); } catch (e) { avisar(e.message); }
    };
    t('f-jogo').onsubmit = async (ev) => {
      ev.preventDefault();
      const marcado = el.querySelector('input[name="jogo"]:checked');
      const v = marcado ? marcado.value : '';
      try { await post('/campeonatos/' + id + '/telao/jogo', { jogo_id: v ? Number(v) : null }); avisar(v ? 'Jogo fixado no telão.' : 'Telão no automático.'); } catch (e) { avisar(e.message); }
    };

    // ---- transmissão
    const tut = t('tut');
    if (tut) {
      tut.addEventListener('toggle', async () => {
        if (!tut.open || tut.dataset.carregado) return;
        try {
          const r = await get('/tutoriais/transmissao?embutido=true');
          t('tut-corpo').innerHTML = r.html + '<p class="suave" style="margin:8px 4px 0"><a href="#/tutorial/transmissao">Abrir em tela cheia →</a></p>';
          tut.dataset.carregado = '1';
        } catch (e) { t('tut-corpo').innerHTML = '<p class="vazio">' + esc(e.message) + '</p>'; }
      });
    }
    const liberar = t('liberar');
    if (liberar) liberar.onclick = async () => {
      try { await post('/campeonatos/' + id + '/transmissao/liberar', { permitir: true, declaro: t('declaro').checked }); montarControle(el, Object.assign({}, o, { k: Object.assign({}, k, { transmissao_permitida: true }) })); } catch (e) { avisar(e.message); }
    };
    const bloquear = t('bloquear');
    if (bloquear) bloquear.onclick = async () => {
      if (!confirm('Bloquear as transmissões deste campeonato? Transmissões preparadas continuam até serem encerradas.')) return;
      try { await post('/campeonatos/' + id + '/transmissao/liberar', { permitir: false }); montarControle(el, Object.assign({}, o, { k: Object.assign({}, k, { transmissao_permitida: false }) })); } catch (e) { avisar(e.message); }
    };
    const cartao = t('cartao');
    if (cartao) {
      const sel = t('jogo-yt');
      const base = () => '/campeonatos/' + id + '/jogos/' + sel.value + '/transmissao';
      const rotuloEstado = { preparada: 'Preparada: aguardando o envio', ao_vivo: '🔴 No ar', encerrada: 'Encerrada', erro: 'Com problema' };
      const rotuloSinal = { active: '✅ sinal ativo', inactive: 'sem sinal ainda', ready: 'sem sinal ainda', created: 'sem sinal ainda', error: '❌ erro no sinal' };
      let temporizador = null;
      const cam = window.PlayGoCamera ? PlayGoCamera.montar(t('camera'), { postar: post, base, aviso: avisar, depois: () => atualizar(), cls: { primario: 'btn roxo', suave: 'btn suave', perigo: 'btn perigo' } }) : null;
      const desenhar = (d) => {
        cartao.hidden = !d.estado; // sem transmissão preparada para este jogo: nada de chave nem botões
        if (!d.estado) return;
        if (cam) cam.mostrar(d);
        t('yt-titulo').textContent = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : '';
        t('yt-estado').textContent = rotuloEstado[d.estado] || d.estado || '—';
        t('yt-sinal').textContent = d.sinal ? (rotuloSinal[d.sinal.estado] || d.sinal.estado) + (d.sinal.saude && d.sinal.saude !== 'noData' ? ' · qualidade ' + d.sinal.saude : '') : 'sem sinal';
        t('yt-endereco').value = (d.envio && d.envio.endereco) || '';
        t('yt-chave').value = (d.envio && d.envio.chave) || '';
        t('no-ar').disabled = d.estado !== 'preparada';
        t('nova-chave').disabled = d.estado !== 'preparada';
        t('encerrar').disabled = !['preparada', 'ao_vivo'].includes(d.estado);
        const a = t('yt-assistir');
        a.hidden = !d.assistir || !['ao_vivo', 'encerrada'].includes(d.estado); a.href = d.assistir || '#';
        const pre = t('yt-previa');
        pre.hidden = !(d.video_id && d.estado === 'ao_vivo');
        if (!pre.hidden && pre.dataset.v !== d.video_id) {
          pre.dataset.v = d.video_id;
          pre.innerHTML = '<div class="tl-video" style="margin-top:12px"><iframe src="https://www.youtube.com/embed/' + encodeURIComponent(d.video_id) + '?mute=1&autoplay=1&rel=0&playsinline=1" allow="autoplay; encrypted-media; fullscreen" allowfullscreen title="Prévia da transmissão"></iframe></div>';
        }
        clearTimeout(temporizador);
        if (['preparada', 'ao_vivo'].includes(d.estado)) temporizador = setTimeout(atualizar, 5000);
      };
      async function atualizar() {
        if (!el.isConnected) return;
        try { desenhar(await get(base())); } catch (e) { avisar(e.message); }
      }
      async function chamar(sufixo) {
        try { desenhar(await post(base() + sufixo, {})); } catch (e) { avisar(e.message); }
      }
      t('preparar').onclick = () => chamar('');
      t('no-ar').onclick = () => chamar('/no-ar');
      t('encerrar').onclick = () => { if (confirm('Encerrar a transmissão deste jogo? A gravação fica no canal.')) chamar('/encerrar'); };
      t('nova-chave').onclick = () => { if (confirm('Gerar uma nova chave? A atual deixa de funcionar e o aplicativo precisa da nova.')) chamar('/nova-chave'); };
      t('yt-ver').onclick = function () { const c = t('yt-chave'); c.type = c.type === 'password' ? 'text' : 'password'; this.textContent = c.type === 'password' ? 'Mostrar' : 'Ocultar'; };
      cartao.addEventListener('click', async (ev) => {
        const b = ev.target.closest('[data-copiar]');
        if (!b) return;
        const alvo = t(b.dataset.copiar);
        try { await navigator.clipboard.writeText(alvo.value); b.textContent = '✅ Copiado'; } catch (e) { /* sem permissão */ }
      });
      sel.onchange = () => { clearTimeout(temporizador); cartao.hidden = true; if (sel.value) atualizar(); };
      if (sel.value) atualizar();
    }
  }

  window.Telao = { montarAcompanhar, montarControle };
})();
