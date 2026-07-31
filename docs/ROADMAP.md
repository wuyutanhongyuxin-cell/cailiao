# 完整路线图

本文档严格区分“已完成”“下一步实施”和“长期候选”，避免把设计目标误写成现有能力。

## 总体目标

建立面向中文政务材料的本地优先写作系统，让每篇材料经过要素检查、权威证据绑定、受约束起草、独立审查、定点修复、正式导出和人工签发。最终以事实错误率、引用可追溯率、必填项覆盖率、返工次数和人工采纳率衡量，而不只看“像不像”。

最终收尾不再以新增分散能力为主，而是把现有模块收束成“资料入库 -> 材料任务 -> 证据推荐/批准 -> 生成 -> 审稿 -> 段落修复 -> DOCX 预检/导出 -> 真实评测”的端到端闭环。详细执行方案见 [FINAL_DELIVERY_PLAN.md](FINAL_DELIVERY_PLAN.md)。

收尾执行进度：MaterialTask v1 后端持久化与 API 已落地，支持任务创建、列表、详情、更新和任务级分析。它把现有 `analyze_payload`、写作状态机、批准事实、段落版本、锁定段落和导出元数据集中到一个可追踪任务记录。下一阶段应接证据搜索/附加/批准，让资料库检索结果进入任务闭环。

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
- [ ] 人工建立 50-100 条真实匿名查询集，替换内置占位集（**仍未完成**：仓库尚无真实匿名集，不得在真实集真正就绪前勾选本项）
  - [x] 真实匿名查询集接入脚手架 v1：确定性 `load_real_query_set(path)`/`validate_real_query_set(dataset)`/`summarize_real_query_readiness(dataset)` 与 `tools/validate_real_query_set.py`；校验结构、匿名化（拒绝 `name`/`phone`/`email`/`身份证` 等 PII/密钥字段名与手机号/身份证/邮箱等 PII 形态值，仅报类别不回显原值）与 provenance（`source`/`collected_at`/`anonymized=true`），就绪度分类为 `invalid`/`template`/`incomplete_real`/`ready_real`/`oversized_real`；占位/合成标记或 <50 条永远不判为 `ready_real`。附人工填写模板 `tests/data/real_query_set_template.json`。**边界：仅接入/校验脚手架，绝不伪造真实数据；真实 50-100 条匿名集仍需人工提供，未提供前上一条保持未勾选。**
  - [x] 公共真实查询集接入路径（DoIT，候选证据）v1：`docs/STAGE2B_REAL_QUERY_PUBLIC_DOIT_INTAKE.md` + 发明性 fixture `tests/data/doit_fixture.sample.jsonl` + 纯标准库 `tools/prepare_stage2b_real_query_set.py`（`prepare`/`validate`）；为行 97 提供基于**公共许可数据**（Hugging Face `ChiyuSONG/dynamics-of-instruction-tuning`，MIT）的真实查询**候选**路径，取代合成占位：仅读取本地已下载的 DoIT 记录，从 `Creative Writing` 选 50-100 条中文用户 prompt，去重并拒绝过短/空/非中文，写出含 source URL/license/extraction_method/record_count/逐 prompt sha256 且**不含助手答案**的确定性产物；`validate` 在数量/许可/来源/schema/内容校验失败时非零退出，从不联网、不读 secret、不伪造记录。**本环境 WSL 无出网/DNS，Claude 未在 WSL 产出真实 DoIT 产物；随后 Codex 在 networked Windows sidecar 下载 MIT 许可源文件并生成 `docs/evidence/stage2b/doit_creative_writing_real_query_candidate_100.json`（100 条，候选证据）。边界：候选证据非私有生产遥测、非合成占位；获得候选集不等于完成行 97——仍需相关性目标/qrels、匿名确认与就绪签核，未完成前父项保持未勾选。**
  - [x] DoIT→ready_real BM25 数据集构建器 v1：`docs/STAGE2B_DOIT_BM25_DATASET.md` + 发明性 fixture `tests/data/doit_bm25_fixture.sample.jsonl` + 纯标准库 `tools/build_stage2b_doit_bm25_dataset.py`（`build`/`validate`）；把本地已下载的 DoIT prompt-answer 记录转成带**配对语料**的 `ready_real` 查询集，供既有受门禁 BM25 扫参（`server.run_bm25_sweep_on_real_query_set`）使用：每条选中的 Creative Writing 记录（`creative_writing`/`Creative Writing` 别名均接受，须有非空助手答案）用户 prompt 作 case `query`、助手答案作**公共 MIT 语料文档**，case 的 `relevant_titles` 指向配对文档且**不含任何助手答案文本**；校验委托 `summarize_real_query_readiness`（须 `ready_real`）+ 语料配对 + set_hash 完整性 + 拒绝内嵌答案字段；`build` 选不满 `--min` 即非零退出、绝不伪造。**本环境 WSL 无出网，未下载真实 DoIT、未产出真实数据集；仅落地工具/文档/测试，并在发明性 fixture 上验证 `ready_real` 且既有 BM25 扫参可运行（title_recall=1.0）。边界：真实公共基准种子（MIT），非私有生产遥测、非最终生产校准；不勾选任何真实父项，Codex 在真实下载文件上决定后续。**
- [x] 评测集结构校验工具 v1：`tools/validate_retrieval_suite.py` + `server.validate_retrieval_suite`（校验 id 唯一/query/受支持过滤键/相关性目标/min_authority/format；错误退出非 0、告警不失败；数量不足或占位元数据给告警）——仅为真实集就绪后纳入门禁前的校验工具，不代表真实集已建立
- [ ] 在真实查询集上用 CLI 扫参校准 BM25 k1/b 与阈值（**仍未完成**：尚无 ready_real 真实集，未在真实数据上实际运行校准，不得勾选本项）
  - [x] 受门禁 BM25 扫参校准脚手架 v1：确定性 `build_bm25_sweep_grid(options=None)`/`validate_bm25_sweep_config(grid)`/`run_bm25_sweep_on_real_query_set(dataset, config=None)` 与 `tools/sweep_bm25_real_queries.py`；**硬门禁**——仅当 `summarize_real_query_readiness(...).status == "ready_real"` 且数据集自带 `corpus` 时才运行 k1/b/阈值网格扫参，逐组复用既有隔离库评测给出 title/chunk recall、MRR、miss，并按 recall→miss 选 best；模板/不足/合成/无语料集一律拒绝、不运行、不伪造结果。**边界：仅受门禁校准脚手架，绝不伪造校准结果，不联网/不调用模型；真实校准须在人工提供 ready_real 真实集后运行，未运行前上一条保持未勾选。**
  - [x] 公共 DoIT seed BM25 真实运行证据 v1：Codex 在已校验 SHA256 `9eed74db9e9fc758104739fa5f5133499606a50485ba11aa6caa01cf5adcec92` 的 MIT DoIT `creative_writing_1000.json` 上生成 `docs/evidence/stage2b/doit_creative_writing_bm25_ready_real_100.json`（100 条、`ready_real`、`set_hash=sha256:fca0300eea0480089a2f44f47d60b9a4cb7cbbc5aa6193f13427fa43e4be464b`），并保存 `docs/evidence/stage2b/doit_creative_writing_bm25_sweep_report.json`：36 组候选，best=`k1=0.9,b=1.0,threshold=0.0`，`title_recall_at_k=0.95`，`miss_count=5`。**边界：这是公共 MIT benchmark seed 的真实运行证据，不是私有生产匿名查询/生产语料校准，BM25 父项仍未完成、保持未勾选。**
