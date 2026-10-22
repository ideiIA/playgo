/* Ensino: Central "Aulas e Treinamentos", perfil do profissional, oferta, "Minhas aulas" e fila de aprovação (site e app).
   Como o telão e as chaves, um módulo só: window.Ensino.montarX(el, o). `o` traz os adaptadores de cada lado:
     api(caminho, {metodo, corpo, form})  href(tipo, id)  toast(msg)  cls {primario, suave, perigo}  modalidades [{id,codigo,nome,icone}]
     pos {lat,lng}|null  mapa(id, centro, pontos)  abrir(caminho)  ir(destino)  eu {id, admin} */
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const brl = (v) => 'R$ ' + Number(v || 0).toFixed(2).replace('.', ',');
  const DIAS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];
  const NIVEIS = { todos: 'Todos os níveis', iniciante: 'Iniciante', intermediario: 'Intermediário', avancado: 'Avançado' };
  const dataBr = (iso) => (iso ? iso.split('T')[0].split('-').reverse().join('/') : '');

  const cls = (o, k) => ((o.cls || {})[k]) || 'btn';
  const modIcone = (m) => (m && m.icone ? esc(m.icone) : '🎓');

  // ------------------------------------------------------------ cartões
  function cardProfissional(p, o) {
    const mods = (p.modalidades || []).map((c) => (o.modalidades || []).find((m) => m.codigo === c)).filter(Boolean).slice(0, 4);
    return '<a class="en-card" href="' + esc(o.href('profissional', p.id)) + '">' +
      '<span class="en-foto">' + (p.foto_url ? '<img src="' + esc(p.foto_url) + '" alt="">' : esc((p.nome || '?').slice(0, 2).toUpperCase())) + '</span>' +
      '<div class="en-card-corpo"><b>' + esc(p.nome) + '</b>' + (p.verificado ? ' <span class="tag verde">✅ Verificado</span>' : '') +
      '<div class="en-meta">' + mods.map((m) => esc(m.icone) + ' ' + esc(m.nome)).join(' · ') + '</div>' +
      '<div class="en-meta">' + esc([p.cidade, p.distancia].filter(Boolean).join(' • ')) + (p.ofertas_publicadas ? ' • ' + p.ofertas_publicadas + ' oferta' + (p.ofertas_publicadas > 1 ? 's' : '') : '') + '</div>' +
      (p.nota_media ? '<div class="en-meta">★ ' + p.nota_media.toFixed(1) + ' (' + p.n_avaliacoes + ')</div>' : '') + '</div></a>';
  }

  function cardOferta(f, o) {
    const pr = f.profissional || {};
    return '<a class="en-card en-oferta" href="' + esc(o.href('oferta', f.id)) + '">' +
      '<div class="en-card-corpo"><div><span class="tag roxo">' + esc(f.tipo_oferta_nome) + '</span> ' + (f.aula_experimental ? '<span class="tag verde">🎁 Aula experimental</span>' : '') + '</div>' +
      '<b class="en-titulo">' + modIcone(f.modalidade) + ' ' + esc(f.titulo) + '</b>' +
      '<div class="en-meta">' + esc(pr.nome || '') + (pr.verificado ? ' ✅' : '') + '</div>' +
      '<div class="en-meta">' + esc([f.local_texto || f.endereco, f.distancia].filter(Boolean).join(' • ')) + '</div>' +
      (f.horarios_texto ? '<div class="en-meta">📅 ' + esc(f.horarios_texto) + '</div>' : '') +
      '<div class="en-preco">' + esc(f.preco_texto) + '</div></div></a>';
  }

  const secao = (titulo, itens, desenhar) => (itens && itens.length ? '<h3 class="en-sec">' + titulo + ' <small>' + itens.length + '</small></h3><div class="en-grade">' + itens.map(desenhar).join('') + '</div>' : '');

  // ------------------------------------------------------------ Central
  async function montarCentral(el, o) {
    const F = { q: '', modalidades: '', raio: '10', dia_semana: '', periodo: '', tipo_oferta: '', preco_max: '', experimental: false };
    el.innerHTML = '<div class="en"><div class="en-hero"><div><h2>🎓 Aulas e Treinamentos</h2><p>Personal, professores, escolinhas e aulas perto de você.</p></div>' +
      '<button class="' + cls(o, 'suave') + '" data-ir="ensino">🧑‍🏫 Quero dar aulas</button></div>' +
      '<form class="en-filtros" data-f="filtros">' +
      '<input name="q" placeholder="Modalidade, professor ou local" aria-label="Buscar">' +
      '<select name="modalidades"><option value="">Todas as modalidades</option>' + (o.modalidades || []).map((m) => '<option value="' + esc(m.codigo) + '">' + esc(m.icone) + ' ' + esc(m.nome) + '</option>').join('') + '</select>' +
      '<select name="raio">' + [2, 5, 10, 20, 50].map((r) => '<option value="' + r + '"' + (String(r) === F.raio ? ' selected' : '') + '>Até ' + r + ' km</option>').join('') + '<option value="0">Toda a cidade</option></select>' +
      '<select name="dia_semana"><option value="">Qualquer dia</option>' + DIAS.map((d, i) => '<option value="' + i + '">' + d + '</option>').join('') + '</select>' +
      '<select name="periodo"><option value="">Qualquer horário</option><option value="manha">Manhã</option><option value="tarde">Tarde</option><option value="noite">Noite</option></select>' +
      '<select name="tipo_oferta"><option value="">Aulas, pacotes e turmas</option><option value="aula">Aula avulsa</option><option value="pacote">Pacote de aulas</option><option value="turma">Turma</option></select>' +
      '<input name="preco_max" inputmode="decimal" placeholder="Valor máx. (R$)" aria-label="Valor máximo">' +
      '<label class="en-check"><input type="checkbox" name="experimental"> Aula experimental</label>' +
      '<button class="' + cls(o, 'primario') + '">Buscar</button></form>' +
      '<div class="en-mapa" id="en-mapa"></div>' +
      '<div id="en-resultado"><p class="carregando">Buscando…</p></div></div>';
    const form = el.querySelector('[data-f="filtros"]');
    el.addEventListener('click', (ev) => { const b = ev.target.closest('[data-ir]'); if (b) o.ir(b.dataset.ir); });

    async function buscar() {
      const d = new FormData(form);
      const q = new URLSearchParams();
      for (const [k, v] of d.entries()) { if (v !== '' && k !== 'experimental') q.set(k, v); }
      if (d.get('experimental')) q.set('experimental', 'true');
      if (d.get('raio') === '0') q.delete('raio');
      if (o.pos) { q.set('lat', o.pos.lat); q.set('lng', o.pos.lng); }
      const alvo = el.querySelector('#en-resultado');
      alvo.innerHTML = '<p class="carregando">Buscando…</p>';
      try {
        const r = await o.api('/aulas?' + q.toString());
        const sec = r.secoes;
        const nada = !r.profissionais.length && !r.ofertas.length;
        alvo.innerHTML = nada
          ? '<div class="vazio">Ainda não há aulas ou profissionais com esses filtros. Tente aumentar a distância ou limpar a busca.<br><br>É professor ou personal? <button class="' + cls(o, 'suave') + '" data-ir="ensino">Cadastre-se e divulgue suas aulas</button></div>'
          : secao('👥 Profissionais perto de você', r.profissionais, (p) => cardProfissional(p, o)) + secao('🏫 Turmas', sec.turmas, (f) => cardOferta(f, o)) +
            secao('📅 Aulas avulsas', sec.aulas, (f) => cardOferta(f, o)) + secao('🎟 Pacotes de aulas', sec.pacotes, (f) => cardOferta(f, o)) +
            secao('🎁 Aulas experimentais', sec.experimentais, (f) => cardOferta(f, o));
        if (o.mapa) o.mapa('en-mapa', r.centro, r.pontos);
      } catch (e) { alvo.innerHTML = '<div class="vazio">' + esc(e.message) + '</div>'; }
    }
    form.addEventListener('submit', (ev) => { ev.preventDefault(); buscar(); });
    buscar();
  }

  // ------------------------------------------------------------ contatos
  function contatos(p, o) {
    const zap = (p.whatsapp || '').replace(/\D/g, '');
    const botoes = [];
    if (zap) botoes.push('<a class="' + cls(o, 'primario') + '" href="https://wa.me/' + (zap.length <= 11 ? '55' + zap : zap) + '" target="_blank" rel="noopener">💬 WhatsApp</a>');
    if (p.telefone) botoes.push('<a class="' + cls(o, 'suave') + '" href="tel:' + esc(p.telefone.replace(/[^\d+]/g, '')) + '">📞 ' + esc(p.telefone) + '</a>');
    if (p.email_contato) botoes.push('<a class="' + cls(o, 'suave') + '" href="mailto:' + esc(p.email_contato) + '">✉️ E-mail</a>');
    return botoes.length ? '<div class="en-botoes">' + botoes.join('') + '</div>' : '<p class="en-meta">Este profissional ainda não informou contato.</p>';
  }

  // ------------------------------------------------------------ perfil público
  async function montarProfissional(el, o) {
    el.innerHTML = '<p class="carregando">Carregando…</p>';
    try {
      const p = await o.api('/profissionais/' + o.id);
      const mods = (p.modalidades || []).map((c) => (o.modalidades || []).find((m) => m.codigo === c)).filter(Boolean);
      el.innerHTML = '<div class="en"><div class="en-perfil"><span class="en-foto grande">' + (p.foto_url ? '<img src="' + esc(p.foto_url) + '" alt="">' : esc((p.nome || '?').slice(0, 2).toUpperCase())) + '</span>' +
        '<div><h2>' + esc(p.nome) + '</h2>' + (p.verificado ? '<span class="tag verde">✅ Verificado pelo PlayGo</span> ' : '') +
        (p.registro_numero ? '<span class="tag cinza">' + esc(p.registro_tipo || 'Registro') + ' ' + esc(p.registro_numero) + '</span>' : '') +
        '<div class="en-meta">' + esc([p.cidade, p.regiao].filter(Boolean).join(' • ')) + (p.distancia ? ' • a ' + esc(p.distancia) + ' de você' : '') + ' • atende até ' + p.raio_atendimento_km + ' km</div>' +
        (p.nota_media ? '<div class="en-meta">★ ' + p.nota_media.toFixed(1) + ' (' + p.n_avaliacoes + ' avaliações)</div>' : '') + '</div></div>' +
        (mods.length ? '<div class="en-chips">' + mods.map((m) => '<span class="tag roxo">' + esc(m.icone) + ' ' + esc(m.nome) + '</span>').join('') + '</div>' : '') +
        (p.apresentacao ? '<div class="en-bloco"><h3>Sobre</h3><p>' + esc(p.apresentacao).replace(/\n/g, '<br>') + '</p></div>' : '') +
        (p.especialidades ? '<div class="en-bloco"><h3>Especialidades</h3><p>' + esc(p.especialidades).replace(/\n/g, '<br>') + '</p></div>' : '') +
        '<div class="en-bloco"><h3>Contato</h3>' + contatos(p, o) + '</div>' +
        '<h3 class="en-sec">Aulas e turmas <small>' + p.ofertas.length + '</small></h3>' +
        (p.ofertas.length ? '<div class="en-grade">' + p.ofertas.map((f) => cardOferta(f, o)).join('') + '</div>' : '<p class="en-meta">Nenhuma oferta publicada no momento.</p>') +
        (p.sou_eu ? '<p><button class="' + cls(o, 'suave') + '" data-ir="ensino">Gerenciar meu perfil e ofertas</button></p>' : '') + '</div>';
      el.addEventListener('click', (ev) => { const b = ev.target.closest('[data-ir]'); if (b) o.ir(b.dataset.ir); });
    } catch (e) { el.innerHTML = '<div class="vazio">' + esc(e.message) + '</div>'; }
  }

  // ------------------------------------------------------------ oferta pública
  async function montarOferta(el, o) {
    el.innerHTML = '<p class="carregando">Carregando…</p>';
    try {
      const f = await o.api('/ofertas/' + o.id);
      const p = f.profissional;
      const precos = [];
      if (f.valor_aula != null) precos.push(['Aula avulsa', brl(f.valor_aula)]);
      if (f.valor_pacote != null) precos.push(['Pacote' + (f.aulas_pacote ? ' de ' + f.aulas_pacote + ' aulas' : ''), brl(f.valor_pacote) + (f.validade_pacote_dias ? ' · válido por ' + f.validade_pacote_dias + ' dias' : '')]);
      if (f.valor_mensal != null) precos.push(['Mensalidade', brl(f.valor_mensal)]);
      if (f.aula_experimental) precos.push(['Aula experimental', f.valor_experimental ? brl(f.valor_experimental) : 'Gratuita']);
      el.innerHTML = '<div class="en"><a class="en-voltar" href="' + esc(o.href('aulas')) + '">← Aulas e Treinamentos</a>' +
        '<div class="en-cab"><span class="tag roxo">' + esc(f.tipo_oferta_nome) + '</span> ' + (f.status !== 'publicada' ? '<span class="tag laranja">' + esc(f.status_nome) + '</span>' : '') +
        '<h2>' + modIcone(f.modalidade) + ' ' + esc(f.titulo) + '</h2><div class="en-meta">' + esc(f.modalidade ? f.modalidade.nome : '') + ' • ' + esc(f.formato_nome) + ' • ' + f.duracao_min + ' min • até ' + f.capacidade + ' aluno' + (f.capacidade > 1 ? 's' : '') +
        ' • ' + esc(f.nivel_nome || 'Todos os níveis') + (f.faixa_etaria_min != null || f.faixa_etaria_max != null ? ' • ' + (f.faixa_etaria_min ?? 0) + ' a ' + (f.faixa_etaria_max ?? '+') + ' anos' : '') + '</div></div>' +
        (f.descricao ? '<div class="en-bloco"><p>' + esc(f.descricao).replace(/\n/g, '<br>') + '</p></div>' : '') +
        '<div class="en-bloco"><h3>Valores</h3><div class="en-precos">' + (precos.length ? precos.map(([k, v]) => '<div><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>').join('') : '<p class="en-meta">Valores a combinar com o profissional.</p>') + '</div></div>' +
        (f.horarios.length ? '<div class="en-bloco"><h3>Horários</h3><div class="en-horarios">' + f.horarios.map((h) => '<span class="tag cinza">' + esc(h.dia) + ' · ' + esc(h.hora_inicio) + ' (' + h.duracao_min + ' min)</span>').join('') + '</div></div>' : '') +
        '<div class="en-bloco"><h3>Local</h3><p>' + esc([f.local_texto, f.endereco].filter(Boolean).join(' — ') || 'A combinar') + (f.distancia ? '<br><span class="en-meta">a ' + esc(f.distancia) + ' de você</span>' : '') + '</p>' +
        (f.latitude != null ? '<a href="https://www.openstreetmap.org/directions?to=' + f.latitude + '%2C' + f.longitude + '" target="_blank" rel="noopener">Como chegar</a>' : '') + '</div>' +
        '<div class="en-bloco"><h3>Profissional</h3><a class="en-card" href="' + esc(o.href('profissional', p.id)) + '"><span class="en-foto">' + (p.foto_url ? '<img src="' + esc(p.foto_url) + '" alt="">' : esc((p.nome || '?').slice(0, 2).toUpperCase())) +
        '</span><div class="en-card-corpo"><b>' + esc(p.nome) + '</b> ' + (p.verificado ? '<span class="tag verde">✅ Verificado</span>' : '') + '</div></a>' + contatos(p, o) + '</div></div>';
    } catch (e) { el.innerHTML = '<div class="vazio">' + esc(e.message) + '</div>'; }
  }

  // ------------------------------------------------------------ seletor de local (mapa)
  function seletorLocal(cont, ini, aoMudar, o) {
    if (!window.L) { cont.innerHTML = '<p class="en-meta">Mapa indisponível sem conexão.</p>'; return; }
    const centro = ini && ini.lat != null ? [ini.lat, ini.lng] : (o.pos ? [o.pos.lat, o.pos.lng] : [-20.4697, -54.6201]);
    const mapa = window.L.map(cont).setView(centro, ini && ini.lat != null ? 15 : 12);
    let pino = null;
    const definir = (la, ln) => {
      if (!pino) { pino = window.L.marker([la, ln], { draggable: true }).addTo(mapa); pino.on('dragend', () => { const q = pino.getLatLng(); aoMudar(q.lat, q.lng); }); } else pino.setLatLng([la, ln]);
      aoMudar(la, ln);
    };
    if (window.PlayGoMapa) window.PlayGoMapa.preparar(mapa, { aoEscolher: (la, ln) => definir(la, ln), marcar: false });
    else window.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(mapa);
    mapa.on('click', (e) => definir(e.latlng.lat, e.latlng.lng));
    if (ini && ini.lat != null) definir(ini.lat, ini.lng);
    setTimeout(() => mapa.invalidateSize(), 250);
    return { definir, mapa };
  }

  // ------------------------------------------------------------ Minhas aulas (gestão do profissional)
  const campo = (rotulo, html, dica) => '<div class="en-campo"><label>' + rotulo + '</label>' + html + (dica ? '<small>' + dica + '</small>' : '') + '</div>';
  const TAG_STATUS = { rascunho: 'cinza', em_analise: 'laranja', aprovado: 'verde', reprovado: 'rosa', suspenso: 'rosa', publicada: 'verde', pausada: 'laranja', encerrada: 'cinza', pendente: 'laranja', recusado: 'rosa' };

  async function montarMinhas(el, o) {
    el.innerHTML = '<p class="carregando">Carregando…</p>';
    let D;
    const recarregar = async () => { try { D = await o.api('/ensino/meu'); } catch (e) { el.innerHTML = '<div class="vazio">' + esc(e.message) + '</div>'; return false; } desenhar(); return true; };

    function blocoPerfil() {
      const p = D.perfil;
      if (!p) {
        return '<div class="en-bloco"><h3>Dar aulas no PlayGo</h3><p>Cadastre seu perfil profissional, envie seus documentos e, depois da aprovação, divulgue aulas particulares, pacotes e turmas para quem procura por perto.</p>' +
          '<button class="' + cls(o, 'primario') + '" data-a="criar-perfil">Criar meu perfil profissional</button></div>';
      }
      const pend = p.pendencias.length && (p.status === 'rascunho' || p.status === 'reprovado') ? '<div class="aviso alerta en-aviso"><b>Falta para enviar:</b> ' + p.pendencias.map(esc).join(', ') + '.</div>' : '';
      const passo = { rascunho: 'Complete o perfil, anexe os documentos e envie para análise.', em_analise: 'A administração está conferindo seus documentos. Você será avisado.', aprovado: 'Perfil aprovado: você pode publicar ofertas.', reprovado: 'Seu perfil foi recusado. Corrija o que foi pedido e envie de novo.', suspenso: 'Seu perfil está suspenso. Fale com a administração.' }[p.status];
      return '<div class="en-bloco en-status"><div><span class="tag ' + (TAG_STATUS[p.status] || 'cinza') + '">' + esc(p.status_nome) + '</span> <b>' + esc(p.nome) + '</b></div><p>' + esc(passo) + '</p>' +
        (p.motivo_recusa ? '<div class="aviso erro en-aviso"><b>Motivo:</b> ' + esc(p.motivo_recusa) + '</div>' : '') + pend +
        '<div class="en-botoes"><button class="' + cls(o, 'suave') + '" data-a="editar-perfil">Editar perfil</button>' +
        ((p.status === 'rascunho' || p.status === 'reprovado') ? '<button class="' + cls(o, 'primario') + '" data-a="enviar-analise">Enviar para análise</button>' : '') +
        '<a class="' + cls(o, 'suave') + '" href="' + esc(o.href('profissional', p.id)) + '">Ver como aluno</a></div></div>';
    }

    function blocoDocumentos() {
      const p = D.perfil;
      if (!p) return '';
      const lista = p.documentos.map((d) => '<div class="en-linha"><div><b>' + esc(d.tipo_nome) + '</b> <span class="tag ' + (TAG_STATUS[d.status] || 'cinza') + '">' + esc(d.status === 'pendente' ? 'Em análise' : d.status === 'aprovado' ? 'Aprovado' : 'Recusado') + '</span>' +
        '<div class="en-meta">' + esc(d.nome_original || '') + ' • enviado em ' + dataBr(d.enviado_em) + (d.validade ? ' • validade ' + dataBr(d.validade) : '') + '</div>' + (d.motivo ? '<div class="en-meta en-erro">Motivo: ' + esc(d.motivo) + '</div>' : '') + '</div>' +
        '<div class="en-botoes"><button class="' + cls(o, 'suave') + ' pequeno" data-a="ver-doc" data-id="' + d.id + '">Ver</button>' +
        (!(d.status === 'aprovado' && p.status === 'aprovado') ? '<button class="' + cls(o, 'perigo') + ' pequeno" data-a="remover-doc" data-id="' + d.id + '">Remover</button>' : '') + '</div></div>').join('');
      return '<div class="en-bloco"><h3>Documentos</h3><p class="en-meta">Seus documentos ficam privados: só você e a administração do PlayGo veem.</p>' + (lista || '<p class="en-meta">Nenhum documento enviado ainda.</p>') +
        '<form class="en-form" data-f="documento">' + campo('Tipo', '<select name="tipo">' + Object.entries(p.tipos_documento).map(([k, v]) => '<option value="' + k + '">' + esc(v) + '</option>').join('') + '</select>') +
        campo('Arquivo (PDF ou foto)', '<input type="file" name="arquivo" accept=".pdf,image/*" required>') + campo('Validade (opcional)', '<input type="date" name="validade">') +
        '<button class="' + cls(o, 'primario') + '">Enviar documento</button></form>' +
        '<label class="en-check"><input type="checkbox" data-a="aceite" ' + (p.aceite_termo_em ? 'checked disabled' : '') + '> Li e aceito o <b>Termo de Adesão do Profissional</b>: declaro que as informações e documentos são verdadeiros, que tenho a habilitação necessária para as atividades que ofereço e que o PlayGo é apenas a plataforma de divulgação' +
        (p.aceite_termo_em ? ' <span class="en-meta">(aceito em ' + dataBr(p.aceite_termo_em) + ')</span>' : '') + '</label></div>';
    }

    function blocoPlano() {
      const pl = D.plano;
      const txt = pl.status === 'administrador' ? 'Administrador: sem cobrança.' : pl.status === 'teste' ? '🎁 Teste grátis do plano ' + esc(pl.plano) + ' até ' + dataBr(pl.fim) + '.' : pl.status === 'ativa' ? '✅ Plano ativo até ' + dataBr(pl.fim) + '.' :
        pl.status === 'sem_plano' ? 'Você ainda não tem plano de profissional. O teste grátis começa quando você publicar a primeira oferta.' : 'Seu acesso de profissional terminou. Assine um plano para voltar a divulgar.';
      return '<div class="en-bloco"><h3>Plano de profissional</h3><p>' + txt + '</p><button class="' + cls(o, 'suave') + '" data-ir="planos">Ver planos</button></div>';
    }

    function blocoOfertas() {
      const p = D.perfil;
      if (!p) return '';
      const lista = D.ofertas.map((f) => '<div class="en-linha"><div><span class="tag roxo">' + esc(f.tipo_oferta_nome) + '</span> <span class="tag ' + (TAG_STATUS[f.status] || 'cinza') + '">' + esc(f.status_nome) + '</span>' +
        '<div><b>' + esc(f.titulo) + '</b></div><div class="en-meta">' + esc(f.preco_texto) + (f.horarios_texto ? ' • ' + esc(f.horarios_texto) : '') + '</div></div>' +
        '<div class="en-botoes"><button class="' + cls(o, 'suave') + ' pequeno" data-a="editar-oferta" data-id="' + f.id + '">Editar</button>' +
        (f.status === 'publicada' ? '<button class="' + cls(o, 'suave') + ' pequeno" data-a="pausar" data-id="' + f.id + '">Pausar</button>' : f.status !== 'encerrada' ? '<button class="' + cls(o, 'primario') + ' pequeno" data-a="publicar" data-id="' + f.id + '">Publicar</button>' : '') +
        (f.status !== 'encerrada' ? '<button class="' + cls(o, 'perigo') + ' pequeno" data-a="encerrar" data-id="' + f.id + '">Encerrar</button>' : '') +
        '<a class="' + cls(o, 'suave') + ' pequeno" href="' + esc(o.href('oferta', f.id)) + '">Ver</a></div></div>').join('');
      return '<div class="en-bloco"><h3>Minhas ofertas</h3>' + (lista || '<p class="en-meta">Você ainda não criou nenhuma oferta. Comece por uma aula avulsa ou uma turma.</p>') +
        '<button class="' + cls(o, 'primario') + '" data-a="nova-oferta">+ Nova oferta</button><div id="en-form-oferta"></div></div>';
    }

    function desenhar() {
      el.innerHTML = '<div class="en"><div class="en-hero"><div><h2>🧑‍🏫 Minhas aulas</h2><p>Perfil profissional, documentos e ofertas de aulas, pacotes e turmas.</p></div>' +
        '<a class="' + cls(o, 'suave') + '" href="' + esc(o.href('aulas')) + '">Ver Central de aulas</a></div>' + blocoPerfil() + '<div id="en-form-perfil"></div>' + blocoDocumentos() + blocoPlano() + blocoOfertas() + '</div>';
    }

    // ---- formulários
    function formPerfil() {
      const p = D.perfil || {};
      const alvo = el.querySelector('#en-form-perfil');
      alvo.innerHTML = '<div class="en-bloco"><h3>' + (D.perfil ? 'Editar perfil' : 'Novo perfil') + '</h3><form class="en-form" data-f="perfil">' +
        campo('Nome profissional', '<input name="nome_profissional" required maxlength="120" value="' + esc(p.nome || '') + '">') +
        campo('Apresentação', '<textarea name="apresentacao" rows="4" placeholder="Sua formação, experiência e como são suas aulas">' + esc(p.apresentacao || '') + '</textarea>') +
        '<div class="en-duas">' + campo('Registro profissional', '<select name="registro_tipo"><option value="">Nenhum</option>' + ['CREF', 'Federação ou confederação', 'Outro'].map((t) => '<option' + (p.registro_tipo === t ? ' selected' : '') + '>' + t + '</option>').join('') + '</select>') +
        campo('Número do registro', '<input name="registro_numero" maxlength="40" value="' + esc(p.registro_numero || '') + '">', 'Se informar, será pedido o comprovante.') + '</div>' +
        '<div class="en-duas">' + campo('Telefone', '<input name="telefone" inputmode="tel" value="' + esc(p.telefone || '') + '">') + campo('WhatsApp', '<input name="whatsapp" inputmode="tel" value="' + esc(p.whatsapp || '') + '">') + '</div>' +
        campo('E-mail de contato', '<input name="email_contato" type="email" value="' + esc(p.email_contato || '') + '">') +
        '<div class="en-duas">' + campo('Cidade', '<input name="cidade" required value="' + esc(p.cidade || '') + '">') + campo('Região de atendimento', '<input name="regiao" placeholder="Bairros ou zonas" value="' + esc(p.regiao || '') + '">') + '</div>' +
        campo('Atende até quantos km do ponto marcado?', '<select name="raio_atendimento_km">' + [3, 5, 10, 20, 30, 50].map((r) => '<option value="' + r + '"' + ((p.raio_atendimento_km || 10) === r ? ' selected' : '') + '>' + r + ' km</option>').join('') + '</select>') +
        campo('Modalidades que você ensina', '<div class="en-checks">' + D.modalidades.map((m) => '<label><input type="checkbox" name="modalidades" value="' + esc(m.codigo) + '"' + ((p.modalidades || []).includes(m.codigo) ? ' checked' : '') + '> ' + esc(m.icone) + ' ' + esc(m.nome) + '</label>').join('') + '</div>') +
        campo('Especialidades', '<textarea name="especialidades" rows="2" placeholder="Ex.: emagrecimento, preparação física, iniciantes, crianças">' + esc(p.especialidades || '') + '</textarea>') +
        campo('Ponto de atendimento', '<div class="en-mapa pequeno" id="en-mapa-perfil"></div>', 'Busque o endereço na lupa ou toque no mapa. Dá para arrastar o pino.') +
        '<input type="hidden" name="latitude" value="' + (p.latitude ?? '') + '"><input type="hidden" name="longitude" value="' + (p.longitude ?? '') + '">' +
        '<div class="en-botoes"><button class="' + cls(o, 'primario') + '">Salvar perfil</button><button type="button" class="' + cls(o, 'suave') + '" data-a="cancelar-form">Cancelar</button></div></form>' +
        (D.perfil ? '<form class="en-form" data-f="foto"><h4>Foto profissional</h4><input type="file" name="arquivo" accept="image/*" required> <button class="' + cls(o, 'suave') + '">Enviar foto</button></form>' : '') + '</div>';
      const f = alvo.querySelector('[data-f="perfil"]');
      seletorLocal(alvo.querySelector('#en-mapa-perfil'), p.latitude != null ? { lat: p.latitude, lng: p.longitude } : null, (la, ln) => { f.latitude.value = la.toFixed(6); f.longitude.value = ln.toFixed(6); }, o);
      alvo.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function linhaHorario(h) {
      h = h || { dia_semana: 1, hora_inicio: '19:00', duracao_min: 60 };
      return '<div class="en-hor"><select name="h_dia">' + DIAS.map((d, i) => '<option value="' + i + '"' + (h.dia_semana === i ? ' selected' : '') + '>' + d + '</option>').join('') + '</select>' +
        '<input type="time" name="h_hora" value="' + esc(h.hora_inicio) + '" required><input type="number" name="h_dur" min="15" max="480" value="' + (h.duracao_min || 60) + '" title="Duração em minutos"> min ' +
        '<button type="button" class="' + cls(o, 'perigo') + ' pequeno" data-a="rem-hor">✕</button></div>';
    }

    function formOferta(f) {
      const alvo = el.querySelector('#en-form-oferta');
      const v = f || { tipo: 'aula', formato: 'individual', duracao_min: 60, capacidade: 1, horarios: [] };
      alvo.innerHTML = '<form class="en-form en-bloco" data-f="oferta" data-id="' + (f ? f.id : '') + '"><h4>' + (f ? 'Editar oferta' : 'Nova oferta') + '</h4>' +
        campo('Tipo', '<select name="tipo">' + Object.entries(D.tipos_oferta).map(([k, n]) => '<option value="' + k + '"' + (v.tipo_oferta === k || v.tipo === k ? ' selected' : '') + '>' + esc(n) + '</option>').join('') + '</select>', 'Aula avulsa: uma aula. Pacote: várias aulas com validade. Turma: dias e horários fixos, com mensalidade.') +
        campo('Título', '<input name="titulo" required maxlength="160" placeholder="Ex.: Beach tennis para iniciantes" value="' + esc(v.titulo || '') + '">') +
        campo('Modalidade', '<select name="modalidade"><option value="">Escolha</option>' + D.modalidades.map((m) => '<option value="' + esc(m.codigo) + '"' + (v.modalidade && v.modalidade.codigo === m.codigo ? ' selected' : '') + '>' + esc(m.icone) + ' ' + esc(m.nome) + '</option>').join('') + '</select>') +
        campo('Descrição', '<textarea name="descricao" rows="3">' + esc(v.descricao || '') + '</textarea>') +
        '<div class="en-duas">' + campo('Nível', '<select name="nivel">' + Object.entries(NIVEIS).map(([k, n]) => '<option value="' + k + '"' + ((v.nivel || 'todos') === k ? ' selected' : '') + '>' + n + '</option>').join('') + '</select>') +
        campo('Formato', '<select name="formato"><option value="individual"' + (v.formato === 'individual' ? ' selected' : '') + '>Individual</option><option value="coletiva"' + (v.formato === 'coletiva' ? ' selected' : '') + '>Em grupo</option></select>') + '</div>' +
        '<div class="en-duas">' + campo('Idade mínima', '<input type="number" name="faixa_etaria_min" min="0" max="120" value="' + (v.faixa_etaria_min ?? '') + '">') + campo('Idade máxima', '<input type="number" name="faixa_etaria_max" min="0" max="120" value="' + (v.faixa_etaria_max ?? '') + '">') + '</div>' +
        '<div class="en-duas">' + campo('Duração (min)', '<input type="number" name="duracao_min" min="15" max="480" value="' + (v.duracao_min || 60) + '">') + campo('Alunos por aula/turma', '<input type="number" name="capacidade" min="1" max="500" value="' + (v.capacidade || 1) + '">') + '</div>' +
        '<div class="en-precos-form"><div class="en-duas">' + campo('Valor da aula (R$)', '<input name="valor_aula" inputmode="decimal" value="' + (v.valor_aula ?? '') + '">') + campo('Mensalidade da turma (R$)', '<input name="valor_mensal" inputmode="decimal" value="' + (v.valor_mensal ?? '') + '">') + '</div>' +
        '<div class="en-duas">' + campo('Valor do pacote (R$)', '<input name="valor_pacote" inputmode="decimal" value="' + (v.valor_pacote ?? '') + '">') + campo('Aulas no pacote', '<input type="number" name="aulas_pacote" min="1" value="' + (v.aulas_pacote ?? '') + '">') + '</div>' +
        campo('Validade do pacote (dias)', '<input type="number" name="validade_pacote_dias" min="1" value="' + (v.validade_pacote_dias ?? '') + '">') + '</div>' +
        '<label class="en-check"><input type="checkbox" name="aula_experimental"' + (v.aula_experimental ? ' checked' : '') + '> Ofereço aula experimental</label>' +
        campo('Valor da aula experimental (R$)', '<input name="valor_experimental" inputmode="decimal" placeholder="Vazio = gratuita" value="' + (v.valor_experimental ?? '') + '">') +
        campo('Local', '<input name="local_texto" maxlength="200" placeholder="Ex.: Parque das Nações, Academia X, quadra do condomínio" value="' + esc(v.local_texto || '') + '">') +
        campo('Endereço', '<input name="endereco" maxlength="300" value="' + esc(v.endereco || '') + '">') +
        campo('Ponto no mapa (opcional)', '<div class="en-mapa pequeno" id="en-mapa-oferta"></div>', 'Sem marcar, usamos o ponto do seu perfil.') +
        '<input type="hidden" name="latitude" value="' + (f && f.latitude != null ? f.latitude : '') + '"><input type="hidden" name="longitude" value="' + (f && f.longitude != null ? f.longitude : '') + '">' +
        '<h4>Horários semanais</h4><div id="en-horarios">' + (v.horarios || []).map(linhaHorario).join('') + '</div><button type="button" class="' + cls(o, 'suave') + ' pequeno" data-a="add-hor">+ Horário</button>' +
        '<div class="en-botoes"><button class="' + cls(o, 'primario') + '">Salvar oferta</button><button type="button" class="' + cls(o, 'suave') + '" data-a="cancelar-form">Cancelar</button></div></form>';
      const form = alvo.querySelector('form');
      seletorLocal(alvo.querySelector('#en-mapa-oferta'), f && f.latitude != null ? { lat: f.latitude, lng: f.longitude } : null, (la, ln) => { form.latitude.value = la.toFixed(6); form.longitude.value = ln.toFixed(6); }, o);
      alvo.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // ---- eventos
    const num = (x) => (x === '' || x == null ? null : Number(String(x).replace(',', '.')));
    const tratar = async (fn) => { try { await fn(); } catch (e) { o.toast(e.message); } };

    el.addEventListener('submit', (ev) => {
      const f = ev.target.closest('form[data-f]');
      if (!f) return;
      ev.preventDefault();
      const d = new FormData(f);
      const tipo = f.dataset.f;
      tratar(async () => {
        if (tipo === 'perfil') {
          await o.api('/ensino/meu/perfil', { metodo: 'PUT', corpo: {
            nome_profissional: d.get('nome_profissional'), apresentacao: d.get('apresentacao'), registro_tipo: d.get('registro_tipo'), registro_numero: d.get('registro_numero'), telefone: d.get('telefone'), whatsapp: d.get('whatsapp'),
            email_contato: d.get('email_contato'), cidade: d.get('cidade'), regiao: d.get('regiao'), raio_atendimento_km: num(d.get('raio_atendimento_km')), modalidades: d.getAll('modalidades'), especialidades: d.get('especialidades'),
            latitude: num(d.get('latitude')), longitude: num(d.get('longitude')) } });
          o.toast('Perfil salvo.');
        } else if (tipo === 'foto') {
          await o.api('/ensino/meu/foto', { metodo: 'POST', form: d }); o.toast('Foto atualizada.');
        } else if (tipo === 'documento') {
          if (!d.get('validade')) d.delete('validade');
          await o.api('/ensino/meu/documentos', { metodo: 'POST', form: d }); o.toast('Documento enviado.');
        } else if (tipo === 'oferta') {
          const horarios = [...f.querySelectorAll('.en-hor')].map((h) => ({ dia_semana: Number(h.querySelector('[name=h_dia]').value), hora_inicio: h.querySelector('[name=h_hora]').value, duracao_min: num(h.querySelector('[name=h_dur]').value) }));
          const corpo = { tipo: d.get('tipo'), titulo: d.get('titulo'), modalidade: d.get('modalidade') || null, descricao: d.get('descricao'), nivel: d.get('nivel'), formato: d.get('formato'),
            faixa_etaria_min: num(d.get('faixa_etaria_min')), faixa_etaria_max: num(d.get('faixa_etaria_max')), duracao_min: num(d.get('duracao_min')), capacidade: num(d.get('capacidade')),
            valor_aula: num(d.get('valor_aula')), valor_mensal: num(d.get('valor_mensal')), valor_pacote: num(d.get('valor_pacote')), aulas_pacote: num(d.get('aulas_pacote')), validade_pacote_dias: num(d.get('validade_pacote_dias')),
            aula_experimental: !!d.get('aula_experimental'), valor_experimental: num(d.get('valor_experimental')), local_texto: d.get('local_texto'), endereco: d.get('endereco'),
            latitude: num(d.get('latitude')), longitude: num(d.get('longitude')), horarios };
          if (f.dataset.id) await o.api('/ensino/ofertas/' + f.dataset.id, { metodo: 'PUT', corpo }); else await o.api('/ensino/ofertas', { metodo: 'POST', corpo });
          o.toast('Oferta salva.');
        }
        await recarregar();
      });
    });

    el.addEventListener('click', (ev) => {
      const ir = ev.target.closest('[data-ir]');
      if (ir) { o.ir(ir.dataset.ir); return; }
      const b = ev.target.closest('[data-a]');
      if (!b || b.tagName === 'INPUT' && b.type === 'checkbox' && b.dataset.a === 'aceite') return;
      const a = b.dataset.a, id = b.dataset.id;
      tratar(async () => {
        if (a === 'criar-perfil' || a === 'editar-perfil') formPerfil();
        else if (a === 'cancelar-form') { el.querySelector('#en-form-perfil').innerHTML = ''; el.querySelector('#en-form-oferta').innerHTML = ''; }
        else if (a === 'enviar-analise') { await o.api('/ensino/meu/analise', { metodo: 'POST', corpo: {} }); o.toast('Enviado para análise.'); await recarregar(); }
        else if (a === 'ver-doc') o.abrir('/ensino/documentos/' + id + '/arquivo');
        else if (a === 'remover-doc') { if (!confirm('Remover este documento?')) return; await o.api('/ensino/meu/documentos/' + id, { metodo: 'DELETE' }); await recarregar(); }
        else if (a === 'nova-oferta') formOferta(null);
        else if (a === 'editar-oferta') formOferta(D.ofertas.find((x) => String(x.id) === id));
        else if (a === 'add-hor') { el.querySelector('#en-horarios').insertAdjacentHTML('beforeend', linhaHorario()); }
        else if (a === 'rem-hor') b.closest('.en-hor').remove();
        else if (a === 'publicar') { await o.api('/ensino/ofertas/' + id + '/publicar', { metodo: 'POST', corpo: {} }); o.toast('Oferta publicada!'); await recarregar(); }
        else if (a === 'pausar') { await o.api('/ensino/ofertas/' + id + '/pausar', { metodo: 'POST', corpo: {} }); await recarregar(); }
        else if (a === 'encerrar') { if (!confirm('Encerrar esta oferta? Ela sai da busca e não pode ser reaberta.')) return; await o.api('/ensino/ofertas/' + id + '/encerrar', { metodo: 'POST', corpo: {} }); await recarregar(); }
      });
    });
    el.addEventListener('change', (ev) => {
      if (ev.target.dataset && ev.target.dataset.a === 'aceite' && ev.target.checked) tratar(async () => { await o.api('/ensino/meu/aceite', { metodo: 'POST', corpo: {} }); o.toast('Termo aceito.'); await recarregar(); });
    });

    await recarregar();
  }

  // ------------------------------------------------------------ fila de aprovação (administração)
  async function montarAdmin(el, o) {
    let status = 'em_analise';
    async function carregar() {
      el.innerHTML = '<p class="carregando">Carregando…</p>';
      try {
        const lista = await o.api('/admin/profissionais?status=' + status);
        el.innerHTML = '<div class="en"><div class="en-botoes">' + [['em_analise', 'Em análise'], ['aprovado', 'Aprovados'], ['reprovado', 'Recusados'], ['suspenso', 'Suspensos'], ['rascunho', 'Rascunhos']].map(([k, n]) =>
          '<button class="' + cls(o, k === status ? 'primario' : 'suave') + ' pequeno" data-st="' + k + '">' + n + '</button>').join('') + '</div>' +
          (lista.length ? lista.map((p) => '<div class="en-bloco" data-p="' + p.id + '"><div><span class="tag ' + (TAG_STATUS[p.status] || 'cinza') + '">' + esc(p.status_nome) + '</span> <b>' + esc(p.nome) + '</b> <span class="en-meta">' + esc(p.usuario) + ' • ' + esc(p.cidade || '') + '</span></div>' +
            '<div class="en-meta">' + esc([p.registro_tipo, p.registro_numero].filter(Boolean).join(' ')) + (p.telefone ? ' • ' + esc(p.telefone) : '') + (p.enviado_analise_em ? ' • enviado em ' + dataBr(p.enviado_analise_em) : '') + '</div>' +
            (p.apresentacao ? '<p>' + esc(p.apresentacao).slice(0, 400) + '</p>' : '') +
            p.documentos.map((d) => '<div class="en-linha"><div><b>' + esc(d.tipo_nome) + '</b> <span class="tag ' + (TAG_STATUS[d.status] || 'cinza') + '">' + esc(d.status) + '</span>' + (d.motivo ? '<div class="en-meta en-erro">' + esc(d.motivo) + '</div>' : '') + '</div>' +
              '<div class="en-botoes"><button class="' + cls(o, 'suave') + ' pequeno" data-a="ver" data-id="' + d.id + '">Ver</button><button class="' + cls(o, 'primario') + ' pequeno" data-a="doc-ok" data-id="' + d.id + '">Aprovar</button><button class="' + cls(o, 'perigo') + ' pequeno" data-a="doc-no" data-id="' + d.id + '">Recusar</button></div></div>').join('') +
            '<div class="en-botoes">' + (p.status === 'em_analise' || p.status === 'reprovado' ? '<button class="' + cls(o, 'primario') + '" data-a="ok" data-id="' + p.id + '">Aprovar profissional</button><button class="' + cls(o, 'perigo') + '" data-a="no" data-id="' + p.id + '">Recusar</button>' : '') +
            (p.status === 'aprovado' ? '<button class="' + cls(o, 'perigo') + '" data-a="susp" data-id="' + p.id + '">Suspender</button>' : '') + (p.status === 'suspenso' ? '<button class="' + cls(o, 'primario') + '" data-a="reativar" data-id="' + p.id + '">Reativar</button>' : '') + '</div></div>').join('') : '<p class="en-meta">Nenhum perfil nesta lista.</p>') + '</div>';
      } catch (e) { el.innerHTML = '<div class="vazio">' + esc(e.message) + '</div>'; }
    }
    el.addEventListener('click', async (ev) => {
      const st = ev.target.closest('[data-st]');
      if (st) { status = st.dataset.st; carregar(); return; }
      const b = ev.target.closest('[data-a]');
      if (!b) return;
      const id = b.dataset.id, a = b.dataset.a;
      try {
        if (a === 'ver') o.abrir('/ensino/documentos/' + id + '/arquivo');
        else if (a === 'doc-ok') { await o.api('/admin/profissionais/documentos/' + id + '/decidir', { metodo: 'POST', corpo: { aprovar: true } }); carregar(); }
        else if (a === 'doc-no') { const m = prompt('Motivo da recusa do documento:'); if (!m) return; await o.api('/admin/profissionais/documentos/' + id + '/decidir', { metodo: 'POST', corpo: { aprovar: false, motivo: m } }); carregar(); }
        else if (a === 'ok') { await o.api('/admin/profissionais/' + id + '/decidir', { metodo: 'POST', corpo: { aprovar: true } }); o.toast('Profissional aprovado.'); carregar(); }
        else if (a === 'no') { const m = prompt('Motivo da recusa (o profissional verá):'); if (!m) return; await o.api('/admin/profissionais/' + id + '/decidir', { metodo: 'POST', corpo: { aprovar: false, motivo: m } }); carregar(); }
        else if (a === 'susp') { const m = prompt('Motivo da suspensão:'); if (!m) return; await o.api('/admin/profissionais/' + id + '/suspender', { metodo: 'POST', corpo: { motivo: m } }); carregar(); }
        else if (a === 'reativar') { await o.api('/admin/profissionais/' + id + '/suspender', { metodo: 'POST', corpo: { motivo: '', reativar: true } }); carregar(); }
      } catch (e) { o.toast(e.message); }
    });
    carregar();
  }

  window.Ensino = { montarCentral, montarProfissional, montarOferta, montarMinhas, montarAdmin };
})();
