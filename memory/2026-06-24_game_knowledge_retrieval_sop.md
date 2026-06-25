# 2026-06-24 游戏知识检索命中率与证据化回答治理 SOP

## 文档目的

本文用于指导 `btd6bot_plugin` 后续对 BTD6 游戏知识查询链路进行持续治理，重点覆盖：

- 游戏知识检索命中率提升
- 检索后证据消费链路收紧
- 知识型回答的可信度治理
- 线上日志、评测集和指标闭环建设

目标不是一次性写完方案，而是作为长期执行 SOP 使用：

- 明确总目标。
- 明确当前实现现状。
- 明确分阶段实施方式。
- 明确每阶段验收标准。
- 明确每阶段完成后如何更新现状、风险和下一阶段计划。

本文默认是该主题的主追踪文档。后续每完成一阶段，应优先更新本文；如阶段内容较大，可新增配套 phase 文档，但本文仍需回写摘要与状态。

## 总目标

### G1. 提升游戏知识查询命中率

让 Agent 在面对 BTD6 游戏知识问题时，更稳定地：

- 识别这是知识库问题而不是普通闲聊。
- 把用户口语问法改写成更适合检索的领域查询。
- 召回正确页面或正确段落。
- 在相似问法下给出相近质量的结果。

### G2. 提升知识型回答的证据性

让知识型回答尽量基于知识库证据生成，而不是在检索失败后直接退化为模型自由发挥。

目标包括：

- 检索成功时必须消费证据。
- 检索失败时默认明确说明未命中。
- 降低“知识库没找到，但模型凭印象回答”的比例。

### G3. 提升复杂游戏问法的理解能力

让系统更好理解以下类型的 BTD6 领域表达：

- 路线表达：`上路/中路/下路`、`路线1/2/3`
- 升级表达：`004`、`0-0-4`
- 塔与英雄别名：中文名、英文名、玩家常用简称
- 机制表达：`能不能抓 DDT`、`打不打得动`、`能否破铅/破黑/反迷彩`
- 数据表达：`详细数据`、`数值`、`价格/伤害/穿透/攻速`

### G4. 控制复杂度和成本

在提升命中率的同时，避免系统变成难维护的多层临时补丁。

需要同时控制：

- 实现复杂度
- token 成本
- 检索延迟
- 日志噪音
- 线上行为不可解释性

### G5. 建立可持续演进的 SOP

后续每一轮改造都应能回答四个问题：

- 当前链路实际上如何工作。
- 本阶段改动了什么。
- 本阶段是否达到预期。
- 下一阶段做什么、为什么做。

## 当前现状

截至 2026-06-24，当前实现的关键现状如下。

### 1. 主入口与代码结构现状

当前仓库中的主装配入口位于：

- `plugin.py`
- `bot_app/agent/runner.py`
- `bot_app/agent/prompt_manager.py`
- `bot_app/agent/tool_registry.py`
- `bot_app/tools/knowledge.py`
- `bot_app/tools/knowledge_service.py`

从代码结构上看，主链路已经切到：

- `AgentRunner`
- 原生 tool calling
- knowledge 工具两段式调用

即：

1. `search_game_knowledge`
2. `read_game_knowledge_file`

随后再由模型组织最终回答。

### 2. 运行时行为存在口径不一致风险

虽然当前仓库入口已使用 `AgentRunner`，但线上运行日志中仍可见大量旧风格轨迹，例如：

- `purpose = agent_tool_calling`
- `stage = agent.llm_response`

而不是仓库里当前更明确的：

- `agent_planning`
- `agent_rendering`
- `agent.plan_result`
- `agent.render_result`

这意味着至少存在以下一种风险：

- 部署环境未同步到当前代码。
- 线上运行的是旧分支或旧插件包。
- 当前读取的日志目录与当前仓库入口并非同一实例。

在知识命中率治理中，这一风险必须优先澄清，否则后续优化可能无法真实落到线上。

### 3. 知识库实现现状

当前知识库核心位于：

- `btd6_knowledge_base/kb_interface.py`
- `btd6_knowledge_base/search_framework.py`
- `btd6_knowledge_base/search_index.db`
- `btd6_knowledge_base/wiki_data_second_cleaned/btd6`
- `btd6_knowledge_base/aliases.json`

当前实现的主要特点：