- [x] 向量检索与可替换 embedding 管线骨架 v1：**默认关闭**（`VectorEmbedder`/`DeterministicHashEmbedder`/`InProcessVectorIndex`/`VectorPipeline`/`resolve_vector_pipeline`，仅标准库、确定性、进程内、绝不联网/读凭证）；`search_library`/`evaluate_retrieval_cases` 新增 opt-in `vector_config`、HTTP `?vector=` 参数，开启后作为 `vector` RRF 通道并诚实回报状态（`is_real_embedding_model: false`）
- [ ] 真实 embedding provider、持久化向量库与生产级向量索引（**仍未完成**：仓库仅有进程内确定性骨架，未接入真实供应商/持久化库/生产索引，不得勾选本项）
  - [x] 向量生产就绪校验脚手架 v1：确定性 `build_embedding_provider_readiness(config=None)`/`validate_embedding_provider_config(config)`/`build_vector_store_plan(config=None)`/`validate_vector_store_plan(plan)`/`build_vector_index_readiness(config=None)` 与 `tools/check_vector_production_readiness.py`；区分既有确定性本地骨架（`is_real_embedding_model=false`、进程内库）与真实生产就绪——仅当声明真实 embedding 供应商（含 `credential_source` 环境变量名而非密钥值）、持久化向量库（`postgres_pgvector`/`qdrant`/… 而非 `in_memory`）与索引描述时才判 `production_ready`；本地测试嵌入器/内存库/凭据形字段一律拒绝，`current_shipped_state.production_ready=false` 诚实回报当前状态；CLI 默认（当前仓库状态）退出非 0。**边界：仅就绪/配置校验脚手架，不接入真实供应商、不安装向量库、不联网、不读 `.env`/凭据；就绪判定仅代表配置完整，真实接入前上一条保持未勾选。**
  - [x] OpenAI-compatible embedding adapter + SQLite 持久化向量索引 v1：`OpenAICompatibleEmbeddingProvider` 用纯标准库 HTTP 调用 OpenAI-compatible `/embeddings`，必须显式传入 `endpoint_url`、`model`、`credential_source` 环境变量名，调用时只读取该 env var、不读 `.env`、不记录密钥值；`SQLiteVectorIndex` 用 stdlib `sqlite3` 持久化文档、向量、维度、provider metadata 与 manifest，并支持重开后的余弦扫描查询；测试使用本地 HTTP stub 验证请求 path/body/auth header、缺失 env 安全失败、metadata/error 不泄密，以及 SQLite 持久化/重开/查询。**边界：这是接入真实供应商和持久化索引的代码路径，不含真实外部 provider 鉴权成功证据、不安装/连接生产向量库或 ANN 索引；父项仍未完成、保持未勾选。**
  - [x] 向量 provider/store/index rollout 协议 v1：`docs/STAGE2B_VECTOR_ROLLOUT_PROTOCOL.md` + 占位示例 `examples/stage2b_vector_rollout.example.json` + 确定性 `build_stage2b_vector_rollout_protocol(config=None)`/`validate_stage2b_vector_rollout_packet(packet)` 与 `tools/check_stage2b_vector_rollout_protocol.py`；定义上线真实 embedding provider、持久化向量库和生产索引前的 metadata-only packet，覆盖 provider/store/index 配置、索引 manifest、迁移/回滚步骤、观测指标（latency p50/p95、error_rate、recall@k）与验收门；校验凭据只可为环境变量名、provider/index/manifest 维度一致、距离 metric 一致、迁移/回滚/观测/验收字段完整；默认与占位示例均不就绪，`roadmap_parent_items_checked=false`。**边界：仅 rollout 元数据协议，不接入真实供应商、不连接/安装向量库、不构建索引、不读 `.env`/凭据；真实接入前上一条父项保持未勾选。**
