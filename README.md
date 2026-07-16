# btd6bot_plugin

面向 BTD6 社群的 NcatBot 智能插件，提供群聊问答、原生 tool-calling 工具编排、知识库检索、活动资讯与排行榜能力。

## 说明

- 插件类: `Btd6BotPlugin`
- 位置: `plugins/btd6bot_plugin/plugin.py`
- 依赖: 复用项目现有 `bot_app` 模块（Agent、LLM、工具注册、数据库、日志等）

## 功能与特性

- 原生工具调用：Model 通过 OpenAI Chat Completions `tools/tool_calls` 直接调用结构化工具，Harness 负责校验和执行。
- 多轮工具编排：支持在一次对话中连续调用多个工具指令，例如先检索知识库，再读取候选文件。
- 群聊降噪：无须回复的群聊消息通过 `ignore_message` 工具显式忽略。
- AI 生图：Agent 只提交 `prompt`，服务端使用私有的接口地址、密钥和模型配置生成并发送图片。
- BTD6 游戏能力：
	- 游戏知识检索与安全文件读取。
	- 活动详情查询（race/boss/odyssey/daily）。
	- 活动排行榜查询（race/boss-standard/boss-elite），支持图片输出。
	- 每天 07:00 自动发送当天活动简报，走现有 Agent 工具调用链路生成文案。
- 会话与数据管理：
	- 群消息写入 SQLite。
	- 今日活跃榜、最近消息总结等统计能力。
	- Agent 短期会话保存在进程内，并按阈值自动压缩，避免长对话无限增长。
- 管理控制指令：
	- 启动/终止处理。
	- 开启/关闭主动接话（可写回配置）。
	- 按群开启/关闭模型过程消息，开关写入 SQLite 并在重启后保留。
- 稳定性设计：
	- LLM 重试与异常分类。
	- Run 总超时、工具级超时、参数 schema 校验和工具异常归一化。
	- 同一群/用户会话串行执行；回复发送成功后才提交会话记忆。
	- 失败降噪策略与全链路结构化日志。

## Agent 架构

本项目使用 `Agent = Model + Harness`：

完整职责边界、运行协议和扩展约束见 [ARCHITECTURE.md](ARCHITECTURE.md)。

- Model 只负责根据 instructions、上下文、工具 schema 和 observation 生成原生 tool calls 或最终文本。
- Harness 维护 `RunState`，负责上下文装配、工具执行、预算、证据策略、结果归一化和 trace。
- `BotApplication` 负责渠道外层事务：接收事件、调用 Agent、发送回复，并在发送成功后提交记忆。
- QQ 插件、独立启动器和定时任务都是 adapter，不包含 Agent 循环。

核心状态流：

```text
event -> context -> model -> tool call -> validate/execute -> observation
                         ^                                  |
                         +----------------------------------+
         -> final outcome -> delivery -> memory commit
```

## 目录结构

- `bot_app/application.py`: 渠道无关的生成、发送和记忆提交事务。
- `bot_app/bootstrap.py`: 插件与独立入口共享的唯一 composition root。
- `bot_app/agent/`: Agent 对外门面、instructions、工具目录与响应类型。
- `bot_app/model/`: 模型供应商协议与 OpenAI-compatible Chat Completions adapter。
- `bot_app/harness/`: Run 循环、状态、会话锁、工具执行器、证据策略和 RunStore。
- `bot_app/tools/`: 按功能域维护 tool schema、handler 与 service；外部 CLI、知识库、小游戏等业务逻辑均在该层执行。
- `btd6_api_processor/`: 活动与榜单数据处理。
- `btd6_knowledge_base/`: 本地知识库与索引。
- `manuals/`: 程序说明书。
- `prompts/`: 系统提示词和工具选择提示。
- `data/`: 插件数据库文件。
- `logs/`: 插件日志输出目录。

## 启用

插件启动后默认处理群消息，不再通过环境变量开关控制启停。

默认只发送最终结果。群主或管理员可以在群里使用以下指令切换模型过程消息：

