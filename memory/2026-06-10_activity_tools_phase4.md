# 2026-06-10 activity 工具迁移

## 背景

执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 4。

目标是把 activity 相关工具从 `CommandProcessor` 转发迁移到 tools/service 层，并消除该链路中的同步 `subprocess.run()` 阻塞。

## 改动

- 新增 `bot_app/tools/activity_service.py`：
  - `build_activity_detail_result()`
  - `build_activity_leaderboard_result()`
  - `build_collection_event_info_result()`
  - `run_btd6_api_cli()`
  - `extract_generated_files()`
  - `AsyncioSubprocessRunner`
- `bot_app/tools/activity.py` 不再使用 `command_tool()` 转发。
  - 三个 activity 工具现在直接调用 activity service。
  - subprocess runner 从 `ToolRuntime.subprocess_runner` 注入，测试可 fake。
- 旧 `CommandProcessor` activity 入口改为 await 同一组 activity service。
  - `_handle_get_activity_detail()`
  - `_handle_get_activity_leaderboard()`
  - `_handle_get_collection_event_info()`
- 删除 `CommandProcessor._run_btd6_api_cli()` 与 `_extract_generated_files()`。
- 删除 `CommandProcessor` 中不再使用的 `_api_processor_cli` / `_api_processor_output_dir` 字段。

## 行为变化

- BTD6 activity CLI 改用 `asyncio.create_subprocess_exec()`。
- 输出 Markdown 文件名加入 `uuid4().hex[:8]`，避免同秒并发覆盖。
- 图片工具结果会检查实际图片文件存在。
- 排行榜与收集活动图片工具的 `extra_prompt` 改为 caption 指令：
  - 外层系统会负责发送图片。
  - 不要说图片无法发送。

## 测试

测试中新增 fake activity runner：

- 不调用真实 `btd6_cli.py`。
- 在临时 plugin root 下写 fake report / png / json。
- 覆盖 direct activity 工具契约与 Agent 图片保留链路。

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests plugin.py
ruff format --check bot_app/tools/activity.py bot_app/tools/activity_service.py tests/test_agent_tool_calling.py
```

## 后续

- Phase 5：迁移 mini_game 工具。
- mini_game 迁移时应复用 `ToolRuntime.subprocess_runner` 或抽出更通用的 subprocess runner 类型，继续消除同步 `subprocess.run()`。
