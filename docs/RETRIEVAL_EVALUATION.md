# 检索评测基座

本文件记录阶段 2B 的第一块可验收能力：在接入 BM25 调优、向量检索、重排或语义核验前，先建立稳定的检索评测运行器。

## 当前能力

- `evaluate_retrieval_cases(cases, k=10)` 可对一组查询运行资料库检索；
- 支持按 `relevant_titles` 做文档级评测；
- 支持按 `relevant_chunk_ids` 做分段级评测；
- 输出 `Recall@K`、`MRR`、miss 列表和每个 case 的 Top K 结果；
- 逐 case 报告 Top K 内漏掉的标注答案（`missed_titles`、`missed_chunk_ids`）与首个命中排名（`first_relevant_rank`），聚合 `misses` 列表同样携带这些诊断字段，便于把质量门禁指向具体缺口；
- HTTP API：`POST /api/library/evaluate-retrieval`；
- 不调用 embedding、向量库、重排模型或外部服务。

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
| `hit` | Top K 是否命中任一标注答案 |

聚合层输出 `case_count`、`k`、`title_recall_at_k`、`title_mrr`、`chunk_recall_at_k`、`chunk_mrr`、`miss_count` 与携带诊断字段的 `misses` 列表。

## 内置占位评测集

`tests/data/retrieval_eval_suite.json` 提供一份匿名合成评测集（10 条 case，含 8 条命中 + 2 条有意 miss），用于固定评测运行器行为：

- 语料为合成、脱敏内容，类别以甲/乙/丙等占位符表示，不含任何真实政策原文、机构或个人信息；
- case 覆盖精确命中、多文档召回、来源类型 / 权威等级 / 地区过滤、分段级金额命中、公文套话，以及两类有意 miss（库中不存在的类别、被 `effective_only` 过滤掉的已废止旧办法）；
- 分段相关性以标记子串（`relevant_chunk_markers`）记录，评测运行时解析为实际 `chunk_id`，因此评测集本身保持稳定、可复用；
- `tests/test_library.py::RetrievalEvalSuiteTest` 加载该集并断言 `case_count=10`、`miss_count=2`、文档级 `Recall@K=0.8`、分段级 `Recall@K=1.0` 与 miss 报告字段。

> 该占位集只用于固定行为，不代表真实检索质量；阶段 2B 仍需人工建立 50-100 条真实匿名查询集替换它。

## 使用边界

- 该评测器只衡量当前检索结果是否召回标注答案；
- 它不证明片段语义蕴含主张；
- 它不检测冲突证据；
- 它不替代人工构建的 50-100 条真实匿名查询集。

后续阶段 2B 的 BM25 参数、embedding 管线和重排器都必须复用这套评测输出，避免凭主观观感判断检索质量。
