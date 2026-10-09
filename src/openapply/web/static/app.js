const csrf = document.querySelector('meta[name="openapply-csrf"]').content;
const state = { data: null, busy: false, currentDetail: null, profilePreview: null, providers: null };

const el = (id) => document.getElementById(id);
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

function requestId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (typeof options.body === 'string') headers['Content-Type'] = 'application/json';
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

function showDeskDialog(message, options = {}) {
  const dialog = el('action-dialog');
  if (dialog.open) dialog.close('replaced');
  const title = options.title || 'Action needed';
  const confirmLabel = options.confirmLabel || 'Dismiss';
  const cancelLabel = options.cancelLabel || '';
  dialog.dataset.tone = options.tone || 'error';
  el('action-dialog-kicker').textContent = options.kicker || 'Desk notice';
  el('action-dialog-title').textContent = title;
  el('action-dialog-message').textContent = String(message || 'The request could not be completed.');
  el('action-dialog-primary').textContent = confirmLabel;
  const cancel = el('action-dialog-cancel');
  cancel.textContent = cancelLabel || 'Cancel';
  cancel.classList.toggle('hidden', !cancelLabel);
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
  });
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

function renderProfile(profile, ready) {
  const status = el('profile-status');
  status.textContent = ready ? 'READY TO PREPARE' : 'PROFILE NEEDED';
  status.classList.toggle('is-ready', ready);
  const current = el('profile-current');
  if (!profile) {
    current.textContent = 'No candidate facts saved yet.';
    return;
  }
  const facts = [
    profile.identity.full_name || 'Name missing',
    profile.identity.email || 'Email missing',
    `${profile.skills.length} skills`,
    `${profile.experience.length} roles`,
    profile.resume ? profile.resume.original_name : 'No resume'
  ];
  current.textContent = facts.join(' · ');
}

function renderProviders(payload) {
  state.providers = payload;
  const select = el('provider-select');
  select.replaceChildren();
  const capable = payload.providers.filter((provider) => provider.supports_generation);
  const configured = capable.find((provider) => provider.name === payload.selected);
  if (!configured) {
    const prompt = node('option', '', 'Choose CLI…');
    prompt.value = '';
    prompt.selected = true;
    select.append(prompt);
  }
  capable.forEach((provider) => {
    const option = node('option', '', `${provider.display_name} · ${provider.available ? 'ready' : 'not ready'}`);
    option.value = provider.name;
    option.disabled = !provider.available;
    option.selected = provider.name === payload.selected;
    select.append(option);
  });
  if (!capable.length) {
    const empty = node('option', '', 'No generation CLI found');
    empty.value = '';
    select.append(empty);
  }
  select.classList.toggle('is-unset', !configured);
  const selected = capable.find((provider) => provider.name === payload.selected && provider.available);
  el('resume-use-ai').disabled = !selected;
  if (!selected) el('resume-use-ai').checked = false;
  el('resume-use-ai').closest('label').title = selected
    ? `Resume structuring will use ${selected.display_name}.`
    : 'Choose a ready AI CLI above to enable structured extraction.';
}

async function refreshProviders() {
  try {
    renderProviders(await api('/api/providers'));
  } catch (error) {
    const select = el('provider-select');
    select.replaceChildren(node('option', '', 'Provider check failed'));
    select.title = error.message;
  }
}

function careerCard(title, items, formatter) {
  const card = node('article', 'career-card');
  card.append(node('h4', '', `${title} · ${items.length}`));
  if (!items.length) {
    card.append(node('small', '', 'Nothing extracted. You can add it later with profile edit.'));
  } else {
    items.slice(0, 5).forEach((item) => card.append(node('p', '', formatter(item))));
    if (items.length > 5) card.append(node('small', '', `+ ${items.length - 5} more`));
  }
  return card;
}

