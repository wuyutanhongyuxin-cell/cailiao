const state = {
  rules: {},
  evidence: JSON.parse(localStorage.getItem('mws_evidence') || '[]'),
  config: { model_mode: localStorage.getItem('mws_model_mode') || 'offline' },
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
  loadConfig();
  loadAccessContext();
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
  const body = payload();
  body.config = { model_mode: state.config.model_mode };
  const data = await api('/api/generate', body);
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

// --- Stage 6: local config / offline model option --------------------------

async function loadConfig() {
  try {
    const cfg = await fetch('/api/config').then((r) => r.json());
    // Server default is offline; prefer the locally-saved mode if present.
    state.config.model_mode = localStorage.getItem('mws_model_mode') || cfg.model_mode;
    if ($('modelMode')) $('modelMode').value = state.config.model_mode;
    renderConfigStatus(cfg);
  } catch (e) {
    if ($('configStatus')) $('configStatus').textContent = '无法读取本地配置。';
  }
}

function renderConfigStatus(cfg) {
  const offline = state.config.model_mode === 'offline' || state.config.model_mode === 'prompt_only';
  const line = offline
    ? '当前为离线/仅提示词模式：不联网、不调用模型、不读取 .env 或凭据。'
    : '当前为在线模式：仅在服务端已配置 MATERIAL_LLM_* 时联网，否则回退为仅提示词。';
  if ($('configStatus')) $('configStatus').textContent = line;
  if (cfg && $('providerState')) {
    $('providerState').textContent = offline ? '离线模式' : (cfg.provider_configured ? '已接入模型' : '未配置模型');
  }
}

async function applyConfig() {
  const mode = $('modelMode').value;
  const report = await fetch('/api/config/validate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_mode: mode }),
  }).then((r) => r.json());
  if (report.passed) {
    state.config.model_mode = mode;
    localStorage.setItem('mws_model_mode', mode);
    renderConfigStatus(null);
    $('configLog').textContent = `已应用模型模式：${mode}` +
      (report.warnings && report.warnings.length ? `\n提示：${report.warnings.join('；')}` : '');
  } else {
    $('configLog').textContent = `配置无效：${(report.errors || []).join('；')}`;
  }
}

if ($('applyConfigBtn')) $('applyConfigBtn').addEventListener('click', applyConfig);

// --- Stage 6: RBAC / workspaces (demo, minimum-permission) -----------------

async function loadAccessContext(role) {
  try {
    const q = role ? `?role=${encodeURIComponent(role)}` : '';
    const ctx = await fetch(`/api/access/context${q}`).then((r) => r.json());
    renderAccessContext(ctx);
  } catch (e) {
    if ($('accessStatus')) $('accessStatus').textContent = '无法读取访问上下文。';
  }
}

function renderAccessContext(ctx) {
  if (!ctx || !ctx.user) return;
  if ($('accessStatus')) {
    $('accessStatus').textContent =
      `演示用户 ${ctx.user.display_name}｜角色 ${ctx.user.role}｜项目空间 ${ctx.workspace.name}（${ctx.workspace.id}）`;
  }
  if ($('accessActions')) {
    $('accessActions').textContent = `允许操作：${(ctx.allowed_actions || []).join('、') || '（无）'}`;
  }
  if ($('accessRole')) $('accessRole').value = ctx.user.role;
}

if ($('applyRoleBtn')) $('applyRoleBtn').addEventListener('click', () => loadAccessContext($('accessRole').value));

// --- Stage 6: governance policy (metadata only) ----------------------------

async function loadGovernancePolicy() {
  try {
    const p = await fetch('/api/governance/policy').then((r) => r.json());
    const enc = p.encryption || {};
    const ret = p.retention || {};
    if ($('governanceStatus')) {
      $('governanceStatus').textContent =
        `加密：${enc.algorithm}（${enc.status}，密钥来源 ${enc.key_source}，不含密钥值）｜` +
        `保留期(天)：${Object.entries(ret.retention_days || {}).map(([k, v]) => `${k}=${v}`).join('、')}`;
    }
  } catch (e) {
    if ($('governanceStatus')) $('governanceStatus').textContent = '无法读取治理策略。';
  }
}

if ($('loadGovernanceBtn')) $('loadGovernanceBtn').addEventListener('click', loadGovernancePolicy);

// --- Stage 6: model-provider data-flow disclosure + risk grade -------------

