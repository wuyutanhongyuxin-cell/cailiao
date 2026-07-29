# 完整路线图

本文档严格区分“已完成”“下一步实施”和“长期候选”，避免把设计目标误写成现有能力。

## 总体目标

建立面向中文政务材料的本地优先写作系统，让每篇材料经过要素检查、权威证据绑定、受约束起草、独立审查、定点修复、正式导出和人工签发。最终以事实错误率、引用可追溯率、必填项覆盖率、返工次数和人工采纳率衡量，而不只看“像不像”。

## 阶段 0：可运行 MVP

状态：已完成

- [x] 本地 HTTP 服务和静态工作台
- [x] 五类常用文种及各自必填项
- [x] 缺项阻断、结构检查和证据台账
- [x] 空泛表达、主体、时限、结果检查
- [x] 政策、年份、比例和数量的无依据主张提示
- [x] OpenAI 兼容接口与无 Key 模式
- [x] SQLite/FTS5 基础设施
- [x] 基础 DOCX 导出、核心测试与 Windows 启动脚本

验收基线：缺少必填事实时阻断生成；无证据的关键主张被标记；服务仅监听本机；Key 和运行数据不进入 Git。

## 阶段 1：可信证据库

状态：已完成（含 1A 与 1B）

优先级：最高

阶段 1 由 1A（文档导入与台账基础）和 1B（权威分级、版本治理、增量更新、XLSX、更丰富快照）组成，两者均已完成。

### 阶段 1A：文档导入与证据台账基础

状态：已完成

- [x] 导入 TXT、HTML、DOCX（DOCX 为标准库 OOXML 正文段落抽取）
- [x] 保存来源 URL、机构、文号、发布日期、有效性/状态、入库时间、SHA256
- [x] 文档去重（内容 SHA256）与分段，分段带相对正文的稳定字符偏移
- [x] 证据分段定位与“可引用/仅参考/禁止使用”状态（废止/失效默认禁止使用）
- [x] PDF/DOC 等显式隔离为不支持格式，解析失败与隔离记录可查询，不静默丢失
- [x] 最小资料库前端面板与后端 API（导入/文档列表/详情/分段/作业查询）
- [x] 单元测试与真实 HTTP API 测试覆盖上述能力

阶段 1A 验收：重复导入不重复入库；失效/废止文档的分段默认标为禁止使用；解析错误与不支持格式进入可查询的失败/隔离作业，不静默丢失。

### 阶段 1B：权威分级与版本治理

状态：已完成

- [x] 导入 XLSX（标准库 OOXML 解析，按行分段，含表名/行号定位；结构损坏或缺工作簿则隔离；.xls/.doc 仍隔离）
- [x] 建立权威等级：法律法规 > 国务院 > 部委 > 地方政府 > 权威媒体 > 用户事实（source_type 归一 + authority_level 派生，可按权威排序/过滤）
- [x] 管理政策版本链与取代关系（supersedes/superseded_by/version），被取代/废止/失效文档默认禁止引用
- [x] 按来源 URL/文号的增量更新：同 SHA256 仍去重；同源变更内容记为新版本并链接旧版本
- [x] 更丰富的证据原文快照与定位（文件名、MIME、字节数、正文快照 raw_text；分段带 location_kind/location_value）

阶段 1B 验收：每个引用可回到原始文件、URL 和原文；同源新版本可增量更新而非重复；失效/被取代政策不默认推荐；权威等级可用于排序与过滤。

保守说明：不实现法律层面的自动废止推断；版本与取代关系仅来自显式/手动指定，或同源（source_url/文号）唯一匹配的新版本处理；权威等级仅由 source_type 派生，绝不从正文内容推断。

验证说明：Codex 在 WSL 与 Windows 发布仓库分别运行 `python -m unittest discover -s tests -v`，39 项测试通过（39 tests OK）。

## 阶段 2：混合检索与引用验证

状态：进行中（2A 已完成，2B 已启动评测基座）

优先级：最高

### 阶段 2A：确定性检索与保守主张核验

