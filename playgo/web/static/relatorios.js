/* PlayGo: relatórios (site). Consulta /api/v1/relatorios e desenha números, barras e tabelas; nada de biblioteca de gráfico.
   Quem usa passa `api(caminho, opções)` e `admin`. */
window.Relatorios = (function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const brl = (v) => Number(v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  const dm = (iso) => iso.slice(8, 10) + '/' + iso.slice(5, 7);
  const iso = (d) => d.toISOString().slice(0, 10);

  const numero = (rotulo, valor, nota, classe) => '<div class="rl-num ' + (classe || '') + '"><span>' + esc(rotulo) + '</span><b>' + valor + '</b>' + (nota ? '<small>' + esc(nota) + '</small>' : '') + '</div>';

  function barras(itens, rotulo, valor, formato) {
    if (!itens.length) return '<p class="rl-nota">Sem dados no período.</p>';
    const max = Math.max(1, ...itens.map((i) => Number(valor(i)) || 0));
    return '<div class="rl-barras">' + itens.map((i) => {
      const v = Number(valor(i)) || 0;
      return '<div class="rl-linha"><span class="rot" title="' + esc(rotulo(i)) + '">' + esc(rotulo(i)) + '</span><span class="rl-trilho"><i style="width:' + Math.round(100 * v / max) + '%"></i></span><span class="val">' + formato(v) + '</span></div>';
    }).join('') + '</div>';
  }

  function serie(dias, campo, formato) {
    const max = Math.max(1, ...dias.map((d) => d[campo]));
    return '<div class="rl-serie" role="img" aria-label="Série diária">' + dias.map((d) => '<i style="height:' + Math.max(2, Math.round(100 * d[campo] / max)) + '%" title="' + dm(d.dia) + ': ' + formato(d[campo]) + '"></i>').join('') + '</div>' +
      '<div class="rl-eixo"><span>' + dm(dias[0].dia) + '</span><span>' + dm(dias[dias.length - 1].dia) + '</span></div>';
  }

  function tabela(cabecalho, linhas, vazio) {
    return '<div class="rl-rolar"><table class="rl-tabela"><thead><tr>' + cabecalho.map(([t, n]) => '<th' + (n ? ' class="n"' : '') + '>' + t + '</th>').join('') + '</tr></thead><tbody>' +
      (linhas.join('') || '<tr><td colspan="' + cabecalho.length + '">' + vazio + '</td></tr>') + '</tbody></table></div>';
  }

  const pct = (v) => Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + '%';
  function taxasVigentes(t) {
    const v = t.vigentes;
    if (!v) return '<p class="rl-nota">As taxas ainda não foram consultadas. Abra "Receber pelo app" para carregar.</p>';
    const x = [], pix = v.pix || {}, c = v.cartao || {}, a = v.antecipacao || {}, s = v.saque || {};
    const ok = (n) => n !== null && n !== undefined;
    if (ok(pix.percentual) && pix.percentual) x.push(['Receber por Pix', pct(pix.percentual)]); else if (ok(pix.fixa)) x.push(['Receber por Pix', brl(ok(pix.fixa_promocional) ? pix.fixa_promocional : pix.fixa)]);
    if (ok(c.pct_1x)) x.push(['Cartão de crédito à vista', pct(c.pct_1x) + (c.fixa ? ' + ' + brl(c.fixa) : '') + (c.dias_para_receber ? ' (cai em ' + c.dias_para_receber + ' dias)' : '')]);
    if (ok(a.cartao_avista_pct_mes)) x.push(['Antecipar cartão', pct(a.cartao_avista_pct_mes) + ' ao mês']);
    if (ok(s.pix)) x.push(['Saque por Pix', brl(s.pix)]);
    return x.length ? '<div class="rl-barras">' + x.map(([n, val]) => '<div class="rl-linha" style="grid-template-columns:minmax(0,1fr) auto"><span class="rot">' + esc(n) + '</span><span class="val">' + esc(val) + '</span></div>').join('') + '</div>' : '<p class="rl-nota">O Asaas ainda não informou as taxas.</p>';
  }

  function desenharArena(el, r) {
    const x = r.resumo;
    el.innerHTML =
      '<div class="rl-numeros">' +
      numero('Reservas no período', x.reservas, x.reservas_atletas + ' de atletas, ' + x.reservas_gestor + ' lançadas por você', 'destaque') +
      numero('Recebido pelo app', brl(x.recebido_bruto), x.pagas_pelo_app + ' reserva(s) paga(s)', 'lima') +
      numero('Líquido estimado', brl(x.liquido_estimado), 'após estornos, comissão e taxas') +
      numero('Comissão do PlayGo', brl(x.comissao_playgo)) +
      numero('Taxas do Asaas', brl(x.taxa_asaas), x.pagas_pelo_app ? 'taxa efetiva de ' + Number(x.taxa_efetiva_pct).toLocaleString('pt-BR') + '%' + (x.taxa_asaas && !x.taxa_asaas_paga_pela_arena ? ' (paga pelo PlayGo)' : '') : 'cobradas nos pagamentos pelo app') +
      numero('Estornos', brl(x.estornado)) +
      numero('A receber pelo app', brl(x.a_receber_pelo_app), x.pendentes_pelo_app + ' pagamento(s) pendente(s)') +
      numero('Combinado na arena', brl(x.previsto_na_arena), x.combinadas_na_arena + ' reserva(s), pagas direto com você') +
      numero('Ticket médio', brl(x.ticket_medio), 'por reserva paga pelo app') +
      numero('Ocupação', x.ocupacao_pct + '%', x.horas_reservadas + ' h de ' + x.horas_disponiveis + ' h abertas') +
      numero('Bloqueios', x.bloqueios, 'horários bloqueados (não entram na ocupação)') +
      '</div><div class="rl-grade">' +
      '<section class="rl-cartao cheio"><h3>Reservas por dia</h3>' + (r.por_dia.length ? serie(r.por_dia, 'reservas', (v) => v + ' reserva(s)') : '') + '</section>' +
      '<section class="rl-cartao"><h3>Por quadra</h3>' + tabela([['Quadra'], ['Reservas', 1], ['Horas', 1], ['Recebido', 1]],
        r.por_quadra.map((q) => '<tr><td>' + esc(q.quadra) + '</td><td class="n">' + q.reservas + '</td><td class="n">' + q.horas + '</td><td class="n">' + brl(q.recebido) + '</td></tr>'), 'Sem quadras.') + '</section>' +
      '<section class="rl-cartao"><h3>Dias da semana</h3>' + barras(r.por_dia_da_semana, (i) => i.dia, (i) => i.reservas, (v) => v) + '</section>' +
      '<section class="rl-cartao"><h3>Horários de pico</h3>' + barras(r.por_hora, (i) => i.hora, (i) => i.reservas, (v) => v) + '</section>' +
      '<section class="rl-cartao"><h3>Melhores clientes</h3>' + tabela([['Atleta'], ['Reservas', 1], ['Valor', 1]],
        r.melhores_clientes.map((c) => '<tr><td>' + esc(c.usuario) + '</td><td class="n">' + c.reservas + '</td><td class="n">' + brl(c.valor) + '</td></tr>'), 'Nenhuma reserva de atleta no período.') +
      '<p class="rl-nota">Atletas aparecem só pelo @usuario.</p></section>' +
      (r.taxas && r.taxas.tem_conta ? '<section class="rl-cartao cheio"><h3>Taxas da sua conta de recebimento</h3>' + taxasVigentes(r.taxas) + '<p class="rl-nota">' + (r.taxas.atualizado_em ? 'Consultadas no Asaas em ' + new Date(r.taxas.atualizado_em).toLocaleString('pt-BR') + '. ' : '') + '<a href="/gestao/recebimentos?arena=' + r.arena.id + '">Ver todas as taxas e simular quanto você recebe</a>.</p></section>' : '') +
      '</div><div class="rl-acoes"><a class="btn soft" id="rl-csv" href="#">Baixar reservas (CSV)</a><a class="btn soft" href="/gestao/recebimentos?arena=' + r.arena.id + '">Saldo e saques</a></div>' +
      '<p class="rl-nota">"Recebido" conta as reservas pagas pelo app. O líquido é uma estimativa: desconta o que foi devolvido, a comissão do PlayGo e as taxas do Asaas que ficam com a arena.</p>';
  }

  function desenharPlataforma(el, r) {
    const x = r.resumo;
    el.innerHTML =
      '<div class="rl-numeros">' +
      numero('Reservas nas arenas', x.reservas, x.arenas_com_reserva + ' de ' + x.arenas_ativas + ' arenas com reserva', 'destaque') +
      numero('Recebido pelas arenas', brl(x.recebido_bruto), 'reservas pagas pelo app', 'lima') +
      numero('Comissão do PlayGo', brl(x.comissao_playgo), 'sobre as reservas') +
      numero('Receita de planos', brl(x.receita_planos), 'assinaturas pagas no período') +
      numero('Novos usuários', x.novos_usuarios) + numero('Atividades criadas', x.atividades_criadas) + numero('Campeonatos criados', x.campeonatos_criados) +
      '</div><div class="rl-grade">' +
      '<section class="rl-cartao cheio"><h3>Arenas por resultado</h3>' + tabela([['Arena'], ['Cidade'], ['Reservas', 1], ['Pagas no app', 1], ['Horas', 1], ['Recebido', 1], ['Comissão', 1], ['']],
        r.arenas.map((a) => '<tr><td>' + esc(a.nome) + '</td><td>' + esc(a.cidade || '') + '</td><td class="n">' + a.reservas + '</td><td class="n">' + a.pagas_pelo_app + '</td><td class="n">' + a.horas + '</td><td class="n">' + brl(a.recebido_bruto) + '</td><td class="n">' + brl(a.comissao_playgo) + '</td><td><a href="/relatorios?arena=' + a.id + '">Ver</a></td></tr>'), 'Nenhuma arena ativa.') + '</section>' +
      '<section class="rl-cartao"><h3>Assinaturas ativas</h3>' + barras(r.assinaturas_ativas, (i) => i.plano, (i) => i.total, (v) => v) + '</section>' +
      '<section class="rl-cartao"><h3>Recebido por arena</h3>' + barras(r.arenas.filter((a) => a.recebido_bruto > 0).slice(0, 8), (i) => i.nome, (i) => i.recebido_bruto, brl) + '</section>' +
      '</div>';
  }

  function montar(el, o) {
    const f = document.getElementById('rl-filtro'), ini = document.getElementById('rl-ini'), fim = document.getElementById('rl-fim'), sel = document.getElementById('rl-arena'), cx = document.getElementById('rl-arena-cx');
    let aba = 'arena';
    const hoje = new Date();
    fim.value = iso(hoje); ini.value = iso(new Date(hoje.getTime() - 29 * 86400000));
    const consulta = () => '?inicio=' + ini.value + '&fim=' + fim.value;

    async function carregar() {
      el.innerHTML = '<p class="suave">Carregando…</p>';
      try {
        if (aba === 'plataforma') desenharPlataforma(el, await o.api('/relatorios/plataforma' + consulta()));
        else if (sel && sel.value) {
          desenharArena(el, await o.api('/relatorios/arenas/' + sel.value + consulta()));
          const csv = document.getElementById('rl-csv');
          if (csv) csv.href = '/api/v1/relatorios/arenas/' + sel.value + '/reservas.csv' + consulta();
        } else el.innerHTML = '<p class="suave">Escolha uma arena.</p>';
      } catch (e) { el.innerHTML = '<div class="panel"><p class="suave">' + esc(e.message) + '</p></div>'; }
    }
    f.onsubmit = (ev) => { ev.preventDefault(); carregar(); };
    if (sel) sel.onchange = carregar;
    document.querySelectorAll('[data-dias]').forEach((b) => { b.onclick = () => { const d = new Date(); fim.value = iso(d); ini.value = iso(new Date(d.getTime() - (Number(b.dataset.dias) - 1) * 86400000)); carregar(); }; });
    document.querySelectorAll('[data-aba]').forEach((b) => { b.onclick = () => { aba = b.dataset.aba; document.querySelectorAll('[data-aba]').forEach((x) => x.classList.toggle('on', x === b)); if (cx) cx.hidden = aba === 'plataforma'; carregar(); }; });
    carregar();
  }
  return { montar };
})();
