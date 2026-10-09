const csrf = document.querySelector('meta[name="openapply-csrf"]').content;
const state = { data: null, busy: false, currentDetail: null };

const el = (id) => document.getElementById(id);
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

function requestId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  if ((options.method || 'GET') !== 'GET') headers['X-OpenApply-CSRF'] = csrf;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || 'Request failed');
  }
  return response.json();
}

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function showNotice(message) {
  const notice = el('detail-notice');
  notice.textContent = message;
  notice.classList.toggle('hidden', !message);
}

function renderMessages(messages) {
  const box = el('messages');
  box.replaceChildren();
  if (!messages.length) {
    box.append(node('p', 'welcome', 'Your desk is ready. Start with “interview me” or ask “what did you apply to today?”'));
    return;
  }
  messages.forEach((message) => {
    const wrap = node('article', `message ${message.role}`);
    wrap.append(node('span', 'who', message.role === 'user' ? 'YOU' : 'OPENAPPLY'));
    wrap.append(node('p', '', message.text));
    box.append(wrap);
  });
  box.scrollTop = box.scrollHeight;
}

function renderInterview(interview) {
  const empty = el('interview-empty');
  const form = el('interview-form');
  if (!interview || !interview.current_turn || interview.status !== 'active') {
    empty.classList.remove('hidden');
    form.classList.add('hidden');
    if (interview && interview.status === 'completed') {
      empty.querySelector('p').textContent = 'Interview complete. Review and confirm the evidence below.';
      el('start-interview').classList.add('hidden');
    }
    return;
  }
  empty.classList.add('hidden');
  form.classList.remove('hidden');
  form.dataset.sessionId = interview.id;
  el('interview-progress').textContent = `Question ${interview.current_turn.sequence} · ${interview.topic}`;
  el('interview-question').textContent = interview.current_turn.question;
}

function renderEvidence(items) {
  el('evidence-count').textContent = String(items.length);
  const list = el('evidence-list');
  list.replaceChildren();
  if (!items.length) {
    list.append(node('p', 'ledger-empty', 'Your confirmed stories will appear here.'));
    return;
  }
  items.forEach((item) => {
    const wrap = node('article', 'evidence');
    wrap.append(node('small', '', `${item.kind.replaceAll('_', ' ')} · ${item.state}`));
    wrap.append(node('p', '', item.claim.summary || item.source_quote));
    if (item.state === 'proposed') {
      const actions = node('div', 'evidence-actions');
      const confirm = node('button', '', 'Confirm');
      const reject = node('button', '', 'Reject');
      confirm.addEventListener('click', () => updateEvidence(item, 'confirmed'));
      reject.addEventListener('click', () => updateEvidence(item, 'rejected'));
      actions.append(confirm, reject);
      wrap.append(actions);
    }
    list.append(wrap);
  });
}

