# 最终交付收尾方案

本文档用于把当前仓库已经具备的规则硬审、可信资料库、检索核验、写作状态、段落修复、DOCX 导出、评测与治理骨架，收束成一个可真实使用、可测试验收、可发布交付的完整系统。

核心原则：

- 先收束主线，不继续发散新骨架。
- 所有能力都围绕一篇材料任务流动。
- 模型只负责表达，事实、证据、门禁和导出状态由程序控制。
- 没有真实数据、真实供应商或真实评测证据时，不声明生产级完成。

## 0. 完工定义

项目只有同时满足以下条件，才算完成：

```text
资料能入库、去重、分段、标注来源与有效性。
用户能创建一篇材料任务，并围绕任务管理事实、证据、草稿、审稿、修复和导出。
系统能从资料库推荐证据，并让用户把分段加入本篇材料。
生成草稿时只能使用任务内事实和已选证据。
审稿能显示阻断项、失败项、警告项、证据不足、未批准事实和段落级修复计划。
失败段落能定点修复、接受、拒绝、锁定和回滚。
导出前有明确门禁和 DOCX 预检。
真实匿名检索集、真实写作任务集和盲评/成效指标跑通并保存报告。
质量门禁全绿，包括 unittest、项目 quality gates、pytest 兼容和 diff 检查。
README、ROADMAP、架构、许可证、发布证据和剩余风险文档一致。
```

推荐的最终用户流程：

```text
资料入库
-> 创建材料任务
-> 自动推荐证据
-> 人工选择并批准证据/事实
-> 生成草稿
-> 硬审与证据核验
-> 段落级定点修复
-> 复审通过
-> DOCX 预检
-> 导出
-> 评测/审计/发布证据归档
```

## 1. 建立 MaterialTask 主轴

### 目标

当前仓库已有资料库、证据台账、写作、审稿、修复计划、DOCX 导出和评测工具，但很多能力是松散的。最终收尾必须先建立一个统一的任务对象，让所有模块围绕同一篇材料流动。

### 数据模型

新增 `MaterialTask`，建议落地为 SQLite 表，第一版保持 stdlib-only。

字段建议：

```text
id: string
title: string
genre: string
fields_json: JSON string
facts: text
selected_evidence_json: JSON string
approved_facts_json: JSON string
draft: text
draft_versions_json: JSON string
locked_paragraphs_json: JSON string
latest_analysis_json: JSON string
repair_history_json: JSON string
export_artifacts_json: JSON string
manual_approvals_json: JSON string
created_at: ISO datetime
updated_at: ISO datetime
```

`selected_evidence_json` 中的每条证据必须保留：

```text
document_id
chunk_id
document_title
source_url
organization
document_number
publish_date
source_type
authority_level
status
chunk_status
location_kind
location_value
content
approved: boolean
approval_note
```

### 后端接口

新增或整理以下任务接口：

```text
POST /api/tasks
GET /api/tasks
GET /api/tasks/{id}
PUT /api/tasks/{id}
POST /api/tasks/{id}/evidence/search
POST /api/tasks/{id}/evidence/attach
POST /api/tasks/{id}/evidence/approve
POST /api/tasks/{id}/facts/approve
POST /api/tasks/{id}/analyze
POST /api/tasks/{id}/generate
POST /api/tasks/{id}/repair/paragraph
POST /api/tasks/{id}/export/docx
```

第一版可以继续兼容现有 `/api/analyze`、`/api/generate`、`/api/export/docx`，但前端主流程应优先使用 task-scoped 接口。

### 前端改造

新增任务列表和当前任务上下文：

```text
左侧或顶部显示当前任务标题、文种、状态。
支持新建任务、打开任务、保存任务。
起草、资料库、审稿、导出都读取当前 task_id。
没有当前任务时，只允许新建或导入演示任务。
```

### 测试

新增测试：

```text
tests/test_material_tasks.py
```

覆盖：

```text
创建任务后可读取。
更新字段后 updated_at 改变。
selected_evidence 保留 chunk_id/source/status/authority_level。
废止/禁止引用分段不能被 approved=true。
任务级 analyze 与旧 analyze 行为兼容。
```

### 验收

```text
用户打开浏览器后能创建一篇材料任务。
刷新页面后任务仍在 SQLite 中存在。
所有后续操作都能追溯到 task_id。
```

## 2. 把资料库接入起草

### 目标

资料库不能只是独立页面。它必须成为起草页的证据供应源。

### 用户流程

```text
用户填写标题、文种字段和事实素材。
点击“推荐证据”。
后端基于 title + fields + facts 生成检索查询。
调用 search_library。
展示候选证据分段。
用户选择“加入本篇证据”。
系统把分段写入当前 MaterialTask.selected_evidence。
```

### 后端