状态：已完成

- [x] 资料库检索 API：`GET /api/library/search`，支持有效性、最低权威等级、来源类型、地区过滤
- [x] 两路确定性召回：词面精确命中通道 + 中文字符 ngram/FTS 回退通道
- [x] RRF 兼容融合排序，返回 `fused_score`、通道命中与命中理由；向量通道显式标记为未启用
- [x] 保守主张核验 API：`POST /api/library/verify-claim`，按文号、年份、数字、政策标记和词面覆盖判断是否有证据支撑
- [x] 资料库前端新增“检索”标签，可执行检索和主张核验
- [x] 评测指标基础：Recall@K 与 MRR helper，并以回归测试固定行为

阶段 2A 验收：检索排序稳定、过滤可复现；证据不足时返回待核实而非编造语义支撑；向量/embedding 未接入时必须明确说明。

验证说明：Codex 在 Windows 发布仓库运行 `python -m unittest discover -s tests -v`，46 项测试通过（46 tests OK）。

### 阶段 2B：语义检索、重排与引用验证深化

状态：进行中（检索评测运行器、BM25/FTS v1、向量管线骨架 v1、重排骨架 v1 与证据不足/拒绝理由 v1 已完成；真实语义 embedding、向量库、真实重排模型和语义级引用验证待实施）