- 使用本地 SQLite FTS5 索引。
- 索引文本来自二次清洗后的 HTML 语料。
- 查询支持 alias 扩展。
- 查询支持 FTS + substring fallback。
- 文件读取支持长文按关键词抽取文本块。

这是一个成本较低、部署简单、便于本地运维的方案，作为 BTD6 中型静态知识库的基础方案是合理的。

### 4. 当前检索仍以词面匹配为主

`search_framework.py` 的本质仍是：

- 词面拆分
- alias 扩展
- FTS5 匹配
- fallback substring search
- title/path/content 排序

当前还不具备真正的语义检索或领域结构化检索能力。

这导致以下问题更突出：

- 用户使用口语问法时，召回不稳定。
- 同一问题换一种表达方式，命中率可能显著下降。
- 对“机制推断型问题”召回较弱，例如：
  - `能不能抓 DDT`
  - `路线3数据`
  - `下路详细数值`

### 5. query 清洗和归一化能力明显不足

当前 `knowledge_service.py` 中的 `sanitize_kb_query()` 主要做的是通用弱词清洗，例如移除：

- `详情`
- `介绍`
- `如何`
- `有关`
- `相关`

但当前尚未完成 BTD6 领域归一化，缺失内容包括：

- 路线表达归一化：
  - `路线3` -> `下路`
  - `路线1` -> `上路`
  - `路线2` -> `中路`
- 升级表达归一化：
  - `004` -> `0-0-4`
- 塔与英雄别名归一化：
  - `野兽之主` -> `Beast Handler`
  - `人鱼猴` -> `Mermaid Monkey`
- 机制表达归一化：
  - `抓 DDT` -> `DDT 交互/能否攻击/目标`
- 数据表达归一化：
  - `数据` -> `价格 伤害 穿透 攻速 射程`

因此当前 query rewrite 过弱，无法充分把用户问题翻译成适合索引命中的查询。

### 6. alias 覆盖范围偏薄

当前 `aliases.json` 和 `search_framework.py` 内置 alias 仅覆盖了少量：

- 英雄中英文对应
- 少数塔名缩写

尚未覆盖高频 BTD6 玩家表达：

- 路线俗称
- 升级数字表达
- 新塔/新英雄别名
- 机制关键词变体
- 常见中英混输

这会导致召回质量对“用户措辞是否刚好贴近页面标题”高度敏感。

### 7. 工具编排现状：两段式方向正确，但约束不够硬

当前 knowledge 工具设计为两段式：

1. 先搜候选文件路径。
2. 再读候选文件内容。

这个设计本身是合理的，因为它：

- 限制了一次性交给模型的上下文量。
- 允许模型二次选择最合适的候选页。
- 允许长文只抽取相关块。

但当前问题在于：工具编排是否继续依赖证据，仍然由模型自由决定。

表现为：

- 检索失败后，模型可能直接凭内部记忆回答。
- 检索成功后，模型未必稳定继续读取候选文件。
- 缺少“知识型问题必须证据化输出”的硬约束。

### 8. 线上样本显示：命中问题与证据消费问题并存

近期日志中至少可以观察到三种典型情况。

#### Case A：`人鱼猴数据`

现象：

- 模型先判断需要搜索知识库。
- `search_game_knowledge` 返回 `matched_count = 5`。
- 第一个结果就是 `人鱼猴.html`。

结论：

- 该类问题并非“完全搜不到”。
- 当前更大的问题是：即使已召回正确页面，后续链路是否稳定消费证据、是否多绕一轮、是否最终按证据输出。

#### Case B：`004野兽之主能不能抓DDT`

现象：

- 搜索 query 为：`野兽之主 004 逆戟鲸 DDT 能否攻击`
- `matched_count = 0`
- 随后模型直接基于自身记忆输出结论

结论：

- 这是典型的“检索未命中 + 模型自由发挥补答”。
- 即使最终结论未必错，这种做法仍会破坏知识型回答的可信度治理。

#### Case C：`野兽之主下路详细数据` / `野兽之主数据`

现象：

- 更具体的问法曾出现“没找到”。
- 更泛化的问法后续又能答出一整段数据。

结论：

- 当前系统对 query 表达形式高度敏感。
- 问题既包括召回不稳定，也包括回答链路不稳定。

### 9. 线上可观测性不足

当前虽然已有 flow log，但对知识命中率治理而言仍缺关键指标：