async function loadProviderRisk() {
  try {
    const s = await fetch('/api/providers/risk').then((r) => r.json());
    const risk = s.risk || {};
    const disc = s.disclosure || {};
    if ($('providerRiskStatus')) {
      $('providerRiskStatus').textContent =
        `供应商 ${s.profile.provider_id}（${s.profile.mode}）｜风险分级：${risk.level}（score ${risk.score}）｜原因：${(risk.reasons || []).join('、')}`;
    }
    if ($('providerDisclosure')) $('providerDisclosure').textContent = disc.disclosure_text || '';
  } catch (e) {
    if ($('providerRiskStatus')) $('providerRiskStatus').textContent = '无法读取供应商风险信息。';
  }
}

if ($('loadProviderRiskBtn')) $('loadProviderRiskBtn').addEventListener('click', loadProviderRisk);

// --- Stage 6: dependency inventory / SBOM / container plan -----------------

async function loadSbom() {
  try {
    const s = await fetch('/api/supply-chain/sbom').then((r) => r.json());
    const inv = s.dependency_inventory || {};
    const sbom = s.sbom || {};
    if ($('sbomStatus')) {
      $('sbomStatus').textContent =
        `项目 ${inv.project} ${inv.version}（Python ${inv.python_requires}）｜运行时第三方依赖：${(inv.runtime_dependencies || []).length}（stdlib-only=${inv.stdlib_only}）｜SBOM 组件：${sbom.component_count}`;
    }
  } catch (e) {
    if ($('sbomStatus')) $('sbomStatus').textContent = '无法读取供应链信息。';
  }
}

if ($('loadSbomBtn')) $('loadSbomBtn').addEventListener('click', loadSbom);

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

// Labels for verify_claim.insufficiency.summary. Audit wording only: this is a
// deterministic lexical check, NOT semantic entailment / NLI / truth judgement.
const INSUFFICIENCY_SUMMARY_LABEL = {
  no_retrieved_evidence: '无检索证据',
  required_markers_missing: '必备标记缺失',
  conflict_candidates_found: '发现冲突候选（词面）',
  weak_lexical_overlap: '词面重合过弱',
  none: '无不足（词面审计）',
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

  // Evidence-insufficiency audit block. Backward compatible: if an older response
  // omits `insufficiency`, this stays empty and nothing else changes. Dense audit
  // rows only; deliberately no semantic/NLI wording.
  const insufficiencyHtml = renderInsufficiency(data.insufficiency);

  $('searchResults').innerHTML = `
    <div class="item ${statusClass}">
      <strong>核验结论：${escapeHtml(VERIFY_STATUS_LABEL[data.status] || data.status)}</strong>
      <div>这是<strong>词面覆盖</strong>检查，不代表语义蕴含；命中项仍需人工语义复核。</div>
      <div>必备标记：${escapeHtml((data.required_markers || []).join('，')) || '无'}</div>
      <div>缺失标记：${escapeHtml((data.missing_markers || []).join('，')) || '无'}</div>
      <div>覆盖率：${ratioText}</div>
      <div>引用分段（cited_chunk_ids）：${escapeHtml((data.cited_chunk_ids || []).join('、')) || '无'}</div>
    </div>
    ${insufficiencyHtml}
    <div class="item">
      <strong>标记覆盖（marker → 分段列表）</strong>
      ${coveredHtml}
    </div>
    <strong>支撑分段详情</strong>
    ${supportingHtml}
  `;
}

