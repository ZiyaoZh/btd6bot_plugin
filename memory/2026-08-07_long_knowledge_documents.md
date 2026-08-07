# 2026-08-07 放宽知识库长文读取限制

## 变更类型
fix

## 内容
- 长回合列表会先被知识工具的 8000/20000 字符限制截断，并可能再次被 Harness 的 60000 字符结果限制截断。
- 新增 `BOT_KNOWLEDGE_MAX_READ_CHARS`，默认 350000；`BOT_MAX_TOOL_RESULT_CHARS` 默认提高到 400000。
- 无关键词时允许读取完整 Markdown 正文；超出配置上限时通过 `content_truncated` 和 `source_content_chars` 明确标记。
- 章节提取结果不再在 `content` 和 `paragraphs[].content` 中重复正文，降低长表格的上下文占用。
- DeepSeek 官方 Chat Completions 与 Responses API 当前均不支持文件输入，因此继续将 Markdown 正文作为文本工具结果传给模型。

## 四位一体
- Code: `bot_app/settings.py`, `bot_app/bootstrap.py`, `bot_app/tools/base.py`, `bot_app/tools/knowledge.py`, `bot_app/tools/knowledge_service.py`, `bot_app/harness/tool_executor.py`
- Test: `tests/test_agent_tool_calling.py`
- Docs: `README.md`, 部署仓库 `.env.example`
- Skill: 不涉及 Agent 技能知识变更

## 备注
- 当前部署使用 `deepseek-v4-flash`，官方上下文长度为 1M；当前知识库最大正文约 312000 字符。
- 若部署到上下文更小的模型，应按模型容量下调两个字符上限，并保持 Harness 上限不小于知识读取上限。
