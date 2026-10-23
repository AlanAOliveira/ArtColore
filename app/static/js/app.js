// Utilitários comuns: moeda, API, seletor de itens e leitor por câmera.
const CSRF = document.querySelector('meta[name=csrf-token]')?.content || '';

function parseBR(t) {
  t = String(t ?? '').replace('R$', '').replace('%', '').trim();
  if (!t) return null;
  if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.');
  const n = parseFloat(t);
  return isNaN(n) ? null : n;
}
const centavos = t => { const n = parseBR(t); return n == null ? null : Math.round(n * 100); };
const brl = c => c == null ? '—' : (c / 100).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});
const brInput = c => c == null ? '' : (c / 100).toFixed(2).replace('.', ',');
const numBR = (n, casas = 3) => n == null ? '' : Number(n).toLocaleString('pt-BR', {maximumFractionDigits: casas});
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

async function api(url, opts = {}) {
  const o = {headers: {'X-CSRFToken': CSRF, 'Accept': 'application/json'}, ...opts};
  if (o.json !== undefined) { o.method = o.method || 'POST'; o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; delete o.json; }
  const r = await fetch(url, o);
  let dados = null;
  try { dados = await r.json(); } catch (e) { dados = {ok: false, erro: `Erro ${r.status}`}; }
  if (!r.ok && dados && dados.ok === undefined) dados.ok = false;
  return dados;
}

/**
 * Seletor de item: digite nome/código ou bipe o código de barras (Enter).
 * new ItemPicker(input, {natureza: 'produto', onSelect: m => ...})
 */
class ItemPicker {
  constructor(input, opts = {}) {
    this.input = input; this.opts = opts; this.itens = []; this.ativo = -1; this.timer = null;
    const wrap = document.createElement('div'); wrap.className = 'picker';
    input.parentNode.insertBefore(wrap, input); wrap.appendChild(input);
    this.lista = document.createElement('div'); this.lista.className = 'lista list-group shadow';
    wrap.appendChild(this.lista);
    input.setAttribute('autocomplete', 'off');
    input.addEventListener('input', () => { clearTimeout(this.timer); this.timer = setTimeout(() => this.buscar(), 250); });
    input.addEventListener('keydown', e => this.teclado(e));
    input.addEventListener('blur', () => setTimeout(() => this.fechar(), 200));
  }
  url(q) {
    const p = new URLSearchParams({q}); if (this.opts.natureza) p.set('natureza', this.opts.natureza);
    return '/materiais/api/buscar?' + p;
  }
  async buscar() {
    const q = this.input.value.trim();
    if (q.length < 2) return this.fechar();
    this.itens = await api(this.url(q)) || [];
    this.ativo = this.itens.length ? 0 : -1; this.render();
  }
  render() {
    if (!this.itens.length) { this.lista.innerHTML = '<div class="list-group-item text-muted small">Nenhum item</div>'; this.lista.classList.add('show'); return; }
    this.lista.innerHTML = this.itens.map((m, i) => `<div class="list-group-item ${i === this.ativo ? 'ativo' : ''}" data-i="${i}">
      <div class="d-flex justify-content-between"><span><code>${esc(m.id)}</code> ${esc(m.nome)}</span>
      <span class="small">${m.natureza === 'servico' ? '<span class="badge bg-info text-dark">serviço</span>' : brl(m.preco) + (m.promo ? ' <span class="badge bg-danger">promo</span>' : '')}</span></div>
      ${m.natureza === 'produto' ? `<small class="text-muted">saldo ${numBR(m.saldo)} ${m.unidade === 'metro' ? 'm' : 'un'}${m.codigo_barras ? ' · ' + esc(m.codigo_barras) : ''}</small>` : ''}</div>`).join('');
    this.lista.classList.add('show');
    this.lista.querySelectorAll('[data-i]').forEach(el => el.addEventListener('mousedown', e => { e.preventDefault(); this.escolher(this.itens[+el.dataset.i]); }));
  }
  fechar() { this.lista.classList.remove('show'); }
  async teclado(e) {
    if (e.key === 'ArrowDown' && this.itens.length) { e.preventDefault(); this.ativo = Math.min(this.ativo + 1, this.itens.length - 1); this.render(); }
    else if (e.key === 'ArrowUp' && this.itens.length) { e.preventDefault(); this.ativo = Math.max(this.ativo - 1, 0); this.render(); }
    else if (e.key === 'Escape') this.fechar();
    else if (e.key === 'Enter') {
      e.preventDefault(); clearTimeout(this.timer);
      const q = this.input.value.trim(); if (!q) return;
      if (this.lista.classList.contains('show') && this.ativo >= 0 && this.itens[this.ativo]) return this.escolher(this.itens[this.ativo]);
      await this.porCodigo(q);
    }
  }
  async porCodigo(q) {
    const r = await api('/materiais/api/codigo/' + encodeURIComponent(q));
    if (r && r.encontrado) {
      if (this.opts.natureza && r.material.natureza !== this.opts.natureza) return this.erro('Item não permitido aqui: ' + r.material.nome);
      return this.escolher(r.material);
    }
    await this.buscar();
    if (this.itens.length === 1) return this.escolher(this.itens[0]);
    if (!this.itens.length) this.erro('Código não encontrado: ' + q);
  }
  escolher(m) {
    this.fechar();
    if (!m.ativo) return this.erro(m.nome + ' está inativo.');
    this.input.value = this.opts.manterTexto ? `${m.id} · ${m.nome}` : '';
    this.opts.onSelect && this.opts.onSelect(m);
  }
  erro(msg) { this.opts.onErro ? this.opts.onErro(msg) : alert(msg); }
}

