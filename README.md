# btd6bot_plugin

面向 BTD6 社群的 NcatBot 智能插件，提供群聊问答、原生 tool-calling 工具编排、知识库检索、活动资讯与排行榜能力。

## 说明

- 插件类: `Btd6BotPlugin`
- 位置: `plugins/btd6bot_plugin/plugin.py`
- 依赖: 复用项目现有 `bot_app` 模块（Agent、LLM、工具注册、数据库、日志等）

## 功能与特性

- 原生工具调用：`AgentRunner` 通过 OpenAI Chat Completions `tools/tool_calls` 直接调用结构化工具。
- 多轮工具编排：支持在一次对话中连续调用多个工具指令，例如先检索知识库，再读取候选文件。
- 群聊降噪：无须回复的群聊消息通过 `ignore_message` 工具显式忽略。
- BTD6 游戏能力：
	- 游戏知识检索与安全文件读取。
	- 活动详情查询（race/boss/odyssey/daily）。
	- 活动排行榜查询（race/boss-standard/boss-elite），支持图片输出。
	- 每天 07:00 自动发送当天活动简报，走现有 Agent 工具调用链路生成文案。
- 会话与数据持久化：
	- 群消息写入 SQLite。
	- 今日活跃榜、最近消息总结等统计能力。
	- Agent 会话上下文按阈值自动压缩，避免长对话无限增长。
- 管理控制指令：
	- 启动/终止处理。
	- 开启/关闭主动接话（可写回配置）。
- 稳定性设计：
	- LLM 重试与异常分类。
	- 失败降噪策略与全链路结构化日志。

## 目录结构

- `bot_app/`: 插件内核心业务模块。
- `bot_app/agent/`: tool-calling Agent、工具注册表、提示词管理与响应类型。
- `bot_app/tools/`: 具体 tool 定义，按功能域维护工具 schema 与 handler；低风险工具已直接在该层执行业务逻辑，外部 CLI/知识库类工具暂复用 `CommandProcessor` 适配。
- `btd6_api_processor/`: 活动与榜单数据处理。
- `btd6_knowledge_base/`: 本地知识库与索引。
- `command_skills/`: 指令技能文档，保留为人工参考；运行时工具协议以 `ToolRegistry` 为准。
- `manuals/`: 程序说明书。
- `prompts/`: 系统提示词；历史路由提示词仅作兼容参考。
- `data/`: 插件数据库文件。
- `logs/`: 插件日志输出目录。

## 启用

插件启动后默认处理群消息，不再通过环境变量开关控制启停。

## 定时活动简报

插件加载时会注册一个每天 `07:00` 的定时任务，向目标群发送“当天活动简报”。

- 简报不是在定时任务里直接硬编码调用活动指令，而是构造一条系统事件消息，继续走现有 Agent 工具调用和结果润色链路。
- 默认发送到 `BOT_TARGET_GROUP_IDS`（未配置时回退到 `BOT_TARGET_GROUP_ID`）中的群。
- 如需覆盖默认行为，可修改插件配置 `data/btd6bot_plugin/config.yaml` 中的 `daily_activity_brief`。

配置结构示例：

```yaml
daily_activity_brief:
  enabled: true
  schedule: "07:00"
  group_ids: []
  prompt: "请生成今天的 BTD6 活动简报，并直接整理成适合发送到群里的晨间播报。"
```

说明：

- `enabled`: 是否启用定时简报。
- `schedule`: 每日触发时间，格式如 `07:00`。
- `group_ids`: 可选，留空时使用机器人默认目标群列表。
- `prompt`: 发给大模型的固定提示词，可按你的群风格自行调整。

## 入口建议

使用 `run_plugin_bot.py` 启动插件模式。插件类入口 `plugin.py` 与独立 `bot_app/runner.py` 均使用同一套 `AgentRunner` tool-calling 链路。

## 纯插件启动配置

- 推荐配置模板：`.env.plugin.example`
- 启动命令：`python run_plugin_bot.py`

## 插件日志目录

- 插件日志写入：`plugins/btd6bot_plugin/logs/`
- 默认文件：`plugins/btd6bot_plugin/logs/network.log`
