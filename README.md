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
- AI 生图：明确的独立生图请求由 Harness 保证调用图片工具；短提示词在服务端补全，私有接口地址、密钥和模型配置不会暴露给 Agent。
- 活动情报图：竞速、Boss、征程和每日挑战使用 Ninja Kiwi 结构化数据、真实地图缩略图与 Pillow 专用模板生成确定性 PNG，同时支持一张四类活动总览图；事实型活动图不交给通用 AI 生图。
- Agent 产物目录：活动报告、排行榜、收集活动图片和 AI 图片统一进入 `data/artifacts/`；当前有效清单会直接提供给模型，由模型结合标题、摘要、标签和日期判断是否复用。
- BTD6 游戏能力：
	- 游戏知识检索与安全文件读取。
	- 活动详情查询（race/boss/odyssey/daily）。
	- 四类单项活动情报图及全活动总览图（current/upcoming/ended/relevant；latest 仅兼容旧调用，征程支持 easy/medium/hard）。
	- 活动排行榜查询（race/boss-standard/boss-elite），支持图片输出。
	- 每天 07:00 自动发送当天活动总览图，走现有 Agent 工具调用与图片发送链路。
- 会话与数据管理：
	- 群消息写入 SQLite。
	- 今日活跃榜、最近消息总结等统计能力。
	- Agent 短期会话保存在进程内，并按阈值自动压缩，避免长对话无限增长。
- 管理控制指令：
	- 按群启动/终止处理。
	- 按群开启/关闭主动接话。
	- 按群开启/关闭模型过程消息。
	- 查看系统指令列表及本群当前状态。
	- 各群开关统一写入 SQLite，重启后保留且互不影响。
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
- `bot_app/artifact_store.py`: Agent 产物的统一目录、SQLite 标签索引与生命周期管理。
- `bot_app/bootstrap.py`: 插件与独立入口共享的唯一 composition root。
- `bot_app/agent/`: Agent 对外门面、instructions、工具目录与响应类型。
- `bot_app/model/`: 模型供应商协议与 OpenAI-compatible Chat Completions adapter。
- `bot_app/harness/`: Run 循环、状态、会话锁、工具执行器、证据策略和 RunStore。
- `bot_app/tools/`: 按功能域维护 tool schema、handler 与 service；外部 CLI、知识库、小游戏等业务逻辑均在该层执行。
- `btd6_api_processor/`: 活动与榜单数据处理。
- `btd6_knowledge_base/`: 版本化 Markdown 本地知识库；运行时直接扫描文档目录，不依赖搜索索引数据库。
- `manuals/`: 程序说明书。
- `prompts/`: 系统提示词和工具选择提示。
- `data/`: 插件数据库文件。
- `logs/`: 插件日志输出目录。

## 启用

插件启动后默认处理所有目标群消息。群内启停、主动接话和模型过程消息开关均按群保存；一个群的管理员操作不会影响其他群。`BOT_PROACTIVE_CHAT_ENABLED` 只作为尚未单独设置过的群的主动接话默认值。

所有系统指令都需要带 `@机器人`。群主或管理员可以执行开关操作：

```text
@机器人 启动
@机器人 终止
@机器人 开启主动接话
@机器人 关闭主动接话
@机器人 开启模型过程
@机器人 关闭模型过程
```

所有群成员都可以查看系统指令及本群当前开关状态：

```text
@机器人 系统指令列表
```

开启后，机器人会按顺序发送每个非终态模型轮次的公开 `content`，然后照常发送最终回复。过程消息不添加标签，不展示工具名称或参数；`content` 为空的轮次不会发送。该功能不会发送 `reasoning_content`、`reasoning_details` 等模型内部思考字段，也不会把过程消息写入会话记忆。

## 定时活动简报

插件加载时会注册一个每天 `07:00` 的定时任务，向目标群发送一张“当天活动总览图”。图片简要覆盖竞速、Boss、征程和每日挑战；详细规则仍由各单项情报图提供。

- 简报构造系统事件并继续走统一的 Model + Harness 链路；Harness 会为该定时事件确定性调用一次 `get_activity_brief_image(all/relevant/hard)`，模型只需生成简短图片说明。
- 后台活动发现任务约每 5 分钟仅检查四类列表；发现 event ID 或生命周期槽位变化时，才失效相关选择、图片和 Agent 产物缓存并预热 metadata。失败时采用指数退避与抖动。
- 默认发送到 `BOT_TARGET_GROUP_IDS`（未配置时回退到 `BOT_TARGET_GROUP_ID`）中的群。
- 已通过群内“终止”指令停用的群会跳过简报，其他目标群不受影响。
- 如需覆盖默认行为，可修改插件配置 `data/btd6bot_plugin/config.yaml` 中的 `daily_activity_brief`。