// Render verify_claim.insufficiency as a dense audit row. Returns '' when the
// field is absent (older responses) so existing rendering stays backward
// compatible. This is deterministic lexical audit metadata only -- it never
// asserts semantic entailment, NLI, contradiction, or truth.
function renderInsufficiency(ins) {
  if (!ins) return '';
  const ov = ins.overlap || {};
  const pct = (v) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(0)}%`);
  const detailsHtml = (ins.details || []).map((d) => {
    const extra = [];
    if (d.markers && d.markers.length) extra.push(`标记：${escapeHtml(d.markers.join('，'))}`);
    if (d.conflict_count) extra.push(`冲突数：${d.conflict_count}`);
    if (d.conflict_types && d.conflict_types.length) extra.push(`类型：${escapeHtml(d.conflict_types.join('，'))}`);
    return `<div>· <code>${escapeHtml(d.code || '')}</code>　${escapeHtml(d.message || '')}` +
           `${extra.length ? '　（' + extra.join('；') + '）' : ''}</div>`;
  }).join('') || '<div>无</div>';
  // Blocking (must not be treated as supported) gets the conservative 'warning'
  // class; a clean 'none' result stays a plain, non-alarming row.
  const insClass = ins.blocking ? 'warning' : '';
  return `
    <div class="item ${insClass}">
      <strong>证据不足审计（insufficiency，词面确定性，非语义/NLI）</strong>
      <div>结论摘要（summary）：${escapeHtml(INSUFFICIENCY_SUMMARY_LABEL[ins.summary] || ins.summary || '—')}　·　${ins.has_insufficiency ? '存在不足' : '无不足'}</div>
      <div>阻断状态（blocking）：${ins.blocking ? '是（不得视为已支撑）' : '否'}</div>
      <div>缺失标记（missing_markers）：${escapeHtml((ins.missing_markers || []).join('，')) || '无'}</div>
      <div>冲突候选数（conflict_count）：${ins.conflict_count ?? 0}</div>
      <div>词面重合（overlap）：命中 ${ov.overlap_token_count ?? '—'} / 主张 ${ov.claim_token_count ?? '—'} 词元　·　重合率 ${pct(ov.overlap_ratio)}　·　标记覆盖率 ${pct(ov.coverage_ratio)}</div>
      <div>明细（details）：</div>
      ${detailsHtml}
      <div class="muted">方法：${escapeHtml(ins.method || '')}（确定性词面审计，不是语义蕴含/NLI/真伪判断）</div>
    </div>`;
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

// --- Final delivery: minimal MaterialTask workspace (v1) --------------------
// Uses only same-origin backend task APIs. No model/embedding/rerank/NLI logic
// lives here; task evidence approval is a manual, deterministic confirmation
// (lexical bookkeeping only, never a semantic entailment / truth judgement).

state.taskEvidenceResults = [];
state.taskSelectedEvidenceIndex = null;

// GET helper, kept separate from the POST `api` above.
async function apiGet(path) {
  const res = await fetch(path, { headers: { 'Accept': 'application/json' } });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function currentTaskId() {
  return ($('taskCurrentId').value || '').trim();
}

function taskLog(message) {
  $('taskLog').textContent = message;
}

function renderTaskStatus(task) {
  const box = $('taskStatus');
  if (!task) {
    box.textContent = '未选择任务。可从当前起草区新建，或刷新后从列表选择。';
    return;
  }
  const selected = (task.selected_evidence || []).length;
  const approved = (task.selected_evidence || []).filter((e) => e && e.approved).length;
  const facts = (task.approved_facts || []).length;
  const status = (task.latest_analysis && task.latest_analysis.status) || '未审';
  box.innerHTML = `当前任务：<strong>${escapeHtml(task.title || '未命名')}</strong>`
    + ` · 文种 ${escapeHtml(task.genre || 'work_plan')}`
    + ` · 状态 ${escapeHtml(String(status))}`
    + ` · 证据 ${selected}（已批准 ${approved}）· 批准事实 ${facts}`;
}

async function refreshTasks() {
  try {
    const data = await apiGet('/api/tasks');
    renderTaskList(data.items || []);
    taskLog(`已载入 ${(data.items || []).length} 条任务。`);
  } catch (e) {
    taskLog(`无法读取任务列表：${e.message}`);
  }
}

function renderTaskList(items) {
  if (!items.length) {
    $('taskList').innerHTML = '<div class="item">暂无任务。用“从当前起草新建任务”创建一条。</div>';
    return;
  }
  $('taskList').innerHTML = items.map((task) => {
    const status = (task.latest_analysis && task.latest_analysis.status) || '未审';
    const selected = (task.selected_evidence || []).length;
    return `
      <div class="item taskItem" data-task-id="${escapeHtml(task.id)}">
        <strong>${escapeHtml(task.title || '未命名任务')}</strong>
        <div>${escapeHtml(task.genre || 'work_plan')} · 状态 ${escapeHtml(String(status))} · 证据 ${selected}</div>
        <div class="muted">更新 ${escapeHtml(task.updated_at || '')}</div>
        <button class="taskPick" data-task-id="${escapeHtml(task.id)}">设为当前</button>
      </div>`;
  }).join('');
  document.querySelectorAll('.taskPick').forEach((btn) => btn.addEventListener('click', () => {
    $('taskCurrentId').value = btn.dataset.taskId;
    loadCurrentTask();
  }));
}

async function createTask() {
  const base = payload();
  const body = {
    title: base.title,
    genre: base.genre,
    fields: base.fields,
    facts: base.facts,
    draft: base.draft,
    // Carry local compose evidence over as the task's selected evidence.
    selected_evidence: state.evidence,
  };
  try {
    const task = await api('/api/tasks', body);
    $('taskCurrentId').value = task.id || '';
    renderTaskStatus(task);
    await refreshTasks();
    taskLog(`已新建任务 ${escapeHtml(task.id || '')}。`);
  } catch (e) {
    taskLog(`新建任务失败：${e.message}`);
  }
}

async function loadCurrentTask() {
  const id = currentTaskId();
  if (!id) { taskLog('未填写任务 ID。请先选择或新建任务。'); return; }
  try {
    const task = await apiGet(`/api/tasks/${encodeURIComponent(id)}`);
    // Push task state into the compose workspace.
    if (task.genre && state.rules[task.genre]) { $('genre').value = task.genre; renderGenreFields(); }
    $('title').value = task.title || '';
    $('facts').value = task.facts || '';
    $('draft').value = task.draft || '';
    const fields = task.fields || {};
    document.querySelectorAll('[data-field]').forEach((el) => { el.value = fields[el.dataset.field] || ''; });
    state.evidence = (task.selected_evidence || []).map((e) => ({
      title: e.document_title || e.title || '', source: e.organization || e.source || '',
      url: e.source_url || e.url || '', body: e.content || e.body || '',
    }));
    localStorage.setItem('mws_evidence', JSON.stringify(state.evidence));
    renderEvidence();
    renderTaskStatus(task);
    taskLog(`已载入任务 ${escapeHtml(id)} 到起草区。`);
  } catch (e) {
    taskLog(`载入任务失败：${e.message}`);
  }
}

async function saveCurrentTask() {
  const id = currentTaskId();
  if (!id) { taskLog('未填写任务 ID。请先选择或新建任务。'); return; }
  const base = payload();
  const body = {
    title: base.title, genre: base.genre, fields: base.fields,
    facts: base.facts, draft: base.draft, selected_evidence: state.evidence,
  };
  try {
    const res = await fetch(`/api/tasks/${encodeURIComponent(id)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    const task = await res.json();
    renderTaskStatus(task);
    taskLog(`已保存起草区到任务 ${escapeHtml(id)}。`);
  } catch (e) {
    taskLog(`保存任务失败：${e.message}`);
  }
}

