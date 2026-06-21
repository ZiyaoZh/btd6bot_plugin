# 2026-06-10 ToolRuntime 重建

## 背景

继续执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 2。

上一阶段已经统一工具结果契约，本阶段目标是切断工具层对 `CommandProcessor` 私有字段的依赖。

## 改动

- `ToolRuntime` 改为显式运行时资源容器：
  - `command_processor`
  - `db`
  - `plugin_root`
  - `manual_file`
  - `command_guide_dir`
  - `kb_root`
  - `api_processor_root`
  - `chat_monkey_root`
  - `subprocess_runner`
- 移除 `ToolRuntime.from_command_processor()`。
- `ToolRegistry` 构造参数从 `CommandProcessor` 改为 `ToolRuntime`。
  - Registry 不再导入 `CommandProcessor`。
  - Registry 不再通过 `getattr()` 读取 `_db` / `_manual_file`。
- 插件入口和独立 runner 都显式构造同一类 runtime：
  - `plugin.py`
  - `bot_app/runner.py`
- 测试中的 fake `CommandProcessor` 移除了 `_db` / `_manual_file`。
  - direct tools 只能从 `ToolRuntime` 获取 db 和 manual file。

## 兼容边界

尚未迁移的 activity / knowledge / mini_game 工具仍通过 `command_tool()` 转发到 `CommandProcessor.execute()`。

也就是说，`CommandProcessor` 仍作为旧工具执行器存在，但不再作为 direct tools 的私有资源容器。

## 测试

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests plugin.py
ruff format --check bot_app/runner.py bot_app/agent/tool_registry.py bot_app/tools/base.py tests/test_agent_tool_calling.py plugin.py
```

## 后续

- Phase 3：收敛 general/social/manual 重复实现。
- Phase 4-6：迁移 activity、mini_game、knowledge 到 tools/service 层。
- 后续迁移 subprocess 时可使用 `ToolRuntime.subprocess_runner` 作为注入点。