新增任务级证据推荐函数：

```text
build_task_evidence_queries(task) -> list[str]
search_task_evidence(task, filters=None, limit=10) -> dict
attach_task_evidence(task_id, chunk_id, approved=False) -> dict
```

查询生成规则：

```text
优先使用标题。
合并文种必填字段中的非空值。
从 facts 中提取年份、文号、数字、政策名、机构名。
每个查询必须短而可解释，不调用模型。
```

证据加入规则：

```text
chunk_status=prohibited 的分段不得作为 approved evidence。
document status 为 repealed/expired/superseded 的分段默认只能 reference_only。
authority_level 低于配置阈值时允许加入，但审稿必须 warning。
```

### 前端

起草页增加“推荐证据”区域：

```text
按钮：推荐证据
过滤：最低权威、来源类型、地区、发文机关、日期范围、仅现行有效
结果：标题、来源、权威等级、发布日期、状态、定位、命中理由、片段内容
操作：加入本篇、加入并批准、仅参考
```

证据页改为“本篇证据”：

```text
显示 task.selected_evidence。
支持取消、批准、降为参考、添加审批说明。
保留手动录入证据，但标记 source_type=user_fact。
```

### 测试

```text
推荐证据不污染资料库。
加入证据后 task.selected_evidence 包含 chunk_id。
禁止引用分段不能批准。
被取代文档只允许参考。
前端按钮存在并调用 task-scoped 接口。
```

### 验收

```text
用户无需复制粘贴，就能从资料库把证据加入当前材料。
生成 prompt 只包含当前任务选中的证据。
```

## 3. 审稿页升级为控制台

### 目标

后端 `analyze_payload` 已经返回以下关键结构：

```text
writing_state
structured_writing_plan
approved_facts_audit
targeted_repair_plan
draft_version
unit_template_profile
forbidden_expression_audit
```

当前前端主要只展示 `issues`，必须把这些返回值变成用户可操作的审稿控制台。

### 前端分区

审稿页最终分为五个区：

```text
1. 状态总览
2. 问题列表
3. 段落结构
4. 事实与证据审计
5. 定点修复计划
```

状态总览展示：

```text
analysis.status
analysis.score
analysis.writing_state.label
analysis.writing_state.can_generate
analysis.writing_state.can_export
analysis.writing_state.required_actions
```

问题列表展示：

```text
level
code
message
target
点击后定位到 pN 或字段
```

段落结构展示：

```text
draft_version.paragraphs
structured_writing_plan.sections
每段所属建议章节
每段是否锁定
每段 issue 数
```

事实与证据审计展示：

```text
approved_facts_audit.unapproved_markers
approved_facts_audit.approved_fact_ids
forbidden_expression_audit.paragraphs
```

定点修复计划展示：

```text
targeted_repair_plan.units
每个 unit 对应段落、失败项、缺失标记、修复指令
按钮：修这一段
```

### 后端

任务级 analyze 应保存 latest_analysis：

```text
POST /api/tasks/{id}/analyze
-> analyze_payload(task payload)
-> save latest_analysis_json
-> return analysis
```

### 测试

```text
analysis 总是包含 writing_state。
草稿有 fail 时 targeted_repair_plan.units 非空。
review_approved 不能覆盖 blocker/fail。
前端 renderAnalysis 能渲染 required_actions、unapproved_markers、repair units。
```

### 验收

```text
用户能清楚看到为什么不能生成、为什么不能导出、哪一段要修、缺什么证据。
```

## 4. 段落级定点修复闭环

### 目标

现有 `build_targeted_repair_plan` 只生成修复计划，不改稿。最终需要把它接成实际改稿闭环。

### 后端接口

新增：

```text
POST /api/tasks/{id}/repair/paragraph
```

请求：

```json
{
  "paragraph_id": "p3",
  "mode": "llm_or_prompt_only",
  "accept_locked_context": true
}
```

后端从 task 中读取：

```text
原草稿
draft_version
targeted_repair_plan
selected_evidence
approved_facts
locked_paragraphs
unit_template
```

生成修复 prompt：

```text
只能重写目标段落。
不得改动其他段落。
只能使用列明的批准事实和证据。
必须解决列明 issue。
没有证据的事实写成“需核实”或删除。
输出只返回修订后的该段文本。
```

响应：

```json
{
  "paragraph_id": "p3",
  "original_text": "...",
  "revised_text": "...",
  "prompt": "...",
  "mode": "llm|prompt_only|offline",
  "analysis_after": {},
  "repair_record_id": "..."
}
```

### 前端

审稿页每个 repair unit 提供：

```text
修这一段
查看修复提示词
接受修改
拒绝修改
查看 diff
锁定本段
解除锁定
回滚到上一版本
```

接受修改后：