async function searchTaskEvidence() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法检索任务证据。'); return; }
  const body = { query: ($('taskEvidenceQuery').value || '').trim(), limit: 10 };
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(id)}/evidence/search`, body);
    state.taskEvidenceResults = data.items || [];
    state.taskSelectedEvidenceIndex = null;
    renderTaskEvidenceResults(state.taskEvidenceResults);
    taskLog(`任务证据检索：命中 ${(data.items || []).length} 条（查询：${escapeHtml(data.query || '')}）。`);
  } catch (e) {
    taskLog(`任务证据检索失败：${e.message}`);
  }
}

function renderTaskEvidenceResults(items) {
  if (!items.length) {
    $('taskEvidenceResults').innerHTML = '<div class="item">无检索结果。先向资料库导入文档，或调整查询。</div>';
    return;
  }
  $('taskEvidenceResults').innerHTML = items.map((item, idx) => {
    const title = item.document_title || item.title || '未命名';
    const org = item.organization || item.source || '';
    const loc = item.location_kind ? `${item.location_kind} ${item.location_value || ''}` : '';
    const body = (item.content || item.body || '').slice(0, 200);
    return `
      <div class="item taskEvItem">
        <label><input type="radio" name="taskEvPick" value="${idx}" /> [${idx + 1}] ${escapeHtml(title)}</label>
        <div>${escapeHtml(org)} ${escapeHtml(item.document_number || '')} ${escapeHtml(loc)}</div>
        <p>${escapeHtml(body)}</p>
      </div>`;
  }).join('');
  document.querySelectorAll('input[name="taskEvPick"]').forEach((el) => el.addEventListener('change', () => {
    state.taskSelectedEvidenceIndex = Number(el.value);
  }));
}

async function attachSelectedEvidence() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法附加证据。'); return; }
  const idx = state.taskSelectedEvidenceIndex;
  if (idx === null || !state.taskEvidenceResults[idx]) { taskLog('请先在检索结果中选择一条再附加。'); return; }
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(id)}/evidence/attach`, { item: state.taskEvidenceResults[idx] });
    renderTaskStatus(data.task);
    const status = data.evidence_status || {};
    taskLog(`已附加证据。当前任务证据 ${status.selected || 0} 条（已批准 ${status.approved_evidence || 0}）。`);
  } catch (e) {
    taskLog(`附加证据失败：${e.message}`);
  }
}