- [x] 检索评测运行器：支持查询集、文档级/分段级相关标注、Recall@K、MRR、miss 列表和 HTTP API
- [x] 逐 case miss 诊断：报告 Top K 内漏召回的标注标题/分段与首个命中排名，聚合 miss 列表携带同样字段
- [x] 内置匿名占位评测集（10 条 case，含 2 条有意 miss）固定评测运行器行为
- [x] 中文 BM25/FTS 调优 v1：新增确定性 `bm25_like` 通道（1-4 字中文 ngram + ASCII/数字词元、IDF、tf 饱和 k1、长度归一 b，权威仅作 tie-breaker），RRF 融合并输出 `hit_reasons`
- [x] 评测可解释性：逐 case 输出 `top_reasons`（融合分/各通道命中/命中理由）
- [x] 评测集可复用 helper：`load_retrieval_eval_suite` / `build_suite_cases` / `run_retrieval_eval_suite`（隔离临时库、marker→chunk_id、不污染主库）
- [x] 评测命令行质量门禁：`python backend/server.py eval-retrieval`（及 `tools/evaluate_retrieval.py`），JSON 输出 + `--output`、阈值 `--min-title-recall`/`--min-chunk-recall`/`--max-misses`、达标 exit 0/不达标非 0
- [x] BM25 参数化：`k1`/`b` 可经 `bm25_params` 或 CLI（`--bm25-k1`/`--bm25-b`/`--sweep-bm25`）覆盖与扫参，默认行为不变
- [x] 统一质量门禁入口 `tools/run_quality_gates.py`（字节编译/单测/检索评测/`git diff --check`/机密与 `.env` 扫描；`--json`/`--skip-git-diff`；达标 exit 0）
- [x] GitHub Actions CI（`.github/workflows/quality-gates.yml`，push/PR，Ubuntu + Python 3.11/3.12，调用统一入口，不打印 secret、不上传 artifact）
- [ ] 人工建立 50-100 条真实匿名查询集，替换内置占位集
- [x] 评测集结构校验工具 v1：`tools/validate_retrieval_suite.py` + `server.validate_retrieval_suite`（校验 id 唯一/query/受支持过滤键/相关性目标/min_authority/format；错误退出非 0、告警不失败；数量不足或占位元数据给告警）——仅为真实集就绪后纳入门禁前的校验工具，不代表真实集已建立
- [ ] 在真实查询集上用 CLI 扫参校准 BM25 k1/b 与阈值
- [x] 向量检索与可替换 embedding 管线骨架 v1：**默认关闭**（`VectorEmbedder`/`DeterministicHashEmbedder`/`InProcessVectorIndex`/`VectorPipeline`/`resolve_vector_pipeline`，仅标准库、确定性、进程内、绝不联网/读凭证）；`search_library`/`evaluate_retrieval_cases` 新增 opt-in `vector_config`、HTTP `?vector=` 参数，开启后作为 `vector` RRF 通道并诚实回报状态（`is_real_embedding_model: false`）
- [ ] 真实 embedding provider、持久化向量库与生产级向量索引
- [x] 可插拔重排骨架 v1：**默认关闭**（`Reranker`/`DeterministicLocalReranker`/`RerankPipeline`/`resolve_rerank_pipeline`，仅标准库、确定性、进程内、绝不联网/读凭证）；`search_library`/`evaluate_retrieval_cases` 新增 opt-in `rerank_config`、HTTP `?rerank=` 参数，开启后**只对已融合 Top K 重排序、绝不检索新分段**并诚实回报状态（`is_real_rerank_model: false`）。**尚缺真实重排模型/cross-encoder**——只钉好了扩展缝
- [ ] 真实重排模型 / cross-encoder provider 与 RRF 融合排序深化
- [x] 地区、机构、时间、格式/文种、有效性过滤的 UI/评测覆盖扩展 v1：`search_library`/HTTP 检索支持 `organization`、`format`、`date_from`/`date_to` 并保持来源类型、地区、权威、文档/分段状态与 `effective_only` 过滤；资料库“检索与核验”面板暴露机关、格式、日期、有效性范围并显示生效过滤摘要；单元/HTTP/评测 case 覆盖过滤先于 BM25/RRF 排序生效
- [x] 主张到证据的精确映射 v1：`map_claim_to_evidence` 逐标记归因到覆盖分段列表（`covered_markers`: marker → [chunk_id, ...]）、`missing_markers`、逐分段 `supporting_items`（`matched_markers`/`matched_terms`/`hit_reasons`）与 `coverage_ratio`，并入 `verify_claim` 的 `evidence_map`；保守判定不变
- [x] 确定性冲突证据候选 v1：`detect_conflict_evidence`/`verify_claim.conflict_evidence` 对同上下文不同数量标记、必备标记附近明确否定进行保守提示；命中时把原本 `supported` 的结论降级为 `needs_verification`
- [x] 证据不足/拒绝理由 v1：`build_evidence_insufficiency`/`verify_claim.insufficiency` 在每次核验中返回稳定、机器可读的 `summary`、`blocking`、`missing_markers`、`conflict_count`、词面 `overlap` 和 `details`；仅为确定性词面审计元数据，不是语义蕴含、NLI 或真伪判断
- [ ] 引用蕴含与完整语义级冲突证据检测（需 LLM/NLI，超出当前词面候选范围）
- [x] 展示检索过程和命中理由的可审计面板 v1：资料库新增“检索与核验”标签，检索结果展示 RRF 融合分、各通道 rank/score、命中理由、向量启用状态与 BM25 参数；主张核验展示 status/理由/必备与缺失标记/覆盖率/`covered_markers`(marker→分段列表)/`supporting_items`/`cited_chunk_ids`，并明确标注“词面覆盖 ≠ 语义蕴含，需人工语义复核”

参考 `zh-policy-rag` 的 MIT 许可实现，复用时保留版权与许可证。

验收：建立至少 100 个真实查询评测集；记录 `Recall@10`、`MRR` 和引用准确率；片段不支撑主张时必须阻断或标为待核实。

## 阶段 3：写作状态机与定点修复

优先级：高

