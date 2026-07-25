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

// --- Phase 1: trusted evidence library UI -----------------------------------

const STATUS_LABEL = {
  citable: '可引用', reference_only: '仅参考', prohibited: '禁止使用',
  effective: '现行有效', revised: '已修订', repealed: '已废止',
  expired: '已失效', superseded: '已被取代', draft: '征求意见', unknown: '未知',
  succeeded: '成功', duplicate: '重复跳过', new_version: '新版本',
  updated: '已更新', failed: '失败', quarantined: '隔离',
  law_regulation: '法律法规', state_council: '国务院', ministry: '部委',
  local_government: '地方政府', official_media: '权威媒体', user_fact: '用户/内部事实',
  paragraph: '段落', row: '行',
};
const label = (v) => STATUS_LABEL[v] || v || '';

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1] || '');
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function importDocument() {
  const file = $('libFile').files[0];
  const body = {
    title: $('libTitle').value.trim(),
    organization: $('libOrg').value.trim(),
    document_number: $('libNumber').value.trim(),
    publish_date: $('libDate').value.trim(),
    source_url: $('libUrl').value.trim(),
    source_type: $('libSourceType').value,
    region: $('libRegion').value.trim(),
    supersedes: $('libSupersedes').value.trim(),
    status: $('libStatus').value,
    format: $('libFormat').value,
  };
  if (file) {
    body.content_base64 = await readFileBase64(file);
    body.original_filename = file.name;
    const ext = file.name.split('.').pop().toLowerCase();
    if (ext) body.format = ext;
  } else {
    body.text = $('libText').value;
  }
  try {
    const res = await fetch('/api/library/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.status === 'succeeded') $('libMsg').textContent = `导入成功，分段 ${data.chunk_count} 段（权威等级 ${data.authority_level}）。`;
    else if (data.status === 'new_version') $('libMsg').textContent = `已作为新版本入库（v${data.version}），取代旧版本 ${data.supersedes || ''}。`;
    else if (data.status === 'duplicate') $('libMsg').textContent = '内容重复，已跳过。';
    else $('libMsg').textContent = `未入库（${label(data.status)}）：${data.error_reason || ''}`;
  } catch (err) {
    $('libMsg').textContent = `导入失败：${err.message}`;
  }
  renderDocuments();
  renderJobs();
}

async function renderDocuments() {
  const { items } = await fetch('/api/library/documents').then((r) => r.json());
  $('libDocs').innerHTML = items.map((d) => `
    <div class="item" data-doc="${d.id}">
      <strong>${escapeHtml(d.title || '未命名')}（${label(d.status)}）</strong>
      <div>${escapeHtml(label(d.source_type))} · 权威等级 ${d.authority_level ?? 0}${d.region ? ' · ' + escapeHtml(d.region) : ''}${d.version ? ' · v' + d.version : ''}</div>
      <div>${escapeHtml(d.organization || '')} ${escapeHtml(d.document_number || '')} ${escapeHtml(d.publish_date || '')}</div>
      <div>格式 ${escapeHtml(d.format || '')} · ${d.char_count || 0} 字 · SHA256 ${escapeHtml((d.sha256 || '').slice(0, 12))}…</div>
      ${d.supersedes ? `<div>取代旧版本：${escapeHtml(d.supersedes)}</div>` : ''}
      ${d.superseded_by ? `<div>已被取代 → ${escapeHtml(d.superseded_by)}</div>` : ''}
      <div>${escapeHtml(d.source_url || '')}</div>
    </div>
  `).join('') || '<div class="item">资料库为空。导入 TXT/HTML/DOCX/XLSX 后在这里查看。</div>';
  document.querySelectorAll('[data-doc]').forEach((el) => el.addEventListener('click', () => renderChunks(el.dataset.doc)));
}

async function renderChunks(docId) {
  const { items } = await fetch(`/api/library/chunks?document_id=${encodeURIComponent(docId)}`).then((r) => r.json());
  $('libChunks').innerHTML = '<strong>分段（' + items.length + '）</strong>' + items.map((c) => `
    <div class="item ${c.status}">
      <strong>#${c.chunk_index} · ${label(c.status)} · ${label(c.location_kind)} ${escapeHtml(c.location_value || '')} · [${c.char_start}-${c.char_end}]</strong>
      <p>${escapeHtml((c.content || '').slice(0, 300))}</p>
    </div>
  `).join('');
}

async function renderJobs() {
  const { items } = await fetch('/api/library/jobs').then((r) => r.json());
  $('libJobs').innerHTML = items.map((j) => `
    <div class="item ${j.status === 'succeeded' ? 'pass' : j.status === 'duplicate' ? 'warning' : 'fail'}">
      <strong>${label(j.status)} · ${escapeHtml(j.title || '')} (${escapeHtml(j.format || '')})</strong>
      <div>${escapeHtml(j.created_at || '')} ${j.quarantined ? '· 已隔离' : ''}</div>
      ${j.error_reason ? `<div>${escapeHtml(j.error_reason)}</div>` : ''}
    </div>
  `).join('') || '<div class="item">暂无导入记录。</div>';
}


function searchFilters() {
  const filters = {
    min_authority: $('libSearchAuthority').value.trim(),
    source_type: $('libSearchSourceType').value.trim(),
    region: $('libSearchRegion').value.trim(),
    organization: $('libSearchOrganization').value.trim(),
    format: $('libSearchFormat').value.trim(),
    date_from: $('libSearchDateFrom').value.trim(),
    date_to: $('libSearchDateTo').value.trim(),
    effective_only: $('libSearchEffectiveOnly').value,
  };
  // Drop blank values so the server ignores them conservatively.
  Object.keys(filters).forEach((k) => { if (!filters[k]) delete filters[k]; });
  return filters;
}

const FILTER_LABEL = {
  min_authority: '最低权威', source_type: '来源类型', region: '地区',
  organization: '发文机关', format: '格式', date_from: '发布起',
  date_to: '发布止', effective_only: '有效性',
};

function activeFilterSummary(filters) {
  const parts = Object.entries(filters).map(([k, v]) => {
    const val = k === 'effective_only'
      ? (v === 'true' ? '仅现行有效' : '全部状态')
      : (k === 'source_type' ? label(v) : v);
    return `${FILTER_LABEL[k] || k}=${val}`;
  });
  return parts.length ? '生效过滤：' + parts.join('，') : '生效过滤：无（先过滤候选，再 BM25/RRF 排序）';
}

function channelsHtml(channels) {
  if (!channels) return '';
  return Object.entries(channels).map(([name, info]) =>
    `${escapeHtml(name)}(rank ${info.rank} / score ${Number(info.score).toFixed(3)})`
  ).join('，');
}

async function renderSearch() {
  const query = $('libSearchQuery').value.trim();
  if (!query) {
    $('searchMsg').textContent = '请输入检索查询后再检索。';
    $('searchResults').innerHTML = '';
    return;
  }
  const filters = searchFilters();
  const params = new URLSearchParams({ q: query, limit: '10', ...filters });
  const data = await fetch(`/api/library/search?${params.toString()}`).then((r) => r.json());
  const vector = data.vector || {};
  const bm25 = data.bm25 || {};
  const bm25Note = bm25.k1 !== undefined
    ? `　BM25：k1 ${bm25.k1}、b ${bm25.b}、语料 ${bm25.corpus_size ?? '-'} 段`
    : '';
  $('searchMsg').textContent =
    `命中 ${data.items.length} 段　向量检索：${vector.enabled ? '已启用' : '未启用（确定性词面/BM25）'}${bm25Note}　·　${activeFilterSummary(filters)}`;
  $('searchResults').innerHTML = data.items.map((item) => `
    <div class="item ${escapeHtml(item.chunk_status || '')}">
      <strong>${escapeHtml(item.document_title || '未命名')}　·　${escapeHtml(label(item.source_type))}　·　权威等级 ${item.authority_level ?? 0}</strong>
      <div>定位：${escapeHtml(label(item.location_kind))} ${escapeHtml(item.location_value || '')}　·　文号 ${escapeHtml(item.document_number || '—')}${item.region ? '　·　' + escapeHtml(item.region) : ''}</div>
      <div>RRF 融合分 ${Number(item.fused_score).toFixed(4)}　·　通道：${channelsHtml(item.channels) || '—'}</div>
      <div>命中理由：${escapeHtml((item.hit_reasons || []).join('，')) || '—'}</div>
      <p>${escapeHtml((item.content || '').slice(0, 360))}</p>
    </div>
  `).join('') || '<div class="item">未检索到符合条件的现行有效分段。</div>';
}

const VERIFY_STATUS_LABEL = {
  supported: '词面覆盖（仍需语义复核）',
  needs_verification: '待核实',
  unsupported: '未获支撑',
};

async function verifyClaim() {
  const claim = $('libClaim').value.trim();
  if (!claim) {
    $('searchMsg').textContent = '请输入需要核验的主张后再核验。';
    $('searchResults').innerHTML = '';
    return;
  }
  const filters = searchFilters();
  const data = await api('/api/library/verify-claim', { claim, filters, limit: 5 });
  const emap = data.evidence_map || {};
  const ratio = emap.coverage_ratio;
  const ratioText = ratio === null || ratio === undefined ? '无必备标记（不适用）' : `${(ratio * 100).toFixed(0)}%`;
  const statusClass = data.status === 'supported' ? 'warning' : data.status === 'unsupported' ? 'fail' : 'warning';
  $('searchMsg').textContent =
    `核验结论：${VERIFY_STATUS_LABEL[data.status] || data.status}　·　理由：${(data.reasons || []).join('，')}　·　${activeFilterSummary(filters)}`;

  const coveredHtml = Object.entries(emap.covered_markers || {}).map(([marker, ids]) =>
    `<div>标记「${escapeHtml(marker)}」→ 分段 ${escapeHtml((ids || []).join('、'))}</div>`
  ).join('') || '<div>无</div>';

  const supportingHtml = (emap.supporting_items || []).map((it) => `
    <div class="item">
      <strong>${escapeHtml(it.document_title || '未命名')}　·　${escapeHtml(label(it.source_type))}　·　权威等级 ${it.authority_level ?? 0}</strong>
      <div>分段 ${escapeHtml(it.chunk_id || '')}</div>
      <div>命中标记：${escapeHtml((it.matched_markers || []).join('，')) || '—'}</div>
      <div>命中词：${escapeHtml((it.matched_terms || []).join('，')) || '—'}</div>
      <div>命中理由：${escapeHtml((it.hit_reasons || []).join('，')) || '—'}</div>
    </div>
  `).join('') || '<div class="item">无支撑分段。</div>';

  $('searchResults').innerHTML = `
    <div class="item ${statusClass}">
      <strong>核验结论：${escapeHtml(VERIFY_STATUS_LABEL[data.status] || data.status)}</strong>
      <div>这是<strong>词面覆盖</strong>检查，不代表语义蕴含；命中项仍需人工语义复核。</div>
      <div>必备标记：${escapeHtml((data.required_markers || []).join('，')) || '无'}</div>
      <div>缺失标记：${escapeHtml((data.missing_markers || []).join('，')) || '无'}</div>
      <div>覆盖率：${ratioText}</div>
      <div>引用分段（cited_chunk_ids）：${escapeHtml((data.cited_chunk_ids || []).join('、')) || '无'}</div>
    </div>
    <div class="item">
      <strong>标记覆盖（marker → 分段列表）</strong>
      ${coveredHtml}
    </div>
    <strong>支撑分段详情</strong>
    ${supportingHtml}
  `;
}

const importBtn = $('importBtn');
if (importBtn) {
  importBtn.addEventListener('click', importDocument);
  document.querySelectorAll('.libTab').forEach((btn) => btn.addEventListener('click', () => {
    document.querySelectorAll('.libTab').forEach((b) => b.classList.toggle('active', b === btn));
    const showDocs = btn.dataset.lib === 'docs';
    const showSearch = btn.dataset.lib === 'search';
    $('libDocs').style.display = showDocs ? '' : 'none';
    $('libChunks').style.display = showDocs ? '' : 'none';
    $('libSearch').style.display = showSearch ? '' : 'none';
    $('libJobs').style.display = (!showDocs && !showSearch) ? '' : 'none';
  }));
  $('searchBtn').addEventListener('click', renderSearch);
  $('verifyClaimBtn').addEventListener('click', verifyClaim);
}

const origSetPanel = setPanel;
setPanel = function (name) {
  origSetPanel(name);
  if (name === 'library') { renderDocuments(); renderJobs(); }
};

init().catch((err) => {
  document.body.innerHTML = `<pre>启动失败：${escapeHtml(err.message)}</pre>`;
});
