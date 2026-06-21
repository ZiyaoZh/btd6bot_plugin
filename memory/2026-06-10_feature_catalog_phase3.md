# 2026-06-10 功能列表单一来源

## 背景

继续执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 3。

本阶段先处理风险最低的重复实现：`list_features` 的功能元数据。

## 改动

- 新增 `bot_app/tools/features.py`。
  - `get_feature_catalog()` 返回功能目录副本。
  - 功能列表数据只维护一份。
- `bot_app/tools/general.py` 的 direct `list_features` 改为读取 `get_feature_catalog()`。
- `bot_app/command_processor.py` 的旧 `_handle_list_features()` 也改为读取同一份 catalog。

## 测试

新增 `test_legacy_command_processor_list_features_uses_shared_catalog`：

- 通过旧 `CommandProcessor.execute(list_features)` 调用功能列表。
- 验证返回的 `features` 与 `get_feature_catalog()` 一致。

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests plugin.py
```

`ruff format --check bot_app/command_processor.py` 会要求重排整个历史文件。为避免把 Phase 3 的小逻辑改动扩大成大规模格式化 diff，本次未格式化 `command_processor.py`。

## 后续

- `today_most_active`、`summarize_recent_messages`、`script_manual_query` 仍存在 direct tools 和旧 `CommandProcessor` 双实现。
- 后续更适合在 service 层抽出共享实现，而不是让旧同步 handler 直接调用 async tool handler。
