/* PlayGo — Receber pelo app (subconta Asaas da Arena). Compartilhado pelo site e pelo PWA: quem usa passa `api(caminho, {metodo, corpo, form})` e `toast(msg)`.
   Tudo acontece aqui dentro: a Arena nunca vê o painel do Asaas. O rodapé do Asaas (instituição prestadora do serviço) aparece em todas as telas. */
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const brl = (v) => Number(v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  const dh = (iso) => (iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '');
  const uuid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
  const KYC = { criando: ['Criando sua conta…', 'rb-amarelo'], documentos_pendentes: ['Documentos pendentes', 'rb-amarelo'], em_analise: ['Em análise pelo Asaas', 'rb-azul'], aprovado: ['Aprovado', 'rb-verde'], recusado: ['Recusado', 'rb-vermelho'] };
  const TIPO_LANC = { recebimento: 'Recebimento', taxa_asaas: 'Taxa do Asaas', comissao: 'Comissão PlayGo', estorno: 'Estorno', saque: 'Saque', saque_falho: 'Saque devolvido' };
  const TIPO_PIX = [['CPF', 'CPF'], ['CNPJ', 'CNPJ'], ['EMAIL', 'E-mail'], ['PHONE', 'Celular'], ['EVP', 'Chave aleatória']];

  function rodape(d) {
    const a = (d && d.asaas) || {};
    return '<p class="rb-rodape">' + esc(a.texto || '') + (a.links || []).map((l) => ' · <a href="' + esc(l.url) + '" target="_blank" rel="noopener">' + esc(l.texto) + '</a>').join('') + '</p>';
  }

  function campo(rotulo, nome, extra) { return '<label class="rb-campo">' + rotulo + '<input name="' + nome + '" ' + (extra || '') + '></label>'; }

  function formCadastro(botao) {
    return '<form class="rb-form" id="rb-cad">' +
      campo('Nome (ou razão social)', 'nome', 'required autocomplete="name"') + campo('E-mail', 'email', 'type="email" required autocomplete="email"') +
      campo('CPF ou CNPJ', 'cpf_cnpj', 'required inputmode="numeric" autocomplete="off"') +
      '<label class="rb-campo" id="rb-nasc">Data de nascimento do titular<input name="data_nascimento" type="date"></label>' +
      '<label class="rb-campo" id="rb-tipo" hidden>Tipo da empresa<select name="tipo_empresa"><option value="">Escolha…</option><option value="MEI">MEI</option><option value="LIMITED">Sociedade limitada</option><option value="INDIVIDUAL">Empresa individual</option><option value="ASSOCIATION">Associação</option></select></label>' +
      campo('Celular com DDD', 'telefone', 'inputmode="tel" required') + campo('CEP', 'cep', 'inputmode="numeric" required') + campo('Endereço', 'endereco', 'required') +
      campo('Número', 'numero', 'required') + campo('Bairro', 'bairro', 'required') + campo('Complemento (opcional)', 'complemento') +
      campo('Faturamento (ou renda) mensal aproximado, R$', 'renda_mensal', 'inputmode="decimal" required') +
      '<p class="rb-nota">O CPF/CNPJ e os demais dados vão direto ao Asaas e <b>não ficam guardados</b> no PlayGo (guardamos só os 4 últimos dígitos para você conferir).</p>' +
      '<button class="rb-btn" id="rb-enviar">' + botao + '</button></form>';
  }

  function ligarCadastro(el) {
    const f = el.querySelector('#rb-cad');
    const cpf = f.elements.cpf_cnpj;
    const ajustar = () => { const pj = cpf.value.replace(/\D/g, '').length > 11; el.querySelector('#rb-nasc').hidden = pj; el.querySelector('#rb-tipo').hidden = !pj; };
    cpf.oninput = ajustar; ajustar();
    return f;
  }

  function lerCadastro(f) {
    const v = (n) => (f.elements[n] ? f.elements[n].value.trim() : '');
    const pj = v('cpf_cnpj').replace(/\D/g, '').length > 11;
    return { nome: v('nome'), email: v('email'), cpf_cnpj: v('cpf_cnpj'), telefone: v('telefone'), cep: v('cep'), endereco: v('endereco'), numero: v('numero'), bairro: v('bairro'), complemento: v('complemento') || null, renda_mensal: v('renda_mensal').replace(',', '.'), data_nascimento: pj ? null : v('data_nascimento') || null, tipo_empresa: pj ? v('tipo_empresa') || null : null };
  }

  // ------------------------------------------------------------ tela da Arena
  async function montarArena(el, o) {
    const { api, toast } = o;
    const base = '/recebimentos/arenas/' + o.arenaId;
    let tentou = false;
    let timer = null;
    const erro = (e) => { if (toast) toast(e.message || 'Não foi possível concluir.'); };

    async function carregar() {
      clearTimeout(timer);
      try { desenhar(await api(base)); } catch (e) { el.innerHTML = '<div class="rb-caixa"><p class="rb-aviso">' + esc(e.message) + '</p></div>'; }
    }

    function desenhar(d) {
      const r = d.recebedor;
      if (!r) return telaAtivar(d);
      if (r.status_kyc !== 'aprovado') return telaStatus(d);
      telaAprovada(d);
    }

    function telaAtivar(d) {
      const bloqueada = d.fase === 'avaliacao' && !d.liberada;
      el.innerHTML = '<div class="rb-caixa"><h2>💰 Receber pelo app</h2>' +
        '<p>Receba o pagamento das reservas direto pelo PlayGo (Pix, boleto ou cartão). O dinheiro cai na <b>sua conta de recebimento</b>, você acompanha saldo e extrato aqui e pede o saque quando quiser. Você não precisa abrir conta nem entrar em nenhum outro site.</p>' +
        (bloqueada ? '<p class="rb-aviso">Sua arena ainda não foi liberada para esta função. Fale com a administração do PlayGo.</p>' : '<h3>Dados do titular da conta</h3>' + formCadastro('Ativar recebimento')) +
        rodape(d) + '</div>';
      if (bloqueada) return;
      const f = ligarCadastro(el);
      f.onsubmit = async (ev) => {
        ev.preventDefault();
        const b = el.querySelector('#rb-enviar'); b.disabled = true;
        try { desenhar(await api(base + '/ativar', { metodo: 'POST', corpo: lerCadastro(f) })); if (toast) toast('Cadastro enviado! Agora é só acompanhar a aprovação.'); } catch (e) { erro(e); b.disabled = false; }
      };
    }

    function telaStatus(d) {
      const r = d.recebedor;
      const [rotulo, cor] = KYC[r.status_kyc] || [r.status_kyc, 'rb-azul'];
      const pend = r.documentos_pendentes || [];
      el.innerHTML = '<div class="rb-caixa"><h2>💰 Receber pelo app</h2><p>Situação do cadastro: <span class="rb-tag ' + cor + '">' + esc(rotulo) + '</span></p>' +
        (r.status_kyc === 'criando' || r.aguardar_s ? '<p class="rb-nota">Estamos preparando sua conta' + (r.aguardar_s ? ' (consulta em ' + r.aguardar_s + ' s)' : '') + '…</p>' : '') +
        (r.status_kyc === 'recusado' ? '<p class="rb-aviso">Seu cadastro foi recusado' + (r.motivo_recusa ? ': ' + esc(r.motivo_recusa) : '') + '. Fale com a administração do PlayGo.</p>' : '') +
        (r.status_kyc === 'em_analise' ? '<p class="rb-nota">Recebemos seus dados e o Asaas está analisando. Avisamos você aqui assim que for aprovado.</p>' : '') +
        (pend.length ? '<h3>Documentos que faltam</h3>' + pend.map((p) =>
          '<div class="rb-doc"><b>' + esc(p.titulo || p.tipo) + '</b>' + (p.descricao ? '<br><span class="rb-nota">' + esc(p.descricao) + '</span>' : '') +
          (p.onboarding_url ? '<br><a class="rb-btn" href="' + esc(p.onboarding_url) + '" target="_blank" rel="noopener">Enviar pelo link seguro</a><span class="rb-nota"> (abre numa nova aba; ao terminar, volte aqui e toque em “Atualizar situação”)</span>'
            : '<br><input type="file" accept="application/pdf,image/jpeg,image/png" data-grupo="' + esc(p.id) + '" data-tipo="' + esc(p.tipo) + '"> <button class="rb-btn suave" data-enviar="' + esc(p.id) + '">Enviar documento</button>') + '</div>').join('') : '') +
        '<p><button class="rb-btn suave" id="rb-atualizar">Atualizar situação</button></p>' + rodape(d) + '</div>';
      el.querySelector('#rb-atualizar').onclick = () => atualizar(true);
      el.querySelectorAll('[data-enviar]').forEach((b) => {
        b.onclick = async () => {
          const inp = el.querySelector('input[data-grupo="' + b.dataset.enviar + '"]');
          if (!inp.files.length) { if (toast) toast('Escolha o arquivo (PDF, JPG ou PNG).'); return; }
          const form = new FormData(); form.append('grupo_id', inp.dataset.grupo); form.append('tipo', inp.dataset.tipo); form.append('arquivo', inp.files[0]);
          b.disabled = true;
          try { desenhar(await api(base + '/documentos', { metodo: 'POST', form })); if (toast) toast('Documento enviado.'); } catch (e) { erro(e); b.disabled = false; }
        };
      });
      if (r.aguardar_s) timer = setTimeout(() => atualizar(false), (r.aguardar_s + 1) * 1000);
      else if (!tentou && (r.status_kyc !== 'recusado')) { tentou = true; atualizar(false); }
    }

    async function atualizar(avisar) {
      try { desenhar(await api(base + '/situacao', { metodo: 'POST' })); if (avisar && toast) toast('Situação atualizada.'); } catch (e) { erro(e); }
    }

    async function telaAprovada(d) {
      const r = d.recebedor;
      el.innerHTML = '<div class="rb-caixa"><h2>💰 Receber pelo app</h2><p><span class="rb-tag rb-verde">Aprovado</span> Você já pode cobrar reservas pela agenda (botão “Cobrar”).</p>' +
        (r.precisa_atualizar_dados ? '<div class="rb-aviso"><b>Atualize seus dados cadastrais.</b> O Asaas exige a confirmação anual. <button class="rb-btn pequeno" id="rb-abrir-dados">Atualizar dados</button><div id="rb-dados" hidden></div></div>' : '') +
        '<div class="rb-saldo"><span>Saldo disponível</span><b id="rb-saldo">…</b><span class="rb-nota" id="rb-div"></span></div>' +
        '<h3>Sacar</h3><div id="rb-destino"></div>' +
        '<h3>Extrato</h3><div id="rb-extrato" class="rb-lista">Carregando…</div><h3>Saques</h3><div id="rb-saques" class="rb-lista">Carregando…</div>' + rodape(d) + '</div>';
      destino(r);
      const dadosBtn = el.querySelector('#rb-abrir-dados');
      if (dadosBtn) dadosBtn.onclick = () => {
        const c = el.querySelector('#rb-dados'); c.hidden = false; c.innerHTML = formCadastro('Confirmar dados'); const f = ligarCadastro(c);
        f.onsubmit = async (ev) => { ev.preventDefault(); try { desenhar(await api(base + '/dados', { metodo: 'POST', corpo: lerCadastro(f) })); if (toast) toast('Dados atualizados.'); } catch (e) { erro(e); } };
      };
      try {
        const [sd, ex, sq] = await Promise.all([api(base + '/saldo'), api(base + '/extrato'), api(base + '/saques')]);
        el.querySelector('#rb-saldo').textContent = brl(sd.saldo);
        if (sd.divergencia) el.querySelector('#rb-div').textContent = ' Conferindo com o Asaas: ' + brl(sd.saldo_asaas) + ' (pode haver valores ainda a liberar ou taxas; a administração acompanha).';
        el.querySelector('#rb-extrato').innerHTML = ex.lancamentos.length ? ex.lancamentos.map((x) => '<div class="rb-linha"><span>' + dh(x.criado_em) + '</span><span>' + esc(TIPO_LANC[x.tipo] || x.tipo) + '</span><b class="' + (x.valor_liquido < 0 ? 'rb-neg' : 'rb-pos') + '">' + brl(x.valor_liquido) + '</b></div>').join('') : '<p class="rb-nota">Nenhum lançamento ainda.</p>';
        el.querySelector('#rb-saques').innerHTML = sq.saques.length ? sq.saques.map((x) => '<div class="rb-linha"><span>' + dh(x.criado_em) + '</span><span>' + esc({ solicitado: 'Solicitado', enviado: 'Enviado', concluido: 'Concluído', falhou: 'Não concluído' }[x.status] || x.status) + (x.motivo ? ' — ' + esc(x.motivo) : '') + '</span><b>' + brl(x.valor) + '</b></div>').join('') : '<p class="rb-nota">Nenhum saque ainda.</p>';
      } catch (e) { erro(e); }
    }

    function destino(r) {
      const dest = r.destino;
      const form = '<form class="rb-form" id="rb-dest"><label class="rb-campo">Tipo da chave Pix<select name="tipo">' + TIPO_PIX.map(([v, n]) => '<option value="' + v + '">' + n + '</option>').join('') + '</select></label>' +
        campo('Chave Pix', 'chave', 'required autocomplete="off"') + '<label class="rb-check"><input type="checkbox" name="titular"> Confirmo que esta chave é da <b>mesma titularidade</b> da conta de recebimento.</label><button class="rb-btn suave">Salvar destino</button></form>';
      const alvo = el.querySelector('#rb-destino');
      alvo.innerHTML = dest
        ? '<p>Destino do saque: <b>' + esc(dest.tipo) + ' ' + esc(dest.chave) + '</b> <button class="rb-btn pequeno suave" id="rb-trocar">Trocar</button></p>' +
          '<form class="rb-form" id="rb-saque">' + campo('Valor do saque, R$', 'valor', 'inputmode="decimal" required') + '<button class="rb-btn">Sacar por Pix</button></form><p class="rb-nota">O saque vai só para o destino cadastrado.</p>'
        : '<p class="rb-nota">Cadastre antes a chave Pix que vai receber os saques.</p>' + form;
      const t = alvo.querySelector('#rb-trocar');
      if (t) t.onclick = () => { alvo.innerHTML = form; ligarDestino(alvo); };
      ligarDestino(alvo);
      const fs = alvo.querySelector('#rb-saque');
      if (fs) {
        let chave = uuid();  // um por tentativa: o duplo clique reaproveita a mesma chave
        fs.onsubmit = async (ev) => {
          ev.preventDefault();
          const b = fs.querySelector('button'); b.disabled = true;
          try { await api(base + '/saques', { metodo: 'POST', corpo: { valor: fs.elements.valor.value.replace(',', '.'), chave_idempotencia: chave } }); chave = uuid(); if (toast) toast('Saque solicitado!'); carregar(); } catch (e) { erro(e); b.disabled = false; chave = uuid(); }
        };
      }
    }

    function ligarDestino(alvo) {
      const f = alvo.querySelector('#rb-dest');
      if (!f) return;
      f.onsubmit = async (ev) => {
        ev.preventDefault();
        try { desenhar(await api(base + '/destino', { metodo: 'PUT', corpo: { tipo: f.elements.tipo.value, chave: f.elements.chave.value, titularidade_confirmada: f.elements.titular.checked } })); if (toast) toast('Destino salvo.'); } catch (e) { erro(e); }
      };
    }

    document.addEventListener('visibilitychange', () => { if (!document.hidden && el.isConnected && el.querySelector('#rb-atualizar')) atualizar(false); });
    await carregar();
  }

  // ------------------------------------------------------------ cobrar uma reserva (botão da agenda)
  async function cobrar(o, reservaId, valorSugerido) {
    const { api, toast } = o;
    const nome = prompt('Nome de quem vai pagar:');
    if (!nome) return null;
    const doc = prompt('CPF ou CNPJ de quem vai pagar (vai direto ao Asaas, não fica guardado):');
    if (!doc) return null;
    const valor = prompt('Valor da cobrança (R$):', valorSugerido ? String(valorSugerido).replace('.', ',') : '');
    if (!valor) return null;
    try {
      const r = await api('/recebimentos/reservas/' + reservaId + '/cobrar', { metodo: 'POST', corpo: { valor: valor.replace(',', '.'), pagador: { nome, cpf_cnpj: doc } } });
      prompt('Cobrança criada! Copie o link e envie ao cliente (Pix, boleto ou cartão).\n' + ((r.asaas && r.asaas.texto) || ''), r.link_pagamento);
      return r;
    } catch (e) { if (toast) toast(e.message); return null; }
  }

  // ------------------------------------------------------------ administração
  async function montarAdmin(el, o) {
    const { api, toast } = o;
    async function carregar() {
      let p;
      try { p = await api('/recebimentos/admin/painel'); } catch (e) { el.innerHTML = '<p class="rb-aviso">' + esc(e.message) + '</p>'; return; }
      const a = p.avaliacao;
      const pct = (v, m) => Math.min(100, Math.round((100 * Number(v)) / m));
      el.innerHTML = '<div class="rb-caixa"><h2>💰 Recebimentos de parceiros</h2><p>Fase: <span class="rb-tag rb-azul">' + esc(p.fase) + '</span> · Ambiente do Asaas: <b>' + esc(a.ambiente) + '</b></p>' +
        (p.fase === 'avaliacao' ? '<div class="rb-aval"><h3>Período de avaliação do Asaas</h3>' +
          '<div class="rb-barra"><span>Subcontas de produção: <b>' + a.subcontas + ' / ' + a.limite_subcontas + '</b></span><i style="width:' + pct(a.subcontas, a.limite_subcontas) + '%"></i></div>' +
          '<div class="rb-barra"><span>Maior total cobrado numa subconta: <b>' + brl(a.maior_cobranca_subconta) + ' / ' + brl(a.limite_cobrancas_subconta) + '</b></span><i style="width:' + pct(a.maior_cobranca_subconta, a.limite_cobrancas_subconta) + '%"></i></div>' +
          '<div class="rb-barra"><span>Dias: <b>' + a.dias_usados + ' / ' + a.limite_dias + '</b> (restam ' + a.dias_restantes + ')' + (a.primeira_subconta_em ? ' · primeira subconta de produção em ' + dh(a.primeira_subconta_em) : ' · ainda sem subconta de produção') + '</span><i style="width:' + pct(a.dias_usados, a.limite_dias) + '%"></i></div>' +
          '<p class="rb-nota">Bloqueio local preventivo a partir de ' + esc(a.bloqueio_em) + ' de cada limite, para não esbarrar no bloqueio do Asaas no meio de uma reserva. Só subcontas de produção contam.</p></div>' : '') +
        '<h3>Arenas</h3><div class="rb-lista">' + p.arenas.map((x) => {
          const r = x.recebedor;
          return '<div class="rb-linha"><span><b>' + esc(x.arena) + '</b>' + (r ? ' <span class="rb-tag ' + ((KYC[r.status_kyc] || [0, 'rb-azul'])[1]) + '">' + esc((KYC[r.status_kyc] || [r.status_kyc])[0]) + '</span> <span class="rb-nota">' + esc(r.ambiente) + ' · saldo ' + brl(r.saldo) + (r.precisa_atualizar_dados ? ' · atualizar dados' : '') + '</span>' : ' <span class="rb-nota">sem subconta</span>') + '</span>' +
            '<span>' + (p.fase === 'avaliacao' ? '<label class="rb-check"><input type="checkbox" data-lib="' + x.arena_id + '" ' + (x.liberada ? 'checked' : '') + '> liberada</label>' : '') + (r ? ' <button class="rb-btn pequeno suave" data-rot="' + x.arena_id + '">Trocar chave</button>' : '') + '</span></div>';
        }).join('') + '</div>' + rodape(p) + '</div>';
      el.querySelectorAll('[data-lib]').forEach((c) => { c.onchange = async () => { try { await api('/recebimentos/admin/arenas/' + c.dataset.lib + '/liberar', { metodo: 'POST', corpo: { liberada: c.checked } }); if (toast) toast(c.checked ? 'Arena liberada.' : 'Arena bloqueada.'); } catch (e) { if (toast) toast(e.message); c.checked = !c.checked; } }; });
      el.querySelectorAll('[data-rot]').forEach((b) => {
        b.onclick = async () => {
          if (!confirm('Emitir uma nova chave de API para esta subconta e desativar a antiga? (exige a liberação temporária de chaves de subcontas no Asaas)')) return;
          try { await api('/recebimentos/admin/arenas/' + b.dataset.rot + '/rotacionar-chave', { metodo: 'POST' }); if (toast) toast('Chave trocada.'); } catch (e) { if (toast) toast(e.message); }
        };
      });
    }
    await carregar();
  }

  window.Recebimentos = { montarArena, montarAdmin, cobrar };
})();
