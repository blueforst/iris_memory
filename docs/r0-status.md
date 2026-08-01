# R0 / R1 Memory Contract Status

本页只记录仓库内可验证的工程状态，不替代 Notion Roadmap。

## 本轮已提供

- `iris-memory-contracts@0.1.0` candidate：capability handshake、HistorianPublication、Publication acceptance request、acceptance/duplicate replay receipts、idempotency conflict、unsupported version、health、RecallRequest、MemoryRecallCard、Expansion request/response；
- 每个 schema 均有 valid/invalid fixtures，且全部在 manifest 中登记；
- OpenAPI 3.1 文档覆盖 health、publication、recall、expand 四个路径；该文档是 candidate/descriptive 描述，权威契约始终是 `manifest.json` 中登记的 JSON Schema，禁止把 OpenAPI 当作第二权威源；
- `0002_router_ledger` forward-only migration：accepted publications、publication idempotency、acceptance receipts、evidence envelopes、ordered ingestion jobs、service metadata；
- Publication acceptance vertical slice：schema validation -> major/minor version check -> canonical payload hash -> idempotency -> 单事务原子持久化 -> 确定性 receipt；
- 幂等消费边界：首次接受、同 key 重放、alternate key replay/conflict 都会把该 idempotency key 持久化消费，防止已被消费的 key 被后续不同 publication 复用；
- stdlib HTTP surface：`GET /health`、`POST /historian/publications`；recall/expand 明确返回 `501 not_implemented`；
- CLI：`migrate`、`check`、`accept`、`serve`；
- health 明确返回 `bootstrap` 或 `degraded`，Graphiti 始终为 `not_configured`，不会伪装成完整 ready。

## 仍未满足的 R4 / R0 前置门

- 真实 Graphiti/Neo4j ingestion、ordered ingestion worker 与 active/building group；
- stable memoryRef、RecallDisposition、recall/search/expand 业务实现；
- Evidence envelope 的实际写入与重建、备份/恢复、reindex；
- GraphitiProfile 完整锁（Neo4j runtime、LLM、embedding、reranker、ontology 与 prompt hashes）；
- 跨进程并发、长时间 crash-window、容量与恢复演练；
- production lock 与正式 package 发布。

该状态不标记 R0 或 R4 complete，也不增加 Notion 已验收实现百分比。