- [x] “资料不足、可起草、待审、待修、可导出”状态机 v1：确定性附加层 `build_writing_state(payload, analysis)`，并入每次 `analyze_payload` 返回与 `/api/generate`（含 blocked / prompt_only / llm / error）响应的 `writing_state`；输出 `state`/`label`/`can_generate`/`can_export`/`blockers`/`failures`/`warnings`/`required_actions`/`method=deterministic_writing_state_v1`；`review_approved`/`approved` 不能覆盖 blocker/fail（坏稿仍为待修/资料不足）。**边界：这是确定性工作流状态，不改动既有 status/issues 语义，不是语义复核，也不是正式 DOCX 排版。**
- [x] 大纲、段落、主张、证据结构化关联 v1：确定性附加层 `build_structured_writing_plan(payload, analysis)`，并入每次 `analyze_payload` 返回的 `structured_writing_plan`；按文种必备章节生成 `outline`，按草稿段落生成 `paragraphs`，用既有 `_required_claim_markers` 与 `map_claim_to_evidence` 将段落中的年份/数字/政策标记映射到 `payload["evidence"]` 转换出的本地证据项，并输出 `summary` 计数。**边界：仅使用请求内证据，不查询资料库、不调用模型、不做语义蕴含/NLI，也不改变既有 `status`/`issues`/`score`/`writing_state` 语义。**
- [x] 每段仅使用预先批准的事实 v1：确定性附加层 `build_approved_facts_audit(payload, analysis)`，并入每次 `analyze_payload` 返回的 `approved_facts_audit`；逐段检查必备主张标记是否被请求内预先批准事实覆盖。批准来源包括 `payload["facts"]`（用户确认事实）、`payload["approved_facts"]`（字符串或含 `id`/`text` 的结构化事实）和带 `approved`/`review_approved`/`is_approved` 的 `payload["evidence"]`；输出 `status`（`no_claim_markers`/`all_facts_approved`/`uses_unapproved_facts`）、`used_fact_markers`、`unapproved_markers`、`approved_fact_ids`、`warnings` 与汇总计数。**边界：确定性词面/标记审计，不查询资料库、不调用模型、不做语义蕴含/NLI，不改变既有 `status`/`issues`/`score`/`writing_state` 语义。**
- [x] 失败段落单独重写，不整篇无差别重生成 v1：确定性附加层 `build_targeted_repair_plan(payload, analysis)`，并入每次 `analyze_payload` 返回的 `targeted_repair_plan`；仅为失败或待核实段落生成 `paragraph_only` 修复单元，聚合 `pN` 失败项、`structured_writing_plan` 缺失标记和 `approved_facts_audit` 未批准标记，输出 `paragraph_index`、`original_text`、`issue_codes`、`required_markers`、`missing_markers`、`unapproved_markers`、`allowed_fact_ids`、`instruction`、`locked=false`。**边界：这是定点修复计划，不直接改写草稿、不调用模型、不整篇重生成、不查询资料库；实际改写、锁定和版本回退仍在后续切片。**
- [x] 人工锁定段落、版本差异和回退 v1：确定性 helper `build_draft_version(payload, version_id=None)`、`diff_draft_versions(previous, current)`、`apply_paragraph_revisions(base_version, revisions, locked_indexes=None)`、`rollback_draft_version(version)`；按 `split_paragraphs` 建立段落快照，支持 `payload["locked_paragraphs"]`，并入每次 `analyze_payload` 返回的 `draft_version`；diff 只比较段落 index，修订应用跳过锁定段并返回 `applied_revisions`/`skipped_locked`/`invalid_revisions`，rollback 从版本重建 draft。**边界：纯本地元数据与 helper，不持久化、不加前端 UI、不调用模型；人工锁定交互、版本存储和模型段落改写仍在后续切片。**
- [x] 可配置单位模板与禁用表达 v1：确定性附加层 `build_unit_template_profile(payload, genre_rule)` 与 `build_forbidden_expression_audit(payload)`；支持请求内 `unit_template`（`unit_name`、`preferred_terms`、`forbidden_terms`、`required_signature`、`contact`、`style_notes`）和 `payload["forbidden_phrases"]`，并入每次 `analyze_payload` 返回的 `unit_template_profile` 与 `forbidden_expression_audit`；`build_prompt` 在配置后追加独立单位模板约束区块；禁用表达来源合并全局空泛词、单位模板禁用词和 payload 禁用词，显式单位/payload 禁用词命中时产生段落级 fail issue 且按段落/短语去重。**边界：确定性文本匹配，不持久化、不加前端 UI、不调用模型、不做语义相似/改写；全局空泛词仍沿用既有 `vague_without_guard` 行为。**

