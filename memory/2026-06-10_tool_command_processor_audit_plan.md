# 2026-06-10 工具层与指令处理器审计计划

## 背景

当前插件已切到 OpenAI-style tool-calling 链路，主入口通过 `ToolRegistry` 注册结构化工具，再由 `AgentRunner` 执行工具调用和最终回复。

但工具拆分还没有完成：一部分工具已经在 `bot_app/tools/` 里直接执行业务逻辑，另一部分工具只有 schema 在 `tools/`，实际处理仍通过 `command_tool()` 转发到 `CommandProcessor`。

本文记录当前问题、风险和后续修改计划，便于按阶段逐步重构。

## 当前工具实现状态

### 已直接在 tools 层实现

- `ignore_message`
- `list_features`
- `today_most_active`
- `summarize_recent_messages`
- `script_manual_query`

相关文件：

- `bot_app/tools/general.py`
- `bot_app/tools/social.py`
- `bot_app/tools/manual.py`

### 仍通过 CommandProcessor 转发

- `search_game_knowledge`
- `read_game_knowledge_file`
- `get_activity_detail`
- `get_activity_leaderboard`
- `get_collection_event_info`
- `simulate_monkey_box_opening`

相关文件：

- `bot_app/tools/base.py`
- `bot_app/tools/activity.py`
- `bot_app/tools/knowledge.py`
- `bot_app/tools/mini_game.py`
- `bot_app/command_processor.py`

## 主要问题

### P0：异步链路中存在同步 subprocess 阻塞

`CommandProcessor` 内部使用同步 `subprocess.run()` 调用外部 CLI：

- `btd6_api_processor/btd6_cli.py`
- `ChatMonkeyTD/main.py`

这些调用发生在 async tool handler 链路中，最长 timeout 为 180 秒。只要 CLI 卡住，事件循环会被阻塞，影响群消息处理、定时任务和其他工具请求。

影响：

- 机器人响应可能整体卡住。
- 并发请求会相互拖慢。
- 定时任务与群聊消息处理互相影响。

建议：

- 抽象 subprocess runner。
- 改为 `asyncio.create_subprocess_exec()`，或在过渡期用 executor 隔离阻塞调用。
- 测试中 mock runner，不真实跑网络 CLI。

### P0：图片/附件契约只保留最后一个工具结果

`AgentRunner` 当前用 `last_command_result` 决定最终 `response_type` 和 `image_path`。

风险：

- 如果一次任务先生成图片，再调用文本工具补充说明，图片会被后续文本结果覆盖。
- 如果未来支持多图或文件，当前 `response_type + image_path` 不够表达。

建议：

- 短期维护 `last_image_result`，最终发送时优先保留最近一个有效图片。
- 中期引入 `attachments` 字段。
- 增加“先 image 后 text 仍发送图片”的 Agent 单测。

### P1：CommandProcessor 职责过重

`CommandProcessor` 当前同时承担：

- 旧指令路由
- 功能列表
- 活动详情查询
- 排行榜图片查询
- 收集活动轮换查询
- 开箱小游戏
- 知识库检索
- 知识文件读取
- 说明书加载
- 群聊统计
- 路径安全检查
- HTML 清洗
- 外部进程调用

影响：

- 工具层拆分不彻底。
- 新功能容易继续堆进 `CommandProcessor`。
- 单测难以按领域隔离。
- 运行时资源通过私有字段泄露给 `ToolRuntime`。

建议：

- `CommandProcessor` 最终退化为兼容适配器。
- 真正业务逻辑迁移到 tools/service 层。
- 每个领域工具独立测试。

### P1：tools 与 CommandProcessor 存在重复实现

重复区域：

- `list_features`
- `today_most_active`
- `summarize_recent_messages`
- `script_manual_query`

问题：

- 两边返回字段不完全一致。
- 后续修改容易只改一边，产生行为漂移。
- 功能列表硬编码两份，新增工具容易忘记同步。

建议：

- 只保留 tools 层实现。
- `CommandProcessor` 中同名旧入口委托到 tools/registry，或在迁移完成后删除。
- `list_features` 从单一工具元数据源生成。

### P1：CommandResultPayload 返回字段不统一

部分旧 handler 没有显式返回：

- `response_type`
- `image_path`

当前 Agent 会用默认值兜底，但这不是稳定契约。

建议：

- 增加统一 result builder：
  - `ok_result(...)`
  - `fail_result(...)`
  - `image_result(...)`
- 所有工具结果都必须包含完整字段。
- 增加 contract test，遍历工具结果验证字段。

### P1：ToolRuntime 依赖 CommandProcessor 私有字段

`ToolRuntime.from_command_processor()` 通过 `getattr()` 读取：

- `_db`
- `_manual_file`

问题：

- 私有字段变动会静默破坏工具。
- 工具层资源边界不清晰。
- 后续工具还会继续从 `CommandProcessor` 偷字段。

建议：

