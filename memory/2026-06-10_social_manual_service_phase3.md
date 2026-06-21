# 2026-06-10 social/manual 共享 service

## 背景

继续执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 3。

上一阶段已经把 `list_features` 收敛到 `get_feature_catalog()`。本阶段继续消除 direct tools 与旧 `CommandProcessor` 之间的重复业务实现。

## 改动

- 新增 `bot_app/tools/social_service.py`：
  - `build_today_most_active_result()`
  - `build_summarize_recent_messages_result()`
- 新增 `bot_app/tools/manual_service.py`：
  - `build_script_manual_query_result()`
- `bot_app/tools/social.py` 改为 thin wrapper。
- `bot_app/tools/manual.py` 改为 thin wrapper，并继续维护 runtime 内的 `manual_text_cache`。
- `bot_app/command_processor.py` 的旧入口改为调用同一组 service：
  - `_handle_today_most_active()`
  - `_handle_summarize_recent_messages()`
  - `_handle_script_manual_query()`
- 删除 `CommandProcessor._load_manual_text()`，说明书加载只保留 service 一份实现。

## 兼容边界

- direct tools 仍返回 `module=tool.social` / `module=tool.manual`。
- 旧 `CommandProcessor` 入口仍返回 `module=command_processor`。
- 手册读取改为统一使用 `errors="ignore"`，避免坏编码文件打断工具调用。

## 测试

新增 `test_legacy_command_processor_uses_shared_social_and_manual_services`：

- 覆盖旧链路的活跃榜、最近消息、说明书查询。
- 验证旧链路仍返回 `command_processor` module 和预期数据。

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests plugin.py
ruff format --check bot_app/tools/social.py bot_app/tools/social_service.py bot_app/tools/manual.py bot_app/tools/manual_service.py tests/test_agent_tool_calling.py
```

## 后续

- Phase 4：迁移 activity 工具。
- activity 迁移时应同步处理 subprocess async runner、输出文件唯一命名、图片 extra_prompt。