function showProfilePreview(payload) {
  const profile = payload.profile;
  state.profilePreview = profile;
  el('profile-name').value = profile.identity.full_name || '';
  el('profile-email').value = profile.identity.email || '';
  el('profile-phone').value = profile.identity.phone || '';
  el('profile-city').value = profile.identity.city || '';
  el('profile-country').value = profile.identity.country || '';
  el('profile-linkedin').value = profile.links.linkedin || '';
  el('profile-github').value = profile.links.github || '';
  el('profile-portfolio').value = profile.links.portfolio || '';
  el('profile-summary').value = profile.summary || '';
  el('profile-skills').value = profile.skills.join(', ');
  el('profile-confirm').checked = false;
  el('profile-review-title').textContent = payload.filename;
  const extraction = payload.used_provider
    ? `${payload.characters_extracted.toLocaleString()} characters read locally, then structured by ${payload.used_provider}.`
    : `${payload.characters_extracted.toLocaleString()} characters parsed locally. Review the detected skills, dates, employers and education below.`;
  el('profile-extraction-meta').textContent = extraction;
  const career = el('profile-career-preview');
  career.replaceChildren(
    careerCard('Experience', profile.experience, (item) => `${item.title} · ${item.company}`),
    careerCard('Education', profile.education, (item) => `${item.degree || 'Study'} · ${item.institution}`)
  );
  el('profile-review').classList.remove('hidden');
  el('profile-review').scrollIntoView({ behavior: 'smooth', block: 'start' });
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

function renderOpportunities(items, profileReady) {
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
      action.disabled = !profileReady;
      if (!profileReady) action.title = 'Upload and save your candidate profile first';
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
  renderProfile(data.profile, data.profile_ready);
  renderOpportunities(data.opportunities, data.profile_ready);
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
    await showDeskDialog(error.message, { title: 'Preparation could not start' });
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
    await showDeskDialog(error.message, { title: 'Message not sent' });
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

el('resume-file').addEventListener('change', (event) => {
  const file = event.currentTarget.files[0];
  el('resume-file-label').textContent = file ? file.name : 'Choose your resume';
});

el('provider-select').addEventListener('change', async (event) => {
  const provider = event.currentTarget.value;
  if (!provider) return;
  event.currentTarget.disabled = true;
  try {
    await api('/api/settings/provider', {
      method: 'POST', body: JSON.stringify({ provider })
    });
    await refreshProviders();
    await refresh();
  } catch (error) {
    await showDeskDialog(error.message, { title: 'CLI selection failed' });
    await refreshProviders();
  } finally {
    event.currentTarget.disabled = false;
  }
});

el('resume-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = el('resume-file').files[0];
  if (!file) return;
  const button = el('extract-resume');
  const errorBox = el('resume-error');
  button.disabled = true;
  button.textContent = el('resume-use-ai').checked ? 'Structuring profile…' : 'Reading locally…';
  errorBox.classList.add('hidden');
  try {
    const provider = el('resume-use-ai').checked ? '&provider=default' : '';
    const payload = await api(`/api/profile/resume/preview?filename=${encodeURIComponent(file.name)}${provider}`, {
      method: 'POST', body: file
    });
    showProfilePreview(payload);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove('hidden');
  } finally {
    button.disabled = false;
    button.textContent = 'Extract for review';
  }
});

el('profile-review').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!state.profilePreview) return;
  const errorBox = el('resume-error');
  if (!el('profile-confirm').checked) {
    errorBox.textContent = 'Check the review confirmation before saving.';
    errorBox.classList.remove('hidden');
    return;
  }
  const profile = structuredClone(state.profilePreview);
  const optional = (value) => value.trim() || null;
  profile.identity.full_name = el('profile-name').value.trim();
  profile.identity.email = el('profile-email').value.trim();
  profile.identity.phone = optional(el('profile-phone').value);
  profile.identity.city = optional(el('profile-city').value);
  profile.identity.country = optional(el('profile-country').value);
  profile.links.linkedin = optional(el('profile-linkedin').value);
  profile.links.github = optional(el('profile-github').value);
  profile.links.portfolio = optional(el('profile-portfolio').value);
  profile.summary = optional(el('profile-summary').value);
  profile.skills = [...new Set(el('profile-skills').value.split(',').map((value) => value.trim()).filter(Boolean))];
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await api('/api/profile', {
      method: 'POST', body: JSON.stringify({ profile, confirmed: true })
    });
    state.profilePreview = null;
    event.currentTarget.classList.add('hidden');
    errorBox.classList.add('hidden');
    await refresh();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove('hidden');
  } finally {
    button.disabled = false;
  }
});

function syncDiscoveryInputs() {
  const analyze = el('source-kind').value === 'analyze';
  const builtIn = Boolean(el('source-platform').value) && !analyze;
  el('source-platform').disabled = analyze;
  el('source-query').disabled = analyze || !builtIn;
  el('source-url').disabled = builtIn;
  el('source-url').required = !builtIn;
}

el('source-kind').addEventListener('change', syncDiscoveryInputs);
el('source-platform').addEventListener('change', syncDiscoveryInputs);

el('discovery-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const url = el('source-url').value.trim();
  const kind = el('source-kind').value;
  const platform = el('source-platform').value;
  if (kind === 'discover' && platform) {
    const query = el('source-query').value.trim() || null;
    await api('/api/tasks/discover-platform', {
      method: 'POST', body: JSON.stringify({ platform, query })
    });
  } else {
    await api(`/api/tasks/${kind}`, { method: 'POST', body: JSON.stringify({ url }) });
  }
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
syncDiscoveryInputs();
Promise.all([refresh(), refreshProviders()]).catch((error) => {
  el('today-summary').textContent = error.message;
});