- 缺少知识型问题总量统计。
- 缺少 `matched_count` 分布看板。
- 缺少“检索成功但未读文件”的统计。
- 缺少“检索失败后模型直接作答”的统计。
- 缺少“最终回答是否 grounded”的标记。

这意味着当前只能靠人工翻日志判断问题，缺少持续优化所需的量化闭环。

## 结论

当前方案的基础路线是合理的：

- 本地知识库
- FTS5 检索
- 两段式工具
- 再由模型组织回答

但当前不合理之处在于：

- 部署口径与代码口径可能不一致。
- 领域 query rewrite 不够。
- alias 覆盖太薄。
- 缺少段落级或结构化索引。
- 工具链路没有强制证据消费。
- 检索失败时允许模型自由发挥。
- 缺少线上命中率和 groundedness 指标。

## 总体实施策略

本次治理遵循以下原则。

### P1. 先统一口径，再谈优化

先确认线上到底跑的是哪套代码、哪份日志、哪份数据库和哪份配置。

### P2. 先提升“可解释性”，再提升“智能度”

优先让系统明确区分：

- 命中
- 未命中
- 基于证据回答
- 无证据兜底

先减少黑盒行为，再谈更复杂的智能化增强。

### P3. 先补领域归一化，再考虑更重的检索体系

在 BTD6 这类规模的知识库里，优先做：

- query rewrite
- alias 扩展
- 结构化标签
- 段落级索引

只有这些做完后仍明显不足，再考虑向量检索或混合检索。

### P4. 先稳定证据消费，再优化文风

知识型问题优先保证回答来源可信，而不是先追求“像群友一样自然”。

### P5. 每阶段都必须可量化、可回退、可观察

每阶段改动都要：

- 有明确日志
- 有明确指标
- 能做前后对比
- 能在必要时快速回退

## 分阶段计划

---

## Phase 0：部署口径统一与观测基线

### 目标

在改知识检索链路前，先确认线上实例的真实运行口径，并建立知识问答的观测基线。

### 实现方式

1. 核对线上实际使用的：
   - `plugin.py`
   - `.env`
   - `BOT_DB_FILE`
   - `BOT_FLOW_LOG_FILE`
   - 插件目录
2. 明确当前线上到底是：
   - 新 `AgentRunner` 链路
   - 还是旧 `agent_tool_calling` 兼容链路
3. 对知识型问题补充统一日志字段，至少包括：
   - `knowledge_query`
   - `matched_count`
   - `matched_paths`
   - `read_count`
   - `answer_grounded`
   - `fallback_reason`
4. 统计近 7 天或近 100 条知识型问题，建立基线。

### 本阶段产出

- 线上运行口径确认结论。
- 一份基线样本集。
- 一组初始指标基线。

### 验收标准

- 能准确说明线上跑的是哪套知识回答链路。
- 能从日志中区分：
  - 只搜索未读
  - 搜索并读取
  - 未命中后直接作答
  - 未命中后明确说明失败
- 能输出当前知识问答基线统计。

### 当前状态

- 状态：已完成（仓库侧 + 本地运行日志侧）
- 说明：已确认真实运行环境里同时存在旧 `agent_tool_calling` 日志和新 `AgentRunner` 日志；当前知识治理应以新链路为主继续推进，同时把旧链路视为待清理兼容路径。

### 2026-06-24 阶段进展

本轮已先完成仓库侧口径核对和第一批观测字段补齐。

#### 已确认的仓库口径

1. 插件加载入口仍是 `plugin.py`。
2. `plugin.py` 在 `on_load()` 中会初始化：
   - `GroupMessageReceiver`
   - `ToolRuntime`
   - `ToolRegistry`
   - `AgentRunner`
3. 默认情况下，插件会强制把以下路径落到插件目录下：
   - `BOT_FLOW_LOG_FILE -> plugins/btd6bot_plugin/logs/network.log`
   - `BOT_DB_FILE -> plugins/btd6bot_plugin/data/bot_data.db`
4. 当前仓库内 Agent 主链路已经是：
   - planning: `purpose=agent_planning`
   - rendering: `purpose=agent_rendering`
   - flow stage: `agent.plan_result`
   - flow stage: `agent.tool_result`
   - flow stage: `agent.render_result`
5. 仓库内没有发现仍主动发出 `purpose=agent_tool_calling` 的当前实现代码。

#### 本轮新增观测

为后续基线统计和 Phase 1 证据化收紧做准备，本轮已在仓库中补充知识链路的结构化观测字段：

