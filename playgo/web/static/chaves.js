/* PlayGo — sorteio, chaves e jogo ao vivo. Compartilhado pelo site e pelo PWA.
   Quem usa passa `get(caminho)` e `post(caminho, corpo)` (a API de cada um) e um construtor de links.
   Sem conexão aberta (Vercel): a tela consulta a API de poucos em poucos segundos enquanto estiver aberta. */
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '');
  const dataHora = (iso) => (iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '');
  const ROTULO = { agendado: 'Agendado', ao_vivo: 'AO VIVO', encerrado: 'Encerrado' };

  function nomeEq(e) { return e ? esc(e.nome) : '<i class="ch-tbd">a definir</i>'; }

  function rodape(j) {
    if (j.status === 'ao_vivo') return '<span class="ch-vivo">● AO VIVO</span>';
    if (j.folga) return '<span class="ch-sub">Classificado direto</span>';
    if (j.status === 'encerrado') return '<span class="ch-sub">Encerrado' + (j.desempate ? ' · desempate' : '') + '</span>';
    const q = [j.inicio_previsto ? dataHora(j.inicio_previsto) : '', j.local || ''].filter(Boolean).join(' · ');
    return '<span class="ch-sub">' + (esc(q) || 'Horário a definir') + '</span>';
  }

  function cartao(j, href) {
    const linha = (eq, lado) => {
      const venceu = j.status === 'encerrado' && j.vencedor_id && eq && j.vencedor_id === eq.id;
      const pl = lado === 'a' ? j.placar_a : j.placar_b;
      const mostra = j.status !== 'agendado' && !j.folga;
      return '<div class="ch-linha' + (venceu ? ' venceu' : '') + '"><span class="ch-nome">' + (!eq && j.folga ? '<i class="ch-tbd">folga</i>' : nomeEq(eq)) + '</span><b class="ch-pl">' + (mostra ? pl : '') + '</b></div>';
    };
    const parciais = j.sets && j.sets.length ? '<div class="ch-sets">' + j.sets.map((x) => '<span class="' + (x.encerrado ? '' : 'atual') + '">' + x.a + '–' + x.b + '</span>').join('') + '</div>' : '';
    return '<a class="ch-jogo ' + j.status + '" href="' + esc(href(j)) + '">' + linha(j.a, 'a') + linha(j.b, 'b') + parciais + '<div class="ch-rod">' + rodape(j) + '</div></a>';
  }

  // Jogo por sets: o que mostrar de cada lado (pontos do set em andamento, sets ganhos e a lista dos sets)
  function lerSets(j, rg) {
    const sets = j.sets || [];
    const aberto = sets.length && !sets[sets.length - 1].encerrado ? sets[sets.length - 1] : null;
    const decidido = Math.max(j.placar_a, j.placar_b) >= rg.sets_para_vencer;
    const numero = aberto ? aberto.numero : Math.min(sets.length + 1, rg.melhor_de);
    const alvo = rg.melhor_de > 1 && numero >= rg.melhor_de ? rg.pontos_tiebreak : rg.pontos_set;
    return { aberto, decidido, numero, alvo, pa: aberto ? aberto.a : 0, pb: aberto ? aberto.b : 0, fechados: sets.filter((x) => x.encerrado) };
  }

  function tabela(c, sets) {
    if (!c.length) return '';
    return '<div class="ch-tabela-wrap"><table class="ch-tabela"><thead><tr><th>#</th><th>Equipe</th><th>P</th><th>J</th><th>V</th><th>E</th><th>D</th><th>' + (sets ? 'SS' : 'SG') + '</th></tr></thead><tbody>' +
      c.map((l) => '<tr><td>' + l.posicao + '</td><td class="ch-eq">' + esc(l.equipe.nome) + '</td><td><b>' + l.pontos + '</b></td><td>' + l.jogos + '</td><td>' + l.vitorias + '</td><td>' + l.empates + '</td><td>' + l.derrotas + '</td><td>' + (l.saldo > 0 ? '+' : '') + l.saldo + '</td></tr>').join('') +
      '</tbody></table></div><p class="ch-sub">' + (sets ? 'Vitória sem ir ao set decisivo vale 3 pontos; vitória no decisivo, 2; derrota no decisivo, 1. Desempate: saldo de sets e depois de pontos.' : 'Vitória 3 pontos, empate 1. Desempate: saldo e depois gols/pontos pró.') + '</p>';
  }

  // Super 8: ranking individual (cada atleta recebe o resultado da dupla em que jogou)
  function tabelaSuper8(c) {
    if (!c.length) return '';
    return '<div class="ch-tabela-wrap"><table class="ch-tabela"><thead><tr><th>#</th><th>Atleta</th><th>V</th><th>D</th><th>J</th><th>Games</th><th>Saldo</th></tr></thead><tbody>' +
      c.map((l) => '<tr><td>' + l.posicao + '</td><td class="ch-eq">' + esc(l.equipe.nome) + '</td><td><b>' + l.vitorias + '</b></td><td>' + l.derrotas + '</td><td>' + l.jogos + '</td><td>' + l.pro + '–' + l.contra + '</td><td>' + (l.saldo > 0 ? '+' : '') + l.saldo + '</td></tr>').join('') +
      '</tbody></table></div><p class="ch-sub">Ranking individual: mais vitórias; desempate por saldo de games e depois games feitos. Cada atleta joga uma vez com cada outro como parceiro.</p>';
  }

  function tabelaGrupo(g, sets) {
    return '<div class="ch-tabela-wrap"><table class="ch-tabela"><thead><tr><th>#</th><th>Equipe</th><th>P</th><th>J</th><th>' + (sets ? 'SS' : 'SG') + '</th></tr></thead><tbody>' +
      g.map((l) => '<tr class="' + (l.classifica ? 'ch-classif' : '') + '"><td>' + l.posicao + '</td><td class="ch-eq">' + esc(l.equipe.nome) + (l.cabeca ? ' <span class="ch-cabeca" title="Cabeça de chave">C' + l.cabeca + '</span>' : '') + '</td><td><b>' + l.pontos + '</b></td><td>' + l.jogos + '</td><td>' + (l.saldo > 0 ? '+' : '') + l.saldo + '</td></tr>').join('') +
      '</tbody></table></div>';
  }

  function htmlGrupos(d, href) {
    let h = '<h3 class="ch-tit">Fase de grupos</h3><p class="ch-sub">Classificam-se os ' + d.classificam + ' primeiros de cada grupo (linhas destacadas). Desempate: saldo' + (d.regras_placar && d.regras_placar.modo === 'sets' ? ' de sets e de pontos' : ', depois pontos/gols pró') + '.</p><div class="ch-grupos">';
    h += d.grupos.map((g) => '<div class="ch-grupo"><h4>Grupo ' + esc(g.grupo) + '</h4>' + tabelaGrupo(g.classificacao, d.regras_placar && d.regras_placar.modo === 'sets') +
      g.rodadas.map((r) => '<div class="ch-sub" style="margin:8px 0 4px">' + esc(r.nome) + '</div><div class="ch-grade pequeno">' + r.jogos.map((j) => cartao(j, href)).join('') + '</div>').join('') + '</div>').join('');
    h += '</div>';
    if (d.rodadas.length) h += '<h3 class="ch-tit">Mata-mata</h3><div class="ch-chave">' + d.rodadas.map((r) => '<div class="ch-coluna"><h4>' + esc(r.nome) + '</h4><div class="ch-col-jogos">' + r.jogos.map((j) => cartao(j, href)).join('') + '</div></div>').join('') + '</div>';
    else h += '<div class="ch-aviso" style="margin-top:14px">O mata-mata é montado automaticamente quando terminar o último jogo da fase de grupos' + (d.jogos_grupos_restantes ? ' (faltam ' + d.jogos_grupos_restantes + ' jogo' + (d.jogos_grupos_restantes !== 1 ? 's' : '') + ').' : '.') + '</div>';
    return h;
  }

  function todosJogos(d) {
    return (d.grupos || []).flatMap((g) => g.rodadas.flatMap((r) => r.jogos)).concat(d.rodadas.flatMap((r) => r.jogos));
  }

  function htmlChaves(d, href) {
    if (!d.rodadas.length && !(d.grupos || []).length) return '<div class="ch-vazio">O chaveamento ainda não foi sorteado.' + (d.pode_gerir ? '' : ' Assim que a organização sortear, ele aparece aqui.') + '</div>';
    let h = '';
    if (d.campeao) h += '<div class="ch-campeao">🏆 Campeão: <b>' + esc(d.campeao.nome) + '</b></div>';
    if (d.ao_vivo.length) h += '<div class="ch-aovivo"><b>🔴 Agora</b>' + d.ao_vivo.map((j) => cartao(j, href)).join('') + '</div>';
    if (d.cabecas && d.cabecas.length) h += '<p class="ch-sub">Cabeças de chave: ' + d.cabecas.map((c) => c.ordem + 'º ' + esc(c.equipe ? c.equipe.nome : '')).join(' · ') + '</p>';
    if (d.formato === 'grupos') {
      h += htmlGrupos(d, href);
    } else if (d.formato === 'eliminatoria') {
      h += '<div class="ch-chave">' + d.rodadas.map((r) => '<div class="ch-coluna"><h4>' + esc(r.nome) + '</h4><div class="ch-col-jogos">' + r.jogos.map((j) => cartao(j, href)).join('') + '</div></div>').join('') + '</div>';
    } else if (d.formato === 'super8') {
      h += '<h3 class="ch-tit">Ranking</h3>' + tabelaSuper8(d.classificacao);
      h += d.rodadas.map((r) => '<h3 class="ch-tit">' + esc(r.nome) + '</h3><div class="ch-grade">' + r.jogos.map((j) => cartao(j, href)).join('') + '</div>').join('');
    } else {
      h += '<h3 class="ch-tit">Classificação</h3>' + tabela(d.classificacao, d.regras_placar && d.regras_placar.modo === 'sets');
      h += d.rodadas.map((r) => '<h3 class="ch-tit">' + esc(r.nome) + '</h3><div class="ch-grade">' + r.jogos.map((j) => cartao(j, href)).join('') + '</div>').join('');
    }
    return h;
  }

  // Esvazia o temporizador sozinho quando a tela sai do ar
  function repetir(el, ms, fn) {
    const t = setInterval(() => { if (!el.isConnected) return clearInterval(t); if (!document.hidden) fn(); }, ms);
    return t;
  }

  function montarChaves(el, o) {
    const { get, href, id } = o;
    let ultimo = '';
    const atualizar = async () => {
      try {
        const d = await get('/campeonatos/' + id + '/chaves');
        const s = JSON.stringify(d);
        if (s === ultimo) return;
        ultimo = s;
        el.innerHTML = htmlChaves(d, href);
        if (o.aoCarregar) o.aoCarregar(d);
      } catch (e) { if (!ultimo) el.innerHTML = '<div class="ch-vazio">' + esc(e.message) + '</div>'; }
    };
    atualizar();
    repetir(el, 10000, atualizar);
  }

  // Controles de quem conduz (organização e mesários) direto na lista do "Ao vivo"
  function controlesLista(j, d) {
    if (!d.pode_pontuar) return '';
    const porSets = d.regras_placar && d.regras_placar.modo === 'sets';
    const mata = (d.formato === 'eliminatoria' || j.fase === 'mata_mata') && !porSets;
    const desempate = mata && j.a && j.b ? '<select class="av-venc" title="Se empatar, quem venceu no desempate"><option value="">Desempate (se empatar)</option><option value="' + j.a.id + '">' + esc(j.a.nome) + '</option><option value="' + j.b.id + '">' + esc(j.b.nome) + '</option></select>' : '';
    if (j.status === 'agendado') {
      if (!j.a || !j.b) return '<div class="ch-ctl"><span class="ch-sub">Aguardando as equipes serem definidas.</span></div>';
      return '<div class="ch-ctl"><button class="ch-btn" data-av="iniciar" data-j="' + j.id + '">▶ Iniciar</button>' +
        '<details><summary>Lançar resultado direto</summary><div class="av-res">' + (porSets
          ? '<input class="av-sets" placeholder="Sets: 25-20, 18-25, 15-12" aria-label="Pontos de cada set">'
          : '<input type="number" min="0" max="999" class="av-a" placeholder="0" aria-label="Placar ' + esc(j.a.nome) + '"> × <input type="number" min="0" max="999" class="av-b" placeholder="0" aria-label="Placar ' + esc(j.b.nome) + '">') + desempate +
        '<button class="ch-btn suave" data-av="resultado" data-j="' + j.id + '">Lançar e encerrar</button></div></details></div>';
    }
    if (j.status === 'ao_vivo') {
      return '<div class="ch-ctl av-fim">' + desempate + '<button class="ch-btn perigo" data-av="encerrar" data-j="' + j.id + '">🏁 Encerrar jogo</button></div>';
    }
    return d.pode_gerir ? '<div class="ch-ctl"><button class="ch-btn suave" data-av="reabrir" data-j="' + j.id + '">Reabrir para corrigir</button></div>' : '';
  }
  let o_href = (j) => '#';

  // Jogo ao vivo: um embaixo do outro, com as duas equipes lado a lado (nome, placar e marcações de cada uma)
  function quadroVivo(j, d) {
    const rg = d.regras_placar || { modo: 'simples' };
    const ss = rg.modo === 'sets' ? lerSets(j, rg) : null;
    const time = (eq, l) => {
      const pl = ss ? (l === 'a' ? ss.pa : ss.pb) : (l === 'a' ? j.placar_a : j.placar_b);
      const subt = ss ? '<div class="av-sub">Sets: <b>' + (l === 'a' ? j.placar_a : j.placar_b) + '</b></div>' : '';
      const bt = d.pode_pontuar ? '<div class="av-bt"><button class="ch-btn grande" data-av="marcar" data-j="' + j.id + '" data-lado="' + l + '" data-d="1">+1</button><button class="ch-btn suave" data-av="marcar" data-j="' + j.id + '" data-lado="' + l + '" data-d="-1">−1</button></div>' : '';
      return '<div class="av-time"><div class="av-nome">' + esc(eq.nome) + '</div><div class="av-pl">' + pl + '</div>' + subt + bt + '</div>';
    };
    const setInfo = ss ? '<div class="av-setinfo">' + (ss.decidido ? 'Jogo decidido — encerre o jogo' : 'Set ' + ss.numero + ' de ' + rg.melhor_de + ' · até ' + ss.alvo + (ss.numero >= rg.melhor_de && rg.melhor_de > 1 ? ' (decisivo)' : '')) +
      (ss.fechados.length ? ' · <span class="ch-sub">' + ss.fechados.map((x) => x.a + '–' + x.b).join(' · ') + '</span>' : '') + '</div>' : '';
    return '<div class="av-quadro"><div class="av-topo"><span class="ch-vivo">● AO VIVO</span><span class="ch-sub">' + esc([j.rodada_nome, j.local].filter(Boolean).join(' · ')) + '</span>' +
      '<a class="ch-link" href="' + esc(o_href(j)) + '">lance a lance →</a></div>' + setInfo + '<div class="av-times">' + time(j.a, 'a') + '<div class="av-x">×</div>' + time(j.b, 'b') + '</div></div>';
  }

  function montarAoVivo(el, o) {
    const { get, post, href, id, toast } = o;
    o_href = href;
    let ultimo = '', ultimoD = null;
    const bloco = (t, js, vazio, d) => '<h3 class="ch-tit">' + t + '</h3>' + (js.length ? '<div class="ch-grade' + (d.pode_pontuar ? ' av' : '') + '">' + js.map((j) => '<div class="ch-av" data-jogo="' + j.id + '">' + cartao(j, href) + controlesLista(j, d) + '</div>').join('') + '</div>' : '<p class="ch-sub">' + vazio + '</p>');
    const blocoVivo = (js, d) => '<h3 class="ch-tit">🔴 Ao vivo agora</h3>' + (js.length ? '<div class="av-vivos">' + js.map((j) => '<div class="ch-av vivo" data-jogo="' + j.id + '">' + quadroVivo(j, d) + controlesLista(j, d) + '</div>').join('') + '</div>' : '<p class="ch-sub">Nenhum jogo ao vivo neste momento.</p>');
    const desenhar = (d) => {
      const todos = todosJogos(d);
      const prox = todos.filter((j) => j.status === 'agendado' && j.a && j.b).sort((x, y) => (x.inicio_previsto || '9').localeCompare(y.inicio_previsto || '9'));
      const fim = todos.filter((j) => j.status === 'encerrado' && !j.folga).reverse().slice(0, 8);
      el.innerHTML = (d.pode_pontuar ? '<p class="ch-aviso">Você conduz este campeonato: inicie jogos, lance o placar e encerre direto daqui.</p>' : '') +
        (d.campeao ? '<div class="ch-campeao">🏆 Campeão: <b>' + esc(d.campeao.nome) + '</b></div>' : '') +
        blocoVivo(d.ao_vivo, d) + bloco('Próximos jogos', prox.slice(0, d.pode_pontuar ? 16 : 8), 'Sem jogos agendados.', d) + bloco('Últimos resultados', fim, 'Ainda não há resultados.', d);
    };
    // não troca a tela enquanto alguém digita um placar
    const digitando = () => ['INPUT', 'SELECT'].includes((document.activeElement || {}).tagName) && el.contains(document.activeElement);
    const atualizar = async (forcar) => {
      if (digitando() && !forcar) return;
      try {
        const d = await get('/campeonatos/' + id + '/chaves');
        ultimoD = d;
        const s = JSON.stringify(d);
        if (s === ultimo && !forcar) return;
        ultimo = s;
        desenhar(d);
      } catch (e) { if (!ultimo) el.innerHTML = '<div class="ch-vazio">' + esc(e.message) + '</div>'; }
    };
    el.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-av]');
      if (!b || b.disabled) return;
      const acao = b.dataset.av, jid = b.dataset.j, base = '/campeonatos/' + id + '/jogos/' + jid;
      const caixa = b.closest('[data-jogo]');
      const venc = caixa && caixa.querySelector('.av-venc') && caixa.querySelector('.av-venc').value;
      b.disabled = true;
      try {
        if (acao === 'iniciar') await post(base + '/iniciar', {});
        else if (acao === 'marcar') await post(base + '/marcar', { lado: b.dataset.lado, delta: Number(b.dataset.d) });
        else if (acao === 'encerrar') {
          if (!confirm('Encerrar o jogo com o placar atual?')) { b.disabled = false; return; }
          await post(base + '/encerrar', { vencedor_id: venc ? Number(venc) : null });
        } else if (acao === 'reabrir') {
          if (!confirm('Reabrir este jogo para corrigir o resultado?')) { b.disabled = false; return; }
          await post(base + '/reabrir', {});
        } else if (acao === 'resultado' && caixa.querySelector('.av-sets')) {
          const sets = [...caixa.querySelector('.av-sets').value.matchAll(/(\d+)\s*[-–xX×]\s*(\d+)/g)].map((m) => [Number(m[1]), Number(m[2])]);
          if (!sets.length) { if (toast) toast('Informe os sets, por exemplo: 25-20, 18-25, 15-12.'); b.disabled = false; return; }
          if (!confirm('Lançar os sets ' + sets.map((x) => x.join('-')).join(', ') + ' e encerrar o jogo?')) { b.disabled = false; return; }
          await post(base + '/iniciar', {});
          await post(base + '/sets', { sets });
          await post(base + '/encerrar', {});
          if (toast) toast('Resultado lançado.');
        } else if (acao === 'resultado') {
          const a = caixa.querySelector('.av-a').value, c2 = caixa.querySelector('.av-b').value;
          if (a === '' || c2 === '') { if (toast) toast('Informe o placar das duas equipes.'); b.disabled = false; return; }
          if (!confirm('Lançar o resultado ' + a + ' × ' + c2 + ' e encerrar o jogo?')) { b.disabled = false; return; }
          await post(base + '/iniciar', {});
          await post(base + '/placar', { a: Number(a), b: Number(c2) });
          await post(base + '/encerrar', { vencedor_id: venc ? Number(venc) : null });
          if (toast) toast('Resultado lançado.');
        }
        await atualizar(true);
      } catch (e) { if (toast) toast(e.message); else alert(e.message); await atualizar(true); }
    });
    atualizar(true);
    repetir(el, 5000, () => atualizar(false));
  }

  // ---------------------------------------------------------------- jogo ao vivo

  function placarHtml(d) {
    const rg = d.regras_placar || { modo: 'simples' };
    if (rg.modo === 'sets') {
      const ss = lerSets(d, rg);
      const lado = (eq, pontos, sets, id) => '<div class="jv-eq' + (d.vencedor_id && eq && d.vencedor_id === id ? ' venceu' : '') + '"><span>' + nomeEq(eq) + '</span><b>' + pontos + '</b><small>Sets: ' + sets + '</small></div>';
      return '<div class="jv-topo"><span class="ch-sub">' + esc(d.rodada_nome) + (d.local ? ' · ' + esc(d.local) : '') + '</span><span class="jv-status ' + d.status + '">' + (d.status === 'ao_vivo' ? '● ' : '') + ROTULO[d.status] + '</span></div>' +
        (d.status === 'ao_vivo' ? '<p class="ch-sub" style="text-align:center;margin:0 0 6px">' + (ss.decidido ? 'Jogo decidido — encerre o jogo' : 'Set ' + ss.numero + ' de ' + rg.melhor_de + ' · até ' + ss.alvo + ' pontos (diferença de ' + rg.diferenca + ')' + (ss.numero >= rg.melhor_de && rg.melhor_de > 1 ? ' · set decisivo' : '')) + '</p>' : '') +
        '<div class="jv-placar">' + lado(d.a, d.status === 'encerrado' ? d.placar_a : ss.pa, d.placar_a, d.a && d.a.id) + '<div class="jv-x">×</div>' + lado(d.b, d.status === 'encerrado' ? d.placar_b : ss.pb, d.placar_b, d.b && d.b.id) + '</div>' +
        (ss.fechados.length ? '<p class="ch-sub" style="text-align:center">Sets: ' + ss.fechados.map((x) => x.a + '–' + x.b).join(' · ') + '</p>' : '') +
        (d.status === 'agendado' ? '<p class="ch-sub" style="text-align:center">' + (d.inicio_previsto ? 'Previsto para ' + dataHora(d.inicio_previsto) : 'Horário a definir') + '</p>' : '');
    }
    return '<div class="jv-topo"><span class="ch-sub">' + esc(d.rodada_nome) + (d.local ? ' · ' + esc(d.local) : '') + '</span><span class="jv-status ' + d.status + '">' + (d.status === 'ao_vivo' ? '● ' : '') + ROTULO[d.status] + '</span></div>' +
      '<div class="jv-placar"><div class="jv-eq' + (d.vencedor_id && d.a && d.vencedor_id === d.a.id ? ' venceu' : '') + '"><span>' + nomeEq(d.a) + '</span><b>' + d.placar_a + '</b></div><div class="jv-x">×</div>' +
      '<div class="jv-eq' + (d.vencedor_id && d.b && d.vencedor_id === d.b.id ? ' venceu' : '') + '"><span>' + nomeEq(d.b) + '</span><b>' + d.placar_b + '</b></div></div>' +
      (d.status === 'agendado' ? '<p class="ch-sub" style="text-align:center">' + (d.inicio_previsto ? 'Previsto para ' + dataHora(d.inicio_previsto) : 'Horário a definir') + '</p>' : '') +
      (d.desempate ? '<p class="ch-sub" style="text-align:center">Decidido no desempate</p>' : '');
  }

  function linhaDoTempo(d) {
    if (!d.eventos.length) return '<p class="ch-sub">O jogo ainda não começou.</p>';
    return d.eventos.slice().reverse().map((e) => '<div class="jv-ev ' + e.tipo + '"><span class="jv-hora">' + hora(e.em) + '</span><span class="jv-txt">' + esc(e.texto || '') + '</span>' + (e.tipo === 'placar' ? '<b class="jv-snap">' + e.placar_a + '×' + e.placar_b + '</b>' : '') + '</div>').join('');
  }

  function controlesHtml(d) {
    if (!d.pode_pontuar) return '';
    const nomeA = d.a ? esc(d.a.nome) : 'A', nomeB = d.b ? esc(d.b.nome) : 'B';
    if (d.status === 'agendado') {
      const prontos = d.a && d.b;
      return '<div class="jv-ctrl"><button class="ch-btn grande" data-j="iniciar" ' + (prontos ? '' : 'disabled') + '>▶ Iniciar jogo</button>' + (prontos ? '' : '<p class="ch-sub">Aguardando as duas equipes serem definidas.</p>') +
        '<div class="jv-agenda"><input type="datetime-local" id="jv-inicio"><input id="jv-local" maxlength="120" placeholder="Quadra / campo"><button class="ch-btn suave" data-j="agendar">Salvar horário</button></div></div>';
    }
    if (d.status === 'ao_vivo') {
      const empate = d.placar_a === d.placar_b && !(d.regras_placar && d.regras_placar.modo === 'sets');
      return '<div class="jv-ctrl"><div class="jv-botoes"><div><b>' + nomeA + '</b><div><button class="ch-btn grande" data-j="marcar" data-lado="a" data-d="1">+1</button><button class="ch-btn suave" data-j="marcar" data-lado="a" data-d="-1">−1</button></div></div>' +
        '<div><b>' + nomeB + '</b><div><button class="ch-btn grande" data-j="marcar" data-lado="b" data-d="1">+1</button><button class="ch-btn suave" data-j="marcar" data-lado="b" data-d="-1">−1</button></div></div></div>' +
        '<div class="jv-agenda"><input id="jv-lance" maxlength="200" placeholder="Escreva um lance (ex.: cartão amarelo, pausa técnica)"><button class="ch-btn suave" data-j="lance">Publicar lance</button></div>' +
        (empate && d.mata_mata ? '<div class="jv-agenda"><select id="jv-venc"><option value="">Empate: quem venceu no desempate?</option><option value="' + d.a.id + '">' + nomeA + '</option><option value="' + d.b.id + '">' + nomeB + '</option></select></div>' : '') +
        '<button class="ch-btn perigo" data-j="encerrar">🏁 Encerrar jogo</button></div>';
    }
    return d.pode_gerir ? '<div class="jv-ctrl"><button class="ch-btn suave" data-j="reabrir">Reabrir jogo (corrigir)</button></div>' : '';
  }

  function montarJogo(el, o) {
    const { get, post, id, jogo, toast } = o;
    const base = '/campeonatos/' + id + '/jogos/' + jogo;
    el.innerHTML = '<div id="jv-placar"></div><div id="jv-controles"></div><h3 class="ch-tit">Lance a lance</h3><div id="jv-tempo"></div>';
    const P = el.querySelector('#jv-placar'), C = el.querySelector('#jv-controles'), T = el.querySelector('#jv-tempo');
    let ultimo = '', chaveCtrl = '', temp = null, ocupado = false;
    const mostrar = (d) => {
      const s = JSON.stringify(d.eventos) + JSON.stringify(d.sets) + d.placar_a + d.placar_b + d.status + d.vencedor_id + (d.a ? d.a.id : '') + (d.b ? d.b.id : '') + d.inicio_previsto + d.local;
      if (s !== ultimo) { ultimo = s; P.innerHTML = placarHtml(d); T.innerHTML = linhaDoTempo(d); if (o.aoCarregar) o.aoCarregar(d); }
      const k = [d.status, d.pode_pontuar, d.pode_gerir, d.placar_a === d.placar_b, d.a && d.a.id, d.b && d.b.id].join('|');
      if (k !== chaveCtrl) { chaveCtrl = k; C.innerHTML = controlesHtml(d); }
      clearInterval(temp);
      temp = repetir(el, d.status === 'ao_vivo' ? 3000 : 12000, atualizar);
    };
    async function atualizar() {
      if (ocupado) return;
      try { mostrar(await get(base)); } catch (e) { if (!ultimo) P.innerHTML = '<div class="ch-vazio">' + esc(e.message) + '</div>'; }
    }
    C.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-j]');
      if (!b || b.disabled) return;
      const acao = b.dataset.j;
      let corpo = {};
      if (acao === 'marcar') corpo = { lado: b.dataset.lado, delta: Number(b.dataset.d) };
      if (acao === 'lance') { const t = el.querySelector('#jv-lance').value; if (!t.trim()) return; corpo = { texto: t }; }
      if (acao === 'encerrar') {
        const v = el.querySelector('#jv-venc');
        if (!confirm('Encerrar o jogo com o placar atual?')) return;
        corpo = { vencedor_id: v && v.value ? Number(v.value) : null };
      }
      if (acao === 'reabrir' && !confirm('Reabrir este jogo para corrigir o resultado?')) return;
      if (acao === 'agendar') { const i = el.querySelector('#jv-inicio').value; corpo = { inicio: i || null, local: el.querySelector('#jv-local').value }; }
      ocupado = true; b.disabled = true;
      try { mostrar(await post(base + '/' + acao, corpo)); if (acao === 'lance') el.querySelector('#jv-lance').value = ''; if (acao === 'agendar' && toast) toast('Horário salvo.'); }
      catch (e) { if (toast) toast(e.message); else alert(e.message); }
      finally { ocupado = false; b.disabled = false; }
    });
    atualizar();
  }

  // ---------------------------------------------------------------- sorteio e mesários

  function painelRegras(d) {
    const r = d.regras_placar || { modo: 'simples', estilo: 'simples', melhor_de: 3, pontos_set: 25, pontos_tiebreak: 15, diferenca: 2 };
    const est = r.estilo || r.modo;
    const trava = d.placar_travado ? ' disabled' : '';
    const games = est === 'games';
    return '<div class="ch-painel"><h3>🏐 Regras de pontuação</h3><p class="ch-sub">' + (d.placar_travado ? 'Já há jogos começados: as regras não podem mais ser trocadas.' : 'Defina antes de iniciar os jogos. Por sets (vôlei) o placar mostra os pontos de cada set; por games (beach tennis, tênis, padel) mostra os games de cada set.') + '</p>' +
      '<div class="ch-campo ch-agenda"><label>Pontuação<select id="rg-modo"' + trava + '><option value="simples"' + (est === 'simples' ? ' selected' : '') + '>Simples (gols/pontos corridos)</option><option value="sets"' + (est === 'sets' ? ' selected' : '') + '>Por sets (vôlei, futevôlei…)</option><option value="games"' + (games ? ' selected' : '') + '>Por games (beach tennis, tênis, padel)</option></select></label>' +
      '<label id="rg-preset-w"' + (games ? '' : ' hidden') + '>Modelo pronto<select id="rg-preset"' + trava + '><option value="">Personalizado</option>' +
      '<option value="1,6,10">Set único de 6 games (7×6 no tie-break)</option><option value="1,8,10">Set único de 8 games (pro set)</option><option value="1,4,10">Set único de 4 games (rápido)</option>' +
      '<option value="3,6,10">Melhor de 3 sets de 6 games (3º = super tie-break de 10)</option></select></label></div>' +
      '<div class="ch-campo ch-agenda" id="rg-sets"' + (r.modo === 'sets' ? '' : ' hidden') + '>' +
      '<label>Quantos sets (vence a maioria)<select id="rg-melhor"' + trava + '>' + [1, 3, 5, 7].map((n) => '<option value="' + n + '"' + (r.melhor_de === n ? ' selected' : '') + '>Melhor de ' + n + (n > 1 ? ' (vence com ' + (Math.floor(n / 2) + 1) + ')' : '') + '</option>').join('') + '</select></label>' +
      '<label><span id="rg-l-pontos">' + (games ? 'Games por set' : 'Pontos por set') + '</span><input type="number" id="rg-pontos" min="1" max="99" value="' + r.pontos_set + '"' + trava + '></label>' +
      '<label><span id="rg-l-tie">' + (games ? 'Pontos do super tie-break (set decisivo)' : 'Pontos do set decisivo (tiebreak)') + '</span><input type="number" id="rg-tie" min="1" max="99" value="' + r.pontos_tiebreak + '"' + trava + '></label>' +
      '<label id="rg-dif-w"' + (games ? ' hidden' : '') + '>Diferença mínima para fechar o set<input type="number" id="rg-dif" min="1" max="5" value="' + r.diferenca + '"' + trava + '></label></div>' +
      '<p class="ch-sub" id="rg-dica"' + (games ? '' : ' hidden') + '>Em games: o set termina em N games com 2 de vantagem, ou 7×5 / 7×6 (tie-break de 7 pontos). No set decisivo de melhor de 3, joga-se um super tie-break (10 pontos, 2 de vantagem). Ponto de ouro (no-ad) é regra de quadra: o placar só conta games.</p>' +
      '<button class="ch-btn" id="bt-regras"' + trava + '>Salvar regras</button></div>';
  }

  function painelHorarios(d) {
    const escopos = d.formato === 'grupos'
      ? [['todos', 'Tudo (grupos e mata-mata)'], ['grupos', 'Fase de grupos'], ['mata_mata', 'Mata-mata' + (d.rodadas.length ? '' : ' (aparece depois dos grupos)')]]
      : [['todos', 'Todas as rodadas']].concat(d.rodadas.map((r) => ['rodada:' + r.rodada, r.nome]));
    const desativa = (v) => (v === 'mata_mata' && !d.rodadas.length ? ' disabled' : '');
    return '<div class="ch-painel"><h3>🕒 Horários dos jogos</h3><p class="ch-sub">Informe o início e a duração: o sistema marca todos os jogos da seleção, um depois do outro, e avisa as equipes. Cada rodada começa num horário novo.</p>' +
      '<div class="ch-campo ch-agenda"><label>Agendar<select id="ag-escopo">' + escopos.map(([v, n]) => '<option value="' + v + '"' + desativa(v) + '>' + esc(n) + '</option>').join('') + '</select></label>' +
      '<label>Início (data e hora)<input type="datetime-local" id="ag-inicio"></label>' +
      '<label>Duração de cada jogo (min)<input type="number" id="ag-dur" min="5" max="600" value="' + (d.duracao_jogo_min || 40) + '"></label>' +
      '<label>Intervalo entre jogos (min)<input type="number" id="ag-int" min="0" max="240" value="10"></label>' +
      '<label>Quadras / campos (separe por vírgula)<input id="ag-locais" placeholder="ex.: Quadra 01, Quadra 02"></label>' +
      '<label>Não marcar depois de (opcional)<input type="time" id="ag-ate"></label>' +
      '<label class="ch-check"><input type="checkbox" id="ag-sobre" checked> Substituir horários já definidos</label></div>' +
      '<p class="ch-sub">Com mais de uma quadra, os jogos de uma mesma rodada acontecem juntos. Se um limite de horário for informado, o que não couber no dia segue no dia seguinte, na hora do início.</p>' +
      '<button class="ch-btn grande" id="bt-agenda">📅 Aplicar horários</button><p class="ch-aviso" id="ag-res" style="margin-top:10px"></p></div>';
  }

  const opcoes = (de, ate, padrao) => Array.from({ length: ate - de + 1 }, (_, i) => de + i).map((n) => '<option value="' + n + '"' + (n === padrao ? ' selected' : '') + '>' + n + '</option>').join('');

  function montarSorteio(el, o) {
    const { get, post, id, toast } = o;
    const enviar = (m, c, corpo) => (m === 'DELETE' ? o.apagar(c) : post(c, corpo));
    async function desenhar(d) {
      const jaTem = d.rodadas.length > 0 || (d.grupos || []).length > 0;
      el.innerHTML =
        '<div class="ch-painel"><h3>🎲 Sorteio do chaveamento</h3>' +
        '<p class="ch-sub">' + d.equipes_confirmadas + ' equipe' + (d.equipes_confirmadas !== 1 ? 's' : '') + ' confirmada' + (d.equipes_confirmadas !== 1 ? 's' : '') + (d.equipes_pendentes ? ' · ' + d.equipes_pendentes + ' ainda pendente' + (d.equipes_pendentes !== 1 ? 's' : '') + ' (confirme antes de sortear)' : '') + '.</p>' +
        (jaTem ? '<p class="ch-aviso">Sorteado em ' + dataHora(d.sorteado_em) + ' · formato: ' + esc(d.formato_nome || '') + ' · semente ' + d.semente + (d.pode_sortear_de_novo ? '. Você pode sortear de novo enquanto nenhum jogo começou.' : '. Já há jogos começados: o sorteio está travado.') + '</p>' : '') +
        (!jaTem || d.pode_sortear_de_novo ? '<div class="ch-campo"><label><input type="radio" name="formato" value="eliminatoria" checked> <b>Eliminatória (mata-mata)</b> — quem perde sai; equipes sem adversário passam direto.</label>' +
          '<label><input type="radio" name="formato" value="grupos"> <b>Fase de grupos + mata-mata</b> — grupos todos contra todos; os melhores de cada grupo seguem para o mata-mata.</label>' +
          '<label><input type="radio" name="formato" value="pontos_corridos"> <b>Pontos corridos</b> — todos jogam contra todos; vale a classificação.</label>' +
          '<label><input type="radio" name="formato" value="super8"> <b>Super 8</b> — 4 duplas inscritas (2 atletas por equipe) ou 8 atletas individuais. O 1º jogo é entre as duplas inscritas; depois os atletas se misturam (7 rodadas, 2 jogos cada) e o ranking é individual.</label></div>' +
          '<div class="ch-campo" id="op-grupos" hidden><div style="display:flex;gap:10px;flex-wrap:wrap"><label>Grupos <select id="n-grupos">' + opcoes(2, Math.max(2, Math.floor(d.equipes_confirmadas / 2)), 2) + '</select></label>' +
          '<label>Classificam por grupo <select id="n-classif">' + opcoes(1, 4, 2) + '</select></label></div><p class="ch-sub">Cada grupo precisa de pelo menos 2 equipes e recebe no máximo um cabeça de chave.</p></div>' +
          '<div class="ch-campo" id="op-cabecas"><b>Cabeças de chave</b><p class="ch-sub" style="margin:0">Opcional. Numere as equipes mais fortes (1 = principal); elas ficam nas melhores posições e, nos grupos, em grupos diferentes. As demais são sorteadas.</p>' +
          '<div class="ch-cabecas">' + d.equipes.map((e) => '<label class="ch-cab"><input type="number" min="1" max="' + d.equipes.length + '" data-cabeca="' + e.id + '" placeholder="–"> ' + esc(e.nome) + '</label>').join('') + '</div></div>' +
          '<button class="ch-btn grande" id="bt-sortear" ' + (d.equipes_confirmadas < 2 ? 'disabled' : '') + '>' + (jaTem ? '🔁 Sortear de novo' : '🎲 Sortear agora') + '</button>' : '') +
        '<p class="ch-sub">O sorteio é aleatório (fora os cabeças de chave), fica registrado e as equipes são avisadas.</p></div>' +
        painelRegras(d) + (jaTem ? painelHorarios(d) : '') +
        '<div class="ch-painel"><h3>📋 Mesários</h3><p class="ch-sub">Pessoas autorizadas a iniciar jogos, marcar o placar e encerrar. Todos os demais só acompanham.</p>' +
        '<div id="mesarios">' + (d.mesarios.length ? d.mesarios.map((m) => '<span class="ch-chip">' + esc(m.arroba) + ' <button data-rm="' + m.usuario_id + '" title="Remover">×</button></span>').join('') : '<span class="ch-sub">Nenhum mesário além de você.</span>') + '</div>' +
        '<div class="ch-campo" style="display:flex;gap:6px"><input id="bt-mes" list="dl-mes" placeholder="@usuario" autocomplete="off"><datalist id="dl-mes"></datalist><button class="ch-btn suave" id="bt-add">Adicionar</button></div></div>';
      const bt = el.querySelector('#bt-sortear');
      const mostrarOpcoes = () => {
        const f = el.querySelector('[name=formato]:checked');
        if (!f) return;
        el.querySelector('#op-grupos').hidden = f.value !== 'grupos';
        el.querySelector('#op-cabecas').hidden = f.value === 'pontos_corridos' || f.value === 'super8';
      };
      el.querySelectorAll('[name=formato]').forEach((r) => (r.onchange = mostrarOpcoes));
      mostrarOpcoes();
      if (bt) bt.onclick = async () => {
        if (jaTem && !confirm('Sortear de novo? O chaveamento atual será substituído.')) return;
        const marcados = [...el.querySelectorAll('[data-cabeca]')].filter((i) => i.value).map((i) => ({ id: Number(i.dataset.cabeca), n: Number(i.value) })).sort((a, b) => a.n - b.n);
        if (new Set(marcados.map((m) => m.n)).size !== marcados.length) { if (toast) toast('Cada cabeça de chave precisa de um número diferente.'); return; }
        bt.disabled = true;
        try {
          const formato = el.querySelector('[name=formato]:checked').value;
          await post('/campeonatos/' + id + '/sorteio', { formato, grupos: formato === 'grupos' ? Number(el.querySelector('#n-grupos').value) : null, classificam: formato === 'grupos' ? Number(el.querySelector('#n-classif').value) : null, cabecas: formato === 'pontos_corridos' || formato === 'super8' ? [] : marcados.map((m) => m.id) });
          if (toast) toast('Sorteio feito!');
          if (o.aoSortear) o.aoSortear(); else desenhar(await get('/campeonatos/' + id + '/chaves'));
        } catch (e) { if (toast) toast(e.message); bt.disabled = false; }
      };
      const rgModo = el.querySelector('#rg-modo');
      if (rgModo) {
        const ajustarRegras = () => {
          const g = rgModo.value === 'games';
          el.querySelector('#rg-sets').hidden = rgModo.value === 'simples';
          el.querySelector('#rg-preset-w').hidden = !g;
          el.querySelector('#rg-dif-w').hidden = g;
          el.querySelector('#rg-dica').hidden = !g;
          el.querySelector('#rg-l-pontos').textContent = g ? 'Games por set' : 'Pontos por set';
          el.querySelector('#rg-l-tie').textContent = g ? 'Pontos do super tie-break (set decisivo)' : 'Pontos do set decisivo (tiebreak)';
          if (g) el.querySelector('#rg-dif').value = 2;
        };
        rgModo.onchange = ajustarRegras;
        const preset = el.querySelector('#rg-preset');
        if (preset) preset.onchange = () => {
          if (!preset.value) return;
          const [m, p, t] = preset.value.split(',');
          el.querySelector('#rg-melhor').value = m;
          el.querySelector('#rg-pontos').value = p;
          el.querySelector('#rg-tie').value = t;
        };
        el.querySelector('#bt-regras').onclick = async () => {
          const v = (i) => Number(el.querySelector('#' + i).value);
          try {
            await post('/campeonatos/' + id + '/placar-regras', { modo: rgModo.value, melhor_de: v('rg-melhor'), pontos_set: v('rg-pontos'), pontos_tiebreak: v('rg-tie'), diferenca: v('rg-dif') });
            if (toast) toast('Regras de pontuação salvas.');
            desenhar(await get('/campeonatos/' + id + '/chaves'));
          } catch (e) { if (toast) toast(e.message); }
        };
      }
      const ag = el.querySelector('#bt-agenda');
      if (ag) ag.onclick = async () => {
        const v = (i) => el.querySelector('#' + i).value;
        if (!v('ag-inicio')) { if (toast) toast('Informe a data e a hora do primeiro jogo.'); return; }
        ag.disabled = true;
        try {
          const r = await post('/campeonatos/' + id + '/agenda', {
            escopo: v('ag-escopo'), inicio: v('ag-inicio'), duracao_min: Number(v('ag-dur')), intervalo_min: Number(v('ag-int') || 0),
            locais: v('ag-locais').split(',').map((x) => x.trim()).filter(Boolean), ate: v('ag-ate') || null, sobrescrever: el.querySelector('#ag-sobre').checked,
          });
          const msg = r.agendados + ' jogo' + (r.agendados !== 1 ? 's' : '') + ' agendado' + (r.agendados !== 1 ? 's' : '') + ': de ' + dataHora(r.primeiro) + ' até ' + dataHora(r.ultimo_fim) + '.';
          if (toast) toast(msg);
          const d2 = await get('/campeonatos/' + id + '/chaves');
          await desenhar(d2);
          const aviso = el.querySelector('#ag-res');
          if (aviso) aviso.textContent = msg;
        } catch (e) { if (toast) toast(e.message); ag.disabled = false; }
      };
      let achados = [];
      const campo = el.querySelector('#bt-mes');
      campo.oninput = async () => {
        if (campo.value.replace('@', '').length < 2) return;
        try { achados = await get('/atletas?q=' + encodeURIComponent(campo.value.replace(/^@/, ''))); } catch (e) { achados = []; }
        el.querySelector('#dl-mes').innerHTML = achados.map((a) => '<option value="' + esc(a.arroba) + '">').join('');
      };
      el.querySelector('#bt-add').onclick = async () => {
        const a = achados.find((x) => x.arroba === campo.value || x.usuario === campo.value.replace(/^@/, ''));
        if (!a) { if (toast) toast('Escolha uma pessoa da lista.'); return; }
        try { await post('/campeonatos/' + id + '/mesarios', { usuario_id: a.id }); desenhar(await get('/campeonatos/' + id + '/chaves')); } catch (e) { if (toast) toast(e.message); }
      };
      el.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = async () => {
        try { await enviar('DELETE', '/campeonatos/' + id + '/mesarios/' + b.dataset.rm); desenhar(await get('/campeonatos/' + id + '/chaves')); } catch (e) { if (toast) toast(e.message); }
      }));
    }
    get('/campeonatos/' + id + '/chaves').then(desenhar).catch((e) => { el.innerHTML = '<div class="ch-vazio">' + esc(e.message) + '</div>'; });
  }

  window.Chaves = { montarChaves, montarAoVivo, montarJogo, montarSorteio };
})();
