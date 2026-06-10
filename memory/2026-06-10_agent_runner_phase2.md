# 2026-06-10 Agent Runner Phase 2

## 变更类型
refactor

## 内容
- 将独立启动入口 `bot_app/runner.py` 从旧 `LLMMessageProcessor + ToolSelector` 链路切换到 `AgentRunner + ToolRegistry`。
- `AgentRunner` 接回 `ContextManager`，在构建消息前和记录回复后执行上下文压缩检查。
- 增强 tool-calling 单测，覆盖工具调用、忽略消息、图片工具结果透传、LLM tool_calls 解析和上下文压缩钩子调用。
- 更新 README，说明插件类入口与独立 runner 均使用同一套 tool-calling agent 链路。

## 四位一体
- Code: `bot_app/runner.py`, `bot_app/agent/runner.py`
- Test: `tests/test_agent_tool_calling.py`
- Docs: `README.md`
- Skill: 未修改 `.agents/skills/`；本次为插件内部 agent 链路收敛。

## 备注
- 旧 `llm_processor.py` 与 `tool_selector.py` 已无主入口引用，但暂未删除，以便单独审查历史兼容风险。