async function approveTaskEvidence() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法批准证据。'); return; }
  let task;
  try {
    task = await apiGet(`/api/tasks/${encodeURIComponent(id)}/evidence/status`);
  } catch (e) { /* status endpoint returns counts, not ids; fall through */ }
  try {
    // Approve every currently-attached evidence item (manual, deterministic).
    const full = await apiGet(`/api/tasks/${encodeURIComponent(id)}`);
    const ids = (full.selected_evidence || [])
      .map((e) => e.id || e.chunk_id).filter(Boolean);
    if (!ids.length) { taskLog('任务暂无已附加证据可批准。'); return; }
    const data = await api(`/api/tasks/${encodeURIComponent(id)}/evidence/approve`, { evidence_ids: ids });
    renderTaskStatus(data.task);
    const status = data.evidence_status || {};
    taskLog(`已人工批准 ${ids.length} 条证据（词面确认，非语义判断）。已批准证据 ${status.approved_evidence || 0}，批准事实 ${status.approved_facts || 0}。`);
  } catch (e) {
    taskLog(`批准证据失败：${e.message}`);
  }
}

async function generateTask() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法生成。'); return; }
  const body = { config: { model_mode: state.config.model_mode } };
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(id)}/generate`, body);
    if (data.draft) $('draft').value = data.draft;
    if (data.prompt) $('prompt').value = data.prompt;
    if (data.analysis) renderAnalysis(data.analysis);
    renderTaskStatus(data.task);
    const ws = data.writing_state || {};
    taskLog(`生成模式：${escapeHtml(String(data.mode || ''))}。工作流状态：${escapeHtml(String(ws.state || ''))}。`);
  } catch (e) {
    taskLog(`生成失败：${e.message}`);
  }
}

async function auditTask() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法硬审。'); return; }
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(id)}/audit`, {});
    const audit = data.audit || {};
    if (data.task && data.task.latest_analysis) renderAnalysis(data.task.latest_analysis);
    renderTaskStatus(data.task);
    taskLog(`硬审：状态 ${escapeHtml(String(audit.status || ''))}`
      + ` · 阻断 ${audit.blocker_count || 0} · 失败 ${audit.failure_count || 0}`
      + ` · 修复单元 ${audit.repair_unit_count || 0} · 可导出 ${audit.can_export ? '是' : '否'}。`);
  } catch (e) {
    taskLog(`硬审失败：${e.message}`);
  }
}

async function preflightTask() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法预检。'); return; }
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(id)}/export/preflight`, {});
    const audit = data.audit || {};
    const summary = (data.preflight && data.preflight.summary) || {};
    renderTaskStatus(data.task);
    taskLog(`导出预检：可导出 ${audit.can_export ? '是' : '否'}`
      + ` · 段落 ${summary.paragraph_count != null ? summary.paragraph_count : '-'}`
      + ` · 未知字体 ${summary.unknown_font_count != null ? summary.unknown_font_count : '-'}`
      + ` · 表格 ${summary.table_count != null ? summary.table_count : '-'}。`);
  } catch (e) {
    taskLog(`导出预检失败：${e.message}`);
  }
}

async function exportTaskDocx() {
  const id = currentTaskId();
  if (!id) { taskLog('未选择任务，无法导出。'); return; }
  try {
    const res = await fetch(`/api/tasks/${encodeURIComponent(id)}/export/docx`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `material-task-${id}.docx`;
    a.click();
    URL.revokeObjectURL(url);
    taskLog(`已导出任务 ${escapeHtml(id)} 的 Word 草稿。`);
  } catch (e) {
    taskLog(`导出失败：${e.message}`);
  }
}

function wireTaskPanel() {
  $('taskCreateBtn').addEventListener('click', createTask);
  $('taskRefreshBtn').addEventListener('click', refreshTasks);
  $('taskLoadCurrentBtn').addEventListener('click', loadCurrentTask);
  $('taskSaveCurrentBtn').addEventListener('click', saveCurrentTask);
  $('taskEvidenceSearchBtn').addEventListener('click', searchTaskEvidence);
  $('taskAttachManualBtn').addEventListener('click', attachSelectedEvidence);
  $('taskApproveSelectedBtn').addEventListener('click', approveTaskEvidence);
  $('taskGenerateBtn').addEventListener('click', generateTask);
  $('taskAuditBtn').addEventListener('click', auditTask);
  $('taskPreflightBtn').addEventListener('click', preflightTask);
  $('taskExportDocxBtn').addEventListener('click', exportTaskDocx);
}
wireTaskPanel();

const origSetPanel = setPanel;
setPanel = function (name) {
  origSetPanel(name);
  if (name === 'library') { renderDocuments(); renderJobs(); }
  if (name === 'tasks') { refreshTasks(); }
};

init().catch((err) => {
  document.body.innerHTML = `<pre>启动失败：${escapeHtml(err.message)}</pre>`;
});