- [x] 可插拔重排骨架 v1：**默认关闭**（`Reranker`/`DeterministicLocalReranker`/`RerankPipeline`/`resolve_rerank_pipeline`，仅标准库、确定性、进程内、绝不联网/读凭证）；`search_library`/`evaluate_retrieval_cases` 新增 opt-in `rerank_config`、HTTP `?rerank=` 参数，开启后**只对已融合 Top K 重排序、绝不检索新分段**并诚实回报状态（`is_real_rerank_model: false`）。**尚缺真实重排模型/cross-encoder**——只钉好了扩展缝
- [ ] 真实重排模型 / cross-encoder provider 与 RRF 融合排序深化（**仍未完成**：仓库仅有进程内确定性重排骨架，未接入真实 cross-encoder，未做真实 RRF 评测深化，不得勾选本项）
  - [x] 重排/RRF 生产就绪校验脚手架 v1：确定性 `build_reranker_provider_readiness(config=None)`/`validate_reranker_provider_config(config)`/`build_rrf_fusion_plan(config=None)`/`validate_rrf_fusion_plan(plan)`/`fuse_ranked_results_rrf(result_sets, rank_constant=60, rank_window_size=None)`/`build_rerank_pipeline_readiness(config=None)` 与 `tools/check_rerank_production_readiness.py`；区分既有确定性本地重排骨架（`is_real_rerank_model=false`）与真实生产就绪——仅当声明真实 cross-encoder 供应商（含 `credential_source` 环境变量名而非密钥值）、评测指标（mrr/ndcg/map）与合法 RRF 融合计划时才判 `production_ready`，本地测试重排器/凭据形字段一律拒绝；`fuse_ranked_results_rrf` 为纯基于名次（`1/(rank_constant+rank)`、无分值刻度依赖，按 Elasticsearch/Qdrant 约定）、确定性、按 id 稳定断连的融合助手，支持 `rank_window_size` 截断；`current_shipped_state.production_ready=false` 诚实回报当前状态；CLI 默认（当前仓库状态）退出非 0。**边界：仅就绪/配置/融合助手脚手架，不接入真实重排模型、不下载模型、不联网、不读 `.env`/凭据；就绪判定仅代表配置完整，真实接入与评测前上一条保持未勾选。**
  - [x] HTTP rerank provider adapter + stub RRF 评测报告 v1：`HTTPRerankProvider` 用纯标准库 HTTP 调用 generic rerank API（`model/query/documents` -> `results`/`scores`），必须显式传入 `endpoint_url`、`model`、`credential_source` 环境变量名，调用时只读取该 env var、不读 `.env`、不记录密钥值；`build_stub_rrf_rerank_eval_report(dataset)` 在 `ready_real+corpus` 数据集上比较 BM25-like 排名与 BM25+本地确定性 rerank 的 RRF 结果，并输出 JSON 可序列化报告，明确 `provider_evidence=local_stub`、`is_real_rerank_provider_evidence=false`。测试用本地 HTTP stub 验证请求 path/body/auth header、缺失 env 安全失败、metadata/error 不泄密、只重排输入候选不新增文档。**边界：这是真实 rerank provider 与 RRF eval 的代码路径/本地 stub 证据，不是真实 cross-encoder provider 鉴权/推理证据，也不是生产 RRF 评测；父项仍未完成、保持未勾选。**
  - [x] 重排 provider/RRF rollout 协议 v1：`docs/STAGE2B_RERANK_ROLLOUT_PROTOCOL.md` + 占位示例 `examples/stage2b_rerank_rollout.example.json` + 确定性 `build_stage2b_rerank_rollout_protocol(config=None)`/`validate_stage2b_rerank_rollout_packet(packet)` 与 `tools/check_stage2b_rerank_rollout_protocol.py`；定义上线真实 reranker/cross-encoder 与 RRF 融合深化前的 metadata-only packet，覆盖 provider/model、候选策略（只重排融合 Top-K，绝不检索新分段）、RRF channels/rank_constant/rank_window_size/tie_policy、离线评测指标（mrr/ndcg/map/recall@k/latency_p95）、观测指标与 canary/rollback；默认与占位示例均不就绪，`roadmap_parent_items_checked=false`。**边界：仅 rollout 元数据协议，不接入真实 provider、不下载模型、不运行真实评测、不读 `.env`/凭据；真实接入与评测前上一条父项保持未勾选。**
