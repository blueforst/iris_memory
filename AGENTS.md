# AGENTS.md

本文件是 `blueforst/iris_memory` 仓库内所有开发 Agent 的工作契约。

## 1. 权威来源

Iris Notion 知识库是架构、规格和 Roadmap 的唯一权威来源。开始实现前必须读取当前规格正文，不得仅依据旧聊天记录、标题、缓存摘要或历史演化页面推测设计。

核心入口：

- 设计根页：https://app.notion.com/p/3a4b98338da58121b863edb88e824edd
- 模块边界：https://app.notion.com/p/3a5b98338da581018d36c47276cb4358
- Evidence 与语义记忆：https://app.notion.com/p/3a4b98338da581589ba5ee8d23bd319b
- 双项目边界：https://app.notion.com/p/3aeb98338da581538acedc7ca9da57b9
- Roadmap：https://app.notion.com/p/3a9b98338da5819a8380f10dfb60932b
- Graphiti 采纳边界：https://app.notion.com/p/3a5b98338da581e18654db2863d3bbee

00–06 是有效规格；07 只记录实施状态和证据。若 Notion 不可访问且任务需要架构或契约裁决，只暂停受影响部分，不得猜测。

## 2. 本仓库职责

本仓库拥有：

- Publication acceptance API、幂等 receipt 与版本验证；
- accepted Publication / Evidence / Assessment ledger；
- ordered ingestion、sourceSequence 与 Graphiti jobs；
- Memory Router、stable memoryRef 与 RecallDisposition；
- Recall / Search / Expand / provenance API；
- Graphiti / Neo4j Adapter、active/building group 与 reindex；
- 独立配置、迁移、备份、恢复、健康检查、指标与容量治理；
- 唯一权威的跨项目 memory contract package、JSON Schema 和 fixtures。

## 3. 明确禁止

不得：

- 读取或打开 `iris_agent` 的 Pi Session、runtime-epochs、Context、Historian、Persona、Tool 或 Body 数据；
- 参与 Agent provider loop、tool loop 或 Runtime Session 生命周期；
- 保存第二份 Iris 普通会话历史；
- 决定 P0–P5 或当前 invocation 的 Context；
- 复制 Graphiti 的实体/事实抽取、dedupe、resolution、时序失效或搜索引擎；
- 将 Runtime Session 映射为 memory scope、role 或权限 namespace；
- 把 Graphiti SDK object、Cypher record、embedding 或 raw UUID 暴露为公共契约。

## 4. 实现前检查

修改代码前：

1. 读取本文件与相关 Notion 当前规格；
2. 检查仓库、现有测试和当前 R0 状态；
3. 标明对应 Roadmap 项与 Exit Gate；
4. 涉及 Graphiti 时核对锁定源码、测试、issue、PR 与 release；
5. 确认没有引入新的重复状态 owner、数据库直连或第二份 wire DTO；
6. 将可逆工程选择与规格性决策分开。

## 5. 工程规则

- 每种持久状态只能有一个 owner；
- 跨项目通信必须使用本仓库发布的版本化 contract；
- migration 必须可从空数据根初始化，并有 smoke test；
- 公共协议变化必须有兼容测试与 fixture；
- Graphiti 只能通过窄 Adapter 使用；
- mock、stub、placeholder 必须明确标记；
- 不得把目录、占位代码或未验证实现描述为完成能力；
- 不得削弱 Exit Gate 来制造进度；
- 不得提交凭证、模型 payload、用户内容或私有运行数据。

## 6. 验证

提交 PR 前至少执行并记录：

```bash
uv sync --locked
uvx ruff==0.15.22 format --check .
uvx ruff==0.15.22 check .
uvx mypy==2.3.0
uv run --with pytest==9.1.1 --with jsonschema==4.26.0 pytest
```

涉及 Graphiti 锁时还要执行：

```bash
uv run --isolated --with graphiti-core==0.29.2 python -c "from importlib.metadata import version; assert version('graphiti-core') == '0.29.2'"
```

涉及持久化、公开 contract、恢复或 reindex 时，追加对应 migration、compatibility、crash-window、benchmark 或 replay 测试。未实际执行的命令不得声明通过。

## 7. Git 与 PR

每个边界清晰的任务使用独立分支并创建 Draft PR。PR 描述必须包含：

- Roadmap milestone 与 Exit Gate；
- 查阅的 Notion 页面和章节；
- 修改内容与状态 owner 影响；
- migration / contract 影响；
- 实际执行的命令与结果；
- mocks、未测试路径、剩余工作与规格影响。

开发 Agent 可以在 PR 中报告 claimed result；Notion 接受进度只能在 diff、CI 和 Exit Gate 经复核后更新。

## 8. 暂停条件

仅在以下情况暂停受影响工作：

- 必需规格、仓库、环境或凭证不可用；
- 会引入无法裁决的跨项目 ownership 冲突；
- 需要付费、破坏性、不可逆或外部发布操作；
- 当前规格存在无法按既定优先级裁决的根冲突。

其余非阻塞工作继续推进。
