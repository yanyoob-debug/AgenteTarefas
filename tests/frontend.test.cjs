const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM } = require('jsdom');
const html = readFileSync('frontend/index.html', 'utf8');
const source = readFileSync('frontend/app.js', 'utf8');
const baseConfig = { today: { data: '2026-10-20', data_br: '20/10/2026', fuso: 'America/Sao_Paulo' }, default_mode: 'demo', board_name: 'DIO' };
const task = { id: 'demo-1', nome: 'Estudar funções', descricao: 'Praticar Python', status: 'A fazer', vencimento: '2026-10-20T21:00:00Z', url: null };
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(data) });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(setImmediate); };

async function setup(t, overrides = {}) {
  const dom = new JSDOM(html, { url: 'http://localhost:8765', runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  const { window } = dom;
  const calls = [];
  window.HTMLElement.prototype.scrollIntoView = function () {};
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; };
  window.fetch = async (path, options = {}) => {
    const method = options.method || 'GET'; calls.push({ path, method, options });
    const key = `${method} ${path.split('?')[0]}`;
    if (overrides[key]) return overrides[key](path, options);
    if (path === '/api/config') return reply(baseConfig);
    if (path.startsWith('/api/tasks') && method === 'GET') return reply({ tarefas: [task] });
    if (path.startsWith('/api/tasks') && method === 'PATCH') return reply({ ok: true });
    if (path === '/api/tasks?mode=demo' && method === 'POST') return reply({ ok: true }, 201);
    if (path.startsWith('/api/chat')) return reply({ reply: 'Resposta simulada', session_id: null });
    throw new Error(`Unexpected request ${method} ${path}`);
  };
  window.eval(source + '\nwindow.hooks={state,api,loadTasks,showTaskDialog,sendMessage,safeLink,render};');
  await flush();
  return { window, document: window.document, calls, hooks: window.hooks, overrides };
}

test('inicialização exibe dados, resumo e mensagem sem herdar o estilo do painel', async t => {
  const { document } = await setup(t);
  assert.equal(document.querySelector('#total-count').textContent, '1');
  assert.equal(document.querySelectorAll('.column').length, 3);
  assert.equal(document.querySelectorAll('.messages .assistant').length, 0);
  assert.match(document.querySelector('.message-text').textContent, /demonstração/);
});

test('HTML e scripts nos cards são exibidos como texto, sem criar elementos', async t => {
  const payload = '<img src=x onerror="alert(1)"><script>alert(1)</script>';
  const { document } = await setup(t, { 'GET /api/tasks': () => reply({ tarefas: [{ ...task, nome: payload, descricao: payload }] }) });
  assert.equal(document.querySelector('.task-card h3').textContent, payload);
  assert.equal(document.querySelectorAll('.task-card img, .task-card script').length, 0);
});

test('links de scripts, outros domínios e HTTP são rejeitados', async t => {
  const { hooks } = await setup(t);
  for (const url of ['javascript:alert(1)', 'https://trello.com.evil.test/a', 'http://trello.com/a', '//trello.com/a', null]) assert.equal(hooks.safeLink(url), null);
  assert.equal(hooks.safeLink('https://trello.com/c/abc'), 'https://trello.com/c/abc');
});

test('busca sem acentos filtra os cards sem alterar o resumo', async t => {
  const { window, document } = await setup(t);
  const search = document.querySelector('#search'); search.value = 'FUNCOES'; search.dispatchEvent(new window.Event('input'));
  assert.equal(document.querySelectorAll('.task-card').length, 1);
  search.value = 'inexistente'; search.dispatchEvent(new window.Event('input'));
  assert.equal(document.querySelectorAll('.task-card').length, 0);
  assert.equal(document.querySelector('#total-count').textContent, '1');
});

test('quadro vazio mostra progresso zero sem NaN', async t => {
  const { document } = await setup(t, { 'GET /api/tasks': () => reply({ tarefas: [] }) });
  assert.equal(document.querySelector('#progress-label').textContent, '0%');
  assert.equal(document.querySelectorAll('.empty-column').length, 3);
});

test('falha de rede apresenta mensagem em português', async t => {
  const { document } = await setup(t, { 'GET /api/config': () => { throw new TypeError('Failed to fetch'); } });
  assert.match(document.querySelector('#board-error').textContent, /conexão|servidor/i);
});

test('Atualizar recupera a interface após falhar a configuração inicial', async t => {
  let failed = true;
  const { document } = await setup(t, { 'GET /api/config': () => { if (failed) throw new TypeError('Failed to fetch'); return reply(baseConfig); } });
  assert.equal(document.querySelector('#new-task').disabled, true);
  failed = false; document.querySelector('#refresh').click(); await flush();
  assert.equal(document.querySelector('#new-task').disabled, false);
  assert.equal(document.querySelector('#board-error').hidden, true);
  assert.equal(document.querySelectorAll('.task-card').length, 1);
});

test('atualização após virar o dia usa a data nova no formulário', async t => {
  const config = structuredClone(baseConfig);
  const { document, hooks } = await setup(t, { 'GET /api/config': () => reply(config) });
  config.today.data = '2026-10-21'; config.today.data_br = '21/10/2026';
  document.querySelector('#refresh').click(); await flush(); hooks.showTaskDialog();
  assert.equal(document.querySelector('#task-date').value, '2026-10-21');
});

test('duplo envio do formulário cria apenas uma solicitação', async t => {
  const pending = deferred();
  const { window, document, calls, hooks } = await setup(t, { 'POST /api/tasks': () => pending.promise });
  hooks.showTaskDialog(); document.querySelector('#task-name').value = 'Teste';
  const form = document.querySelector('#task-form');
  form.dispatchEvent(new window.Event('submit', { cancelable: true }));
  form.dispatchEvent(new window.Event('submit', { cancelable: true }));
  await flush(); assert.equal(calls.filter(c => c.method === 'POST').length, 1);
  pending.resolve(reply({ ok: true }, 201)); await flush();
  assert.equal(document.querySelector('#save-task').disabled, false);
});

test('não apaga um formulário em salvamento ao abrir Nova tarefa novamente', async t => {
  const pending = deferred();
  const { window, document, hooks } = await setup(t, { 'POST /api/tasks': () => pending.promise });
  hooks.showTaskDialog(); document.querySelector('#task-name').value = 'Não perder';
  document.querySelector('#task-form').dispatchEvent(new window.Event('submit', { cancelable: true }));
  hooks.showTaskDialog();
  assert.equal(document.querySelector('#task-name').value, 'Não perder');
  pending.resolve(reply({ ok: true }, 201)); await flush();
});

test('resposta antiga não substitui quadro de outro modo', async t => {
  const pending = deferred(); let first = true;
  const { window, document, hooks } = await setup(t, { 'GET /api/tasks': path => { if (first) { first = false; return pending.promise; } return reply({ tarefas: [] }); } });
  const mode = document.querySelector('#mode'); mode.value = 'live'; mode.dispatchEvent(new window.Event('change')); await flush();
  pending.resolve(reply({ tarefas: [task] })); await flush();
  assert.equal(hooks.state.mode, 'live'); assert.equal(document.querySelectorAll('.task-card').length, 0);
});

test('chat bloqueia cliques repetidos durante a resposta', async t => {
  const pending = deferred();
  const { hooks, calls } = await setup(t, { 'POST /api/chat': () => pending.promise });
  const first = hooks.sendMessage('Oi'); const second = hooks.sendMessage('Oi novamente');
  await flush(); assert.equal(calls.filter(c => c.path.startsWith('/api/chat')).length, 1);
  pending.resolve(reply({ reply: 'Tudo certo' })); await Promise.all([first, second]);
});

test('chat continua funcionando sem acesso ao armazenamento do navegador', async t => {
  const { window, hooks, calls } = await setup(t);
  hooks.state.mode = 'live';
  Object.defineProperty(window, 'sessionStorage', { get() { throw new window.DOMException('Storage blocked', 'SecurityError'); } });
  await hooks.sendMessage('Oi');
  assert.equal(calls.filter(c => c.path.startsWith('/api/chat')).length, 1);
});

test('Shift+Enter e composição de texto não enviam mensagens', async t => {
  const { window, document, calls } = await setup(t);
  const input = document.querySelector('#chat-input'); input.value = 'Oi';
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
  await flush(); assert.equal(calls.filter(c => c.path.startsWith('/api/chat')).length, 0);
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await flush(); assert.equal(calls.filter(c => c.path.startsWith('/api/chat')).length, 1);
});

test('diálogos têm nomes acessíveis associados a títulos', async t => {
  const { document } = await setup(t);
  for (const dialog of document.querySelectorAll('dialog')) {
    const labelledBy = dialog.getAttribute('aria-labelledby'); assert.ok(labelledBy);
    assert.ok(document.getElementById(labelledBy)?.textContent.trim());
  }
});

test('textos principais do quadro e chat têm contraste de pelo menos 4,5:1', async t => {
  const { window, document } = await setup(t);
  const style = document.createElement('style'); style.textContent = readFileSync('frontend/style.css', 'utf8'); document.head.append(style);
  function luminance(values) { return values.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0); }
  for (const [selector, background] of [
    ['.task-card h3', [255,255,255]], ['.task-card p', [255,255,255]], ['.card-date', [255,255,255]],
    ['.status-select', [250,249,252]], ['.message-text', [250,247,253]], ['.mode-note', [248,249,252]],
    ['.mode-control select', [244,240,249]], ['.overview-item div>span', [255,255,255]], ['.suggestions button', [255,255,255]],
  ]) {
    const foreground = window.getComputedStyle(document.querySelector(selector)).color.match(/[\d.]+/g).slice(0,3).map(Number);
    const a = luminance(foreground), b = luminance(background);
    const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    assert.ok(ratio >= 4.5, `${selector}: contraste ${ratio.toFixed(2)}:1`);
  }
});