- [x] 地区、机构、时间、格式/文种、有效性过滤的 UI/评测覆盖扩展 v1：`search_library`/HTTP 检索支持 `organization`、`format`、`date_from`/`date_to` 并保持来源类型、地区、权威、文档/分段状态与 `effective_only` 过滤；资料库“检索与核验”面板暴露机关、格式、日期、有效性范围并显示生效过滤摘要；单元/HTTP/评测 case 覆盖过滤先于 BM25/RRF 排序生效
- [x] 主张到证据的精确映射 v1：`map_claim_to_evidence` 逐标记归因到覆盖分段列表（`covered_markers`: marker → [chunk_id, ...]）、`missing_markers`、逐分段 `supporting_items`（`matched_markers`/`matched_terms`/`hit_reasons`）与 `coverage_ratio`，并入 `verify_claim` 的 `evidence_map`；保守判定不变
- [x] 确定性冲突证据候选 v1：`detect_conflict_evidence`/`verify_claim.conflict_evidence` 对同上下文不同数量标记、必备标记附近明确否定进行保守提示；命中时把原本 `supported` 的结论降级为 `needs_verification`
- [x] 证据不足/拒绝理由 v1：`build_evidence_insufficiency`/`verify_claim.insufficiency` 在每次核验中返回稳定、机器可读的 `summary`、`blocking`、`missing_markers`、`conflict_count`、词面 `overlap` 和 `details`；仅为确定性词面审计元数据，不是语义蕴含、NLI 或真伪判断
- [ ] 引用蕴含与完整语义级冲突证据检测（需 LLM/NLI，超出当前词面候选范围）（**仍未完成**：仓库仅有确定性词面冲突检测，未接入真实 NLI/LLM、未做真实蕴含/冲突推理与评测，不得勾选本项）
  - [x] NLI/语义冲突生产就绪校验脚手架 v1：确定性 `build_nli_provider_readiness(config=None)`/`validate_nli_provider_config(config)`/`build_semantic_conflict_policy(config=None)`/`validate_semantic_conflict_policy(policy)`/`build_semantic_conflict_readiness(config=None)` 与确定性标签映射 `map_nli_label_to_verdict(label)`（SNLI/MNLI 的 entailment/contradiction/neutral 与 FEVER/CFEVER 的 supports/refutes/NEI → `supports`/`refutes`/`not_enough_info`，未知标签抛 `ValueError`）与 `tools/check_semantic_conflict_readiness.py`；区分既有确定性**词面**冲突检测（`is_real_nli_model=false`、`does_semantic_entailment=false`）与真实语义就绪——仅当声明真实 NLI/LLM 供应商（含 `credential_source` 环境变量名而非密钥值）、覆盖三类判定的评测标签、合法冲突策略（`min_confidence`∈[0,1]、`block_on`/`warn_on` 为已知判定）时才判 `production_ready`，词面检测器/凭据形字段一律拒绝；`current_shipped_state.production_ready=false` 诚实回报当前状态；CLI 默认（当前仓库状态）退出非 0。**边界：仅就绪/配置/标签映射脚手架，不接入真实 NLI/LLM、不下载模型、不联网、不读 `.env`/凭据；既有词面冲突检测保持词面，真实推理与评测前上一条保持未勾选。**
  - [x] HTTP NLI/LLM semantic judge adapter + stub semantic eval v1：`HTTPSemanticJudgeProvider` 用纯标准库 HTTP 调用 generic semantic judge API（`model/claim/evidence/labels` -> `verdict/confidence`），必须显式传入 `endpoint_url`、`model`、`credential_source` 环境变量名，调用时只读取该 env var、不读 `.env`、不记录密钥值；`normalize_semantic_eval_verdict` 支持 `entailment`/`contradiction`/`neutral`/`abstain` 及常见别名；`build_stub_semantic_eval_report(cases)` 对 claim/evidence/expected/predicted verdict 样例输出 confusion matrix、per-label precision/recall/F1、accuracy，并明确 `provider_evidence=local_stub`、`is_real_nli_provider_evidence=false`。测试用本地 HTTP stub 验证请求 path/body/auth header、缺失 env 安全失败、metadata/error 不泄密、JSON report 完整。**边界：这是真实 NLI/LLM provider 与语义评测的代码路径/本地 stub 证据，不是真实 provider 鉴权/推理证据，也不是生产语义蕴含/冲突评测；父项仍未完成、保持未勾选。**
  - [x] 公共 NLI 语义评测数据接入 v1：`load_public_nli_eval_records`/`build_public_nli_semantic_eval_dataset`/`validate_public_nli_semantic_eval_dataset`/`summarize_public_nli_semantic_eval_readiness` 与 `tools/prepare_stage2b_public_nli_eval.py`；仅读取本地已下载 JSON/JSONL，支持 FEVER/CFEVER/SNLI/MNLI 风格 `claim/evidence` 或 `hypothesis/premise` 与 `entailment`/`contradiction`/`neutral`/`NEI` 标签，校验来源 URL、license、set_hash、标签覆盖、重复 id、空 claim/evidence、凭据形字段，并可接入 `build_stub_semantic_eval_report` 生成本地 stub 指标。**边界：仅公共数据 intake/schema/本地 stub eval 路径，不下载数据、不调用真实 NLI/LLM provider、不证明真实语义蕴含/冲突检测；`is_real_nli_provider_evidence=false`、`roadmap_parent_items_checked=false`，父项仍保持未勾选。**
  - [x] NLI/LLM 语义 rollout 协议 v1：`docs/STAGE2B_NLI_SEMANTIC_ROLLOUT_PROTOCOL.md` + 占位示例 `examples/stage2b_nli_semantic_rollout.example.json` + 确定性 `build_stage2b_nli_semantic_rollout_protocol(config=None)`/`validate_stage2b_nli_semantic_rollout_packet(packet)` 与 `tools/check_stage2b_nli_semantic_rollout_protocol.py`；定义上线真实 NLI/LLM 语义蕴含/冲突检测前的 metadata-only packet，覆盖 provider/model、FEVER/SNLI 标签映射与三类 verdict 覆盖、`min_confidence`/`block_on`/`warn_on` 策略、证据字段（claim_text/cited_chunk_ids/context_window/provenance）、per-label precision/recall/F1/confusion matrix/calibration/abstention/refusal eval packet、人审升级、观测指标与 canary/rollback；默认与占位示例均不就绪，`roadmap_parent_items_checked=false`。**边界：仅 rollout 元数据协议，不接入真实 NLI/LLM、不下载模型、不运行真实评测、不读 `.env`/凭据；真实推理与评测前上一条父项保持未勾选。**
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
- [x] 表格、附件说明、文号、签发人、主送机关等结构化字段 v1：确定性 `build_docx_structured_fields(options=None)` 与 `docx_table_xml(table, style_id)`；通过 `style_profile` 支持 `document_number`、`issuer`、`recipient`、`attachments`（字符串或 `title`/`name` 结构）和简单 `tables`（`headers`/`rows`），导出时文号/签发人/主送机关靠前插入，附件说明和基础 OOXML `w:tbl` 表格追加在正文后、落款/版记前；字段输出稳定归一化并给出 counts。**边界：基础字段段落和简单表格 XML，不做复杂表格样式、附件分页、正式版记排版、不加 UI/持久化。**
- [x] 字体缺失检测和替代策略 v1：确定性 `build_font_fallback_plan(style_profile=None)`，基于 `build_docx_style_profile` 逐角色（body/title/heading/latin）报告请求字体、是否在保守内置已知字体列表 `_KNOWN_DOCX_FONTS` 中、按角色的回退候选链（去重且排除请求字体本身），并对未知字体给出告警；输出 `method=docx_font_fallback_plan_v1` 与 counts。**边界：仅建议性元数据，不替换/嵌入/下载字体，不读取宿主已安装字体，不改动 `export_docx` 输出。**
- [x] 导出前格式预检报告 v1：确定性 `build_export_preflight_report(title, body, style_profile=None)`，汇总 `method`/`version`、字体回退计划、布局计划摘要、结构化字段摘要与导出边界告警（含未知字体告警），输出 `method=docx_export_preflight_v1` 与 summary counts。**边界：仅检查请求本地输入，不写文件、不调用模型、不访问网络，不是正式排版认证。**
- [x] DOCX 版式回归（结构/标记）v1：确定性 `inspect_docx_package_layout(raw_docx)` 与 `build_docx_layout_regression_report(title, body, style_profile=None)`；导出后打开 OOXML 包，校验 `word/document.xml`/`word/styles.xml` 是否存在、启用页码时是否含 `word/footer1.xml` 与 PAGE 字段、标题/正文/层级样式引用、页面尺寸与页边距，并复用既有 helper 汇总表格/附件/未知字体计数，输出命名的通过/失败检查项与 `method=docx_layout_regression_v1`。**边界：仅对生成的 OOXML 包做结构/标记回归，不内置或调用真实渲染器；视觉渲染截图与像素级版式回归仍为后续工作。**

验收：Word/WPS 打开无修复提示；关键版式自动检查通过；多页材料无异常分页或溢出；模板由熟悉公文格式的人员验收。

## 阶段 5：质量评测体系

优先级：高

