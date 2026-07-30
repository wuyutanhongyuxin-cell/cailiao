# 材料写作硬审系统

<p align="center"><strong>把“写作要求”变成可执行的质量门禁，而不是只写进提示词。</strong></p>
<p align="center">本地优先 · 证据约束 · 规则硬审 · OpenAI 兼容接口 · Word 导出</p>

> 当前版本为可运行的早期版本（MVP + 阶段 1 可信资料库 + 阶段 2A 确定性检索基础）。它已经具备材料录入、缺项阻断、证据台账、确定性审稿、模型调用、DOCX 导出，以及可信资料库的文档导入、去重、分段与引用状态、权威分级、版本/取代关系、同源新版本处理、XLSX 基础导入、确定性资料库检索和保守主张核验；向量检索、语义蕴含/冲突检测、逐段修复和国标级 Word 排版仍在路线图中。

## 为什么做这个项目

传统“材料写作提示词”常见的问题不是规则写得不够多，而是模型可以忽略规则、自检流于形式，甚至补造政策、数据和责任信息。本项目把关键要求放到模型之外执行：

```text
任务录入 → 文种必填项检查 → 证据绑定 → 受约束起草 → 独立复审 → Word 导出
             不通过则阻断          不通过则标记或阻断
```

模型负责表达，程序负责守门。即使更换模型，核心质量约束仍然存在。

## 当前能力

