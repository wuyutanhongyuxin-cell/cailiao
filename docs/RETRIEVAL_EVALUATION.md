# 检索评测基座

本文件记录阶段 2B 的可验收能力：稳定的检索评测运行器，以及在其之上落地的 BM25/FTS v1 确定性检索通道。向量检索、重排与语义核验仍在后续阶段。

## 当前能力

- `evaluate_retrieval_cases(cases, k=10)` 可对一组查询运行资料库检索；
- 支持按 `relevant_titles` 做文档级评测；
- 支持按 `relevant_chunk_ids` 做分段级评测；
- 输出 `Recall@K`、`MRR`、miss 列表和每个 case 的 Top K 结果；
- 逐 case 报告 Top K 内漏掉的标注答案（`missed_titles`、`missed_chunk_ids`）与首个命中排名（`first_relevant_rank`），聚合 `misses` 列表同样携带这些诊断字段，便于把质量门禁指向具体缺口；
- 逐 case 输出 `top_reasons`（Top K 结果的 `fused_score`、各通道 `rank`/`score` 与 `hit_reasons`），可审计每个分段为何排到该位置；
- HTTP API：`POST /api/library/evaluate-retrieval`；
- 不调用 embedding、向量库、重排模型或外部服务。

## BM25/FTS v1 检索通道

`search_library` 在阶段 2A 两路通道基础上新增确定性 `bm25_like` 通道，三路以 RRF 融合：

| 通道 | 作用 |
|---|---|
| `lexical_exact` | 整串精确命中与必备标记（文号/年份/数值/《标题》）子串命中 |
| `fts_or_ngram` | 混合中英分词的 token 覆盖召回 |
| `bm25_like` | 在更丰富的词元空间上做 Okapi BM25 加权 |

BM25 词元空间（`_bm25_terms`，仅标准库、完全确定性）：

- ASCII 词与数字 token 整体保留（如 `alpha`、`2026`）；
- 中文按连续汉字串切出长度 1-4 的 ngram（如 `现场`、`现场核查`），使多字政策术语成为带独立文档频次的加权单元。

打分为标准 Okapi BM25：`idf(t) · tf·(k1+1) / (tf + k1·(1-b + b·dl/avgdl))`，其中

- `idf(t) = log(1 + (N - df + 0.5)/(df + 0.5))`，恒为正；稀有词（df 小）权重更高；
- `k1`（默认 `1.5`）控制词频饱和，`b`（默认 `0.75`）控制文档长度归一，默认取模块常量 `BM25_K1`/`BM25_B`；`search_library(..., bm25_params={"k1","b"})` 与 `evaluate_retrieval_cases(..., bm25_params=...)` 可覆盖以便扫参，非法值回退默认；
- 权威等级只作极小 tie-breaker（`AUTHORITY_TIEBREAK`），绝不替代词面支撑；
- 命中项写入 `hit_reasons`（`bm25:<按 idf 排序的命中词>`），检索响应的 `bm25` 字段回报 `k1`/`b`/`cjk_ngram_max`/`corpus_size`/`avg_doc_len`。

过滤（`effective_only`/`source_type`/`min_authority`/`region` 等）在 SQL 层先行生效，BM25 只在过滤后的候选集上打分，因此不会绕过有效性或权威门禁。

## Case 格式

```json
{
  "id": "case-001",
  "query": "Alpha project 30 grants 2026",
  "filters": {"effective_only": "true", "min_authority": "4"},
  "relevant_titles": ["Alpha Support Policy"],
  "relevant_chunk_ids": ["chunk-id-optional"]
}
```

`relevant_titles` 适合早期匿名评测集；`relevant_chunk_ids` 适合已经完成证据分段标注的精确评测集。两者可以同时提供。

## 每个 case 的输出字段

| 字段 | 含义 |
|---|---|
| `top_titles` / `top_chunk_ids` | 该 case 检索到的 Top K 文档标题 / 分段 id |
| `title_recall_at_k` / `chunk_recall_at_k` | 文档级 / 分段级 Recall@K（无对应标注时为 `null`） |
| `missed_titles` / `missed_chunk_ids` | Top K 内未命中的标注答案，直接指出缺口 |
| `first_relevant_rank` | 首个命中的排名（未命中为 `null`） |
| `top_reasons` | Top K 结果的 `rank`/`chunk_id`/`document_title`/`fused_score`/`channels`/`hit_reasons`，用于审计排序理由 |
| `hit` | Top K 是否命中任一标注答案 |

