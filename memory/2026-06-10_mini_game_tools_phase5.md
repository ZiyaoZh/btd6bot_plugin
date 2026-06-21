# 2026-06-10 mini_game 工具迁移

## 背景

执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 5。

目标是将 `simulate_monkey_box_opening` 从 `CommandProcessor` 转发迁移到 tools/service 层，并消除 ChatMonkeyTD CLI 的同步 `subprocess.run()`。

## 改动

- 新增 `bot_app/tools/subprocess_runner.py`：
  - `ProcessResult`
  - `AsyncSubprocessRunner`
  - `AsyncioSubprocessRunner`
- `bot_app/tools/activity_service.py` 改为复用公共 async subprocess runner。
- 新增 `bot_app/tools/mini_game_service.py`：
  - `build_monkey_box_opening_result()`
  - `run_chat_monkey_simulator()`
- `bot_app/tools/mini_game.py` 不再使用 `command_tool()` 转发。
- 旧 `CommandProcessor` 的 `simulate_monkey_box_opening` 入口改为 await 同一 service。
- 删除旧同步 `_run_chat_monkey_simulator()`。
- 清理 `CommandProcessor` 中不再使用的 `subprocess` / `datetime` import 和 ChatMonkey 输出字段。

## 行为变化

- ChatMonkeyTD CLI 改用 `asyncio.create_subprocess_exec()`。
- report/png 输出文件名加入 `uuid4().hex[:8]`，避免同秒并发覆盖。
- `box_count <= 50` 时返回图片。
- `box_count > 50` 时只返回文本统计。
- 新增机器人侧上限 `MAX_BOT_BOX_COUNT = 10000`，避免超大模拟任务拖垮机器人。
- 图片结果的 `extra_prompt` 改为 caption 指令：
  - 外层系统会负责发送图片。
  - 不要说图片无法发送。

## 测试

测试 fake runner 已扩展支持 ChatMonkeyTD 命令：

- 小数量开箱生成 fake png。
- 大于 50 的开箱只生成 report。
- 超过机器人侧上限时拒绝，不调用 runner。

新增 `test_mini_game_tool_uses_runtime_runner`。

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests plugin.py
ruff format --check bot_app/tools/mini_game.py bot_app/tools/mini_game_service.py bot_app/tools/subprocess_runner.py bot_app/tools/activity_service.py tests/test_agent_tool_calling.py
```

## 后续

- Phase 6：迁移 knowledge 工具。
- `command_tool()` 目前只剩 knowledge 工具在使用。