- [x] 匿名化真实任务集和标准答案要素（schema）v1：确定性 `load_benchmark_suite`/`validate_benchmark_suite` 与 `tools/validate_benchmark_suite.py`；定义基准集 schema——`metadata`（name/version/`anonymized=true`）与 `cases[]`（id、genre、prompt_fields、facts、evidence、expected_elements），`expected_elements` 分 facts/citations/structure/language 四维；校验唯一 id、必填字段、类型与维度合法性，占位/小规模集给告警不失败。附合成样例 `tests/data/benchmark_suite_sample.json`。**边界：仅结构 schema 与校验骨架，不含真实私有数据（样例为合成占位），真实 50-100 条匿名集仍需人工建立。**
- [x] 事实、引用、结构、语言分项评分（骨架）v1：确定性 `score_benchmark_suite(suite, responses=None)`；对每个 case 按词面标记覆盖率逐维（facts/citations/structure/language）评分，候选文本取 `responses[case_id]` 或 case 的 `reference_answer`，返回逐 case 维度分与聚合维度均值/总均值，`method=benchmark_lexical_scoring_v1`。**边界：仅词面覆盖率打分骨架，非语义/事实/人工质量评分，不调用模型、不访问网络。**
- [x] 模型和版本隐藏的盲评（打包/揭盲骨架）v1：确定性 `build_blind_evaluation_pack(suite, candidates)`/`validate_blind_evaluation_pack(pack)`/`reveal_blind_evaluation_results(pack, scores_or_reviews)` 与 `tools/validate_blind_eval.py`；候选按稳定盲标签（`candidate_a`/`candidate_b`…）打包，评审视图仅暴露盲 id、case id 与答案文本，隐藏 provider/model/version，身份映射另存 `reveal_map`；校验元数据、case id 唯一、各 case 盲 id 一致、评审面无身份字段（model/provider/version/vendor…）泄漏；揭盲按 case+盲 id 汇入评审分并给出逐候选数值均值。附合成样例 `tests/data/blind_eval_candidates_sample.json` 与 `tests/data/blind_eval_scores_sample.json`。**边界：仅 v1 打包/揭盲骨架，盲标签按输入顺序（防位置泄漏需上游预洗牌），不含真实数据、不调用模型/网络、不加 UI/持久化。**
- [x] 与人工写作、通用提示词和不同模型对照（矩阵/聚合骨架）v1：确定性 `build_comparison_baseline_matrix(suite, candidate_outputs)`/`validate_comparison_baseline_matrix(matrix)`/`summarize_comparison_baseline_scores(matrix, scoring_results=None)` 与 `tools/validate_comparison_baselines.py`；将 case id 与各对照臂（`human` 人工写作、`generic_prompt` 通用提示词、`project_prompt` 项目提示词、`model` 模型/版本臂，均用离线预产出文本，不调用模型）拼成矩阵，臂描述仅暴露 arm_id/arm_type/label/可选 blind_id，身份另存 `identity_map`；校验 case/臂 id 唯一、各 case 臂 id 一致、arm_type 合法、评审面无身份字段泄漏，缺失产出记为空串+告警而非崩溃；给分时仅按数值 case 分逐臂聚合均值（不臆造分数，可接受盲评揭盲的 `cases_scored` 形态）。附合成样例 `tests/data/comparison_baselines_sample.json` 与 `tests/data/comparison_baseline_scores_sample.json`。**边界：仅 v1 矩阵+聚合骨架，不含真实人工/模型数据、不调用模型/网络、不加 UI/持久化；采纳率/修改距离/完成时间/回归运行等项仍未完成。**
- [x] 记录采纳率、修改距离、完成时间、返工轮次（记录/汇总骨架）v1：确定性 `load_outcome_metrics_log(path)`/`validate_outcome_metrics_log(log)`/`summarize_outcome_metrics(log)` 与 `tools/validate_outcome_metrics.py`；日志 `rows[]` 逐行按 `case_id`+`arm_id` 记录采纳（`accepted` 或 `accepted_sections`/`total_sections`）、修改距离（有 `draft_text`/`final_text` 则用标准库 Levenshtein，否则用给定 `edit_distance`）、完成时间（`duration_seconds` 或 `started_at`/`completed_at` 之差，标准库解析 ISO 时间戳）、返工轮次（`revision_rounds`/`rework_rounds`）；确定性算出 `adoption_rate`/`edit_distance`/`duration_seconds`/`rework_rounds` 并逐臂与整体聚合；校验 case/臂身份唯一、数值范围、缺 duration 时时间戳可解析，缺失可选指标给告警而非报错。附合成样例 `tests/data/outcome_metrics_sample.json`。**边界：仅 v1 确定性记录+汇总骨架，无持久化 DB/UI、不含真实人工/模型数据、不调用模型/网络，非因果或显著性分析；回归评测运行自动化仍未完成。**
- [x] 每次规则或模型更新运行回归评测（运行汇总骨架）v1：确定性 `build_regression_evaluation_run(config)`/`validate_regression_evaluation_run(run)`/`summarize_regression_evaluation_run(run)` 与 `tools/run_regression_evaluation.py`；config 记录触发类型（`rules_update`/`model_update`/`manual`）、`baseline_ref`/`candidate_ref` 版本引用、基准集与各组件报告（基准打分、盲评、对照矩阵、成效指标、检索评测）；汇总列出各报告名与 pass/fail/warn 计数、在 baseline/candidate 均有数值时算 candidate−baseline 差值，给出 `passed`/`failed`/`needs_review` 状态；校验必填元数据、触发类型合法、报告名唯一、数值差值良构，缺失可选报告给告警。附合成样例 `tests/data/regression_run_sample.json`。**边界：仅 v1 本地回归汇总骨架，仅归一化既有组件报告、不执行底层评测、不调用模型/网络、除 CLI 外不做调度/CI 集成。**

阶段 5 骨架状态：以上评测体系条目均已落地为确定性、纯标准库的 v1 骨架（schema/校验/汇总/CLI 与合成样例）。**尚未完成的是真实基础：50-100 条真实匿名任务集、真实模型/供应商产出、真实盲评与人工对照数据，以及生产级调度/CI 集成——这些骨架不代表已具备成熟材料写作评测能力。**

发布门槛建议：关键事实虚构率 0；强制字段漏检率 0；引用可追溯率 100%；回归可定位到规则、检索、模型或模板版本。

## 阶段 6：部署与治理

优先级：中

