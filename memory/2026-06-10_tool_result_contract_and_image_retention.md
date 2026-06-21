# 2026-06-10 工具结果契约与图片保留修复

## 背景

按照 `2026-06-10_tool_command_processor_audit_plan.md` 开始收敛工具层。

本次只处理两个低风险基础问题：

- Phase 1：统一工具返回契约。
- Phase 7：Agent 多工具调用时保留最近一次有效图片结果。

## 改动

- 在 `bot_app/tools/base.py` 新增统一结果构造器：
  - `ok_result()`
  - `fail_result()`
  - `image_result()`
  - `normalize_result()`
- 直接工具改用 result builder，减少手写字段漂移：
  - `bot_app/tools/general.py`
  - `bot_app/tools/social.py`
  - `bot_app/tools/manual.py`
- `command_tool()` 和 `ToolRegistry.execute()` 对 handler 返回统一执行 `normalize_result()`。
  - 迁移期内，即使旧 `CommandProcessor` handler 漏掉 `response_type` 或 `image_path`，registry 对外也会补齐字段。
- `ToolRegistry` 未知工具错误改用 `fail_result()`。
- `AgentRunner` 新增 `last_image_result`，最终回复时优先使用最近一次有效图片工具的 `response_type=image` 和 `image_path`。
  - 最终文本仍来自 LLM 最终回复，或最后一个工具结果 fallback。

## 测试

新增/更新 `tests/test_agent_tool_calling.py`：

- `test_tool_results_follow_contract`
  - 覆盖 direct tools、CommandProcessor 转发工具和未知工具。
  - 验证结果包含 `module/ok/command/data/message/extra_prompt/response_type/image_path`。
- `test_image_tool_result_survives_later_text_tool`
  - 模拟先调用图片工具，再调用文本工具，最终仍发送图片。

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests --fix
```

## 后续

- Phase 2：重建 `ToolRuntime`，切断工具层对 `CommandProcessor` 私有字段的依赖。
- Phase 4-6：迁移 activity、mini_game、knowledge 工具，替换同步 subprocess 和 cwd/sys.path 副作用。