1. `search_game_knowledge` 结果新增：
   - `knowledge_query`
   - `knowledge_query_raw`
   - `matched_count`
   - `matched_paths`
2. `read_game_knowledge_file` 结果新增：
   - `content_chars`
3. `AgentRunner` 在知识工具链路结束后新增统一日志摘要：
   - stage: `agent.knowledge_summary`
   - 字段包括：
     - `knowledge_query`
     - `knowledge_query_raw`
     - `matched_count`
     - `matched_paths`
     - `read_count`
     - `read_paths`
     - `successful_read_count`
     - `successful_read_paths`
     - `answer_grounded`
     - `fallback_reason`
     - `content_chars`
4. 当前 `fallback_reason` 已能区分至少以下几类：
   - `knowledge_search_failed`
   - `knowledge_not_found`
   - `search_hit_but_not_read`
   - `knowledge_read_failed`

#### 当前结论

Phase 0 在仓库内已拿到两个关键结论：

1. 代码口径本身已经是新链路，SOP 中提到的旧日志口径，大概率来自部署未同步、插件目录混用，或读取了错误日志文件。
2. 之前缺的不是基础 flow log，而是知识问答专用摘要字段；这一层现已补上，后续可以直接基于 `agent.knowledge_summary` 做基线统计。

补充结论（基于本地运行日志）：

3. 真实运行日志已经证明：当前环境中确实同时存在两类口径：
   - 旧链路：`purpose=agent_tool_calling`、`stage=agent.llm_response`
   - 新链路：`purpose=agent_planning` / `purpose=agent_rendering`、`stage=agent.plan_result` / `agent.render_result`
4. 新链路下最典型的失效模式不是“搜索完全失效”，而是：
   - planning 把能力类别名 `game_knowledge` 当成工具名输出
   - 解析器丢弃无效 tool call
   - decision 从 `call_tools` 退化为 `final`
   - rendering 在没有任何工具结果时自由发挥
5. 因此，Phase 1 必须优先补的是“知识问题 hard rule”，而不是只继续扩 alias。

#### 尚未完成

以下事项仍建议继续做，但已不阻塞进入 Phase 1：

1. 进一步确认线上是否还保留旧 `agent_tool_calling` 兼容入口。
2. 在真实 `agent.knowledge_summary` 日志积累起来后，补首版基线统计：
   - `knowledge_query_count`
   - `matched_count` 分布
   - `search_hit_but_not_read` 占比
   - `answer_grounded` 占比
   - `knowledge_not_found` 占比

---

## Phase 1：知识型回答证据化收紧

### 目标

把“知识型问题必须优先基于知识库证据回答”从软约束变成硬约束。

### 实现方式

1. 在 planning 或工具编排层增加知识类 hard rule：
   - 若识别为 `game_knowledge`，必须先调用 `search_game_knowledge`
2. 若 `matched_count > 0`：
   - 必须至少调用一次 `read_game_knowledge_file`
   - 不允许直接跳过文件读取进入最终回答
3. 若 `matched_count = 0`：
   - 默认明确说明知识库未命中
   - 不允许模型直接给出事实性定论
4. 仅在明确标记为“经验性补充”且用户接受不确定性时，才允许非知识库补答
5. 在 rendering 提示词中强调：
   - 知识型回答优先依据 `content`
   - 没有证据时不要编造

### 本阶段产出

- 知识型问题的强制证据消费链路。
- 检索失败后的统一兜底规范。
- 对应日志字段和测试。

### 验收标准

- 知识型问题中，“搜到但不读文件”的比例显著下降。
- “检索失败后直接自由发挥”的比例降到可控范围。
- 日志中能明确区分 grounded 和 ungrounded 的回答。

### 当前状态

- 状态：进行中
- 说明：已根据真实运行日志进入第一轮落地，优先修正“知识问题未真正调用工具就直接 final”这一失效模式。

### 2026-06-24 阶段进展

根据真实 case 日志，本轮已完成第一批 Phase 1 收紧：

1. 修正 planning 提示词，明确：
   - `tool_calls.name` 必须使用真实工具名
   - 不允许输出 `game_knowledge`、`game_news` 这类能力类别名
2. 修正 `router_tool_hints.md`，把能力映射改成真实工具：
   - `search_game_knowledge`
   - `read_game_knowledge_file`
   - 以及其他实际工具名