function renderLedger(report) {
  el('today-summary').textContent = report.short_text;
  const ledger = el('ledger');
  ledger.replaceChildren();
  if (!report.items.length) {
    ledger.append(node('p', 'ledger-empty', 'No application activity recorded today. Prepared and submitted applications will be listed here.'));
    return;
  }
  report.items.forEach((item) => {
    const row = node('article', 'ledger-row');
    const date = new Date(item.created_at);
    row.append(node('span', 'date', date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
    row.append(node('strong', '', item.title));
    row.append(node('span', 'company', item.company || 'Company not recorded'));
    row.append(node('span', 'state', item.state.replaceAll('_', ' ')));
    const open = node('button', '', '↗');
    open.setAttribute('aria-label', `Review ${item.title}`);
    open.addEventListener('click', () => showDetail(item.id));
    row.append(open);
    ledger.append(row);
  });
}

function renderTasks(tasks, paused) {
  el('worker-toggle').textContent = paused ? 'RESUME WORKER' : 'PAUSE WORKER';
  el('worker-status').textContent = paused ? 'WORKER PAUSED' : 'LOCAL DESK ONLINE';
  const list = el('task-list');
  list.replaceChildren();
  tasks.slice(0, 6).forEach((task) => {
    const row = node('div', 'task');
    row.append(node('b', '', task.type.replaceAll('_', ' ')));
    row.append(node('span', '', `${task.state} · ${task.id.slice(0, 8)}`));
    list.append(row);
  });
  if (!list.children.length) list.append(node('p', 'ledger-empty', 'No queued work.'));
}

function renderOpportunities(items) {
  const list = el('opportunity-list');
  list.replaceChildren();
  items.slice(0, 6).forEach((item) => {
    const row = node('div', 'opportunity');
    row.append(node('b', '', item.title));
    const score = item.match && item.match.overall_score !== undefined ? ` · ${item.match.overall_score}%` : '';
    row.append(node('span', '', `${item.company || 'Company unknown'}${score}`));
    const action = node('button', '', item.application ? 'Review' : 'Prepare');
    if (item.application) {
      action.title = item.application.state.replaceAll('_', ' ');
      action.addEventListener('click', () => showDetail(item.application.id));
    } else {
      action.addEventListener('click', () => queuePreparation(item.id, action));
    }
    row.append(action);
    list.append(row);
  });
  if (!list.children.length) list.append(node('p', 'ledger-empty', 'No discovered opportunities yet.'));
}

async function refresh() {
  const data = await api(`/api/state?timezone=${encodeURIComponent(timezone)}`);
  state.data = data;
  renderMessages(data.messages);
  renderInterview(data.interview);
  renderEvidence(data.evidence);
  renderLedger(data.report);
  renderTasks(data.tasks, data.worker_paused);
  renderOpportunities(data.opportunities);
}

async function updateEvidence(item, newState) {
  await api(`/api/knowledge/${item.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ expected_revision: item.revision, state: newState })
  });
  await refresh();
}

async function queuePreparation(opportunityId, button) {
  button.disabled = true;
  button.textContent = 'Queued';
  try {
    await api(`/api/opportunities/${opportunityId}/prepare`, {
      method: 'POST', body: JSON.stringify({})
    });
    await refresh();
  } catch (error) {
    button.disabled = false;
    button.textContent = 'Prepare';
    alert(error.message);
  }
}

function fieldEditor(field, editable, blockedIds) {
  const row = node('div', `detail-field${blockedIds.has(field.field_id) ? ' is-blocked' : ''}`);
  const label = node('label', '');
  label.append(node('b', '', field.label));
  label.append(node('small', '', `${field.required ? 'required · ' : ''}${field.status}`));
  row.append(label);

  if (!editable || !field.editable) {
    row.append(node('span', '', field.value || '—'));
  } else {
    let input;
    if ((field.type === 'select' || field.type === 'radio') && field.options.length) {
      input = node('select', 'draft-input');
      const blank = node('option', '', 'Choose…');
      blank.value = '';
      input.append(blank);
      field.options.forEach((option) => {
        const choice = node('option', '', option.label);
        choice.value = option.value;
        choice.selected = option.value === field.answer_value;
        input.append(choice);
      });
    } else if (field.type === 'checkbox' && field.options.length) {
      input = node('select', 'draft-input');
      input.multiple = true;
      field.options.forEach((option) => {
        const choice = node('option', '', option.label);
        choice.value = option.value;
        choice.selected = field.answer_values.includes(option.value);
        input.append(choice);
      });
    } else if (field.type === 'checkbox') {
      input = node('select', 'draft-input');
      [['', 'Choose…'], ['true', 'Yes / checked'], ['false', 'No / unchecked']].forEach(([value, text]) => {
        const choice = node('option', '', text);
        choice.value = value;
        choice.selected = value === field.answer_value;
        input.append(choice);
      });
    } else if (field.type === 'textarea' || field.intent === 'open_ended' || field.intent === 'cover_letter') {
      input = node('textarea', 'draft-input');
      input.rows = 4;
      input.value = field.answer_value || '';
    } else {
      input = node('input', 'draft-input');
      input.type = ['email', 'tel', 'date'].includes(field.type) ? field.type : 'text';
      input.value = field.answer_value || '';
    }
    input.dataset.fieldId = field.field_id;
    if (field.max_length && input.tagName !== 'SELECT') input.maxLength = field.max_length;
    row.append(input);
  }
  if (field.reason) row.append(node('small', 'field-note', field.reason));
  return row;
}

function renderApplicationDetail(detail) {
  state.currentDetail = detail;
  el('detail-title').textContent = `${detail.title}${detail.company ? ` at ${detail.company}` : ''}`;
  const revision = detail.revision ? `revision ${detail.revision} · ` : '';
  el('detail-meta').textContent = `${revision}${detail.state.replaceAll('_', ' ')} · ${new Date(detail.created_at).toLocaleString()}`;
  el('detail-destination').textContent = `Destination: ${detail.destination || 'not reported'}`;
  showNotice(detail.outcome || '');

  const editable = ['awaiting_review', 'authorized'].includes(detail.state);
  const blockers = new Set(detail.blockers.map((item) => item.field_id));
  const fields = el('detail-fields');
  fields.replaceChildren();
  detail.fields.forEach((field) => fields.append(fieldEditor(field, editable, blockers)));
  if (!fields.children.length) fields.append(node('p', 'ledger-empty', 'No fields in this saved draft.'));

  el('draft-actions').classList.toggle('hidden', !editable);
  const authPanel = el('authorization-panel');
  const canReview = ['awaiting_review', 'authorized', 'dispatch_queued'].includes(detail.state);
  authPanel.classList.toggle('hidden', !canReview);
  el('authorize-confirm').checked = false;
  el('dispatch-confirm').checked = false;

  const authorized = ['authorized', 'dispatch_queued'].includes(detail.state) && detail.authorization && !detail.authorization.revoked_at && !detail.authorization.used_at;
  el('authorize-check-row').classList.toggle('hidden', authorized);
  el('authorize-draft').classList.toggle('hidden', authorized);
  el('authorized-actions').classList.toggle('hidden', !authorized);
  el('authorize-draft').disabled = detail.blockers.length > 0;
  if (authorized) {
    el('authorization-summary').textContent = `Authorized until ${new Date(detail.authorization.expires_at).toLocaleTimeString()}. Editing creates a new revision and revokes this authorization.`;
    const queued = detail.state === 'dispatch_queued';
    el('dispatch-application').disabled = queued;
    el('dispatch-application').textContent = queued ? 'Submission queued' : 'Queue one submission';
    if (queued) showNotice('Submission is queued. You can still revoke it until dispatch begins.');
  } else if (detail.blockers.length) {
    showNotice(`Resolve ${detail.blockers.length} required field${detail.blockers.length === 1 ? '' : 's'} before authorizing.`);
  }
}

async function showDetail(id) {
  const detail = await api(`/api/applications/${id}`);
  renderApplicationDetail(detail);
  if (!el('detail-dialog').open) el('detail-dialog').showModal();
}

function collectDraftValues() {
  const values = {};
  el('detail-fields').querySelectorAll('.draft-input[data-field-id]').forEach((input) => {
    values[input.dataset.fieldId] = input.multiple
      ? Array.from(input.selectedOptions).map((option) => option.value)
      : input.value;
  });
  return values;
}

el('draft-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const detail = state.currentDetail;
  if (!detail || !detail.revision) return;
  try {
    await api(`/api/applications/${detail.id}/draft`, {
      method: 'PATCH',
      body: JSON.stringify({ expected_revision: detail.revision, values: collectDraftValues() })
    });
    await refresh();
    await showDetail(detail.id);
  } catch (error) {
    showNotice(error.message);
  }
});

el('authorize-draft').addEventListener('click', async () => {
  const detail = state.currentDetail;
  if (!detail || !detail.revision) return;
  if (!el('authorize-confirm').checked) {
    showNotice('Check the review confirmation before authorizing.');
    return;
  }
  try {
    await api(`/api/applications/${detail.id}/authorize`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: detail.revision, confirmed: true })
    });
    await refresh();
    await showDetail(detail.id);
  } catch (error) {
    showNotice(error.message);
  }
});

el('revoke-authorization').addEventListener('click', async () => {
  const detail = state.currentDetail;
  if (!detail || !detail.authorization) return;
  try {
    await api(`/api/applications/${detail.id}/authorization/${detail.authorization.id}`, { method: 'DELETE' });
    await refresh();
    await showDetail(detail.id);
  } catch (error) {
    showNotice(error.message);
  }
});

el('dispatch-application').addEventListener('click', async () => {
  const detail = state.currentDetail;
  if (!detail || !detail.authorization) return;
  if (!el('dispatch-confirm').checked) {
    showNotice('Check the one-time submission confirmation first.');
    return;
  }
  const button = el('dispatch-application');
  button.disabled = true;
  try {
    await api(`/api/applications/${detail.id}/dispatch`, {
      method: 'POST',
      body: JSON.stringify({ authorization_id: detail.authorization.id, confirmed: true })
    });
    el('detail-dialog').close();
    await refresh();
  } catch (error) {
    button.disabled = false;
    showNotice(error.message);
  }
});

el('message-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const input = el('message-input');
  const text = input.value.trim();
  if (!text) return;
  state.busy = true;
  input.value = '';
  try {
    await api('/api/messages', {
      method: 'POST',
      body: JSON.stringify({ text, conversation_id: state.data.conversation_id, request_id: requestId(), timezone })
    });
    await refresh();
  } catch (error) {
    input.value = text;
    alert(error.message);
  } finally {
    state.busy = false;
  }
});

el('start-interview').addEventListener('click', async () => {
  await api('/api/interviews', { method: 'POST', body: JSON.stringify({ topic: 'career story' }) });
  await refresh();
});

el('interview-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const answer = el('interview-answer');
  const text = answer.value.trim();
  if (!text) return;
  await api(`/api/interviews/${event.currentTarget.dataset.sessionId}/answers`, {
    method: 'POST', body: JSON.stringify({ text, request_id: requestId() })
  });
  answer.value = '';
  await refresh();
});

el('discovery-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const url = el('source-url').value.trim();
  const kind = el('source-kind').value;
  await api(`/api/tasks/${kind}`, { method: 'POST', body: JSON.stringify({ url }) });
  el('source-url').value = '';
  await refresh();
});

el('worker-toggle').addEventListener('click', async () => {
  const action = state.data.worker_paused ? 'resume' : 'pause';
  await api(`/api/worker/${action}`, { method: 'POST' });
  await refresh();
});

el('detail-dialog').querySelector('.dialog-close').addEventListener('click', () => el('detail-dialog').close());
setInterval(() => { el('clock').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }, 1000);
setInterval(() => { if (!el('detail-dialog').open) refresh().catch(() => {}); }, 5000);
refresh().catch((error) => { el('today-summary').textContent = error.message; });