```text
@机器人 开启模型过程
@机器人 关闭模型过程
```

开启后，机器人会按顺序发送每个非终态模型轮次的公开 `content`，然后照常发送最终回复。过程消息不添加标签，不展示工具名称或参数；`content` 为空的轮次不会发送。该功能不会发送 `reasoning_content`、`reasoning_details` 等模型内部思考字段，也不会把过程消息写入会话记忆。

## 定时活动简报

插件加载时会注册一个每天 `07:00` 的定时任务，向目标群发送“当天活动简报”。

- 简报不是在定时任务里直接硬编码调用活动指令，而是构造一条系统事件消息，继续走统一的 Model + Harness 链路。
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

使用 `run_plugin_bot.py` 启动插件模式。插件类入口 `plugin.py` 与独立 `bot_app/runner.py` 通过 `bot_app/bootstrap.py` 构造同一个 `BotApplication`。

## Harness 限制配置

- `BOT_MAX_TOOL_ROUNDS`: 单个 Run 最多执行的工具批次，默认 4。
- `BOT_TOOL_TIMEOUT_SECONDS`: 单个工具调用超时，默认 30 秒。
- `BOT_MAX_TOOL_RESULT_CHARS`: 注入模型上下文的单个工具结果最大字符数，默认 60000。
- `BOT_RUN_TIMEOUT_SECONDS`: 单个 Agent Run 总超时，默认 120 秒。

## AI 生图配置

`generate_image` 使用 OpenAI-compatible Images API。Agent 可见的工具参数只有 `prompt`；以下配置只在服务端读取，不会出现在工具 schema 或工具结果中：

- `IMAGE_GEN_API_BASE_URL`: 接口根地址，例如 `https://api.openai.com/v1`。也可直接填写以 `/images/generations` 结尾的完整地址。
- `IMAGE_GEN_API_KEY`: 生图接口密钥。
- `IMAGE_GEN_MODEL`: 生图模型名，例如 `gpt-image-1`。
- `IMAGE_GEN_REQUEST_TIMEOUT`: 单次生图请求和图片下载超时，默认 180 秒。
- `IMAGE_GEN_MAX_RETRIES`: 可重试失败发生后的最大重试次数，默认 2，即最多请求 3 次，范围 0-5。
- `IMAGE_GEN_RETRY_DELAY_SECONDS`: 首次重试等待时间，默认 2 秒，后续按 2 倍指数退避，单次最多等待 60 秒。
- `IMAGE_GEN_USE_PROXY`: 是否继承进程的 `HTTP_PROXY`/`HTTPS_PROXY`，默认 `false`。设为 `false` 时，生图请求及返回图片下载使用独立直连会话，不影响 LLM、网页搜索、QQ 或其他网络请求的代理设置。
- `IMAGE_GEN_MAX_BYTES`: 单张返回图片的最大字节数，默认 20 MiB。

接口响应支持 `data[0].b64_json`、`data[0].url` 或 Base64 data URL。图片保存在 `data/generated_images/`，随后通过现有图片发送链路发送。连接失败、超时、HTTP 429、服务端 5xx、临时无效响应和图片下载失败会自动重试；鉴权失败、普通 4xx 请求拒绝、非法地址和图片超限不会重试。生图工具会按单次超时、重试次数与退避时间计算独立的 Harness 超时，不影响其他工具；`BOT_RUN_TIMEOUT_SECONDS` 会在配置加载时自动保证覆盖这段预算并额外预留 60 秒供模型生成最终回复。部署环境通过 SSH/Privoxy 提供全局代理时，保持 `IMAGE_GEN_USE_PROXY=false` 可避免生图请求受到代理层短超时限制。

## 纯插件启动配置

- 推荐配置模板：`.env.plugin.example`
- 启动命令：`python run_plugin_bot.py`

## 插件日志目录

- 插件日志写入：`plugins/btd6bot_plugin/logs/`
- 默认文件：`plugins/btd6bot_plugin/logs/network.log`