3. 在 `AgentRunner` 中新增知识问题 hard rule：
   - 若判定为知识问题且尚未执行 `search_game_knowledge`，强制补一次搜索
   - 若 `matched_count > 0` 且尚未执行 `read_game_knowledge_file`，强制补一次读取
   - 防止模型因为无效 tool call 或过早 `final` 直接跳过证据消费

### 已确认的真实失效模式

从本轮日志可明确看到以下问题已在线上真实出现：

1. 用户问 `人鱼猴数据` / `野兽之主数据` / `004野兽之主能不能抓DDT`
2. planning 的 `reason` 明确说“需要调用 game_knowledge 工具”
3. 但输出的工具名是 `game_knowledge`，不是实际注册工具名
4. 解析后 `tool_calls` 为空
5. `decision=call_tools` 被退化成 `final`
6. rendering 在 `tool_results=[]` 的情况下直接生成回答

这说明当前首要问题不是模型不会判断“该查知识库”，而是：

- 提示词把“能力类别名”与“真实工具名”混用了
- 缺少运行时硬约束兜底

### 当前结论

Phase 1 第一轮已经把最危险的一类问题收紧：

1. 现在模型即使还输出 `game_knowledge` 这类无效类别名，运行时也会对知识问题强制补 `search_game_knowledge`。
2. 搜索命中后，如果模型没有主动继续读文件，运行时也会强制补 `read_game_knowledge_file`。
3. 这能显著降低“知识问题没有任何证据却直接 final”的比例。

### 下一步

Phase 1 仍未完成，下一轮继续补：

1. 若 `matched_count = 0`，rendering 提示词再收紧，明确禁止凭印象补事实性结论。
2. 对 `answer_grounded` / `fallback_reason` 做真实日志回归统计。
3. 在此基础上再推进 Phase 2 的 BTD6 query rewrite 和 alias 扩展。

---

## Phase 2：BTD6 领域 query rewrite 与 alias 扩展

### 目标

提升用户口语问法、俗称问法和混合问法的召回稳定性。

### 实现方式

1. 扩展 `aliases.json`，至少覆盖：
   - 路线表达
   - 升级数字表达
   - 新塔/新英雄中英文别名
   - 常见机制简称
2. 在知识 query 预处理层增加领域归一化，至少包括：
   - `路线1/2/3` <-> `上/中/下路`
   - `004` -> `0-0-4`
   - `数据` -> `价格 伤害 穿透 攻速 射程`
   - `抓 DDT` -> `DDT 能否攻击/交互`
3. 为常见问题模板增加扩展词：
   - `详细数据`
   - `能不能打`
   - `能不能抓`
   - `属性`
   - `机制`
4. 保持 rewrite 过程可追踪：
   - 原 query
   - 归一化 query
   - 扩展 query

### 本阶段产出

- 扩展版 alias 词表。
- BTD6 领域 query rewrite 模块。
- 对应单测和线上对照样本。

### 验收标准

- 真实样本中，同义表达的召回差异明显缩小。
- `路线3数据`、`下路数据`、`004 数据` 这类问题的召回更稳定。
- 样本集上的 `search_hit_rate` 明显提升。

### 当前状态

- 状态：待开始
- 说明：这是提升命中率的核心阶段，优先级仅次于 Phase 1。

---

## Phase 3：段落级与结构化索引建设

### 目标

减少“搜到页面但抓不到关键答案”的情况，让检索从页面级逐步过渡到段落级或结构字段级。

### 实现方式

1. 在现有页面级索引之外，增加段落级索引：
   - 按标题、小节、表格行、段落块切分
2. 提取结构化字段，视页面情况至少包含：
   - `title`
   - `path`
   - `tower_or_hero`
   - `upgrade_path`
   - `upgrade_tier`
   - `tags`
   - `mechanics`
3. 对常见数据类问题优先查询结构字段：
   - 价格
   - 伤害
   - 穿透
   - 攻速
   - 射程
4. 对长文提取逻辑进行适配：
   - 优先返回命中的小节或段落
   - 减少整页大块文本截断

### 本阶段产出

- 段落级索引方案。
- 结构化字段抽取方案。
- 针对知识页读取的更细粒度结果格式。

### 验收标准

- “已命中但回答仍然空泛”的情况下降。
- `read_game_knowledge_file` 返回的内容更贴近用户问题。
- 长文中关键信息的抽取质量提高。

