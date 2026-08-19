# Vision Phase 6: Session Media Memory

## 变更类型

feat

## 内容

- 短期用户会话记忆保存渠道无关的安全媒体引用，不保存源 URL、Base64 或图片正文。
- 会话上下文保留图片数量和可选视觉摘要，缓存过期时可作为文本降级信息。
- 多模态装配按当前消息、最近群聊、用户会话的优先级选择图片，并通过哈希或缓存路径跨来源去重。
- 用户会话图片与历史群聊图片共享既有历史图片预算和单次总预算。

## 四位一体

- Code: `bot_app/context_contracts.py`, `bot_app/group_context.py`, `bot_app/model/multimodal.py`
- Test: `tests/test_vision_session_memory.py` (`VS-01` 至 `VS-04`)
- Docs: `README.md` 识图上下文说明
- Skill: 不涉及 NcatBot 公共 Skill API，现有 `framework-dev` 流程无需修改

## 边界

本阶段不生成新的视觉摘要，不持久化图片正文，也不让 200 条群聊总结附带原图。视觉摘要生成策略属于后续阶段。

## 验证

- `PYTHONPATH=. python3 tests/test_vision_session_memory.py -v`
- `PYTHONPATH=. python3 tests/test_vision_multimodal_context.py -v`
- `PYTHONPATH=. python3 tests/test_agent_tool_calling.py -v`
- `ruff check` 与 `ruff format --check`