| 模块 | 已实现 |
|---|---|
| 文种建模 | 工作方案、工作汇报、领导讲话、通知、请示 |
| 缺项阻断 | 按文种检查必填事实和必要章节，缺失时禁止直接生成 |
| 证据台账 | 录入政策、数据、事实与来源，供审稿和提示词使用 |
| 可信资料库 | 阶段 1A：资料库面板与 API，导入 TXT/HTML/DOCX，保存来源与有效性元数据，SHA256 去重，分段与引用状态，解析失败/隔离可查询 |
| 资料库权威分级 | 阶段 1B：来源类型与权威等级（法律法规>国务院>部委>地方>媒体>用户事实），可按权威排序/过滤 |
| 资料库版本治理 | 阶段 1B：版本链与取代关系（supersedes/superseded_by），同源同文号变更自动记为新版本，被取代文档默认禁止引用 |
| 资料库格式扩展 | 阶段 1B：XLSX 基础导入（按行分段、含表名/行号定位），分段定位信息与更丰富来源快照（文件名、MIME、字节数、正文快照） |
| 资料库确定性检索 | 阶段 2A：词面精确通道 + 中文字符 ngram/FTS 回退通道；阶段 2B（BM25/FTS v1）：新增确定性 `bm25_like` 通道（1-4 字中文 ngram + ASCII/数字词元、IDF、tf 饱和与文档长度归一，权威仅作 tie-breaker），三路 RRF 融合并输出命中理由；过滤覆盖 v1 支持有效性、权威等级、来源类型、地区、发文机关、发布日期区间、文档状态、分段状态与格式过滤，过滤先约束候选再参与 BM25/RRF 排序 |
| 向量检索管线骨架（默认关闭） | 阶段 2B：可替换 embedding 管线骨架 v1（`VectorEmbedder`/`DeterministicHashEmbedder`/`InProcessVectorIndex`/`VectorPipeline`/`resolve_vector_pipeline`），仅标准库、完全确定性、进程内，**默认关闭且绝不联网或读取凭证**；显式开启（`search_library(vector_config=...)` 或 `?vector=true`）后新增 `vector` RRF 通道，带 rank/score 与 `vector_sim:*` 命中理由，`vector` 元信息诚实回报 enabled/mode 并标注 `is_real_embedding_model: false`。这是**骨架不是真实语义检索**：伪 embedder 只做词面特征哈希，为将来真实 embedding provider/向量库/重排预留扩展缝 |
| 可插拔重排骨架（默认关闭） | 阶段 2B：重排骨架 v1（`Reranker`/`DeterministicLocalReranker`/`RerankPipeline`/`resolve_rerank_pipeline`），仅标准库、完全确定性、进程内，**默认关闭且绝不联网或读取凭证**；显式开启（`search_library(rerank_config=...)` 或 `?rerank=true`）后**只对已融合的 Top K 重排序、绝不检索新分段**，命中项挂 `rerank`（`score`/`original_rank`/`mode`）与 `rerank_score:*` 命中理由，关闭时逐项明细完全不出现；`rerank` 元信息诚实回报 enabled/mode 并标注 `is_real_rerank_model: false`。这是**骨架不是真实重排模型**：仅按词面覆盖重排序，为将来真实 cross-encoder/重排 provider 预留扩展缝 |
| 保守主张核验 | 阶段 2A：按文号/年份/数值/政策标记和词面覆盖判断证据是否支撑主张；不足时返回“待核实”，不伪造语义证明。阶段 2B：主张到证据精确映射，逐标记归因到覆盖它的分段列表（`covered_markers`: marker → [chunk_id, ...]）、列出漏标记与逐分段命中详情（`supporting_items`）、给出覆盖率，未覆盖必填标记绝不判 `supported`；并新增确定性冲突证据候选 v1（`conflict_evidence`），对同上下文不同数量或标记附近明确否定进行保守提示，命中时降级为“待核实”；再新增确定性证据不足/拒绝理由 v1（`insufficiency`），用机器可读的稳定结构（`summary`/`blocking`/`missing_markers`/`conflict_count`/`overlap`/`details`）说明主张为何不能安全支撑 |
| 检索评测基础 | 阶段 2A：内置 Recall@K 与 MRR 指标 helper；阶段 2B：检索评测运行器输出文档级/分段级 Recall@K、MRR、逐 case miss 诊断与 `top_reasons` 可解释性；可复用 helper（加载/运行评测集、隔离临时库）与命令行质量门禁 `eval-retrieval`（阈值判定、达标 exit 0）；BM25 `k1`/`b` 可经 API/CLI 覆盖与扫参，附 10 条匿名占位评测集固定行为 |
| 评测集校验工具 | 阶段 2B：`tools/validate_retrieval_suite.py` 校验评测集结构（id 唯一、query 非空、过滤键受支持、至少一个相关性目标、min_authority/format 合法性），错误退出非 0、告警不失败；用于在人工建立真实匿名评测集后、纳入门禁前先行校验（真实 50-100 条评测集仍待建立） |
| 检索与核验面板 | 阶段 2B：资料库新增“检索与核验”标签，可审计地展示 RRF 融合分、各通道 rank/score、命中理由、向量启用状态、BM25 参数，以及主张核验的必备/缺失标记、覆盖率、`covered_markers`(marker→分段列表)、`supporting_items` 与 `cited_chunk_ids`；并以稠密审计块展示 `insufficiency`（证据不足/拒绝理由：`summary`/`blocking`/`missing_markers`/`conflict_count`/`overlap`/`details`/`method`）；界面明确标注“词面覆盖 ≠ 语义蕴含，需人工语义复核”，`insufficiency` 亦标注为确定性词面审计、非 NLI/真伪判断 |
| 确定性审稿 | 检查空泛表述、责任主体、完成时限、可验证结果和无依据主张 |
| 写作状态机 | 阶段 3 v1：确定性附加层 `build_writing_state`，在每次 `analyze_payload` 与 `/api/generate` 响应中给出 `writing_state`——五态 `materials_insufficient/资料不足`、`ready_to_draft/可起草`、`needs_revision/待修`、`ready_for_review/待审`、`ready_to_export/可导出`，附 `can_generate`/`can_export`/`blockers`/`failures`/`warnings`/`required_actions`；`review_approved`/`approved` 不能覆盖 blocker/fail。这是确定性工作流状态，非语义复核、非正式排版 |
| 结构化写作计划 | 阶段 3 v1：确定性附加层 `build_structured_writing_plan`，在每次 `analyze_payload` 中给出 `structured_writing_plan`，把文种必备章节、草稿段落、段落标记和请求内证据项做机器可读关联；仅使用 `payload["evidence"]`，不查询资料库、不调用模型、不做语义蕴含 |
| 预先批准事实审计 | 阶段 3 v1：确定性附加层 `build_approved_facts_audit`，在每次 `analyze_payload` 中给出 `approved_facts_audit`；逐段检查必备主张标记是否被请求内预先批准事实覆盖。批准来源包括 `payload["facts"]`、`payload["approved_facts"]` 和带 `approved`/`review_approved`/`is_approved` 的 `payload["evidence"]`；输出 `no_claim_markers`/`all_facts_approved`/`uses_unapproved_facts`、`unapproved_markers` 和 `approved_fact_ids`。这是确定性词面审计，非语义蕴含、非 NLI、非正式复核 |
| 定点修复计划 | 阶段 3 v1：确定性附加层 `build_targeted_repair_plan`，在每次 `analyze_payload` 中给出 `targeted_repair_plan`；只为失败或待核实段落生成 `paragraph_only` 修复单元，聚合 `pN` 失败项、缺失证据标记和未批准事实标记，并生成只改该段、保留其他段落、仅使用列明批准事实的修复指令。不调用模型、不改写草稿、不整篇重生成 |
| 段落版本与锁定 | 阶段 3 v1：确定性 helper `build_draft_version`/`diff_draft_versions`/`apply_paragraph_revisions`/`rollback_draft_version`；按段落建立快照，在 `analyze_payload` 中给出 `draft_version`，支持 `locked_paragraphs`、段落级 diff、跳过锁定段的修订应用和从版本回退重建草稿。当前仅为本地元数据与纯函数，不含持久化、前端 UI 或模型改写 |
| 单位模板与禁用表达 | 阶段 3 v1：确定性附加层 `build_unit_template_profile`/`build_forbidden_expression_audit`，支持请求内 `unit_template`（单位名、推荐术语、禁用术语、落款、联系人、风格说明）与 `forbidden_phrases`；`build_prompt` 在配置后追加独立单位模板约束区块，`analyze_payload` 返回 `unit_template_profile` 和 `forbidden_expression_audit`，并对单位/payload 显式禁用表达生成段落级 fail issue。边界：仅做确定性文本匹配，不含 UI、持久化、模型调用或语义改写 |
| DOCX 样式 profile | 阶段 4 v1：确定性 `build_docx_style_profile`/`docx_style_xml`，为 DOCX 导出加入 GB/T 9704-2012-inspired 的 A4、页边距、标题/正文/层级样式、字体、行距、首行缩进和页脚页码元数据；`export_docx` 保持旧签名兼容，同时支持可选 `style_profile` 并写入 `word/styles.xml`。边界：stdlib OOXML 样式骨架，不是完整正式排版认证，不含 UI/持久化/第三方依赖 |
| DOCX 布局角色 | 阶段 4 v1：确定性 `build_docx_layout_plan`/`docx_footer_xml`，把导出文本映射为 `title`、`heading`、`body`、`signature`、`imprint` 角色；`export_docx` 对简单中文/数字层级标题使用 `MaterialHeading`，支持可选落款/版记，并默认写入页码 footer part，可通过 `page_number=false` 关闭。边界：词面角色映射和 OOXML 骨架，不做正式分页、版记排版认证或语义章节识别 |
| DOCX 结构化字段 | 阶段 4 v1：确定性 `build_docx_structured_fields`/`docx_table_xml`，通过可选 `style_profile` 支持 `document_number`、`issuer`、`recipient`、`attachments` 和简单 `tables`；导出时文号/签发人/主送机关靠前插入，附件说明和表格追加在正文后、落款/版记前。边界：基础 OOXML 表格和字段段落，不做复杂表格样式、附件分页、正式版记排版或 UI 持久化 |
| DOCX 字体回退与导出预检 | 阶段 4 v1：确定性 `build_font_fallback_plan`/`build_export_preflight_report`，基于 `build_docx_style_profile` 报告各角色（body/title/heading/latin）请求字体、是否在保守内置已知字体列表中、按角色的回退候选链，并为未知字体给出告警；预检汇总方法/版本、字体回退计划、布局计划摘要、结构化字段摘要与导出边界告警。边界：仅生成建议性元数据，不替换/嵌入/下载字体，不读取宿主字体，不改动 `export_docx` 输出，不加 UI/持久化 |
| DOCX 版式回归检查 | 阶段 4 v1：确定性 `inspect_docx_package_layout`/`build_docx_layout_regression_report`，导出后打开 OOXML 包并校验结构化版式不变量：`word/document.xml`/`word/styles.xml` 是否存在、启用页码时是否有 `word/footer1.xml` 与 PAGE 字段、标题/正文/层级样式引用、页面尺寸与页边距，并汇总表格/附件/未知字体计数，输出命名的通过/失败检查项。边界：仅对生成的 OOXML 包做结构/标记回归，不内置或调用真实渲染器，视觉截图与像素级版式回归仍为后续工作 |
| 评测基准 schema 与打分 | 阶段 5 v1：确定性 `load_benchmark_suite`/`validate_benchmark_suite`/`score_benchmark_suite` 与 `tools/validate_benchmark_suite.py`；基准集 schema 含 `metadata`（name/version/`anonymized=true`）与 `cases[]`（id/genre/prompt_fields/facts/evidence/expected_elements），`expected_elements` 分 facts/citations/structure/language 四维；打分骨架按词面标记覆盖率逐维评分并给出逐 case 与聚合分。附合成样例 `tests/data/benchmark_suite_sample.json`。边界：仅词面覆盖率骨架，非语义/事实/人工质量评分，不含真实私有数据、不调用模型、不访问网络 |
| 模型隐藏盲评打包 | 阶段 5 v1：确定性 `build_blind_evaluation_pack`/`validate_blind_evaluation_pack`/`reveal_blind_evaluation_results` 与 `tools/validate_blind_eval.py`；将候选按稳定盲标签（`candidate_a`/`candidate_b`…）打包，评审视图仅暴露盲 id、case id 与答案文本，隐藏 provider/model/version，身份映射另存 `reveal_map` 供事后揭盲；校验元数据、case id 唯一、各 case 盲 id 一致、评审面无身份字段泄漏；揭盲按 case+盲 id 汇入评分并给出逐候选数值均值。附合成样例 `tests/data/blind_eval_candidates_sample.json`、`tests/data/blind_eval_scores_sample.json`。边界：仅打包/揭盲骨架，标签按输入顺序（如需防位置泄漏由上游预洗牌），不含真实数据、不调用模型、不访问网络 |
| 对照基线矩阵 | 阶段 5 v1：确定性 `build_comparison_baseline_matrix`/`validate_comparison_baseline_matrix`/`summarize_comparison_baseline_scores` 与 `tools/validate_comparison_baselines.py`；将 case id 与各对照臂（human 人工写作、generic_prompt 通用提示词、project_prompt 项目提示词、model 模型/版本臂，均用离线预产出文本）的产出拼成矩阵，臂描述仅暴露 arm_id/arm_type/label/可选 blind_id，身份另存 `identity_map`；校验 case/臂 id 唯一、各 case 臂 id 一致、arm_type 合法，缺失产出记为空串+告警不崩溃；给分时按数值 case 分逐臂聚合均值（不臆造分数）。附合成样例 `tests/data/comparison_baselines_sample.json`、`tests/data/comparison_baseline_scores_sample.json`。边界：仅矩阵+聚合骨架，不含真实人工/模型数据、不调用模型、不访问网络 |
| 成效指标记录与汇总 | 阶段 5 v1：确定性 `load_outcome_metrics_log`/`validate_outcome_metrics_log`/`summarize_outcome_metrics` 与 `tools/validate_outcome_metrics.py`；日志 schema 逐行按 case_id+arm_id 记录采纳（accepted 或 accepted_sections/total_sections）、修改距离（有 draft/final 文本则用标准库 Levenshtein，否则用给定数值）、完成时间（duration_seconds 或 started_at/completed_at 之差）、返工轮次（revision_rounds/rework_rounds）；确定性算出 adoption_rate/edit_distance/duration_seconds/rework_rounds 并逐臂与整体聚合；校验身份唯一、数值范围、缺 duration 时时间戳可解析，缺失可选指标给告警。附合成样例 `tests/data/outcome_metrics_sample.json`。边界：仅确定性记录+汇总骨架，无持久化/UI、不含真实数据、不调用模型/网络，非因果或显著性分析 |
| 回归评测运行汇总 | 阶段 5 v1：确定性 `build_regression_evaluation_run`/`validate_regression_evaluation_run`/`summarize_regression_evaluation_run` 与 `tools/run_regression_evaluation.py`；config 记录触发类型（rules_update/model_update/manual）、baseline/candidate 版本引用、基准集与各组件报告（基准打分、盲评、对照矩阵、成效指标、检索评测）；汇总列出各报告名与 pass/fail/warn 计数、在有 baseline/candidate 数值时算 candidate−baseline 差值，给出 passed/failed/needs_review 状态；校验必填元数据、触发类型合法、报告名唯一、数值差值良构，缺失可选报告给告警。附合成样例 `tests/data/regression_run_sample.json`。边界：仅本地回归汇总骨架，仅归一化既有组件报告、不执行评测、不调用模型/网络、除 CLI 外不做调度/CI 集成 |
| 本地配置与离线模型 | 阶段 6 v1：确定性 `build_local_config`/`validate_local_config`/`build_offline_placeholder_draft` 与模型模式感知的 `call_llm(prompt, config=None)`，HTTP `GET /api/config`、`POST /api/config/validate`；模型模式支持 `offline`（默认，不联网、返回本地占位草稿）、`prompt_only`（不联网，仅输出严格提示词）、`openai_compatible`（仅服务端已配置 MATERIAL_LLM_* 时联网，否则回退 prompt_only）；离线/仅提示词模式硬保证 `network_used=false`；配置仅以布尔暴露 provider 是否已配置，绝不读取 .env 或凭据值。前端“设置”面板提供模式选择与离线边界说明。边界：v1 本地配置/离线选项骨架，不内置本地推理引擎、不安装依赖、不联网、不读 .env/凭据 |
| RBAC/项目空间 | 阶段 6 v1：确定性 `build_access_context`/`check_permission`/`validate_access_policy` 与 HTTP `GET /api/access/context`、`POST /api/access/validate`、`POST /api/access/check`；角色 owner/admin/editor/reviewer/viewer 各映射到显式权限矩阵（read/generate/review/export/manage_library/manage_users/manage_config），默认最小权限 viewer；`check_permission` 校验动作合法、角色允许并做项目空间隔离（resource 的 workspace_id 不符即拒绝）；无鉴权供应商，仅确定性演示当前用户上下文。前端“设置”面板显示角色/项目空间/允许操作。边界：v1 RBAC/项目空间策略骨架，无密码/会话/鉴权供应商、无持久化、不联网、不读 .env/凭据，非生产级访问控制 |
| 治理（加密/备份/保留/审计） | 阶段 6 v1：确定性 `build_encryption_policy`/`build_backup_manifest`/`build_restore_plan`/`build_retention_policy`/`build_audit_record`（各配 validator）与 `build_governance_policy`，HTTP `GET /api/governance/policy`、`POST /api/governance/audit/validate`；加密仅记录算法/密钥来源/状态标签并硬拒绝任何密钥字段（key/secret/password…），备份清单以 sort_keys sha256 生成确定性校验和、恢复计划逐条给出校验/未校验步骤与告警、保留期按类型天数列出删除候选（不删除），审计记录含 timestamp/actor/action/workspace_id/resource/result/reason 且缺 actor/action 即拒。前端“设置”面板可查看治理策略。边界：v1 元数据/清单/审计骨架，不实现真实加密、不做破坏性删除、不执行真实备份/恢复、不联网、不读 .env/凭据 |
| 供应商数据流与风险分级 | 阶段 6 v1：确定性 `build_provider_profile`/`build_provider_disclosure`/`grade_provider_risk`/`validate_provider_profile` 与 `build_provider_risk_summary`，HTTP `GET /api/providers/risk`、`POST /api/providers/risk/grade`；供应商画像记录 provider_id/mode/endpoint_type/发送数据类别/存储/训练/数据驻留/保留说明（本地模式不外发任何数据），披露对象给出使用模型前的数据流说明，风险分级按标记确定性给出 低/中/高/blocked（本地=低、未知模式=blocked、敏感类别/存储/训练/驻留未知逐级升高）；画像与校验硬拒绝任何 api_key/token/含凭据地址等字段。前端“设置”面板可查看数据流与风险。边界：v1 披露/分级骨架，不接入真实供应商、不联网、不读 .env/凭据 |
| 依赖清单/SBOM/容器 | 阶段 6 v1：确定性 `build_dependency_inventory`/`build_sbom_document`/`build_container_deploy_plan`（各配 validator）与 `build_supply_chain_summary`，HTTP `GET /api/supply-chain/sbom`；项目为 stdlib-first（运行时零第三方依赖），仓库内 `requirements.txt`（仅注释、无第三方包）、`docs/sbom.json`（确定性 CycloneDX 风格、仅自有组件、无时间戳可复现，与 helper 保持同步）、可选 `Containerfile`（离线优先骨架、无 `RUN`/安装步骤、不构建不推送）。前端“设置”面板可查看依赖与 SBOM。边界：v1 供应链元数据骨架，不安装依赖、不构建/推送镜像、不联网、不含凭据 |
| 真实匿名查询集接入 | 阶段 5 v1（接入脚手架，非真实集）：确定性 `load_real_query_set`/`validate_real_query_set`/`summarize_real_query_readiness` 与 `tools/validate_real_query_set.py`；校验结构、匿名化（拒绝 PII/密钥字段名与手机号/身份证/邮箱等 PII 形态值，仅报类别不回显原值）与 provenance（source/collected_at/anonymized=true），并将就绪度分类为 invalid/template/incomplete_real/ready_real/oversized_real；占位/合成标记或不足 50 条永远不判为 ready_real。附 `tests/data/real_query_set_template.json` 供人工填写。边界：仅接入/校验脚手架，绝不伪造真实数据；真实 50-100 条人工匿名集在其真正就绪前仍未完成 |
| 真实集 BM25 扫参校准（受门禁） | 阶段 5 v1（受门禁脚手架）：确定性 `build_bm25_sweep_grid`/`validate_bm25_sweep_config`/`run_bm25_sweep_on_real_query_set` 与 `tools/sweep_bm25_real_queries.py`；仅当 `summarize_real_query_readiness(...).status == "ready_real"` 且数据集自带 `corpus` 时才在 k1/b/阈值网格上运行确定性扫参，逐组给出 title/chunk recall、MRR、miss，并按 recall→miss 选出 best；模板/不足/合成/无语料集一律拒绝、不运行。边界：仅受门禁校准脚手架，绝不伪造校准结果，不联网、不调用模型；真实校准需人工提供 ready_real 真实集后运行 |
| 向量生产就绪检查（受门禁） | 阶段 2 v1（就绪脚手架，非真实接入）：确定性 `build_embedding_provider_readiness`/`validate_embedding_provider_config`/`build_vector_store_plan`/`validate_vector_store_plan`/`build_vector_index_readiness` 与 `tools/check_vector_production_readiness.py`；区分既有确定性本地骨架（`is_real_embedding_model=false`）与真实生产就绪——仅当声明了真实 embedding 供应商（含 `credential_source` 环境变量名而非密钥值）、持久化向量库与索引描述时才判 `production_ready`；进程内/内存库与本地测试嵌入器一律不判就绪，凭据形字段一律拒绝。前端不涉及。边界：仅就绪/配置校验脚手架，不接入真实供应商、不安装向量库、不联网、不读 .env/凭据；就绪判定仅代表配置完整而非已实际接入 |
| 重排/RRF 生产就绪检查（受门禁） | 阶段 2 v1（就绪脚手架，非真实接入）：确定性 `build_reranker_provider_readiness`/`validate_reranker_provider_config`/`build_rrf_fusion_plan`/`validate_rrf_fusion_plan`/`fuse_ranked_results_rrf`/`build_rerank_pipeline_readiness` 与 `tools/check_rerank_production_readiness.py`；区分既有确定性本地重排骨架（`is_real_rerank_model=false`）与真实生产就绪——仅当声明真实 cross-encoder 供应商（含 `credential_source` 环境变量名而非密钥值）、评测指标（mrr/ndcg/map）与合法 RRF 融合计划时才判 `production_ready`；本地测试重排器/凭据形字段一律拒绝。`fuse_ranked_results_rrf` 为纯基于名次（`1/(rank_constant+rank)`、无分值刻度依赖）、确定性、按 id 稳定断连的 RRF 融合助手。边界：仅就绪/配置/融合助手脚手架，不接入真实重排模型、不下载模型、不联网、不读 .env/凭据；就绪判定仅代表配置完整而非已实际接入或评测 |
| NLI/语义冲突生产就绪检查（受门禁） | 阶段 3 v1（就绪脚手架，非真实接入）：确定性 `build_nli_provider_readiness`/`validate_nli_provider_config`/`build_semantic_conflict_policy`/`validate_semantic_conflict_policy`/`build_semantic_conflict_readiness` 与确定性标签映射 `map_nli_label_to_verdict`（SNLI/MNLI 的 entailment/contradiction/neutral 与 FEVER/CFEVER 的 supports/refutes/NEI → supports/refutes/not_enough_info），CLI `tools/check_semantic_conflict_readiness.py`；区分既有确定性**词面**冲突检测（`is_real_nli_model=false`、`does_semantic_entailment=false`）与真实语义就绪——仅当声明真实 NLI/LLM 供应商（含 `credential_source` 环境变量名而非密钥值）、覆盖三类判定的评测标签与合法冲突策略（阈值/block_on/warn_on）时才判 `production_ready`；未知标签抛错、词面检测器/凭据形字段一律拒绝。边界：仅就绪/配置/标签映射脚手架，不接入真实 NLI/LLM、不下载模型、不联网、不读 .env/凭据；既有词面冲突检测保持词面，就绪判定仅代表配置完整而非已实际推理或评测 |
| 外部依赖终审门禁 | 收尾 v1：确定性 `build_external_dependency_audit(config=None)` 与 `tools/check_external_dependency_audit.py`，把 5 项无法在本仓库内诚实完成（需人工数据或真实供应商凭据）的 ROADMAP 依赖聚合为机器可读阻断项——真实匿名查询集、真实集 BM25 校准、真实 embedding 供应商+持久化向量库+生产索引、真实重排/cross-encoder+RRF、真实 NLI/LLM 语义冲突；每项列出 blocker id、ROADMAP 行/主题、所需外部输入、当前仓库状态与已保护它的就绪脚手架/门禁；默认 `all_external_dependencies_satisfied=false`、`roadmap_parent_items_checked=false`。仅当 config 声明的工件“元数据形状”通过对应就绪 helper 时才判该项满足（纯元数据形状校验，非真实连通/鉴权/评测）。边界：仅聚合门禁，绝不伪造数据/连通/指标/凭据/模型下载/评测结果，不联网、不读 .env/凭据，不自动勾选任何 ROADMAP 父项 |
| 阶段 2B 生产落地 playbook | 收尾 v1：`docs/STAGE2B_PRODUCTION_PLAYBOOK.md` + 确定性 `build_stage2b_production_playbook_status(config=None)` 与 `tools/check_stage2b_production_playbook.py`；把仍未完成的 Stage 2B 真实项转化为行业对齐的落地计划——目标架构（BM25/FTS + 稠密向量 + 可选稀疏 + RRF/加权 RRF/DBSF 选型 + 仅对 Top-K 做 CrossEncoder 重排 + NLI 引用蕴含/语义冲突）、6 步落地序列、BEIR 风格指标（recall/precision/nDCG/MRR/MAP/miss/延迟 p50-p95/拒答率）、验收门与回滚标准、凭据仅记环境变量名的安全边界，并链接 Elasticsearch RRF / Qdrant hybrid / SBERT retrieve-rerank / BEIR 参考；每个阶段的就绪度镜像 `build_external_dependency_audit` 的 5 项阻断，默认 `ready_for_real_provider_rollout=false`、`roadmap_parent_items_checked=false`。边界：仅规划/就绪矩阵，不联网、不接入供应商、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 工件契约 | 收尾 v1：`docs/STAGE2B_ARTIFACT_CONTRACTS.md` + 占位示例 `examples/stage2b_artifacts.example.json` + 确定性 `build_stage2b_artifact_contracts(config=None)` 与 `tools/check_stage2b_artifact_contracts.py`；为 `config.artifacts` 下 5 个真实输入键（real_query_set / real_query_bm25_calibration / real_embedding_provider_vector_store / real_reranker_rrf / real_nli_semantic_conflict）定义必填字段、禁用（密钥值）字段、对应校验 helper、能证明与不能证明的内容，`contract_count=5`、`example_is_placeholder_only=true`、`ready_artifact_count` 依据既有 audit、`roadmap_parent_items_checked=false`；示例仅占位（含 template 标记、凭据仅记环境变量名），默认不会让真实 audit 满足。CLI 默认退出 0（契约/模板作为文档存在且有效，同时声明未提供真实工件、父项仍未勾选），`--require-ready-artifacts` 默认退出 1。边界：仅数据/元数据形状契约，非真实供应商校验，不联网、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 评测运行契约 | 收尾 v1：`docs/STAGE2B_EVAL_RUN_CONTRACT.md` + 占位示例 `examples/stage2b_eval_run.example.json` + 确定性 `build_stage2b_eval_run_contract(config=None)` 与 `validate_stage2b_eval_run_manifest(manifest)`，CLI `tools/check_stage2b_eval_run_contract.py`；对齐 TREC qrels/runfile/结果快照 + ir-measures/BEIR 指标约定，定义评测运行清单形状：`run_id`/`created_at`/`dataset_id`/`dataset_readiness_status`/`query_count`、各检索配置版本摘要、qrels/runfile/结果快照的 path|uri+sha256 指针、必填指标（recall@k/precision@k/ndcg@k/mrr/map/miss_rate/latency_p50/latency_p95/refusal_insufficiency_rate）与显式验收裁决；校验要求非模板、`dataset_readiness_status==ready_real`、`query_count∈[50,100]`、指标齐全为数值、`latency_p95≥latency_p50`、指针与哈希存在、裁决明确；绝不读取指针文件、不校验哈希内容、不调用供应商；默认（无清单）与占位示例均判不就绪，`roadmap_parent_items_checked=false`。CLI 默认退出 1、清单校验通过退出 0。边界：仅清单形状契约，证明“声明完整一致”而非“运行真发生/文件存在/指标为真”，不联网、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 向量生产 rollout 协议 | 收尾 v1：`docs/STAGE2B_VECTOR_ROLLOUT_PROTOCOL.md` + 占位示例 `examples/stage2b_vector_rollout.example.json` + 确定性 `build_stage2b_vector_rollout_protocol(config=None)` 与 `validate_stage2b_vector_rollout_packet(packet)`，CLI `tools/check_stage2b_vector_rollout_protocol.py`；定义真实 embedding provider、持久化向量库与生产索引上线前的 metadata-only rollout packet：provider/store/index 配置、索引 manifest、迁移/回滚步骤、观测指标与验收门。校验要求凭据只用环境变量名、provider/index/manifest 维度一致、距离 metric 一致、迁移/回滚/观测/验收字段完整；默认与占位示例均判不就绪，`roadmap_parent_items_checked=false`。边界：仅 rollout 元数据协议，不调用 provider、不连接向量库、不构建索引、不读 .env/凭据，不勾选真实 embedding/vector store 父项 |
| 阶段 2B 重排 rollout 协议 | 收尾 v1：`docs/STAGE2B_RERANK_ROLLOUT_PROTOCOL.md` + 占位示例 `examples/stage2b_rerank_rollout.example.json` + 确定性 `build_stage2b_rerank_rollout_protocol(config=None)` 与 `validate_stage2b_rerank_rollout_packet(packet)`，CLI `tools/check_stage2b_rerank_rollout_protocol.py`；定义真实 reranker/cross-encoder 与 RRF 融合深化上线前的 metadata-only packet：provider/model 配置、只重排融合 Top-K 的候选策略、RRF channels/rank_constant/rank_window_size/tie_policy、离线评测指标（mrr/ndcg/map/recall@k/latency_p95）、观测与 canary/rollback。默认与占位示例均判不就绪，`roadmap_parent_items_checked=false`。边界：仅 rollout 元数据协议，不调用 provider、不下载模型、不运行真实评测、不读 .env/凭据，不勾选真实重排父项 |
| 阶段 2B/3 NLI 语义 rollout 协议 | 收尾 v1：`docs/STAGE2B_NLI_SEMANTIC_ROLLOUT_PROTOCOL.md` + 占位示例 `examples/stage2b_nli_semantic_rollout.example.json` + 确定性 `build_stage2b_nli_semantic_rollout_protocol(config=None)` 与 `validate_stage2b_nli_semantic_rollout_packet(packet)`，CLI `tools/check_stage2b_nli_semantic_rollout_protocol.py`；定义真实 NLI/LLM 语义蕴含/冲突检测上线前的 metadata-only packet：provider/model、FEVER/SNLI 标签映射、supports/refutes/not_enough_info 覆盖、min_confidence/block_on/warn_on、证据字段、per-label precision/recall/F1/confusion matrix/calibration/abstention/refusal eval packet、人审升级、观测与 rollback。默认与占位示例均判不就绪，`roadmap_parent_items_checked=false`。边界：仅 rollout 元数据协议，不调用 provider、不下载模型、不运行真实评测、不读 .env/凭据，不勾选真实语义冲突父项 |
| 阶段 2B rollout 协议汇总门禁 | 收尾 v1：确定性 `build_stage2b_rollout_protocols_status(config=None)` 与 CLI `tools/check_stage2b_rollout_protocols_status.py`；聚合向量、重排、NLI 三类 rollout packet 就绪状态，并把 external dependency audit 的 `protected_by` 更新为同时列出 readiness helper 与 rollout 协议。默认不就绪，完整声明 packet 仅证明 metadata shape 完整，`roadmap_parent_items_checked=false`。边界：不调用 provider、不连接向量库、不下载模型、不运行评测、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 人工行动包 | 收尾 v1：`docs/STAGE2B_HUMAN_ACTION_PACKET.md` + 占位示例 `examples/stage2b_human_action_packet.example.json` + 确定性 `build_stage2b_human_action_packet(config=None)` 与 CLI `tools/check_stage2b_human_action_packet.py`；把 5 个仍需人工/外部输入的 blocker 转成 action item，逐项列出 ROADMAP 行 97/100/103/107/114、所需真实输入、验收工件、当前仓库状态和保护门禁；默认 `all_human_actions_resolved=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。边界：仅行动清单，不采集数据、不调用 provider、不下载模型、不运行评测、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 标准追踪矩阵 | 收尾 v1：`docs/STAGE2B_STANDARDS_TRACEABILITY.md` + 占位示例 `examples/stage2b_standards_traceability.example.json` + 确定性 `build_stage2b_standards_traceability(config=None)` 与 CLI `tools/check_stage2b_standards_traceability.py`；把 5 个 blocker 映射到 NIST AI RMF/GenAI、BEIR、Elastic RRF、Qdrant hybrid、SBERT retrieve-rerank 等参考，逐项列出标准/URL、所需证据工件、仓库已有 guardrail、仍缺真实证明；默认 `all_external_proofs_present=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。边界：仅标准/参考追踪，运行期不联网、不调用 provider、不下载模型、不运行评测、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 推广/SLO 门禁 | 收尾 v1：`docs/STAGE2B_PROMOTION_GATES.md` + 占位示例 `examples/stage2b_promotion_gates.example.json` + 确定性 `build_stage2b_promotion_gates(config=None)` 与 CLI `tools/check_stage2b_promotion_gates.py`；把 5 个 blocker 转成推广门禁/SLO 策略，逐项列出必需指标/工件、默认 gate、rollback trigger、证据来源与当前 `blocked_by_external_input` 状态；对齐 NIST AI RMF/GenAI、BEIR/ir-measures、OpenTelemetry latency/error 观测和 SRE SLO/error-budget 纪律；默认 `ready_for_promotion=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。边界：仅确定性推广策略，不联网、不调用 provider、不下载模型、不运行评测、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 可观测性契约 | 收尾 v1：`docs/STAGE2B_OBSERVABILITY_CONTRACT.md` + 占位示例 `examples/stage2b_observability_contract.example.json` + 确定性 `build_stage2b_observability_contract(config=None)` 与 CLI `tools/check_stage2b_observability_contract.py`；把 5 个 blocker 转成 telemetry contract，逐项列出必需 metrics/events、dimensions、SRE 四金信号映射、alert/rollback signal、证据来源与默认 `missing_real_telemetry`；对齐 OpenTelemetry metrics/traces/GenAI 语义约定、SRE latency/traffic/errors/saturation、NIST monitoring/measurement；默认 `observability_ready=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。边界：仅遥测契约，不联网、不调用 provider、不下载模型、不运行评测、不创建 dashboard、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 发布证据包 | 收尾 v1：`docs/STAGE2B_RELEASE_DOSSIER.md` + 占位示例 `examples/stage2b_release_dossier.example.json` + 确定性 `build_stage2b_release_dossier(config=None)` 与 CLI `tools/check_stage2b_release_dossier.py`；把 5 个 blocker 转成 release dossier/model-data card 契约，逐项列出所需 cards/records、accountable owner、reviewer、approval record shape、required links 与默认 `missing_release_evidence`；对齐 NIST AI RMF Govern/Map/Measure/Manage、OECD transparency/accountability、ISO/IEC 42001 AIMS、Google Model Cards 报告模式；默认 `ready_for_release=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。边界：仅发布证据包契约，不联网、不调用 provider、不下载模型、不运行评测、不伪造 approval、不读 .env/凭据，不勾选任何真实父项 |
| 阶段 2B 真实查询采集协议 | 收尾 v1：`docs/STAGE2B_REAL_QUERY_COLLECTION_PROTOCOL.md` + 占位示例 `examples/real_query_collection_packet.example.json` + 确定性 `build_real_query_collection_protocol(config=None)` 与 `validate_real_query_collection_packet(packet)`，CLI `tools/check_real_query_collection_protocol.py`；对齐 NIST/FTC/ICO 隐私最小化、去标识化和假名化实践，为人工采集 50-100 条真实匿名查询前定义采集目的、角色、保留/销毁、访问控制、去标识化 checklist（移除直接标识符、泛化准标识符、稀有事实复核、保留无身份 provenance、复核签字）与 PII 形态样例拦截；默认无 packet 时 `ready_for_collection=false`，占位示例含 `is_template` 不就绪，完整声明 packet 仅代表采集流程元数据/checklist 就绪。边界：仅采集协议与脱敏门禁，不收集真实查询、不提交映射表/盐/pepper、不读 secret、不联网，不勾选真实匿名查询集父项 |
| 模型接入 | 支持 OpenAI 兼容的 `/chat/completions` 接口 |
| 无 Key 模式 | 不调用模型，仍可输出严格提示词、缺项报告和审稿结果 |
| 本地存储 | 草稿保存在浏览器本地；后端使用本地 SQLite |
| Word 导出 | 生成基础 DOCX 草稿 |
| 零前端依赖 | 无 CDN、无 npm，Python 标准库即可启动 |

## 快速开始

要求 Windows 10/11 与 Python 3.10+。模型服务可选。

```powershell
git clone https://github.com/wuyutanhongyuxin-cell/cailiao.git
cd cailiao
.\start.ps1
```

启动脚本会隐藏询问 API Key；Key 只进入当前进程，不写入文件。打开 `http://127.0.0.1:8765`。