### 当前状态

- 状态：待开始
- 说明：本阶段是命中率提升后的第二层优化，主要解决召回后消费质量问题。

---

## Phase 4：知识问答评测集与指标闭环

### 目标

把“感觉命中率低”转成可以持续跟踪的客观指标。

### 实现方式

1. 从近期消息和日志中整理真实知识问句样本，建议 50 到 100 条。
2. 对样本人工标注：
   - 是否应命中
   - 正确候选页
   - 是否需要多跳读取
   - 正确回答是否必须引用知识库
3. 建立指标，至少包括：
   - `knowledge_query_count`
   - `search_hit_rate`
   - `first_doc_success_rate`
   - `grounded_answer_rate`
   - `no_result_rate`
   - `ungrounded_fallback_rate`
4. 在每阶段结束后跑一次回归评测。

### 本阶段产出

- 一份真实知识问答评测集。
- 一套固定指标。
- 一次阶段回归结果模板。

### 验收标准

- 后续每轮优化都能通过同一批样本回归验证。
- 不再只依赖体感判断命中率好坏。

### 当前状态

- 状态：待开始
- 说明：没有这套闭环，后续优化难以长期收敛。

---

## Phase 5：混合检索可行性评估

### 目标

在完成规则化增强后，再判断是否需要引入更重的检索体系。

### 实现方式

1. 基于 Phase 4 指标评估当前上限。
2. 若 FTS + rewrite + 段落索引后仍存在明显短板，再评估：
   - embedding 召回
   - FTS + rerank 混合排序
   - 轻量语义检索
3. 不直接替换现有 FTS5，而优先评估：
   - FTS5 召回
   - embedding rerank
   - 结构化字段优先

### 本阶段产出

- 是否需要混合检索的结论。
- 若需要，则形成单独方案文档。

### 验收标准

- 能基于指标而非直觉决定是否引入更复杂方案。
- 若不需要，也能明确说明当前静态方案上限已经足够。

### 当前状态

- 状态：待开始
- 说明：这是后置阶段，不应在前面基础治理未完成前提前启动。

## 近期优先级结论

当前最优先的事情不是换模型，也不是直接上向量库，而是按以下顺序推进：

1. Phase 0：统一线上运行口径和观测基线。
2. Phase 1：收紧知识型回答的证据化约束。
3. Phase 2：补齐 BTD6 领域 query rewrite 和 alias。
4. Phase 3：建设段落级和结构化索引。
5. Phase 4：建立评测集与指标闭环。
6. Phase 5：再决定是否需要混合检索。

## 风险清单

### R1. 线上实例口径不统一

若线上运行代码与当前仓库不同，后续优化可能无法直接体现到线上。

### R2. alias 词表无序膨胀

如果只靠不断堆别名，可能引入维护成本和误召回问题。

### R3. 过度依赖提示词修补

若不在工具层和数据层加约束，只靠提示词要求 grounded，很难长期稳定。

### R4. 段落切分质量不稳

若段落切分不合理，可能导致召回更碎、上下文丢失。

### R5. 评测集覆盖不足

若样本集只覆盖少数热门问法，优化结果可能对真实线上问题代表性不足。

## 更新规则

后续推进本主题时，建议遵循以下更新规则：

1. 每完成一个 phase，优先更新本文：
   - 当前状态
   - 核心改动
   - 验收结果
   - 残留问题
2. 若单个 phase 改动较大，可新增配套文档，例如：
   - `2026-06-xx_knowledge_query_rewrite_phase1.md`
   - `2026-06-xx_knowledge_eval_dataset_phase4.md`
3. 配套文档创建后，本文仍需补一段摘要，保持主追踪入口唯一。

## 当前状态摘要

截至 2026-06-24：

- 已完成：
  - 知识命中率问题代码审计
  - 近期真实样本抽样
  - 知识库实现与模型调用方式评估
  - 本 SOP 建立
  - 仓库内运行口径核对
  - 知识链路结构化观测字段第一轮补齐
- 尚未完成：
  - 线上口径统一
  - grounded hard rule 落地
  - BTD6 query rewrite
  - 段落级索引
  - 评测集与指标闭环

当前判断：

- 基础知识库路线可保留。
- 首要矛盾是“检索和回答链路不够收敛”，不是单纯模型能力不足。
- 下一步应从证据化约束和领域归一化开始推进。