- [x] 本地配置页面和完全离线模型选项（骨架）v1：确定性 `build_local_config(overrides=None)`/`validate_local_config(config)`/`build_offline_placeholder_draft(prompt)` 与模型模式感知的 `call_llm(prompt, config=None)`；HTTP `GET /api/config` 返回安全默认（离线）配置、`POST /api/config/validate` 校验模式；模型模式 `offline`（默认，不联网、返回本地占位草稿）、`prompt_only`（不联网，仅严格提示词）、`openai_compatible`（仅服务端已配置 MATERIAL_LLM_* 时联网，否则回退 prompt_only），离线/仅提示词模式硬保证 `network_used=false`；配置仅以布尔暴露 provider 是否已配置，绝不读取 `.env`/凭据值；前端新增“设置”面板（模式选择 + 离线边界说明），并按模式把 `config` 传入 `/api/generate`。**边界：v1 本地配置/离线选项骨架，不内置本地推理引擎、不安装依赖、不联网、不读取 `.env`/凭据；其余阶段 6 项（多用户/加密备份/数据流分级/依赖锁定）未开始。**
- [x] 多用户角色、项目空间、最小权限（策略骨架）v1：确定性 `build_access_context(user=None, workspace=None)`/`check_permission(context, action, resource=None)`/`validate_access_policy(policy_or_context)` 与 HTTP `GET /api/access/context`、`POST /api/access/validate`、`POST /api/access/check`；角色 `owner`/`admin`/`editor`/`reviewer`/`viewer` 各映射到显式权限矩阵（`read`/`generate`/`review`/`export`/`manage_library`/`manage_users`/`manage_config`），未知/缺省角色回退最小权限 `viewer`；`check_permission` 校验动作合法、角色允许并做项目空间隔离（resource 的 `workspace_id` 与上下文不符即拒绝）；`validate_access_policy` 校验角色/项目空间与 `allowed_actions` 是否与角色规范矩阵一致；无鉴权供应商，仅确定性演示当前用户上下文；前端“设置”面板显示角色/项目空间/允许操作（无伪造登录 UI）。**边界：v1 RBAC/项目空间策略骨架，无密码/会话/鉴权供应商、无持久化 DB、不联网、不读 `.env`/凭据，非生产级访问控制；加密备份/审计日志/数据流分级/SBOM 等项未开始。**
- [x] 加密、备份、恢复、保留期和审计日志（元数据/清单/审计骨架）v1：确定性 `build_encryption_policy`/`validate_encryption_policy`、`build_backup_manifest`/`validate_backup_manifest`、`build_restore_plan`/`validate_restore_plan`、`build_retention_policy`/`validate_retention_policy`、`build_audit_record`/`validate_audit_record` 与汇总 `build_governance_policy`，HTTP `GET /api/governance/policy`、`POST /api/governance/audit/validate`；加密仅记录算法/密钥来源/状态标签并硬拒绝任何密钥字段（`key`/`secret`/`password`/`private_key`/`passphrase`…），备份清单以 `sort_keys` sha256 生成确定性校验和、恢复计划逐条给出 verify/unverified 步骤与告警、保留期按类型天数列出删除候选（仅报告不删除），审计记录含 `timestamp`/`actor`/`action`/`workspace_id`/`resource`/`result`/`reason` 且缺 `actor`/`action` 即拒；前端“设置”面板可查看治理策略。**边界：v1 元数据/清单/审计骨架，不实现真实加密、不做破坏性删除、不执行真实备份/恢复、无持久化、不联网、不读 `.env`/凭据；模型供应商数据流分级/SBOM 等项未开始。**
- [x] 模型供应商数据流提示与风险分级（披露/分级骨架）v1：确定性 `build_provider_profile(options=None)`/`build_provider_disclosure(profile)`/`grade_provider_risk(profile)`/`validate_provider_profile(profile)` 与汇总 `build_provider_risk_summary`，HTTP `GET /api/providers/risk`、`POST /api/providers/risk/grade`；供应商画像记录 `provider_id`/`mode`（offline/local_only/openai_compatible/external_api）/`endpoint_type`/发送数据类别/`stores_data`/`trains_on_data`/数据驻留/保留说明，本地模式不外发任何数据；披露对象给出使用模型前的数据流说明；风险分级按标记确定性给出 `low`/`medium`/`high`/`blocked`（本地=low、未知/不支持 mode=blocked，外发数据、敏感类别、存储、训练、驻留未知逐级加分升级）；画像与校验硬拒绝任何 `api_key`/`token`/`secret`/含凭据地址等字段；前端“设置”面板可查看数据流与风险。**边界：v1 披露/分级策略骨架，不接入真实供应商、不联网、不读 `.env`/凭据；依赖锁定/SBOM/容器部署等项未开始。**
- [x] 依赖锁定、软件物料清单和可选容器部署（供应链元数据骨架）v1：确定性 `build_dependency_inventory`/`validate_dependency_inventory`、`build_sbom_document`/`validate_sbom_document`、`build_container_deploy_plan`/`validate_container_deploy_plan` 与汇总 `build_supply_chain_summary`，HTTP `GET /api/supply-chain/sbom`；项目为 stdlib-first（运行时零第三方依赖），仓库内新增 `requirements.txt`（仅注释、无第三方包）、`docs/sbom.json`（确定性 CycloneDX 风格、仅自有组件、无时间戳可复现，测试断言与 `build_sbom_document` 同步）与可选 `Containerfile`（离线优先骨架、无 `RUN`/安装步骤、不构建不推送）；清单/SBOM 校验硬拒绝任何凭据字段；前端“设置”面板可查看依赖与 SBOM。**边界：v1 供应链元数据骨架，不安装依赖、不构建/推送镜像、不使用网络/镜像仓库、不读 `.env`/凭据。**

阶段 6 骨架状态：以上部署与治理条目均已落地为确定性、纯标准库的 v1 骨架（本地/离线配置、RBAC/项目空间、加密备份审计元数据、供应商数据流分级、依赖/SBOM/容器）。**尚未完成的是真实实现：真实鉴权/会话与持久化、真实加密与备份/恢复执行、生产级审计存储、真实供应商合规核验与镜像构建/发布流水线——这些骨架不代表已具备生产级部署与治理能力。**

验收：默认不暴露公网；凭据不进日志、前端存储或导出；操作可追溯；恢复演练通过。

## 外部依赖终审门禁

