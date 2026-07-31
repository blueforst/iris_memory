# R0 工程基线状态

本页只记录仓库内可验证的工程状态，不替代 Notion Roadmap。

## 本基线已提供

- Python 3.12–3.13 兼容范围与 uv 锁文件；
- `iris_memory` 可安装包、CLI 与仓库边界声明；
- contract assets 的版本化目录、manifest、JSON Schema 与 fixture；
- SQLite migration runner、初始 migration 与空数据根 smoke test；
- format、lint、typecheck、unit、contract、migration 检查；
- Graphiti `0.29.2` 可选依赖锁解析检查；
- Draft PR 模板与开发 Agent 契约。

## 仍未满足的 R0 / R4 前置门

- 完整 Publication / Evidence / Assessment / Recall / Expand wire schemas 尚未实现；
- OpenAPI 尚未从接受的 wire contract 生成；
- GraphitiProfile 仍需锁定 Neo4j runtime、LLM、embedding、reranker、ontology 与 prompt hashes；
- Router 数据模型、真实 migration、acceptance API、ordered ingestion 与 stable memoryRef 未实现；
- 还没有真实 Graphiti/Neo4j integration、backup/restore、reindex 或 crash tests；
- 该基线不应被标记为 R0 complete，也不增加已验收实现百分比。

## 推荐下一步

1. 在本仓库先完成跨项目 memory contract v1 的完整 schema 与兼容 fixture；
2. 再建立 Router ledger 的最小 SQLite schema 和 migration；
3. 实现不连接 Graphiti 的 Publication acceptance vertical slice；
4. 锁定完整 GraphitiProfile 后接入 ordered ingestion adapter。