- `ToolRuntime` 直接包含明确资源：
  - `db`
  - `plugin_root`
  - `manual_file`
  - `command_guide_dir`
  - `kb_root`
  - `api_processor_root`
  - `chat_monkey_root`
  - `subprocess_runner`
- `ToolRegistry` 接收 `ToolRuntime`，而不是接收 `CommandProcessor`。

### P1：输出文件名只精确到秒，有并发覆盖风险

活动 CLI 和小游戏输出文件使用秒级时间戳。

风险：

- 同一秒内多个请求可能写同一路径。
- 图片、报告可能被覆盖。
- 后续发送可能发错图或旧图。

建议：

- 文件名加入 `uuid4().hex[:8]` 或纳秒时间。
- 测试并发生成路径不重复。

### P1：知识库工具有全局 sys.path 修改和 cwd 依赖

知识库 handler 运行时修改 `sys.path` 并导入 `kb_interface`。

同时 `_resolve_safe_kb_path()` 对相对路径使用 `Path.cwd()`，不同启动方式下 cwd 可能不同。

风险：

- 全局 `sys.path` 副作用影响整个进程。
- 插件模式、独立 runner、测试模式路径行为可能不一致。
- 依赖替换和测试困难。

建议：

- 抽出 `KnowledgeService`。
- 明确以 `kb_root` / `corpus_dir` 解析路径。
- 尽量避免全局 `sys.path` 修改；如果短期无法避免，集中封装并测试。

### P2：工具 schema 与 handler 容错策略不完全一致

示例：

- `simulate_monkey_box_opening` schema 只列中文箱子枚举，但 handler 支持英文别名。
- `get_collection_event_info` schema 是 boolean，但 handler 兼容字符串。
- `read_game_knowledge_file` schema 中 `keywords` 是 array，但 handler 也兼容 string。

影响：

- 模型看到的参数边界和实际服务端边界不一致。
- 测试难以判断哪一层是权威。

建议：

- schema 面向模型，尽量明确推荐格式。
- handler 保留兼容，但测试覆盖兼容输入。
- 文档说明“schema 是推荐协议，handler 兼容历史参数”。

### P2：manual 工具直接返回全文

`script_manual_query` 当前把完整说明书放进 tool result。

风险：

- 说明书增长后 token 成本高。
- Agent 上下文膨胀。
- 与知识库检索逻辑重复。

建议：

- 短期保留。
- 中期改为按章节/关键词检索。
- 增加最大字符数或摘要缓存。

### P2：测试覆盖集中在 Agent 编排，领域工具测试不足

现有测试主要覆盖：

- ToolRegistry 注册
- Agent tool_calls 编排
- 图片结果透传
- sender 图片发送
- 部分 direct tools

缺少：

- activity CLI 成功/失败/图片解析测试
- collection-event `# 图片:` / `# JSON:` 解析测试
- mini_game 参数校验、png 路径、失败/超时测试
- knowledge 安全路径、长文提取、关键词输入测试
- result contract 测试
- 多工具调用图片不丢失测试

## 分阶段修改计划

### Phase 0：提交或隔离当前图片修复

目标：

- 先把当前图片发送修复和开箱 png 输出作为独立变更收口。
- 避免后续重构和 bug fix 混在一个 diff 中。

当前相关变更：

- `bot_app/sender.py`
- `bot_app/command_processor.py`
- `ChatMonkeyTD/main.py`
- `tests/test_agent_tool_calling.py`
- `memory/2026-06-10_image_sending_and_box_png.md`

验收：

- `python -m unittest tests.test_agent_tool_calling`
- `ruff check bot_app/sender.py bot_app/command_processor.py tests/test_agent_tool_calling.py ChatMonkeyTD/main.py`
- 手动确认开箱 CLI 输出 png。

### Phase 1：统一工具返回契约

目标：

- 所有工具返回完整且一致的 `CommandResultPayload`。
- 降低 Agent 和 sender 对缺省字段的隐式依赖。

修改项：

- 新增 result builder。
- direct tools 改用 builder。
- `CommandProcessor` 旧 handler 也改为完整字段。
- 增加 contract tests。

验收：

- 所有工具结果包含：
  - `module`
  - `ok`
  - `command`
  - `data`
  - `message`
  - `extra_prompt`
  - `response_type`
  - `image_path`

### Phase 2：重建 ToolRuntime

目标：

- 切断工具层对 `CommandProcessor` 私有字段的依赖。

修改项：

- `ToolRuntime` 显式包含运行时资源。
- `ToolRegistry` 接收 `ToolRuntime`。
- 插件入口和独立 runner 构造同一份 runtime。
- `CommandProcessor` 暂时保留，但不再作为工具资源容器。

验收：

- `ToolRuntime` 中不再出现 `from_command_processor()` 或只保留兼容包装。
- direct tools 不读取 `CommandProcessor` 私有字段。

### Phase 3：收敛 general/social/manual 重复实现

目标：

- 消除已经迁移完成工具的双实现。

修改项：