验收：失败项可定位；修复不改锁定段落；连续失败时停止并请求事实；所有版本可回退。

## 阶段 4：正式 Word 排版

优先级：高

- [x] 按 GB/T 9704-2012 建立可配置样式 v1：确定性 `build_docx_style_profile(payload_or_options=None)` 与 `docx_style_xml(profile)`，为 DOCX 导出提供 GB/T 9704-2012-inspired 的 A4 页面、页边距、标题/正文/层级样式、字体、字号、首行缩进、行距和页脚页码元数据；`export_docx(title, body, style_profile=None)` 保持旧调用兼容，并写入 `word/styles.xml`、styles content type 和 document relationship。**边界：stdlib OOXML 样式骨架，不是完整国标排版认证；不加前端 UI、不持久化、不调用模型、不引入第三方依赖。**
- [x] 页面、版心、标题、正文、层级标题、落款、页码和版记 v1：确定性 `build_docx_layout_plan(title, body, options=None)` 与 `docx_footer_xml(profile, layout_plan)`；导出时将文本映射为 `title`、`heading`、`body`、`signature`、`imprint` 角色，简单中文/数字层级标题（如 `一、`、`（一）`、`1.`）使用 `MaterialHeading`，可选 `signature`/`imprint` 追加落款和版记元数据，默认写入带 PAGE 字段的 footer part 和 relationship，并可通过 `page_number=false` 关闭。**边界：词面角色映射和 stdlib OOXML 骨架，不做正式分页/版记排版认证、不做语义章节识别、不加 UI/持久化。**
- [ ] 表格、附件说明、文号、签发人、主送机关等结构化字段
- [ ] 字体缺失检测和替代策略
- [ ] DOCX 渲染截图与版式回归
- [ ] 导出前格式预检报告

验收：Word/WPS 打开无修复提示；关键版式自动检查通过；多页材料无异常分页或溢出；模板由熟悉公文格式的人员验收。

## 阶段 5：质量评测体系

优先级：高

- [ ] 匿名化真实任务集和标准答案要素
- [ ] 事实、引用、结构、语言分项评分
- [ ] 模型和版本隐藏的盲评
- [ ] 与人工写作、通用提示词和不同模型对照
- [ ] 记录采纳率、修改距离、完成时间、返工轮次
- [ ] 每次规则或模型更新运行回归评测

发布门槛建议：关键事实虚构率 0；强制字段漏检率 0；引用可追溯率 100%；回归可定位到规则、检索、模型或模板版本。

## 阶段 6：部署与治理

优先级：中

- [ ] 本地配置页面和完全离线模型选项
- [ ] 多用户角色、项目空间、最小权限
- [ ] 加密、备份、恢复、保留期和审计日志
- [ ] 模型供应商数据流提示与风险分级
- [ ] 依赖锁定、软件物料清单和可选容器部署

验收：默认不暴露公网；凭据不进日志、前端存储或导出；操作可追溯；恢复演练通过。

## 不应过早做的事

- 不先抓取“所有政府网站”：先定义权威范围、更新策略和评测集。
- 不先训练小模型：先用检索、规则和评测定位稳定失败环节。
- 不把模型自评当门禁：关键事实和引用由独立机制检查。
- 不打包无许可证仓库、字体或数据。
- 没有真实评测时不宣称达到成熟材料写作水平。

## 近期实施顺序

1. 证据库数据模型与文档导入（阶段 1A 已完成）。
2. 完善资料库：权威分级、版本链与增量更新（阶段 1B 已完成）。
3. 建立 50 至 100 个真实检索问题的匿名评测集。
4. 完成阶段 2B：中文 BM25/FTS 调优、向量检索、重排、引用定位与语义核验。
5. 实现段落级状态机与定点修复。
6. 建立 Word 模板和可视化版式回归。
7. 用真实任务盲评，再决定是否微调模型。