也可预先配置：

```powershell
$env:MATERIAL_LLM_BASE_URL = "https://api.openai.com/v1"
$env:MATERIAL_LLM_API_KEY = "YOUR_KEY"
$env:MATERIAL_LLM_MODEL = "gpt-4.1"
.\start.ps1
```

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MATERIAL_PORT` | `8765` | 本地服务端口 |
| `MATERIAL_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容接口根地址 |
| `MATERIAL_LLM_API_KEY` | 无 | API Key，不应写入仓库 |
| `MATERIAL_LLM_MODEL` | `gpt-4.1` | 模型名称 |

## 推荐流程

1. 选择文种，填写标题和必填事实。
2. 将政策条文、统计数据、内部事实录入证据台账。
3. 点击审稿，先解决所有阻断项。
4. 点击生成；有模型时起草，无模型时生成严格提示词。
5. 对成稿复审，确认没有无依据政策、数据和空泛措施。
6. 导出 Word，进行人工终审和正式版式处理。

## 目录结构

```text
cailiao/
├─ backend/server.py          # 服务、规则、模型调用、SQLite、DOCX
├─ frontend/                  # 本地工作台
├─ rules/material_rules.json # 文种和硬审规则
├─ tests/test_rules.py        # 核心门禁回归测试
├─ tests/test_library.py      # 资料库单元与 HTTP API 测试
├─ tests/data/retrieval_eval_suite.json # 匿名占位检索评测集
├─ tests/test_quality_gates.py # 质量门禁运行器单元测试
├─ tools/evaluate_retrieval.py # 检索评测命令行质量门禁封装
├─ tools/run_quality_gates.py # 本地与 CI 统一质量门禁入口
├─ .github/workflows/quality-gates.yml # push/PR 质量门禁 CI
├─ docs/ARCHITECTURE.md       # 架构和边界
├─ docs/ROADMAP.md            # 路线图与验收标准
├─ docs/RETRIEVAL_EVALUATION.md # 检索评测运行器说明
├─ CODEX_HANDOFF.json         # 阶段交付说明（实现范围、风险、测试命令）
└─ start.ps1                  # Windows 启动脚本
```

