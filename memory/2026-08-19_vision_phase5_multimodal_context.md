# Vision Phase 5: Multimodal Model Context

## 变更类型

feat

## 内容

- 将前台缓存结果回填到当前事件的渠道无关图片引用。
- 新增模型层多模态装配器，把当前消息和最近群聊中的有效缓存转换为带明确标签的 Chat Completions `image_url` 内容段。
- 增加当前、历史和单次总图片预算；视觉开关关闭或缓存无效时保持原有纯文本请求。
- 保留现有 assistant/tool 多轮循环，并统一脱敏 LLM 请求日志中的 Base64 图片正文。

## 四位一体

- Code: `bot_app/model/multimodal.py`, `bot_app/harness/runtime.py`, `bot_app/application.py`, `bot_app/receiver.py`, `bot_app/llm_client.py`, `bot_app/settings.py`
- Test: `tests/test_vision_multimodal_context.py` (`VX-01` 至 `VX-04`), `tests/test_agent_tool_calling.py` (`VX-05`)
- Docs: `README.md` 识图兼容性探针章节
- Skill: 不涉及 NcatBot 公共 Skill API，现有 `framework-dev` 流程无需修改

## 边界

本阶段不把 Base64 或图片正文写入 SQLite/短期记忆，不生成视觉摘要，也不迁移 Responses API。缓存过期后的图片追问降级和会话媒体记忆属于下一阶段。

## 验证

- `PYTHONPATH=. python3 tests/test_vision_multimodal_context.py -v`
- `PYTHONPATH=. python3 tests/test_vision_event_contract.py -v`
- `PYTHONPATH=. python3 tests/test_vision_media_store.py -v`
- `PYTHONPATH=. python3 tests/test_agent_tool_calling.py -v`
- `ruff check` 与 `ruff format --check`（本次新增文件）