```text
apply_paragraph_revisions
生成新 draft_version
自动重新 analyze
保存 repair_history
```

### 测试

```text
锁定段不会被修改。
修复只影响目标段。
缺少 approved evidence 时修复 prompt 不允许编造。
接受修复后 draft_version 增加。
拒绝修复不改变 draft。
回滚能恢复旧版本。
```

### 验收

```text
用户能从 fail issue 一路点到修复该段，并安全接受或回滚。
```

## 5. 真实语义检索、重排和 NLI 接入

### 目标

昨晚补的 embedding、rerank、NLI 相关工作应从“adapter 与 stub”变成可选真实能力。默认仍保守离线，真实接入必须显式配置。

### Embedding

最小实现：

```text
使用 OpenAICompatibleEmbeddingProvider。
新增任务或资料库级“重建向量索引”工具。
使用 SQLiteVectorIndex 持久化资料库 chunk 向量。
search_library(vector_config=...) 可查询真实向量通道。
```

新增接口：

```text
POST /api/library/vector-index/rebuild
GET /api/library/vector-index/status
```

验收：

```text
无 credential_source 时不运行。
不读 .env。
错误信息不泄露密钥。
重启服务后 SQLite 向量索引仍可查询。
```

### Rerank

最小实现：

```text
BM25/FTS/RRF 先召回 Top K。
HTTPRerankProvider 只重排 Top K。
不得检索新分段。
返回 original_rank、rerank_score、final_rank。
```

前端展示：

```text
重排启用状态
重排前排名
重排后排名
provider boundary
```

### NLI / Semantic Judge

最小实现：

```text
对草稿 claim 与候选 evidence 做 supports/refutes/not_enough_info。
refutes 阻断导出。
not_enough_info 降级为待核实。
低置信度进入人工复核。
```

新增接口：

```text
POST /api/tasks/{id}/semantic-review
```

输出：

```text
claim_id
paragraph_id
evidence_chunk_id
label: supports|refutes|not_enough_info
confidence
provider_metadata without secrets
human_review_required
```

### 测试

```text
本地 HTTP stub 验证请求 shape。
缺 env var 安全失败。
密钥不出现在 metadata/error/log。
NLI refutes 生成 blocker。
```

### 验收

```text
真实 provider 配置存在时可跑通一次端到端语义复核。
没有真实 provider 时系统仍能使用词面硬审，不伪装语义能力。
```

## 6. DOCX 正式导出门禁

### 目标

DOCX 当前是结构和样式骨架。最终交付需要导出预检、字段 UI 和门禁。

### 前端

导出页增加字段：

```text
文号
签发人
主送机关
附件
落款
版记
页码开关
样式 profile
```

导出页展示：

```text
writing_state.can_export
remaining blockers/fails
证据不足 blocking
未批准事实
DOCX preflight warnings
layout regression checks
manual override 区
```

### 后端

任务级导出：

```text
POST /api/tasks/{id}/export/docx
```

导出前运行：

```text
analyze_payload
build_export_preflight_report
export_docx
build_docx_layout_regression_report
```

默认导出门禁：

```text
blocker_count = 0
fail_count = 0
writing_state.can_export = true
approved_facts_audit.uses_unapproved_facts = false
insufficiency.blocking 不存在或为 false
DOCX layout regression 无 fail
```

人工强制导出必须记录：

```text
manual_override=true
override_reason
operator
timestamp
remaining_issues
```

### 测试

```text
有 blocker 时不能默认导出。
manual_override 缺 reason 时拒绝。
导出的 DOCX 包含 document.xml、styles.xml、footer。
结构化字段进入正确位置。
layout regression fail 时返回阻断。
```

### 验收

```text
用户能导出一份结构有效、字段完整、可追溯审稿状态的 Word 草稿。
```

## 7. 真实评测闭环

### 目标

当前评测骨架完整，但最终交付必须补真实数据与真实报告。

### 三套必备数据

1. 真实匿名检索集

```text
数量：50-100 条
内容：真实材料写作/查证问题
要求：匿名化、无 PII、带 provenance
标注：相关 document_id/chunk_id/qrels
用途：检索 Recall@K、MRR、miss 诊断、BM25/向量/重排对比
```

2. 真实写作任务集

```text
数量：30-50 个
内容：文种、标题、字段、事实素材、证据、禁用表达、期望结构
要求：匿名化、可内部复核
用途：生成质量、硬审、修复、导出流程评测
```

3. 盲评与成效指标集

```text
候选：人工稿、通用 prompt、本系统、其他模型
隐藏：provider/model/version
指标：采纳率、修改距离、完成时间、返工轮次、人工评分
用途：证明系统比通用 prompt 更有实际价值
```

### 质量门槛

建议最终门槛：