配置结构示例：

```yaml
daily_activity_brief:
  enabled: true
  schedule: "07:00"
  group_ids: []
  prompt: "请生成今天的 BTD6 每日活动早报，调用 get_activity_brief_image 并使用 activity_type=all。"
```

说明：

- `enabled`: 是否启用定时简报。
- `schedule`: 每日触发时间，格式如 `07:00`。
- `group_ids`: 可选，留空时使用机器人默认目标群列表。
- `prompt`: 发给大模型的固定提示词，可按群风格调整图片说明；默认要求调用 `get_activity_brief_image(activity_type=all)` 发送总览图。历史默认纯文本提示会在加载时自动迁移为图片早报提示，自定义提示保持不变。

## 入口建议

使用 `run_plugin_bot.py` 启动插件模式。插件类入口 `plugin.py` 与独立 `bot_app/runner.py` 通过 `bot_app/bootstrap.py` 构造同一个 `BotApplication`。

## Harness 限制配置

- `BOT_MAX_TOOL_ROUNDS`: 单个 Run 最多执行的工具批次，默认 4。
- `BOT_TOOL_TIMEOUT_SECONDS`: 单个工具调用超时，默认 30 秒。
- `BOT_KNOWLEDGE_MAX_READ_CHARS`: 单次知识库文件读取或章节提取的最大字符数，默认 350000，可覆盖当前知识库中的完整长文档。查询带有问题或关键词时仍优先提取相关章节。
- `BOT_MAX_TOOL_RESULT_CHARS`: 注入模型上下文的单个工具结果最大字符数，默认 400000。该值应不小于 `BOT_KNOWLEDGE_MAX_READ_CHARS`，否则知识正文仍可能在 Harness 层被二次截断。
- `BOT_RUN_TIMEOUT_SECONDS`: 单个 Agent Run 总超时，默认 120 秒。

## 识图兼容性探针

生产识图开关启用前，先从仓库根目录对当前 OpenAI-compatible 代理运行三项门槛测试：Base64 单图、双图比较，以及图片与强制 `tools/tool_calls` 组合。探针读取 `.env` 中的 `LLM_API_KEY`、`LLM_API_BASE_URL` 和 `LLM_MODEL`/`LLM_MODELS`，不会接入 QQ 消息处理链路。

```bash
PYTHONPATH=plugins/btd6bot_plugin python3 -m bot_app.model.vision_probe \
  --output plugins/btd6bot_plugin/data/vision-probe-report.json
```

默认使用仓库 `image_samples/` 中的两张非敏感图片。输出只包含文件路径、MIME、字节数、SHA-256、响应结构、usage、耗时和错误摘要，不包含 API key、Base64 图片或完整请求 payload。三项全部通过时退出码为 `0`；任一项失败时退出码为 `1`，此时不得开启生产识图链路。可用两个 `--image` 参数覆盖样本，用 `--timeout` 和 `--max-attempts` 控制单次超时及尝试次数。

代理门槛通过后，QQ 接收层会从 NcatBot `MessageArray.to_list()` 提取结构化图片元数据，并在渠道事件契约中提供 `message_id` 和 `images`。每张图片包含原消息段位置，以及可用的 URL、文件 ID、文件名、字节数和 `sub_type`；接收层不下载图片、不保存 Base64，也不通过正则解析 CQ 图片码。

图片元数据与消息在同一 SQLite 事务内写入 `message_media`，以消息行和原始段位置保证幂等。纯图片会进入消息处理和最近群聊窗口，历史上下文以“该消息含 N 张图片”占位，并携带不含源 URL 的媒体状态引用；当前消息会按 `message_id` 从历史窗口去重。

启用 `LLM_VISION_ENABLED` 后，接收层会将图片下载到独立的有界缓存。实际进入 Agent 的当前消息在处理前完成缓存；目标群内其他消息后台尽力缓存，下载失败只更新媒体状态，不中断群消息处理。缓存只接受公网 HTTP(S) 地址，逐跳检查重定向，限制下载字节数和解码像素数，并使用 Pillow 将首帧归一化为无 EXIF 的 JPEG/PNG。文件按归一化内容的 SHA-256 去重，SQLite 只保存 MIME、尺寸、哈希、缓存路径、状态和过期时间，不保存图片正文或 Base64。

