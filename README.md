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
