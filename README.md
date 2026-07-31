# Iris Memory

`iris_memory` 是 Iris 的独立长期记忆服务仓库。它拥有 Publication 接收、Evidence / Assessment ledger、ordered ingestion、stable memoryRef、RecallDisposition、Graphiti / Neo4j 适配、Recall / Expand API 和 reindex 生命周期。

本仓库不读取 `iris_agent` 的 Pi Session、Context、Historian、Persona、Tool 或 Body 数据；双方只通过版本化 memory contract 通信。

## 当前状态

当前是 **R0 工程基线**，用于让开发 Agent 从可重复验证的仓库状态开始工作。它已经提供：

- Python / uv 工具链与锁文件；
- 包结构、CLI 和项目边界声明；
- 版本化 contract assets 与首个 capability handshake schema；
- SQLite migration runner 与空数据根 smoke test；
- Ruff、mypy、pytest 和 GitHub Actions；
- R0 尚未完成事项的显式清单。

这不代表 Publication、Recall、Graphiti 或 reindex 能力已经实现，也不增加 Notion Roadmap 的已验收进度。

## 本地开发

需要 Python 3.12 与 uv 0.11.32 兼容版本。

```bash
uv sync --locked
uvx ruff==0.15.22 format --check .
uvx ruff==0.15.22 check .
uvx mypy==2.3.0
uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest
```

验证 Graphiti 锁可解析：

```bash
uv run --isolated --with graphiti-core==0.29.2 python -c "from importlib.metadata import version; assert version('graphiti-core') == '0.29.2'"
```

初始化空数据根：

```bash
uv run iris-memory migrate --data-root ./var/iris-memory
uv run iris-memory check --data-root ./var/iris-memory
```

开发前必须先阅读 [`AGENTS.md`](./AGENTS.md) 和 Notion 当前规格。