- `LLM_VISION_ENABLED`: 识图媒体链路总开关，默认 `false`。代理探针未通过时不得开启。
- `LLM_VISION_CACHE_ROOT`: 图片缓存目录，默认 `data/chat_media`。
- `LLM_VISION_DOWNLOAD_TIMEOUT_SECONDS`: 单图下载超时，默认 10 秒。
- `LLM_VISION_MAX_IMAGE_BYTES`: 单张源图片最大字节数，默认 10 MiB。
- `LLM_VISION_MAX_PIXELS`: 单张图片最大解码像素数，默认 20,000,000。
- `LLM_VISION_MAX_DIMENSION`: 归一化图片最长边，默认 2048 像素。
- `LLM_VISION_CACHE_TTL_SECONDS`: 缓存有效期，默认 21,600 秒（6 小时）。
- `LLM_VISION_CACHE_CAPACITY_BYTES`: 缓存磁盘上限，默认 512 MiB；超限时优先删除最久未使用的文件。

缓存成功的图片会在模型适配边界转换为 Chat Completions 图文 `content`：当前消息图片优先，剩余预算用于最近群聊中的有效缓存，并用“当前消息图片 N”“群聊历史第 N 条图片 N”标签保持语义对应。图片仅在请求内编码为 Base64 data URL，不写入 SQLite；请求日志会将正文替换为 MIME、字节数和 SHA-256 摘要。开关关闭或缓存失效时继续使用原有纯文本 payload。

- `LLM_VISION_CURRENT_IMAGE_LIMIT`: 当前消息最多提交的图片数，默认 4。
- `LLM_VISION_HISTORY_IMAGE_LIMIT`: 最近群聊最多提交的图片数，默认 2。
- `LLM_VISION_TOTAL_IMAGE_LIMIT`: 单次模型请求最多提交的图片总数，默认 4。
- `LLM_VISION_IMAGE_DETAIL`: Chat Completions 图片细节级别，可选 `auto`、`low`、`high`，默认 `auto`。非法值会在启动时拒绝，`high` 通常消耗更多图片 token。

识图日志执行统一的出站脱敏：任何结构化字段中的 Base64 图片都会替换为 MIME、字节数和 SHA-256，HTTP(S) URL 的查询参数与 fragment 会被移除，因此上游错误回显和异常堆栈也不会保留 QQ 签名参数。`llm.request` 额外记录 `vision_image_count`、`vision_total_bytes` 和 `vision_detail`；`media.cache_completed` 记录图片总数、缓存成功/拒绝数、内容命中数、归一化总字节和 `duration_ms`。这些字段用于灰度观察，不包含图片正文或完整源 URL。

现有 assistant/tool 消息和 `tools/tool_choice` 循环保持不变。短期会话记忆会保存图片数量及安全媒体引用（消息 ID、段序号、哈希、MIME、缓存路径、状态和可选视觉摘要），不保存源 URL、Base64 或图片正文。后续追问“上一张图”时，装配器按“当前消息、最近群聊、同一用户会话”的顺序，在同一总预算内去重并复用仍有效的缓存；缓存过期时上下文仍保留图片数量和已有视觉摘要作为文本降级，不会重新提交失效文件。200 条群聊总结仍只消费文本和视觉摘要，不附带原图。

机器人成功交付含图消息的回复后，会在提交该用户会话前按需生成一句视觉摘要，并回填当前事件和 SQLite `message_media.visual_summary`。摘要只处理本轮已缓存且尚无摘要的图片，使用 `detail=low`，不会为未进入机器人对话的全部群图片预计算；模型或元数据回填失败不会影响已发送回复和会话提交。系统提示和摘要提示都明确将图片像素、OCR、截图文字及图片元数据视为不可信用户内容，不允许其中的指令覆盖系统规则或工具策略。

- `LLM_VISION_SUMMARY_ENABLED`: 是否为已参与对话的图片按需生成视觉摘要，默认 `true`；仅在识图总开关开启时生效。
- `LLM_VISION_SUMMARY_IMAGE_LIMIT`: 每个已交付会话轮次最多生成的图片摘要数，默认 4。
- `LLM_VISION_SUMMARY_MAX_CHARS`: 单张图片摘要最多保留的字符数，默认 160，最小 40。

## 产物索引与生命周期

Agent 产生的可复用文件统一保存在 `data/artifacts/`，不再由各工具长期散落到自己的输出目录：

- `index.db`: 产物标题、摘要、标签、文件角色、访问次数和生命周期时间的 SQLite 索引。
- `active/<kind>/<artifact_id>/`: 未过期、可直接复用的产物。
- `archive/<kind>/<artifact_id>/`: 已过期但仍处于保留期的存档。
- `.staging/`: 外部命令和生图服务的临时输出，登记后会迁入 `active/`。

每轮 Agent 请求的系统上下文都会附带一份精简的未过期产物清单，其中包含 `artifact_id`、易读标题、摘要、标签、生成时间、过期时间和文件路径。模型根据用户语义自行判断是否相关，不要求标签完全一致。需要查看更多条目时调用 `list_artifacts`；选中条目后调用 `read_artifact`，文档会返回正文，图片会直接进入现有图片发送链路。只有清单中没有合适内容时，模型才调用活动获取、生图或其他生成工具。

