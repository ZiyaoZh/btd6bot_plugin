# btd6bot_plugin

面向 BTD6 社群的 NcatBot 智能插件，提供群聊问答、工具路由、多轮任务编排、知识库检索、活动资讯与排行榜能力。

## 说明

- 插件类: `Btd6BotPlugin`
- 位置: `plugins/btd6bot_plugin/plugin.py`
- 依赖: 复用项目现有 `bot_app` 模块（LLM、路由、工具、数据库、日志等）

## 功能与特性

- 智能路由：根据消息语义在 chat/tool_selector/noop 之间自动分流。
- 多轮工具编排：支持在一次对话中连续调用多个工具指令，必要时切换工具集。
- BTD6 游戏能力：
	- 游戏知识检索与安全文件读取。
	- 活动数据后台定时刷新（集成 refresh-service）。
	- 活动详情查询（race/boss/odyssey/daily）。
	- 活动排行榜查询（race/boss-standard/boss-elite），支持图片输出。
- 会话与数据持久化：
	- 群消息写入 SQLite。
	- 今日活跃榜、最近消息总结等统计能力。
- 管理控制指令：
	- 启动/终止处理。
	- 开启/关闭主动接话（可写回配置）。
- 稳定性设计：
	- LLM 重试与异常分类。
	- 失败降噪策略与全链路结构化日志。

## 目录结构

- `bot_app/`: 插件内核心业务模块。
- `btd6_api_processor/`: 活动与榜单数据处理。
- `btd6_knowledge_base/`: 本地知识库与索引。
- `command_skills/`: 指令技能文档。
- `manuals/`: 程序说明书。
- `prompts/`: 系统提示词与路由提示词。
- `data/`: 插件数据库文件。
- `logs/`: 插件日志输出目录。

## 启用

插件启动后默认处理群消息，不再通过环境变量开关控制启停。

## 入口建议

使用 `run_plugin_bot.py` 启动插件模式。

## 纯插件启动配置

- 推荐配置模板：`.env.plugin.example`
- 可选配置：`BOT_GAME_NEWS_REFRESH_INTERVAL_SECONDS`（默认 600 秒）
- 启动命令：`python run_plugin_bot.py`

## 插件日志目录

- 插件日志写入：`plugins/btd6bot_plugin/logs/`
- 默认文件：`plugins/btd6bot_plugin/logs/network.log`