聚合层输出 `case_count`、`k`、`title_recall_at_k`、`title_mrr`、`chunk_recall_at_k`、`chunk_mrr`、`miss_count` 与携带诊断字段的 `misses` 列表。

## 内置占位评测集

`tests/data/retrieval_eval_suite.json` 提供一份匿名合成评测集（10 条 case，含 8 条命中 + 2 条有意 miss），用于固定评测运行器行为：

- 语料为合成、脱敏内容，类别以甲/乙/丙等占位符表示，不含任何真实政策原文、机构或个人信息；
- case 覆盖精确命中、多文档召回、来源类型 / 权威等级 / 地区过滤、分段级金额命中、公文套话，以及两类有意 miss（库中不存在的类别、被 `effective_only` 过滤掉的已废止旧办法）；
- 分段相关性以标记子串（`relevant_chunk_markers`）记录，评测运行时解析为实际 `chunk_id`，因此评测集本身保持稳定、可复用；
- `tests/test_library.py::RetrievalEvalSuiteTest` 加载该集并断言 `case_count=10`、`miss_count=2`、文档级 `Recall@K=0.8`、分段级 `Recall@K=1.0` 与 miss 报告字段。

> 该占位集只用于固定行为，不代表真实检索质量；阶段 2B 仍需人工建立 50-100 条真实匿名查询集替换它。

## 可复用 helper

评测集的加载与运行已抽成可复用函数（`backend/server.py`），测试与 CLI 共用同一套逻辑：

- `load_retrieval_eval_suite(path)`：读取 JSON，校验为对象且含 `cases`，返回 `dict`；
- `build_suite_cases(suite)`：在语料已导入当前库的前提下，把 `relevant_chunk_markers` 解析为实际 `relevant_chunk_ids`；标记无法解析时抛 `ValueError`（漂移守卫）；
- `run_retrieval_eval_suite(suite, k=None, db_path=None, bm25_params=None)`：
  - 把 `suite.corpus` 导入一个**隔离的临时 SQLite**（未显式给 `db_path` 时用一次性临时文件），
  - 解析 marker 为 `chunk_id`，
  - 调用 `evaluate_retrieval_cases`，
  - 返回带 `suite` 名的 JSON 可序列化报告，
  - 结束后恢复调用方的 `DB_PATH` 并删除自建临时库（不污染主库）。

## 命令行质量门禁（Eval CLI）

同一套评测可作为确定性质量门禁运行，供 CI 或 Codex 复核：

```bash
python backend/server.py eval-retrieval --suite tests/data/retrieval_eval_suite.json --k 10 \
    --min-title-recall 0.8 --min-chunk-recall 1.0 --max-misses 2
# 等价薄封装：
python tools/evaluate_retrieval.py --suite tests/data/retrieval_eval_suite.json --k 10
```

CLI 行为：

- 报告 JSON 打印到 stdout；`--output <path>` 同时落盘；
- 阈值 `--min-title-recall`（默认 `0.8`）、`--min-chunk-recall`（默认 `1.0`）、`--max-misses`（默认 `2`）；报告内 `gate.passed`/`gate.failures`/`gate.thresholds` 说明判定；
- 全部达标 exit `0`；任一阈值不达标 exit `1`（JSON 仍完整输出）；套件加载/marker 解析失败 exit `2`；
- BM25 调参：`--bm25-k1`、`--bm25-b` 覆盖单次运行；`--sweep-bm25` 在内置小网格（k1∈{0.9,1.2,1.5,1.8}×b∈{0.5,0.75,1.0}）上扫参，输出每组结果与 `best`（默认参数不受影响）。

`eval-retrieval` 也是统一质量门禁 `tools/run_quality_gates.py` 的一个门禁环节；本地与 GitHub Actions CI 通过该统一入口一并运行编译、单测、检索评测、`git diff --check` 与机密/`.env` 扫描。

## 使用边界

- 该评测器只衡量当前检索结果是否召回标注答案；
- 它不证明片段语义蕴含主张；
- 它不检测冲突证据；
- 它不替代人工构建的 50-100 条真实匿名查询集。

后续阶段 2B 的 BM25 参数、embedding 管线和重排器都必须复用这套评测输出，避免凭主观观感判断检索质量。
