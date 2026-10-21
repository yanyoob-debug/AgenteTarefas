/* Tudo que vem do usuário e do Trello é inserido com textContent, nunca HTML. */
const $ = (selector) => document.querySelector(selector);
const statuses = ['A fazer', 'Em andamento', 'Concluído'];
const state = { tasks: [], mode: 'demo', config: null, loading: false, chatting: false, mutation: false, request: 0, dragged: null };
const sessionKey = 'dia-chat-session';
let toastTimer;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function notify(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  } catch {
    throw new Error('Falha de conexão. Confira se o servidor está ligado e tente atualizar o quadro.');
  }
  let data;
  try { data = await response.json(); } catch { throw new Error('O servidor não respondeu. Confira se ele está ligado.'); }
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : 'Confira os dados e tente novamente.';
    throw new Error(message);
  }
  return data;
}
function modeQuery(mode = state.mode) { return `?mode=${encodeURIComponent(mode)}`; }
function normalized(text) { return text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR'); }
function formatDate(iso) {
  if (!iso) return 'Sem prazo';
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return 'Sem prazo';
  const options = { timeZone: state.config.today.fuso };
  const day = new Intl.DateTimeFormat('sv-SE', options).format(value);
  const label = day === state.config.today.data ? 'Hoje' : new Intl.DateTimeFormat('pt-BR', { ...options, day: '2-digit', month: 'short' }).format(value);
  return `${label} · ${new Intl.DateTimeFormat('pt-BR', { ...options, hour: '2-digit', minute: '2-digit' }).format(value)}`;
}
function safeLink(url) {
  try { const parsed = new URL(url); return parsed.protocol === 'https:' && parsed.hostname === 'trello.com' ? parsed.href : null; } catch { return null; }
}
function showTaskDialog() {
  if (!state.config || state.mutation || state.chatting) { notify('Aguarde a operação em andamento.'); return; }
  $('#task-form').reset();
  $('#task-date').value = state.config.today.data;
  $('#task-error').hidden = true;
  $('#task-dialog').showModal();
  $('#task-name').focus();
}
function card(task) {
  const article = node('article', 'task-card');
  article.draggable = !state.mutation;
  article.dataset.id = task.id;
  const top = node('div', 'card-top');
  top.append(node('span', 'task-category', state.mode === 'demo' ? 'EXEMPLO' : 'TAREFA'), node('span', 'card-indicator', task.status === 'Concluído' ? '✓' : '⋯'));
  const date = node('div', 'card-date');
  date.append(node('span', '', '◷'), node('span', '', formatDate(task.vencimento)));
  const bottom = node('div', 'card-bottom');
  const select = node('select', 'status-select');
  select.setAttribute('aria-label', `Status de ${task.nome}`);
  select.disabled = state.mutation;
  statuses.forEach(status => { const option = node('option', '', status); option.value = status; option.selected = task.status === status; select.append(option); });
  select.addEventListener('change', () => moveTask(task.id, select.value));
  bottom.append(select);
  const url = safeLink(task.url);
  if (url) {
    const link = node('a', 'card-link', '↗');
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.setAttribute('aria-label', `Abrir ${task.nome} no Trello`);
    bottom.append(link);
  }
  article.append(top, node('h3', '', task.nome), node('p', '', task.descricao || ''), date, bottom);
  article.addEventListener('dragstart', event => {
    if (state.mutation) { event.preventDefault(); return; }
    state.dragged = task.id;
    event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', task.id);
    article.classList.add('dragging');
  });
  article.addEventListener('dragend', () => { state.dragged = null; article.classList.remove('dragging'); document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over')); });
  return article;
}
function render() {
  const total = state.tasks.length;
  const done = state.tasks.filter(task => task.status === 'Concluído').length;
  const progress = total ? Math.round(done / total * 100) : 0;
  $('#nav-count').textContent = total;
  $('#total-count').textContent = total;
  $('#progress-count').textContent = state.tasks.filter(task => task.status === 'Em andamento').length;
  $('#done-count').textContent = done;
  $('#progress-label').textContent = `${progress}%`; $('#progress-bar').value = progress;
  const query = normalized($('#search').value.trim());
  const board = $('#kanban'); board.replaceChildren();
  statuses.forEach(status => {
    const column = node('section', 'column'); column.dataset.status = status;
    column.setAttribute('aria-label', status);
    const tasks = state.tasks.filter(task => task.status === status && normalized(`${task.nome} ${task.descricao || ''}`).includes(query));
    const header = node('div', 'column-header');
    const add = node('button', 'column-add', '+'); add.setAttribute('aria-label', 'Criar tarefa em A fazer'); add.addEventListener('click', showTaskDialog);
    header.append(node('span', 'column-dot'), node('span', '', status), node('span', 'column-count', tasks.length));
    if (status === 'A fazer') header.append(add);
    column.append(header);
    if (!tasks.length) {
      const empty = node('div', 'empty-column');
      empty.append(node('span', '', status === 'Concluído' ? '✓' : '◌'), node('div', '', query ? 'Nenhuma tarefa encontrada.' : 'Um espaço para o próximo passo.'));
      column.append(empty);
    }
    tasks.forEach(task => column.append(card(task)));
    if (status === 'A fazer') {
      const addTask = node('button', 'add-column-task'); addTask.append(node('span', '', '+'), node('span', '', 'Adicionar tarefa')); addTask.addEventListener('click', showTaskDialog); column.append(addTask);
    }
    column.addEventListener('dragover', event => { if (state.dragged && !state.mutation) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; column.classList.add('drag-over'); } });
    column.addEventListener('dragleave', event => { if (!column.contains(event.relatedTarget)) column.classList.remove('drag-over'); });
    column.addEventListener('drop', event => {
      event.preventDefault(); column.classList.remove('drag-over');
      const id = state.dragged;
      state.dragged = null;
      if (id && state.tasks.some(task => task.id === id && task.status !== status)) moveTask(id, status);
    });
    board.append(column);
  });
}
async function loadTasks() {
  if (!state.config) return start();
  const version = ++state.request;
  const mode = state.mode;
  state.loading = true; $('#quadro').classList.add('loading'); $('#board-error').hidden = true;
  try {
    const [data, config] = await Promise.all([api('/api/tasks' + modeQuery(mode)), api('/api/config')]);
    if (version !== state.request) return;
    state.config = config;
    updateDate();
    state.tasks = data.tarefas; render();
    $('#sync-label').textContent = mode === 'demo' ? 'Demonstração local · alterações temporárias' : `Sincronizado às ${new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
  } catch (error) {
    if (version !== state.request) return;
    $('#board-error').textContent = error.message; $('#board-error').hidden = false;
    $('#sync-label').textContent = 'Não foi possível sincronizar.';
  } finally {
    if (version === state.request) { state.loading = false; $('#quadro').classList.remove('loading'); }
  }
}
async function moveTask(id, status) {
  if (state.mutation || state.chatting) { render(); notify('Aguarde a operação em andamento.'); return; }
  state.mutation = true; $('#mode').disabled = true; render();
  try {
    await api(`/api/tasks/${encodeURIComponent(id)}/status` + modeQuery(), { method: 'PATCH', body: JSON.stringify({ status }) });
    notify(`Tarefa movida para ${status.toLocaleLowerCase('pt-BR')}.`);
  } catch (error) { notify(error.message); }
  finally { state.mutation = false; $('#mode').disabled = false; await loadTasks(); }
}
function addMessage(text, role = 'assistant') {
  const message = node('div', role === 'user' ? 'message user' : 'message');
  const body = node('div', 'message-content');
  body.append(node('span', 'message-author', role === 'user' ? 'Você' : 'Assistente Dia'), node('div', 'message-text', text), node('span', 'message-time', new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })));
  message.append(node('span', 'message-symbol', role === 'user' ? 'EU' : '✧'), body);
  $('#messages').append(message); $('#messages').scrollTop = $('#messages').scrollHeight;
}
function updateMode() {
  const demo = state.mode === 'demo';
  $('#board-name').textContent = demo ? 'DEMO' : state.config.board_name;
  $('#mode-note').lastElementChild.textContent = demo ? 'Modo demonstração · tarefas de exemplo, sem alterar o Trello.' : 'Quadro real · suas alterações serão salvas no Trello.';
  $('#chat-mode-label').textContent = demo ? 'Prévia do assistente' : 'Conversa conectada';
  $('#assistant-state').textContent = demo ? 'Explore a demonstração' : 'Vamos organizar?';
  $('#messages').replaceChildren();
  addMessage(demo ? `Olá! Hoje é ${state.config.today.data_br}. Que tal deixar seu dia mais leve?\n\nEsta é uma demonstração. Crie e mova os cards para experimentar. O chat com IA fica disponível ao conectar suas contas.` : `Olá! Hoje é ${state.config.today.data_br}. ✨\n\nQuais são as tarefas do seu dia? Me conte o que você precisa fazer e eu organizo tudo no seu quadro.`);
}
async function sendMessage(text) {
  text = text.trim(); if (!text || state.chatting || state.mutation) return;
  state.chatting = true; $('#send-message').disabled = true; $('#mode').disabled = true;
  $('#assistant-state').textContent = 'Organizando as ideias...';
  addMessage(text, 'user'); $('#chat-input').value = '';
  try {
    const data = await api('/api/chat' + modeQuery(), { method: 'POST', body: JSON.stringify({ message: text, session_id: state.mode === 'live' ? readSession() : null }) });
    if (data.session_id) { try { sessionStorage.setItem(sessionKey, data.session_id); } catch { /* O chat ainda funciona sem armazenamento. */ } }
    addMessage(data.reply);
  } catch (error) { addMessage(error.message); }
  finally { state.chatting = false; $('#send-message').disabled = false; $('#mode').disabled = false; $('#assistant-state').textContent = state.mode === 'demo' ? 'Explore a demonstração' : 'Vamos organizar?'; await loadTasks(); }
}
$('#new-task').addEventListener('click', showTaskDialog);
$('#refresh').addEventListener('click', () => loadTasks());
$('#search').addEventListener('input', render);
$('#help-button').addEventListener('click', () => $('#help-dialog').showModal());
$('#open-chat').addEventListener('click', () => { $('#assistente').scrollIntoView({ behavior: 'smooth', block: 'center' }); $('#chat-input').focus({ preventScroll: true }); });
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.close).close()));
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); } }));
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));
$('#mode').addEventListener('change', async event => { state.mode = event.target.value; state.tasks = []; render(); updateMode(); await loadTasks(); });
$('#chat-form').addEventListener('submit', event => { event.preventDefault(); sendMessage($('#chat-input').value); });
$('#chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(event.target.value); } });
$('#task-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.mutation || state.chatting) { notify('Aguarde a operação em andamento.'); return; }
  state.mutation = true; $('#save-task').disabled = true; $('#mode').disabled = true; $('#task-error').hidden = true;
  try {
    const body = Object.fromEntries(new FormData(event.target));
    await api('/api/tasks' + modeQuery(), { method: 'POST', body: JSON.stringify(body) });
    $('#task-dialog').close(); notify(state.mode === 'demo' ? 'Tarefa criada na demonstração.' : 'Tarefa criada no Trello.');
  } catch (error) { $('#task-error').textContent = error.message; $('#task-error').hidden = false; }
  finally { state.mutation = false; $('#save-task').disabled = false; $('#mode').disabled = false; await loadTasks(); }
});
function readSession() {
  try { const value = sessionStorage.getItem(sessionKey); return /^[a-f0-9]{32}$/.test(value || '') ? value : null; } catch { return null; }
}
function updateDate() {
  const date = new Date(`${state.config.today.data}T12:00:00`);
  $('#date-label').textContent = new Intl.DateTimeFormat('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' }).format(date).toLocaleUpperCase('pt-BR');
}
async function start() {
  try {
    state.config = await api('/api/config');
    state.mode = state.config.default_mode; $('#mode').value = state.mode;
    updateDate();
    $('#new-task').disabled = false; $('#send-message').disabled = false; $('#mode').disabled = false;
    updateMode(); render(); await loadTasks();
  } catch (error) {
    $('#board-error').textContent = error.message; $('#board-error').hidden = false;
    $('#new-task').disabled = true; $('#send-message').disabled = true; $('#mode').disabled = true;
  }
}
start();