## 数据与安全边界

- 服务仅监听 `127.0.0.1`，默认不暴露到局域网或公网。
- `.env`、SQLite 数据库、缓存和导出文件都被 Git 忽略。
- API Key 不通过前端保存，也不应提交到仓库。
- 配置云模型后，提交给模型的任务内容与证据会离开本机；敏感材料是否可用取决于所选服务的数据政策或私有部署。
- 当前版本不替代保密审查、法制审核、事实核验或签发流程。

## 质量原则

- **事实先于表达**：缺少事实时阻断，不让模型自行补齐。
- **证据绑定主张**：政策名、年份、比例和数量应可追溯。
- **措施必须可执行**：空泛用语应具备主体、时限、机制和结果。
- **失败必须可定位**：审稿指出具体问题，不用笼统的“请优化”。
- **生成与校验分离**：不让同一模型既写作又证明自己正确。

## 项目状态

- 当前阶段：`MVP + 阶段 1 完成 + 阶段 2A 完成 + 阶段 2B 检索评测基座已启动 + 阶段 3 写作状态机 v1 + 结构化写作计划 v1 + 预先批准事实审计 v1 + 定点修复计划 v1 + 段落版本/锁定/回退 v1`
- 当前重点：阶段 2B 混合检索与引用验证深化（真实匿名查询集、中文 BM25/FTS 调优、向量检索、重排、引用蕴含与冲突检测）、阶段 3 逐段修复闭环（状态机 v1、结构化关联 v1、预先批准事实审计 v1、定点修复计划 v1、段落版本/锁定/回退 v1 已落地，实际模型段落改写和 UI 工作流待续）
- [完整路线图](docs/ROADMAP.md)
- [架构与实施方案](docs/ARCHITECTURE.md)