/** Leitor por câmera (html5-qrcode). abrirCamera(texto => ...) */
let _leitor = null;
function abrirCamera(callback) {
  const el = document.getElementById('modalCamera');
  if (!el || typeof Html5Qrcode === 'undefined') { alert('Leitor de câmera indisponível (sem internet para carregar a biblioteca?).'); return; }
  const modal = bootstrap.Modal.getOrCreateInstance(el);
  modal.show();
  const parar = () => { if (_leitor) { _leitor.stop().catch(() => {}).finally(() => { _leitor.clear(); _leitor = null; }); } };
  el.addEventListener('hidden.bs.modal', parar, {once: true});
  setTimeout(() => {
    _leitor = new Html5Qrcode('leitorCamera');
    _leitor.start({facingMode: 'environment'}, {fps: 10, qrbox: {width: 260, height: 160}},
      texto => { modal.hide(); callback(texto.trim()); }, () => {})
      .catch(err => { document.getElementById('leitorCamera').innerHTML = '<div class="alert alert-warning m-2">Não foi possível acessar a câmera: ' + esc(err) + '</div>'; });
  }, 300);
}

/** Linhas de itens para formulários de estoque. */
function LinhasItens(cfg) {
  const tbody = document.querySelector(cfg.tbody);
  const tpl = document.querySelector(cfg.template);
  function preencher(tr, m, dados = {}) {
    tr._m = m;
    tr.querySelector('[name=item_id]').value = m.id;
    tr.querySelector('.nome').innerHTML = `<code>${esc(m.id)}</code> ${esc(m.nome)}` +
      (m.saldo != null ? `<br><small class="text-muted">saldo ${numBR(m.saldo)} ${m.unidade === 'metro' ? 'm' : 'un'}</small>` : '');
    const un = tr.querySelector('[name=unidade]');
    un.innerHTML = m.unidade === 'metro' ? '<option value="metro">m</option>'
      : '<option value="item">un</option>' + ((m.fator_caixa || 1) > 1 ? `<option value="caixa">cx (${m.fator_caixa})</option>` : '');
    if (dados.unidade) un.value = dados.unidade;
    const qtd = tr.querySelector('[name=qtd]'); qtd.step = m.unidade === 'metro' ? '0.001' : '1';
    if (dados.qtd != null) qtd.value = dados.qtd;
    const val = tr.querySelector('[name=validade]');
    if (val) { val.closest('.campo-validade').classList.toggle('d-none', !m.controla_validade); val.required = !!m.controla_validade; if (dados.validade) val.value = dados.validade; }
    const loc = tr.querySelector('select[name=loc]');
    if (loc) loc.value = dados.loc ?? (cfg.locPadrao ? (m.localizacao_padrao_id || '') : '');
    const preco = tr.querySelector('[name=preco]');
    if (preco && dados.preco != null) preco.value = dados.preco;
    else if (preco && !preco.value && m.custo) {
      preco.value = brInput(un.value === 'caixa' ? m.custo * (m.fator_caixa || 1) : m.custo); preco.dataset.auto = '1';
      preco.addEventListener('input', () => delete preco.dataset.auto, {once: true});
    }
    tr.classList.remove('d-none');
    cfg.onChange && cfg.onChange();
  }
  function nova(m, dados) {
    const tr = tpl.content.firstElementChild.cloneNode(true);
    tbody.appendChild(tr);
    tr.querySelector('.remover').addEventListener('click', () => { tr.remove(); cfg.onChange && cfg.onChange(); });
    preencher(tr, m, dados);
    (tr.querySelector('[name=qtd]')).focus();
    return tr;
  }
  const picker = new ItemPicker(document.querySelector(cfg.busca), {natureza: 'produto', onSelect: m => nova(m)});
  (cfg.iniciais || []).forEach(l => nova(l.material, l));
  return {picker, nova};
}