业务工具只负责登记新产物，不在内部按标签偷偷替模型做精确缓存命中。过期产物会在启动或后续产物访问时自动移入 `archive/`，超过存档保留期后删除文件及索引记录。

模拟开箱是实时随机游戏执行，明确排除在产物目录之外。每次请求都会实际运行 `ChatMonkeyTD` 生成新报告和图片，输出统一放在 `data/tool_outputs/mini_game/`，不会写入 `index.db` 或出现在模型的有效产物清单中；超过一天的旧开箱输出会在后续模拟时自动清理。

- `BOT_ARTIFACT_ROOT`: 统一产物根目录，默认 `data/artifacts`。
- `BOT_ARTIFACT_ACTIVITY_TTL_SECONDS`: 活动报告和图片有效期，默认 600 秒。
- `BOT_ARTIFACT_IMAGE_TTL_SECONDS`: AI 图片有效期，默认 604800 秒（7 天）。
- `BOT_ARTIFACT_DEFAULT_TTL_SECONDS`: 其他产物默认有效期，默认 86400 秒（1 天）。
- `BOT_ARTIFACT_ARCHIVE_RETENTION_SECONDS`: 过期后的存档保留期，默认 604800 秒（7 天）；设为 `0` 时在归档维护周期内直接清理。
- `BTD6_DISPLAY_UTC_OFFSET_HOURS`: 活动详情与情报图的显示时区偏移，默认 `8`；结构化快照中的原始毫秒时间戳不受影响。

## AI 生图配置

`generate_image` 使用 OpenAI-compatible Images API，只处理同人插画、壁纸、头像、场景画等创意视觉。竞速、Boss、征程、每日挑战的单项情报图及四类活动总览图由 `get_activity_brief_image` 使用真实数据确定性绘制，不进入本接口。Agent 可见的 AI 生图参数只有 `prompt`；Harness 和工具服务会对短提示词补全主体、构图、场景和光线。以下配置只在服务端读取，不会出现在工具 schema 或工具结果中：

- `IMAGE_GEN_API_BASE_URL`: 接口根地址，例如 `https://api.openai.com/v1`。也可直接填写以 `/images/generations` 结尾的完整地址。
- `IMAGE_GEN_API_KEY`: 生图接口密钥。
- `IMAGE_GEN_MODEL`: 生图模型名，例如 `gpt-image-1`。
- `IMAGE_GEN_REQUEST_TIMEOUT`: 单次生图请求和图片下载超时，默认 180 秒。
- `IMAGE_GEN_MAX_RETRIES`: 可重试失败发生后的最大重试次数，默认 2，即最多请求 3 次，范围 0-5。
- `IMAGE_GEN_RETRY_DELAY_SECONDS`: 首次重试等待时间，默认 2 秒，后续按 2 倍指数退避，单次最多等待 60 秒。
- `IMAGE_GEN_USE_PROXY`: 是否继承进程的 `HTTP_PROXY`/`HTTPS_PROXY`，默认 `false`。设为 `false` 时，生图请求及返回图片下载使用独立直连会话，不影响 LLM、网页搜索、QQ 或其他网络请求的代理设置。
- `IMAGE_GEN_MAX_BYTES`: 单张返回图片的最大字节数，默认 20 MiB。

接口响应支持 `data[0].b64_json`、`data[0].url` 或 Base64 data URL。图片先写入 `data/artifacts/.staging/`，登记后迁入统一活跃产物目录并通过现有图片发送链路发送；后续模型可以在有效产物清单中看到图片的标题、摘要、标签和日期，并通过 `read_artifact` 主动复用。连接失败、超时、HTTP 429、服务端 5xx、临时无效响应和图片下载失败会自动重试；鉴权失败、普通 4xx 请求拒绝、非法地址和图片超限不会重试。生图工具会按单次超时、重试次数与退避时间计算独立的 Harness 超时，不影响其他工具；`BOT_RUN_TIMEOUT_SECONDS` 会在配置加载时自动保证覆盖这段预算并额外预留 60 秒供模型生成最终回复。部署环境通过 SSH/Privoxy 提供全局代理时，保持 `IMAGE_GEN_USE_PROXY=false` 可避免生图请求受到代理层短超时限制。

## 纯插件启动配置

- 推荐配置模板：`.env.plugin.example`
- 启动命令：`python run_plugin_bot.py`

## 插件日志目录

- 插件日志写入：`plugins/btd6bot_plugin/logs/`
- 默认文件：`plugins/btd6bot_plugin/logs/network.log`
