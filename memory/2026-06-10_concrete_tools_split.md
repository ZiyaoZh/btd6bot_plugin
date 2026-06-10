# 2026-06-10 Concrete Tools Split

## 变更类型
refactor

## 内容
- 将 `ToolRegistry` 内联维护的工具 schema 和 handler 拆到 `bot_app/tools/`。
- 新增按功能域组织的具体工具模块：
  - `general.py`: `ignore_message`, `list_features`
  - `social.py`: `today_most_active`, `summarize_recent_messages`
  - `manual.py`: `script_manual_query`
  - `knowledge.py`: `search_game_knowledge`, `read_game_knowledge_file`
  - `activity.py`: `get_activity_detail`, `get_activity_leaderboard`, `get_collection_event_info`
  - `mini_game.py`: `simulate_monkey_box_opening`
- `ToolRegistry` 收缩为工具装载、去重、OpenAI tools 转换和执行分发层。
- 增加工具注册测试，覆盖工具集合完整性、schema 可序列化和 handler 路由。

## 四位一体
- Code: `bot_app/tools/`, `bot_app/agent/tool_registry.py`
- Test: `tests/test_agent_tool_calling.py`
- Docs: `README.md`
- Skill: 未修改 `.agents/skills/`；本次为插件内部 tool 层拆分。

## 备注
- 本阶段先拆 tool 定义层，具体业务执行仍复用 `CommandProcessor`，下一阶段可继续按领域迁移业务实现。
