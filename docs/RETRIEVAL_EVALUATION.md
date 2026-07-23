# 检索评测基座

本文件记录阶段 2B 的第一块可验收能力：在接入 BM25 调优、向量检索、重排或语义核验前，先建立稳定的检索评测运行器。

## 当前能力

- `evaluate_retrieval_cases(cases, k=10)` 可对一组查询运行资料库检索；
- 支持按 `relevant_titles` 做文档级评测；
- 支持按 `relevant_chunk_ids` 做分段级评测；
- 输出 `Recall@K`、`MRR`、miss 列表和每个 case 的 Top K 结果；
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

## 使用边界

- 该评测器只衡量当前检索结果是否召回标注答案；
- 它不证明片段语义蕴含主张；
- 它不检测冲突证据；
- 它不替代人工构建的 50-100 条真实匿名查询集。

后续阶段 2B 的 BM25 参数、embedding 管线和重排器都必须复用这套评测输出，避免凭主观观感判断检索质量。
