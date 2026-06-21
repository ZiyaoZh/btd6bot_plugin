# 2026-06-10 CommandProcessor Phase 9

## 背景

执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 9。

目标是让 `CommandProcessor` 不再持有领域业务，插件主链路只依赖 `AgentRunner + ToolRegistry + tools/service`。

## 改动

- `CommandProcessor` 已退化为旧 `IntentPayload` 协议兼容路由器：
  - 不再作为 Agent 主链路资源容器。
  - 不再被 `plugin.py` 或 `bot_app/runner.py` 构造。
  - 领域逻辑委托到 `bot_app/tools/*_service.py`。
- `llm_processor.py` 与 `tool_selector.py` 标记为历史两阶段链路：
  - 保留文件以避免外部 import 直接断裂。
  - 不作为当前插件运行时入口。
- README 同步工具层描述：
  - 外部 CLI、知识库、小游戏均已迁到 tools/service 层。
  - `CommandProcessor` 只保留旧意图协议兼容入口。

## 保留策略

本阶段不删除 `llm_processor.py`、`tool_selector.py` 和 `command_skills/`。

理由：

- 它们已无主入口引用，但可能仍被临时脚本、历史测试或人工排障资料引用。
- 直接删除会把 Phase 9 从职责收敛扩大成兼容性破坏。
- 后续如要删除，应单独做一次引用审计和迁移说明。

## 验收点

- 新工具不需要修改 `CommandProcessor`，只需要注册到 `bot_app/tools/` 与 `ToolRegistry`。
- `rg -n "command_tool\\(" bot_app/tools` 应无结果。
- `plugin.py` 与 `bot_app/runner.py` 只构造 `ToolRuntime` / `ToolRegistry`，不构造 `CommandProcessor`。

## 四位一体

- Code: `bot_app/command_processor.py`, `bot_app/llm_processor.py`, `bot_app/tool_selector.py`
- Test: 继续使用 `tests/test_agent_tool_calling.py` 覆盖新工具链路和旧兼容入口。
- Docs: `README.md`
- Skill: 未修改 `.agents/skills/`；这是插件内部架构收敛，不改变 NcatBot 通用开发技能。
