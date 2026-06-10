# 2026-06-10 Direct Tool Handlers Phase 1

## 变更类型
refactor

## 内容
- 新增 `ToolRuntime`，从现有 `CommandProcessor` 提取工具运行依赖。
- 将低风险工具从 `CommandProcessor.execute()` 适配迁移为直接 handler：
  - `list_features`
  - `today_most_active`
  - `summarize_recent_messages`
  - `script_manual_query`
- `activity` / `knowledge` / `mini_game` 工具仍通过 `command_tool` 复用 `CommandProcessor`，后续可按外部依赖风险逐步迁移。
- 更新测试，覆盖 direct social/manual handler 不再调用 `CommandProcessor.execute()`。

## 四位一体
- Code: `bot_app/tools/base.py`, `bot_app/tools/general.py`, `bot_app/tools/social.py`, `bot_app/tools/manual.py`
- Test: `tests/test_agent_tool_calling.py`
- Docs: `README.md`
- Skill: 未修改 `.agents/skills/`；本次为插件内部 tool handler 迁移。

## 备注
- 本阶段保留 `CommandProcessor` 作为复杂工具后端，避免同时移动外部 CLI、知识库索引和文件安全逻辑。
