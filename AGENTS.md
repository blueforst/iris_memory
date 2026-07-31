# AGENTS.md

本文件是 `blueforst/iris_memory` 仓库内所有开发 Agent 的工作契约。
开始任务前必须读取本文件，并遵守 Iris Notion 规格、项目边界和验证要求。

## 1. 权威来源

Iris Notion 知识库是架构、规格和 Roadmap 的唯一权威来源。

开始实现前必须读取当前规格正文，不得仅根据旧聊天记录、标题、缓存摘要或历史演化页面推测设计。

核心参考：

- 设计总览：Iris Agent 与长期记忆双项目设计
- 模块边界与端口契约
- Evidence & Semantic Memory
- Project Boundaries｜Iris Agent 与长期记忆双项目边界
- Roadmap & Implementation Status

当前项目拆分：

```text
blueforst/iris_agent
= Agent 本体

blueforst/iris_memory
= 长期记忆服务
```

## 2. 本仓库职责

本仓库负责 Iris 长期记忆服务：

- Publication acceptance API；
- 幂等 receipt 与版本验证；
- accepted Publication / Evidence / Assessment ledger；
- ordered ingestion 与 sourceSequence；
- Memory Router；
- stable memoryRef；
- RecallDisposition；
- Recall / Search / Expand API；
- Graphiti / Neo4j Adapter；
- active/building group 生命周期；
- reindex、备份、恢复、健康检查和容量治理；
- 发布跨项目 memory contract package、JSON Schema 和兼容性 fixtures。

## 3. 明确禁止

不得：

- 读取 `iris-agent` 的 Pi Session；
- 打开 Agent 的 context.db、historian.db、persona 或 tool 数据；
- 参与 Agent provider loop、tool loop 或 Runtime Session 生命周期；
- 保存第二份 Iris 普通会话历史；
- 自行决定 P0-P5 Context 语义；
- 复制 Graphiti 的 entity/fact resolution；
- 用自研图数据库替代 Graphiti 语义能力；
- 将 Runtime Session 映射为 memory scope、role 或权限 namespace。

## 4. 长期记忆边界

唯一方向：

```text
iris-agent Historian
    ↓ publication_outbox
Memory Client
    ↓ versioned contract
iris-memory
    ↓
Memory Router
    ↓
Graphiti / Neo4j
```

`iris-memory` 拥有 memory service 内部真相。

Memory Router 负责：

- Publication 接收；
- Evidence ledger；
- ingestion 顺序；
- stable memoryRef；
- RecallDisposition；
- Graphiti 调度与重建。

Graphiti 负责：

- Episode；
- Entity；
- Fact；
- temporal resolution；
- semantic search。

Router 不实现第二套实体合并、事实解析或搜索引擎。

## 5. 实现原则

- 每种持久状态只能有一个 owner；
- 跨项目通信必须使用版本化 contract；
- 不直接访问其他模块数据库；
- 不复制第三方 SDK 对象作为公开接口；
- 优先适配 Graphiti 原生语义；
- 所有 migration 必须支持空数据根初始化；
- 所有公开协议变化必须有兼容测试；
- mock 必须明确标注；
- 不得把占位代码描述为完成能力。

## 6. 开发流程

修改代码前：

1. 阅读本文件；
2. 阅读对应 Notion 当前规格；
3. 检查仓库现状；
4. 确认 Roadmap 里程碑和 Exit Gate；
5. 检查是否引入新的状态 owner、数据库或协议。

涉及 Graphiti 时必须确认：

- Episode 生命周期；
- group ownership；
- entity/fact resolution；
- temporal edge；
- search contract。

不得因为 Iris 需求简单复制一个平行语义层。

## 7. 验证要求

提交 PR 前必须记录真实执行结果：

- format / lint；
- typecheck；
- unit tests；
- contract tests；
- migration smoke test；
- 相关 benchmark 或恢复测试。

未执行的命令不得声明通过。

## 8. Git 与 PR

每个边界清晰的任务使用独立分支。

Draft PR 描述必须包含：

- 对应 Roadmap 项；
- 查阅的 Notion 规格；
- 修改内容；
- 状态 owner 影响；
- migration / contract 影响；
- 实际验证命令和结果；
- 已知缺口和未测试路径。

正式进度不能因为文档、占位代码或未验证实现而提升。

## 9. 暂停条件

仅在以下情况暂停受影响工作：

- 必需规格无法访问；
- 缺少必要仓库或环境；
- 出现无法裁决的状态所有权冲突；
- 需要不可逆外部操作；
- 需要未授权付费资源。

其他不相关任务继续推进。
