# Vision Phase 7: Lazy Visual Summaries

## 变更类型

feat

## 内容

- 新增渠道无关 `VisualSummaryService`，为已参与机器人对话、缓存有效且尚无摘要的当前图片按需生成短事实摘要。
- 摘要在回复成功交付后、会话提交前生成，并同步回填事件安全媒体引用和 SQLite `message_media.visual_summary`。
- 图片或 OCR 内的指令明确视为不可信用户内容；摘要请求使用 `detail=low`，受独立图片数和字符数预算约束。
- 摘要模型或数据库回填失败均按 best-effort 降级，不阻断已发送回复和正常会话提交。

## 四位一体

- Code: `bot_app/visual_summary.py`, `bot_app/application.py`, `bot_app/bootstrap.py`, `bot_app/db_manager.py`, `bot_app/settings.py`
- Test: `tests/test_vision_visual_summary.py` (`VS-05` 至 `VS-08`)
- Docs: `README.md` 识图上下文与视觉摘要配置说明
- Skill: 不涉及 NcatBot 公共 Skill API，现有 `framework-dev` 流程无需修改

## 边界

本阶段不为未进入机器人对话的全部群图片预计算摘要，不把 Base64 或图片正文写入 SQLite/会话记忆，也不让 200 条群聊总结附带原图。更广泛的灰度指标和生产告警属于后续阶段。

## 验证

- `PYTHONPATH=. python3 tests/test_vision_visual_summary.py -v`
- `PYTHONPATH=. python3 tests/test_vision_session_memory.py -v`
- `PYTHONPATH=. python3 tests/test_vision_multimodal_context.py -v`
- `PYTHONPATH=. python3 tests/test_vision_event_contract.py -v`
- `PYTHONPATH=. python3 tests/test_vision_media_store.py -v`
- `PYTHONPATH=. python3 tests/test_agent_tool_calling.py -v`
- `ruff check` 与 `ruff format --check`（本阶段新增文件）
