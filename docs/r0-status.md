# R0 / R1 Memory Status（Roadmap v13）

本页只记录仓库内可验证的工程状态，不替代 Notion Roadmap。Roadmap v13（2026-08-04）
将进度重置为 0%，R0 阶段只要求三仓库 clean build、production lock 无 TBD 与
contracts 单一权威；本仓库不标记任何 milestone complete，完成率以 Notion Roadmap 为准。

## R0 交付状态（v13）

- **契约 artifact**：`iris-memory-contracts@0.1.1`（14 schemas / 30 fixtures），
  manifest.json 为唯一权威；agent 侧由 `test/memory-contract-gate.test.ts`
  实际重算 manifestSha256（`2cb22deb…`）逐项验证。
- **production lock**：`docs/production-locks.toml`——toolchain（python 3.12、
  uv 0.11.32）、graphiti-core `0.29.2`（candidate）、neo4j driver min `5.26.0`；
  无 TBD/TODO/unknown 占位符；GraphitiProfile 完整锁待 R4。
- **干净环境验证**（mcp-remote，2026-08-05）：
  - `uv sync --locked` → Resolved 7 packages
  - `uvx ruff==0.15.22 format --check .` → 28 files already formatted
  - `uvx ruff==0.15.22 check .` → All checks passed
  - `uvx mypy==2.3.0` → Success: no issues found in 17 source files
  - `uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest` → **69 passed**
- **契约能力**（v0.1.1）：capability handshake、HistorianPublication、Publication
  acceptance request、acceptance/duplicate replay receipts、idempotency conflict、
  unsupported version、health、RecallRequest、MemoryRecallCard、Expansion request/response；
  每个 schema 均有 valid/invalid fixtures；OpenAPI 3.1 为 descriptive 文档，不构成
  第二权威源。
- **服务纵切**：`0002_router_ledger` forward-only migration；Publication acceptance
  vertical slice（schema validation → major/minor version check → canonical payload
  hash → idempotency → 单事务原子持久化 → 确定性 receipt）；stdlib HTTP surface
  （`GET /health`、`POST /historian/publications`，recall/expand 明确 `501
  not_implemented`）；CLI `migrate`/`check`/`accept`/`serve`；health 返回 `bootstrap`
  或 `degraded`，Graphiti 始终 `not_configured`，不伪装 ready。

## 仍未满足的 R4 前置门（不阻塞 R0）

- 真实 Graphiti/Neo4j ingestion、ordered ingestion worker 与 active/building group；
- stable memoryRef、RecallDisposition、recall/search/expand 业务实现；
- Evidence envelope 实际写入与重建、备份/恢复、reindex；
- GraphitiProfile 完整锁（Neo4j runtime、LLM、embedding、reranker、ontology 与
  prompt hashes）；
- 跨进程并发、长时间 crash-window、容量与恢复演练；
- 正式 package 发布（当前 `publishStatus: artifact_ready_pending_release`）。

## R0 Exit Gate（本仓库负责部分）

| Gate | 状态 | 证据 |
| --- | --- | --- |
| production lock 无 TBD | PASS | `docs/production-locks.toml`（grep 无 TBD/TODO/unknown；candidate 值全字段） |
| 干净环境独立构建 | PASS | 上述 uv/ruff/mypy/pytest 全部通过 |
| contracts/schema 单一权威来源 | PASS | `src/iris_memory/contracts/assets/manifest.json`（0.1.1）为唯一权威；agent 端 gate 验证 manifestSha256 |