## 权威来源与参考项目

- [GB/T 9704-2012《党政机关公文格式》官方标准信息](https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=F3CC9BEF482524C895FDA7A08BB4A70E&refer=outter)
- [国务院政策文件库](https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary)
- [国家法律法规数据库](https://flk.npc.gov.cn/)
- [MackDing/zh-policy-rag](https://github.com/MackDing/zh-policy-rag)：MIT 许可，作为政策 RAG 架构参考
- [Rimagination/gongwen-draft](https://github.com/Rimagination/gongwen-draft)：仅研究产品流程；因未确认许可证，不复制其代码、字体或资源

## 已知限制

- 阶段 2B（BM25/FTS v1）已加入确定性 `bm25_like` 通道（IDF + tf 饱和 + 文档长度归一，1-4 字中文 ngram），与词面/ngram 通道 RRF 融合；但 `k1`/`b` 仍为默认值、尚未在更大真实查询集上扫参校准。
- 向量检索目前只是**骨架**：`vector_config` 默认关闭，开启后用的是进程内确定性伪 embedder（词面特征哈希），**不是**真实语义 embedding 模型、向量数据库或重排器，也无持久化向量索引；它只跑通了通道/融合/评测的接口缝，不代表真实语义检索能力。真实 embedding provider、向量库与重排仍待实施。
- 重排同样只是**骨架**：`rerank_config` 默认关闭，开启后用的是进程内确定性重排器（按词面覆盖对已融合 Top K 重排序），**不是**真实重排模型或 cross-encoder，也不检索新分段；它只跑通了重排调用点/元信息/评测的接口缝，不代表真实重排质量。真实重排 provider 仍待实施。
- 主张核验为保守词面规则：阶段 2A 判断文号/年份/数字/政策标记是否被证据覆盖，阶段 2B 进一步把每个标记精确归因到覆盖它的分段并给出覆盖率与逐分段命中详情，加入确定性冲突证据候选 v1，并新增确定性证据不足/拒绝理由 v1（`insufficiency`，机器可读审计元数据）；但这些仍是**词面覆盖/审计**，不等于语义蕴含——`insufficiency` 也只是确定性词面理由，不是 NLI 或真伪判断，尚无真正语义蕴含、完整语义级冲突检测和跨句推理。
- XLSX 解析为基础实现：按行读取共享/内联字符串与单元格文本，不处理公式计算、合并单元格语义、样式、图表与日期数字格式。
- 不保留二进制原文：仅保存规范化正文快照（raw_text，上限约 20 万字）、文件名、MIME 与字节数，不落地原始文件字节。
- 版本关系不做法律层面的自动废止推断：仅支持显式/手动关系与同源（source_url/文号）唯一匹配的新版本处理。
- DOCX 抽取仅取 OOXML 正文段落文本（w:t），XLSX 仅取单元格文本，均不含页眉页脚、文本框、图表等结构。
- DOCX 导出是结构有效的基础稿，尚未完整落实 GB/T 9704-2012 的正式排版（阶段 4）。
- 起草面板的证据台账仍主要保存在浏览器本地；资料库与台账尚未打通为统一检索来源。
- 检索评测已内置匿名合成占位集（`tests/data/retrieval_eval_suite.json`，10 条 case）固定运行器行为，但尚未建立 50-100 条真实材料匿名评测集，不能以主观观感或占位集代替真实质量指标（阶段 2B/阶段 5）。
- 所有模型输出仍需人工终审。

## 测试

```powershell
python -m unittest discover -s tests -v
python backend\server.py
```

检索评测质量门禁（确定性，可用于 CI）：

```powershell
python backend\server.py eval-retrieval --suite tests\data\retrieval_eval_suite.json --k 10 --min-title-recall 0.8 --min-chunk-recall 1.0 --max-misses 2
```

达标退出码为 0，未达标为非 0；报告 JSON 打印到 stdout，可用 `--output` 落盘。

统一质量门禁（本地与 CI 共用同一入口）：

```powershell
python tools\run_quality_gates.py          # 人类可读
python tools\run_quality_gates.py --json   # 机器可读（CI 使用）
```

按序执行：字节编译 → 单元测试 → 检索评测门禁 → `git diff --check`（不在 git 工作树时安全跳过）→ 机密/`.env` 扫描（仅按文件名报告 `.env*`，绝不读取其内容）。任一门禁失败则退出码非 0。`--skip-git-diff` 可跳过 diff 检查。GitHub Actions（`.github/workflows/quality-gates.yml`）在 push/PR 时于 Ubuntu + Python 3.11/3.12 上调用同一入口。

健康检查：`GET http://127.0.0.1:8765/api/health`

## 许可证

仓库暂未附加开源许可证。除非后续明确加入许可证，否则默认保留全部权利。第三方项目的权利归各自作者所有。
