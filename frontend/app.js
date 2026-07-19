const state = {
  rules: {},
  evidence: JSON.parse(localStorage.getItem('mws_evidence') || '[]'),
};

const $ = (id) => document.getElementById(id);

function payload() {
  const genre = $('genre').value;
  const fields = {};
  document.querySelectorAll('[data-field]').forEach((el) => fields[el.dataset.field] = el.value.trim());
  return {
    genre,
    title: $('title').value.trim(),
    fields,
    facts: $('facts').value.trim(),
    draft: $('draft').value.trim(),
    evidence: state.evidence,
  };
}

async function api(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function setPanel(name) {
  document.querySelectorAll('.nav').forEach((btn) => btn.classList.toggle('active', btn.dataset.panel === name));
  document.querySelectorAll('.panel').forEach((panel) => panel.classList.toggle('active', panel.id === name));
}

function renderGenreFields() {
  const genre = $('genre').value;
  const rule = state.rules[genre];
  const box = $('fields');
  box.innerHTML = '';
  rule.required_fields.forEach((name) => {
    const wrap = document.createElement('div');
    wrap.className = 'block';
    wrap.innerHTML = `<label>${name}</label><input data-field="${name}" placeholder="填写${name}" />`;
    box.appendChild(wrap);
  });
  restoreDraft(false);
}

function renderEvidence() {
  $('evidenceList').innerHTML = state.evidence.map((item, idx) => `
    <div class="item">
      <strong>[${idx + 1}] ${escapeHtml(item.title || '未命名来源')}</strong>
      <div>${escapeHtml(item.source || '')} ${escapeHtml(item.url || '')}</div>
      <p>${escapeHtml((item.body || '').slice(0, 260))}</p>
    </div>
  `).join('') || '<div class="item">暂无证据。涉及政策、年份、数据、讲话精神时，先补证据再生成。</div>';
}

function renderAnalysis(data) {
  $('score').textContent = data.score;
  $('reviewState').textContent = data.status;
  $('statusText').textContent = data.status;
  $('issues').innerHTML = data.issues.map((issue) => `
    <div class="issue ${issue.level}">
      <strong>${issue.level.toUpperCase()} · ${issue.code}</strong>
      <div>${escapeHtml(issue.message)}</div>
    </div>
  `).join('') || '<div class="issue pass"><strong>PASS</strong><div>当前草稿未触发硬性问题。</div></div>';
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function saveDraft() {
  localStorage.setItem('mws_draft', JSON.stringify(payload()));
  localStorage.setItem('mws_evidence', JSON.stringify(state.evidence));
  $('exportLog').textContent = '已保存到浏览器本地存储。';
}

function restoreDraft(includeGenre = true) {
  const raw = localStorage.getItem('mws_draft');
  if (!raw) return;
  try {
    const data = JSON.parse(raw);
    if (includeGenre && data.genre) $('genre').value = data.genre;
    $('title').value = data.title || $('title').value;
    $('facts').value = data.facts || '';
    $('draft').value = data.draft || '';
    document.querySelectorAll('[data-field]').forEach((el) => el.value = (data.fields || {})[el.dataset.field] || '');
  } catch {}
}

async function init() {
  const health = await fetch('/api/health').then((r) => r.json());
  state.rules = health.rules;
  $('providerState').textContent = health.provider_configured ? '已接入模型' : '未配置模型';
  Object.entries(state.rules).forEach(([key, rule]) => {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = rule.name;
    $('genre').appendChild(opt);
  });
  renderGenreFields();
  restoreDraft(true);
  renderGenreFields();
  renderEvidence();
}

document.querySelectorAll('.nav').forEach((btn) => btn.addEventListener('click', () => setPanel(btn.dataset.panel)));
$('genre').addEventListener('change', renderGenreFields);
$('saveLocalBtn').addEventListener('click', saveDraft);
$('addEvidenceBtn').addEventListener('click', () => {
  const item = { title: $('evTitle').value.trim(), source: $('evSource').value.trim(), url: $('evUrl').value.trim(), body: $('evBody').value.trim() };
  if (!item.title && !item.body) return;
  state.evidence.push(item);
  localStorage.setItem('mws_evidence', JSON.stringify(state.evidence));
  ['evTitle','evSource','evUrl','evBody'].forEach((id) => $(id).value = '');
  renderEvidence();
});
$('analyzeBtn').addEventListener('click', async () => {
  const data = await api('/api/analyze', payload());
  renderAnalysis(data);
  setPanel('review');
});
$('generateBtn').addEventListener('click', async () => {
  const data = await api('/api/generate', payload());
  $('prompt').value = data.prompt || '';
  if (data.draft) $('draft').value = data.draft;
  renderAnalysis(data.analysis);
  $('exportLog').textContent = data.error ? `模型未完成：${data.error}` : `生成模式：${data.mode}`;
  setPanel('review');
});
$('copyPromptBtn').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('prompt').value || '');
  $('exportLog').textContent = '提示词已复制。';
});
$('exportDocxBtn').addEventListener('click', async () => {
  const res = await fetch('/api/export/docx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('title').value, body: $('draft').value }) });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'material-draft.docx';
  a.click();
  URL.revokeObjectURL(url);
  $('exportLog').textContent = '已导出 Word 草稿。';
});
$('clearBtn').addEventListener('click', () => {
  localStorage.removeItem('mws_draft');
  localStorage.removeItem('mws_evidence');
  location.reload();
});

init().catch((err) => {
  document.body.innerHTML = `<pre>启动失败：${escapeHtml(err.message)}</pre>`;
});