- `CommandProcessor` 中对应旧 handler 改为委托 tools，或删除旧入口。
- `list_features` 改为单一元数据源。
- 确认 README/command_skills 不再描述旧链路为主链路。

验收：

- 修改功能列表只需要改一处。
- `today_most_active`、`summarize_recent_messages`、`script_manual_query` 只有一个业务实现。

### Phase 4：迁移 activity 工具

目标：

- `get_activity_detail`
- `get_activity_leaderboard`
- `get_collection_event_info`

从 `CommandProcessor` 迁到 activity tool/service。

修改项：

- 抽出 `ActivityService` 或直接在 `tools/activity.py` 实现。
- 外部 CLI 调用改异步。
- 输出文件名加入 uuid。
- 图片/json 路径解析后检查文件存在。
- `CommandProcessor` 只保留兼容委托。

测试：

- detail 成功/失败。
- leaderboard 成功返回 image。
- collection-event 同时解析 json 和 png。
- CLI returncode 非 0。
- output 文件缺失。

### Phase 5：迁移 mini_game 工具

目标：

- `simulate_monkey_box_opening` 从 `CommandProcessor` 迁到 mini_game tool/service。

修改项：

- 抽出 `MiniGameService`。
- 开箱数量增加机器人侧上限。
- 外部 CLI 调用改异步。
- 输出文件名加入 uuid。
- 保持 png 输出。
- 保留中文/英文别名兼容。

测试：

- 合法小数量返回 image。
- 大于 50 返回 text。
- 非法箱子返回失败。
- 非法数量返回失败。
- CLI 超时/失败返回文本错误。

### Phase 6：迁移 knowledge 工具

目标：

- `search_game_knowledge`
- `read_game_knowledge_file`

从 `CommandProcessor` 迁到 knowledge tool/service。

修改项：

- 抽出 `KnowledgeService`。
- 路径解析基于 `kb_root` / `corpus_dir`，不依赖 cwd。
- 封装 `kb_interface` 导入。
- 减少或集中管理 `sys.path` 副作用。
- 保留长文关键词提取。

测试：

- search 成功/失败。
- top_n 边界。
- file_path 越界拒绝。
- html/md/txt 可读。
- 不允许的后缀拒绝。
- 长文无 keywords 返回提示。
- keywords list/string 都能处理。

### Phase 7：修复 Agent 图片保留逻辑

目标：

- 多工具调用时不丢失图片结果。

修改项：

- `AgentRunner` 维护 `last_image_result`。
- 最终文本来自 LLM，附件来自最近有效图片结果。
- 或升级 `AgentResponse.attachments`。

测试：

- image tool 后接 text tool，最终仍 `response_type=image`。
- image tool 后 LLM 最终回复 caption，图片仍发送。
- 无图片工具时保持 text。

### Phase 8：优化图片工具提示词

目标：

- 避免模型说“图片发不出来”“去群里找图”等错误自述。

修改项：

- activity/mini_game 图片工具 `extra_prompt` 改为：
  - 只写一句适合作为图片 caption 的说明。
  - 外层系统会负责发送图片。
  - 不要说自己不能发送图片。

测试：

- 不强测文案内容，但可检查 fallback 文本不包含明显错误短语。

### Phase 9：瘦身 CommandProcessor

目标：

- `CommandProcessor` 不再持有领域业务。

最终形态二选一：

1. 兼容适配器：
   - 输入旧 `IntentPayload`
   - 映射到 `ToolRegistry.execute()`
   - 不包含领域业务实现

2. 完全移除主链路依赖：
   - 插件运行时只走 `AgentRunner + ToolRegistry`
   - 旧 `llm_processor.py`、`tool_selector.py`、`command_skills/` 仅作为历史参考或逐步删除

验收：

- 新工具不需要修改 `CommandProcessor`。
- `CommandProcessor` 文件体积明显下降。
- 所有领域测试都指向 tools/service 层。

## 建议执行顺序

1. Phase 0：先收口当前图片发送和 png 输出修复。
2. Phase 1：统一工具返回契约。
3. Phase 2：重建 ToolRuntime。
4. Phase 3：清理已迁移 direct tools 的重复实现。
5. Phase 4：迁 activity。
6. Phase 5：迁 mini_game。
7. Phase 6：迁 knowledge。
8. Phase 7：修 Agent 图片保留逻辑。
9. Phase 8：优化图片工具提示词。
10. Phase 9：瘦身或移除 CommandProcessor 主链路职责。

## 每阶段通用验收

每完成一个阶段至少运行：

```bash
python -m unittest tests.test_agent_tool_calling
ruff check bot_app tests
```

涉及 `ChatMonkeyTD` 时额外运行：

```bash
python main.py 钻石 1 --report output/_codex_test_open_png.md --output output/_codex_test_open_png.png
```

涉及文档变更时同步更新：

- `README.md`
- `memory/`
- 必要时更新 `command_skills/`

涉及 agent 行为变更时补充：

- Agent 编排单测
- 工具结果契约单测
- 对应领域工具单测