```text
必填项漏检率 = 0
废止/失效证据推荐批准率 = 0
无证据关键主张默认放行率 = 0
数字/年份/文号错误默认放行率 = 0
检索 Recall@10 >= 0.90
证据引用可追溯率 >= 0.95
NLI refutes 阻断召回率 >= 0.90
DOCX 结构回归通过率 = 100%
人工采纳率高于 generic_prompt baseline
```

### 输出文档

新增最终报告：

```text
docs/EVAL_REPORT.md
docs/RELEASE_DOSSIER.md
docs/KNOWN_LIMITATIONS.md
```

### 测试和命令

```powershell
python tools\validate_real_query_set.py --input path\to\real_query_set.json
python tools\sweep_bm25_real_queries.py --input path\to\real_query_set.json --output docs\evidence\final\bm25_sweep.json
python tools\validate_benchmark_suite.py --input path\to\writing_suite.json
python tools\validate_blind_eval.py --input path\to\blind_pack.json
python tools\validate_outcome_metrics.py --input path\to\outcome_metrics.json
python tools\run_regression_evaluation.py --input path\to\regression_run.json
```

### 验收

```text
真实评测报告能复现。
所有数据来源、hash、时间、配置和模型版本可追溯。
占位集不能被误写成真实验收。
```

## 8. 发布与治理收尾

### 文档收束

最终发布前必须保持以下文档一致：

```text
README.md: 面向用户的实际使用流程。
docs/ROADMAP.md: 已完成和未完成状态，不能夸大。
docs/ARCHITECTURE.md: 最终架构和数据流。
docs/FINAL_DELIVERY_PLAN.md: 本收尾方案。
docs/EVAL_REPORT.md: 真实评测结果。
docs/RELEASE_DOSSIER.md: 发布证据。
docs/KNOWN_LIMITATIONS.md: 已知限制和人工复核边界。
docs/sbom.json: 供应链元数据。
LICENSE: 明确许可证。
CHANGELOG.md: 版本变更。
```

### 质量门禁

最终发布前必须通过：

```powershell
python tools\run_quality_gates.py --json
python -m unittest discover -s tests -v
pytest -q
git diff --check
python -m json.tool CODEX_HANDOFF.json
```

如果项目仍保留 `tools/check_final_completion_blocker_audit.py`，最终完成模式应新增显式真实工件配置，而不是把默认 blocker 静默跳过。

### pytest 兼容

当前项目 quality gate 以 unittest 为准。最终收尾应让 pytest 也通过。

优先方案：

```text
新增 tests/__init__.py
或改掉测试间 from tests.xxx import ... 的导入方式。
```

验收：

```powershell
pytest -q
```

必须全绿。

### 许可证

当前 README 声明仓库暂未附加许可证。发布前必须决策：

```text
私有保留：保留 README 声明，不发布开源使用承诺。
开源发布：新增 LICENSE，并更新 README。
```

第三方参考和公共数据必须分别记录许可证，不得混入无许可证代码、字体或数据。

## 9. 推荐实施顺序

严格按以下顺序做，避免继续堆分散能力：

```text
1. MaterialTask 数据模型和任务 API
2. 起草页接资料库推荐证据
3. 本篇证据批准和事实批准
4. 审稿页展示完整 analysis 附加层
5. 段落级修复接口和 UI
6. 导出门禁和 DOCX 预检
7. 真实 embedding/rerank/NLI provider 接入
8. 真实匿名检索集和写作任务评测集
9. 盲评、成效指标、回归评测
10. LICENSE、README、ROADMAP、发布证据包
11. 全量质量门禁和发布
```

## 10. 最小收尾版本

如果必须先做一个可交付版本，不接真实 embedding/rerank/NLI，也可以定义为“本地硬审版”。最小版本必须完成：

```text
MaterialTask
资料库推荐证据
本篇证据批准
审稿控制台
段落级修复
DOCX 导出门禁
真实写作任务样例 10 个
质量门禁全绿
README/ROADMAP/许可证状态清楚
```

这个版本可以诚实命名为：

```text
Material Writing System v1.0 Local Hard-Review Edition
```

不得宣称：

```text
生产级语义检索
生产级 NLI 真伪判断
自动替代人工审核
完整 GB/T 9704 正式排版认证
```

## 11. 不再继续扩展的事项

以下事项在主线闭环完成前不应继续投入：

```text
新增更多 Stage 2B 协议文档。
继续扩展占位 readiness helper。
继续引入公共非政务数据作为完成证明。
训练或微调模型。
抓取大规模政府网站。
重构成复杂框架或引入前端构建链。
```

主线闭环完成前，所有新增工作都必须能明确落到：

```text
资料入库
证据选择
证据批准
草稿生成
审稿定位
段落修复
导出预检
真实评测
```

其中至少一个环节。

