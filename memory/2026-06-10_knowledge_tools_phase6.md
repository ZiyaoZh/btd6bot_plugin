# 2026-06-10 knowledge 工具迁移

## 背景

执行 `2026-06-10_tool_command_processor_audit_plan.md` 的 Phase 6。

目标是将 `search_game_knowledge` 和 `read_game_knowledge_file` 从 `CommandProcessor` 转发迁移到 tools/service 层，并集中处理知识库导入、路径安全和长文提取。

## 改动

- 新增 `bot_app/tools/knowledge_service.py`：
  - `build_search_game_knowledge_result()`
  - `build_read_game_knowledge_file_result()`
  - `resolve_safe_kb_path()`
  - `extract_readable_content()`
  - `sanitize_kb_query()`
- `bot_app/tools/knowledge.py` 不再使用 `command_tool()` 转发。
- 旧 `CommandProcessor` knowledge 入口改为调用同一 service。
- 删除 `CommandProcessor` 中 knowledge 相关私有 helper：
  - `_build_extract_keywords_query()`
  - `_split_keywords_for_view()`
  - `_resolve_safe_kb_path()`
  - `_extract_readable_content()`
  - `_sanitize_kb_query()`
- 删除 `CommandProcessor` 中 knowledge 派生路径字段：
  - `_kb_db_path`
  - `_kb_corpus_dir`
  - `_kb_legacy_corpus_dir`
  - `_kb_alias_file`
- 删除 `command_tool()` 旧转发 helper。
- `ToolRuntime` 不再持有 `CommandProcessor`。
- 插件入口和独立 runner 不再为 Agent 主链路构造 `CommandProcessor`。

## 路径与导入

- `read_game_knowledge_file` 的相对路径解析改为基于 `kb_root` / `kb_root.parent`，不再依赖 `Path.cwd()`。
- 允许读取范围集中为：
  - `btd6_knowledge_base/wiki_data_second_cleaned/btd6`
  - `btd6_knowledge_base/wiki_data_cleaned/btd6`
- `kb_interface.py` 通过 `importlib` 按文件加载。
- 为兼容 `kb_interface.py` 内部同目录 import，service 会临时插入 `kb_root` 到 `sys.path`，导入结束后恢复，避免长期污染全局路径。

## 测试

新增 fake knowledge base：

- 临时生成 `btd6_knowledge_base/kb_interface.py`
- 临时生成 corpus 文件
- 覆盖：
  - search 返回候选路径
  - read 读取普通文档
  - 长文按 keywords 提取
  - 越界路径拒绝
  - `sys.path` 恢复

已运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests plugin.py
ruff format --check bot_app/tools/knowledge.py bot_app/tools/knowledge_service.py bot_app/tools/base.py bot_app/runner.py plugin.py tests/test_agent_tool_calling.py
```

## 后续

- Phase 7/8 已在前序阶段基本完成：
  - Agent 图片保留逻辑已修复。
  - 图片工具 extra_prompt 已改为 caption 指令。
- Phase 9：继续瘦身 `CommandProcessor`，将旧 `llm_processor.py` / `tool_selector.py` / `command_skills` 的历史链路定位清楚，再决定保留兼容适配器还是移除主链路依赖。
