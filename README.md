# Iris Memory

`iris_memory` 是 Iris 的独立长期记忆服务仓库。它拥有 Publication 接收、Evidence / Assessment ledger、ordered ingestion、stable memoryRef、RecallDisposition、Graphiti / Neo4j 适配、Recall / Expand API 和 reindex 生命周期。

本仓库不读取 `iris_agent` 的 Pi Session、Context、Historian、Persona、Tool 或 Body 数据；双方只通过版本化 memory contract 通信。

## 当前状态

本轮交付 contract v1 第一版与不连接 Graphiti 的 Publication acceptance vertical slice：

- 13 个 versioned JSON Schema 与 valid/invalid fixtures；
- OpenAPI 3.1 候选描述（`/health`、`/historian/publications`、`/memory/recall`、`/memory/expand`）；权威契约以 `manifest.json` 登记的 JSON Schema 为准，OpenAPI 不作为第二权威源；
- Router bootstrap ledger migration（accepted publications、idempotency、receipts、evidence envelopes、ordered ingestion jobs）；
- 确定性、原子、幂等的 Publication acceptance 服务与 stdlib HTTP surface；
- health 明确返回 `bootstrap` / `degraded`，Graphiti 状态保持 `not_configured`。

仍未实现：真实 Graphiti/Neo4j ingestion、stable memoryRef、Recall/Expand 业务实现、reindex、备份恢复和 production lock。它们不会伪装为已完成。

## 本地开发

需要 Python 3.12 与 uv 0.11.32 兼容版本。

```bash
uv sync --locked
uvx ruff==0.15.22 format --check .
uvx ruff==0.15.22 check .
uvx mypy==2.3.0
uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest
```

初始化空数据根：

```bash
uv run iris-memory migrate --data-root ./var/iris-memory
uv run iris-memory check --data-root ./var/iris-memory
```

开发前必须先阅读 [`AGENTS.md`](./AGENTS.md) 和 Notion 当前规格。
