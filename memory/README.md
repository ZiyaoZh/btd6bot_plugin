# Agent 设计与变更记录

本目录保存各阶段的设计、实现与当时验证结果。历史记录中的模型、默认参数和运行架构仅对应记录日期；当前行为以插件 [README](../README.md) 和 [ARCHITECTURE](../ARCHITECTURE.md) 为准。

## 上下文与记忆

- [2026-10-12 上下文预算与持久记忆](2026-10-12_context_budget_and_persistent_memory.md)：当前输入预算、SQLite 记忆、回归与离线评估。
- [Agent 上下文与群聊记录治理 SOP](2026-06-24_agent_context_and_group_memory_sop.md)：长期追踪、最新现状与历史阶段记录。
- [2026-08-07 放宽知识库长文读取限制](2026-08-07_long_knowledge_documents.md)：完整读取能力的历史背景；默认模型观察策略已由 2026-10-12 记录更新。
- [游戏知识检索 SOP](2026-06-24_game_knowledge_retrieval_sop.md)：知识库检索历史规划。

离线规范与运行方式见 [测试索引](../tests/README.md)。
