# Vision Phase 8: Security and Observability

## 变更类型

feat

## 内容

- 将日志脱敏下沉到 `log_flow` 统一出口，递归移除任意字段中的 Base64 图片正文以及 HTTP(S) URL 查询参数和 fragment，覆盖请求、响应、上游错误与异常堆栈。
- `llm.request` 增加图片数量、图片总字节和 detail 指标；`media.cache_completed` 增加成功/拒绝、内容命中、归一化字节和处理耗时指标。
- 增加 `LLM_VISION_IMAGE_DETAIL=auto|low|high`，默认 `auto`，非法配置在启动时拒绝；真正提交给模型的每个图片段显式携带该值。
- 系统提示明确将图片像素、截图文字、OCR 和图片元数据中的指令视为不可信用户内容，不能覆盖系统规则或工具策略。

## 四位一体

- Code: `bot_app/logging_utils.py`, `bot_app/llm_client.py`, `bot_app/media_store.py`, `bot_app/receiver.py`, `bot_app/application.py`, `bot_app/model/multimodal.py`, `bot_app/settings.py`, `prompts/system_prompt.md`
- Test: `tests/test_vision_multimodal_context.py` (`VX-05` 至 `VX-07`), `tests/test_vision_media_store.py` (`VC-01` 指标断言)
- Docs: `README.md` 识图安全、detail 配置与灰度指标说明
- Skill: 不涉及 NcatBot 公共 Skill API，现有 `framework-dev` 流程无需修改

## 边界

本阶段不自动开启生产识图开关，不替代代理兼容性探针，也不接入外部指标平台。完整多模态回归、单目标群灰度和生产告警阈值属于下一阶段。

## 验证

- `PYTHONPATH=. python3 tests/test_vision_multimodal_context.py -v`
- `PYTHONPATH=. python3 tests/test_vision_media_store.py -v`
- `PYTHONPATH=. python3 tests/test_vision_visual_summary.py -v`
- `PYTHONPATH=. python3 tests/test_vision_session_memory.py -v`
- `PYTHONPATH=. python3 tests/test_agent_tool_calling.py -v`
- `ruff format --check`（本阶段变更文件）