- [x] 外部依赖终审门禁 v1：确定性 `build_external_dependency_audit(config=None)` 与 `tools/check_external_dependency_audit.py`，把 5 项无法在本仓库内诚实完成（需人工数据或真实供应商凭据）的 ROADMAP 依赖聚合为机器可读阻断项——真实匿名查询集（行 97）、真实集 BM25 校准（行 100）、真实 embedding 供应商+持久化向量库+生产索引（行 103）、真实重排/cross-encoder+RRF（行 107）、真实 NLI/LLM 语义冲突（行 114）；每项列出 blocker id、ROADMAP 行/主题、所需外部输入、当前仓库状态与已保护它的就绪脚手架/门禁；默认 `all_external_dependencies_satisfied=false`、`roadmap_parent_items_checked=false`，CLI 默认退出 1。仅当 config 声明工件“元数据形状”通过对应就绪 helper 时才判该项满足（纯元数据形状，非真实连通/鉴权/评测）。**诚实边界：本门禁**不**勾选上述 5 个父项；它们只有在真实数据/真实供应商接入并评测后才可由人工勾选。绝不伪造数据/连通/指标/凭据/模型下载/评测结果，不联网、不读 `.env`/凭据。**
- 说明：上述 5 个父项（行 97/100/103/106/112）在本仓库内保持未勾选状态；本条仅记录聚合“终审门禁”脚手架本身已就位。
- [x] 阶段 2B 生产落地 playbook v1：`docs/STAGE2B_PRODUCTION_PLAYBOOK.md` + 确定性 `build_stage2b_production_playbook_status(config=None)` 与 `tools/check_stage2b_production_playbook.py`；把仍未完成的 Stage 2B 真实项转化为行业对齐的落地计划——目标架构（BM25/FTS + 稠密向量 + 可选稀疏 + RRF/加权 RRF/DBSF 选型 + 仅对 Top-K 做 CrossEncoder 重排 + NLI 引用蕴含/语义冲突）、6 步落地序列、BEIR 风格评测指标（recall@k/precision@k/nDCG@k/MRR/MAP/miss/延迟 p50-p95/拒答率）、验收门与回滚标准、凭据仅记环境变量名的安全边界，并链接 Elasticsearch RRF / Qdrant hybrid / SBERT retrieve-rerank / BEIR 参考；6 个阶段的就绪度镜像 `build_external_dependency_audit` 的 5 项阻断，默认 `ready_for_real_provider_rollout=false`，CLI 默认退出 1。**诚实边界：本 playbook 仅为规划/就绪矩阵，`roadmap_parent_items_checked=false`，绝不勾选上述 5 个真实父项，也不联网/不接入供应商/不读 `.env`/凭据。**
- [x] 阶段 2B 工件契约 v1：`docs/STAGE2B_ARTIFACT_CONTRACTS.md` + 占位示例 `examples/stage2b_artifacts.example.json` + 确定性 `build_stage2b_artifact_contracts(config=None)` 与 `tools/check_stage2b_artifact_contracts.py`；为 `config.artifacts` 下 5 个真实输入键定义必填/禁用（密钥值）字段、对应校验 helper、能证明与不能证明的内容，`contract_count=5`、`example_is_placeholder_only=true`、`ready_artifact_count` 依据既有 audit satisfied_ids、`roadmap_parent_items_checked=false`；占位示例含 `is_template` 标记且凭据仅记环境变量名，默认不会让 `build_external_dependency_audit` 满足；CLI 默认退出 0（契约/模板作为文档存在且有效，同时声明未提供真实工件、父项仍未勾选），`--require-ready-artifacts` 默认退出 1。**诚实边界：仅数据/元数据形状契约，非真实供应商校验，绝不勾选上述 5 个真实父项，也不伪造数据/凭据/连通，不联网/不读 `.env`/凭据。**
- [x] 阶段 2B 评测运行契约 v1：`docs/STAGE2B_EVAL_RUN_CONTRACT.md` + 占位示例 `examples/stage2b_eval_run.example.json` + 确定性 `build_stage2b_eval_run_contract(config=None)` 与 `validate_stage2b_eval_run_manifest(manifest)` 及 `tools/check_stage2b_eval_run_contract.py`；对齐 TREC qrels/runfile/结果快照与 ir-measures/BEIR 指标约定，定义评测运行清单形状（`run_id`/`created_at`/`dataset_id`/`dataset_readiness_status`/`query_count`、各检索配置版本摘要、qrels/runfile/结果快照 path|uri+sha256 指针、必填指标 recall@k/precision@k/ndcg@k/mrr/map/miss_rate/latency_p50/latency_p95/refusal_insufficiency_rate、显式验收裁决）；校验要求非模板、`dataset_readiness_status==ready_real`、`query_count∈[50,100]`、指标齐全为数值、`latency_p95≥latency_p50`、指针与哈希存在、裁决明确，绝不读取指针文件/校验哈希内容/调用供应商；默认与占位示例均判不就绪，`roadmap_parent_items_checked=false`，CLI 默认退出 1、清单校验通过退出 0。**诚实边界：仅清单形状契约，证明“声明完整一致”而非“运行真发生/文件存在/指标为真”，绝不勾选上述 5 个真实父项，也不伪造评测结果，不联网/不读 `.env`/凭据。**
- [x] 阶段 2B 真实查询采集协议 v1：`docs/STAGE2B_REAL_QUERY_COLLECTION_PROTOCOL.md` + 占位示例 `examples/real_query_collection_packet.example.json` + 确定性 `build_real_query_collection_protocol(config=None)`/`validate_real_query_collection_packet(packet)` 与 `tools/check_real_query_collection_protocol.py`；对齐 NIST/FTC/ICO 隐私最小化、去标识化和假名化实践，为人工采集 50-100 条真实匿名查询前定义采集目的、角色、保留/销毁、访问控制、去标识化 checklist（移除直接标识符、泛化准标识符、稀有事实复核、保留无身份 provenance、复核签字）与 PII 形态样例拦截；默认无 packet 时 `ready_for_collection=false`，占位示例含 `is_template` 不就绪，完整声明 packet 仅代表采集流程元数据/checklist 就绪。**诚实边界：仅采集协议与脱敏门禁，不收集真实查询、不提交映射表/盐/pepper、不读 secret、不联网；行 97 的真实匿名查询集父项仍保持未勾选。**
- [x] 阶段 2B rollout 协议汇总门禁 v1：确定性 `build_stage2b_rollout_protocols_status(config=None)` 与 `tools/check_stage2b_rollout_protocols_status.py`；聚合向量、重排、NLI 三类 rollout packet 的就绪状态，并把外部依赖 audit 的 `protected_by` 更新为同时列出 readiness helper 与 rollout 协议；默认不就绪，完整声明 packet 仅证明 metadata shape 完整，`roadmap_parent_items_checked=false`。**诚实边界：仅汇总门禁，不接入 provider、不连接向量库、不下载模型、不运行评测、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 人工行动包 v1：`docs/STAGE2B_HUMAN_ACTION_PACKET.md` + 占位示例 `examples/stage2b_human_action_packet.example.json` + 确定性 `build_stage2b_human_action_packet(config=None)` 与 `tools/check_stage2b_human_action_packet.py`；把 5 个仍需人工/外部输入的 blocker 转成机器可读 action item，逐项列出 ROADMAP 行 97/100/103/107/114、所需真实输入、验收工件、当前仓库状态和保护门禁；默认 `all_human_actions_resolved=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅行动清单，不采集数据、不调用 provider、不下载模型、不运行评测、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 标准追踪矩阵 v1：`docs/STAGE2B_STANDARDS_TRACEABILITY.md` + 占位示例 `examples/stage2b_standards_traceability.example.json` + 确定性 `build_stage2b_standards_traceability(config=None)` 与 `tools/check_stage2b_standards_traceability.py`；把 5 个 blocker 映射到 NIST AI RMF/GenAI、BEIR、Elasticsearch RRF、Qdrant hybrid、SBERT retrieve-rerank 等参考，逐项列出标准/URL、所需证据工件、仓库已有 guardrail、仍缺真实证明；默认 `all_external_proofs_present=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅标准/参考追踪，运行期不联网、不调用 provider、不下载模型、不运行评测、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 推广/SLO 门禁 v1：`docs/STAGE2B_PROMOTION_GATES.md` + 占位示例 `examples/stage2b_promotion_gates.example.json` + 确定性 `build_stage2b_promotion_gates(config=None)` 与 `tools/check_stage2b_promotion_gates.py`；把 5 个 blocker 转成推广门禁/SLO 策略，逐项列出必需指标/工件、默认 gate、rollback trigger、证据来源与当前 `blocked_by_external_input` 状态；对齐 NIST AI RMF/GenAI、BEIR/ir-measures、OpenTelemetry latency/error 观测和 SRE SLO/error-budget 纪律；默认 `ready_for_promotion=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅确定性推广策略，运行期不联网、不调用 provider、不下载模型、不运行评测、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 可观测性契约 v1：`docs/STAGE2B_OBSERVABILITY_CONTRACT.md` + 占位示例 `examples/stage2b_observability_contract.example.json` + 确定性 `build_stage2b_observability_contract(config=None)` 与 `tools/check_stage2b_observability_contract.py`；把 5 个 blocker 转成 telemetry contract，逐项列出必需 metrics/events、dimensions、SRE 四金信号映射、alert/rollback signal、证据来源与默认 `missing_real_telemetry`；对齐 OpenTelemetry metrics/traces/GenAI 语义约定、SRE latency/traffic/errors/saturation、NIST monitoring/measurement；默认 `observability_ready=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅遥测契约，运行期不联网、不调用 provider、不下载模型、不运行评测、不创建 dashboard、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 发布证据包 v1：`docs/STAGE2B_RELEASE_DOSSIER.md` + 占位示例 `examples/stage2b_release_dossier.example.json` + 确定性 `build_stage2b_release_dossier(config=None)` 与 `tools/check_stage2b_release_dossier.py`；把 5 个 blocker 转成 release dossier/model-data card 契约，逐项列出所需 cards/records、accountable owner、reviewer、approval record shape、required links 与默认 `missing_release_evidence`；对齐 NIST AI RMF Govern/Map/Measure/Manage、OECD transparency/accountability、ISO/IEC 42001 AIMS、Google Model Cards 报告模式；默认 `ready_for_release=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅发布证据包契约，运行期不联网、不调用 provider、不下载模型、不运行评测、不伪造 approval、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 可复现/溯源清单 v1：`docs/STAGE2B_REPRODUCIBILITY_PROVENANCE.md` + 占位示例 `examples/stage2b_reproducibility_provenance.example.json` + 确定性 `build_stage2b_reproducibility_provenance(config=None)` 与 `tools/check_stage2b_reproducibility_provenance.py`；把 5 个 blocker 转成 W3C PROV 风格 entity/activity/agent 与 reproducibility artifact/immutable id 清单，默认 `missing_reproducibility_proof`；对齐 W3C PROV、TREC qrels/run/eval、ML reproducibility checklist、NIST measurement provenance；默认 `reproducibility_ready=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅可复现/溯源清单，运行期不联网、不调用 provider、不下载模型、不运行评测、不校验哈希内容、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 风险登记册 v1：`docs/STAGE2B_RISK_REGISTER.md` + 占位示例 `examples/stage2b_risk_register.example.json` + 确定性 `build_stage2b_risk_register(config=None)` 与 `tools/check_stage2b_risk_register.py`；把 5 个 blocker 转成风险声明、影响、默认可能性/严重性、treatment plan、owner role、closure evidence 与默认 `open_external_risk`；对齐 NIST AI RMF / AI RMF Playbook / ISO 31000 风险治理与登记册概念；默认 `all_risks_closed=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅风险登记/处置计划，运行期不联网、不调用 provider、不下载模型、不运行评测、不伪造 risk acceptance、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 最终完成阻断审计 v1：`docs/FINAL_COMPLETION_BLOCKER_AUDIT.md` + 确定性 `build_final_completion_blocker_audit(config=None)` 与 `tools/check_final_completion_blocker_audit.py`；复用 external dependency audit 与 risk register，声明当前 `project_complete=false`、`repo_only_work_remaining=false`、`blocked_by_external_input=true`，并列出 5 个真实外部 blocker 与 ROADMAP 行 97/100/103/107/114；默认 CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅最终完成边界审计，运行期不联网、不调用 provider、不下载模型、不运行评测、不伪造 risk acceptance/approval、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 业界实施清单 v1：`docs/STAGE2B_INDUSTRY_IMPLEMENTATION_CHECKLIST.md` + 确定性 `build_stage2b_industry_implementation_checklist(config=None)` 与 `tools/check_stage2b_industry_implementation_checklist.py`；把联网复核的 NIST AI RMF/AIRC、BEIR、Elasticsearch RRF、Qdrant hybrid、SentenceTransformers retrieve-rerank、OpenTelemetry、FEVER/SNLI/CFEVER 方法转成 5 个 blocker 的实施步骤、最小证据、质量门禁、观测要求、回滚/人审规则；默认 `ready_for_stage2b_completion=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅清单/元数据，运行期不联网、不调用 provider/向量库、不下载模型、不运行评测、不创建 telemetry、不伪造 approval/risk acceptance、不读 `.env`/凭据；不勾选任何真实父项。**
- [x] 阶段 2B 证据包校验器 v1：`docs/STAGE2B_EVIDENCE_PACKAGE_VALIDATOR.md` + 模板占位 `examples/stage2b_evidence_package.example.json` + 确定性 `build_stage2b_evidence_package_validator(config=None)` 与 `tools/check_stage2b_evidence_package_validator.py`；复用既有 `build_external_dependency_audit` 与七个 Stage 2B 契约/状态 helper（artifact contracts / eval-run contract / observability / release dossier / reproducibility / risk register / industry checklist）为单一事实源，把全部外部 blocker 证据聚合为一处就绪报告：固定 7 个证据组（`declared_artifacts`/`eval_run_manifest`/`observability_snapshot`/`release_dossier`/`reproducibility_provenance`/`risk_treatment`/`industry_checklist`）、按既有顺序的 5 个 `blocker_ids`（行 97/100/103/107/114）、逐 blocker×组的所需证据摘要，并报告各组当前就绪度；默认 `ready_for_stage2b_completion=false`、CLI 退出 1、`roadmap_parent_items_checked=false`。**诚实边界：仅元数据/证据包校验，运行期不联网、不调用 provider、不下载模型、不运行评测、不读工件文件或哈希、不读 `.env`/凭据、不伪造 approval/risk acceptance；不勾选任何真实父项。**

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
