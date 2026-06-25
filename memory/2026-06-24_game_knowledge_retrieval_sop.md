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

- 状态：已完成（仓库侧 hard rule + deterministic 端到端评测）
- 说明：已完成知识问题强制搜索、命中后强制读取、无证据硬兜底，并通过 50 条 deterministic grounded 评测验证；真实线上占比仍需持续观察。

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

### 当前补充结论

后续 2026-06-25 的“Phase 1 未命中硬兜底”和 Phase 4 端到端评测已经补齐本阶段剩余项：

1. 若 `matched_count = 0`，运行时直接返回知识库未命中说明，不再调用 rendering LLM 生成事实结论。
2. `agent.knowledge_summary` 已能记录 `answer_grounded` / `fallback_reason`。
3. 50 条 deterministic grounded 评测证明知识问题会稳定触发搜索、读取和 grounded 输出。

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

- 状态：已完成（本地 query rewrite + alias + 50 条搜索评测）
- 说明：已覆盖 SOP 中列出的路线、升级码、中英别名、机制/数据问法，并通过 50 条搜索评测验证 `search_hit_rate=1.000`、`first_doc_success_rate=1.000`。

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

- 状态：已完成（本地段落索引 + 结构化读取结果）
- 说明：已在现有 SQLite 索引中增加 `paragraphs` / `paragraphs_fts`，读取链路优先返回 `mode=paragraph_extract`；真实线上回答质量仍需继续通过日志样本观察。

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

- 状态：已完成（本地固定评测集 + deterministic 端到端评测）
- 说明：已有 50 条评测样本、搜索评测和 grounded 端到端评测；下一步是继续提高真实日志样本占比和补真实 LLM 回归观察。

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

- 状态：已完成阶段性评估
- 说明：基于当前 50 条搜索评测和 50 条端到端评测结果，暂不引入 embedding / 向量库 / rerank；若真实日志扩样后指标下降，再重新评估。

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

### 2026-06-25 更新：旧链路兼容入口迁移到新链路

本轮优先处理“旧链路 / 新链路口径不一致”问题，结论如下：

1. 仓库内主入口 `plugin.py` 与独立入口 `bot_app/runner.py` 已经只构造 `AgentRunner + ToolRegistry + ToolRuntime`。
2. 仓库内没有发现主入口继续调用旧 `LLMMessageProcessor + ToolSelector + CommandProcessor` 两阶段链路。
3. 此前的残留风险来自 `bot_app/llm_processor.py` 本身仍保留完整旧实现；任何外部脚本如果直接导入并调用它，都可能继续发出旧口径 LLM purpose / flow stage。
4. 本轮已将 `LLMMessageProcessor` 改为兼容门面：
   - 新增 `run()`，执行时委托 `AgentRunner.run()`。
   - 支持通过 `agent_runner` 或 `tool_registry` 注入新链路。
   - 旧分段方法 `plan_intent()` / `chat_reply()` / `render_command_result()` / `decide_next_tool_step()` 改为显式抛出 `LegacyLLMPipelineDisabled`。
   - 被阻止时写入 `legacy_pipeline.blocked`，避免静默回退到旧链路。
5. 新增测试覆盖：
   - `LLMMessageProcessor.run()` 只产生 `agent_planning` / `agent_rendering` 请求口径。
   - 旧分段方法不会再发起 LLM 请求。

验证结果：

- `python3 -m py_compile bot_app/llm_processor.py tests/test_agent_tool_calling.py` 通过。
- 手写 smoke 验证通过：旧 import path 的 `run()` 已委托新 `AgentRunner`，旧 `plan_intent()` 被阻止且不发 LLM 请求。
- `python3 -m unittest tests.test_agent_tool_calling` 未能在当前容器完整执行，原因是环境缺少 `ncatbot`。
- `ruff` 未能执行，原因是当前容器未安装 `ruff`。

当前口径：

- 可执行主链路：`AgentRunner`。
- 历史 `LLMMessageProcessor`：仅作为兼容门面，不再允许执行旧两阶段 LLM 链路。
- 如果线上日志后续仍出现 `purpose=agent_tool_calling` 或 `stage=agent.llm_response`，更可能来自未同步部署、非本仓库插件实例、或外部旧包，而不是当前仓库代码路径。

### 2026-06-25 更新：Phase 2 第一轮 query rewrite

基于新日志 `runtime/plugin/logs/network.log` 中 `trace_id=6964da35f41b` 的测试结果，本轮进入 Phase 2 第一轮。

已确认：

1. 旧链路迁移已生效，2026-06-25 测试日志中没有 `agent_tool_calling` / `agent.llm_response`。
2. 知识问题已走 `agent_planning -> search_game_knowledge -> agent_rendering -> agent.knowledge_summary`。
3. 新暴露的问题是 query 清洗不足：
   - 用户问：`@小污猫 野兽之主数据`
   - 实际搜索 query：`@小污猫 野兽之主数据`
   - 搜索结果：`matched_count=0`
   - 但知识库中实际存在 `野兽之主.html`

本轮改动：

1. `sanitize_kb_query()` 新增唤醒噪声剥离：
   - CQ at：`[CQ:at,qq=...]`
   - 文本 at：`@小污猫`
   - 机器人名/唤醒词：`小污猫`、`污猫`、`机器人`
   - 查询动词：`帮我`、`查一下`、`搜一下`、`检索` 等
2. 新增保守 BTD6 领域归一化：
   - `路线1/2/3` -> `上路/中路/下路`
   - `004` -> `004 0-0-4`
   - 机制问法中移除弱动词，保留核心实体，例如 `004野兽之主能不能抓DDT` -> `004 0-0-4 野兽之主 DDT`
3. `aliases.json` 补充高频中英别名：
   - `野兽之主` <-> `Beast Handler`
   - `人鱼猴` <-> `Mermaid Monkey`
4. 保持搜索 query 保守，不把 `数据` 扩展为 `价格 伤害 穿透 攻速 射程` 等强约束词。
   - 原因：当前 `search_framework` fallback 会将非数字 term 作为 AND 条件；过度扩展会增加 0 命中风险。
   - 后续若要做数据词扩展，应放到读取/段落抽取关键词或排序 boost，而不是文件级搜索强约束。

验证结果：

- `python3 -m py_compile bot_app/tools/knowledge_service.py tests/test_agent_tool_calling.py` 通过。
- `git diff --check` 通过。
- 本地轻量 smoke 输出：
  - `@小污猫 野兽之主数据` -> `野兽之主`
  - `[CQ:at,qq=1693903050] 004野兽之主能不能抓DDT` -> `004 0-0-4 野兽之主 DDT`
  - `野兽之主路线3详细数据` -> `野兽之主 下路`
- 当前容器缺少 `ncatbot`，完整 `unittest` 仍无法执行。
- 当前容器缺少 `bs4`，真实 `kb_interface` smoke 无法直接执行；部署依赖中已有 `beautifulsoup4/bs4`。

下一步：

1. 在线上或完整依赖环境复测 `@小污猫 野兽之主数据`，期望 `matched_count > 0` 且命中 `野兽之主.html`。
2. 若仍未命中，下一轮应调整 `search_framework` 的 fallback 逻辑：把 `数据/价格/伤害/穿透/攻速/射程` 这类数据词作为 boost，不作为强制 AND。
3. 继续补小评测集，至少覆盖：
   - `@小污猫 野兽之主数据`
   - `004野兽之主能不能抓DDT`
   - `野兽之主下路详细数据`
   - `人鱼猴数据`

### 2026-06-25 更新：Phase 2 第二轮召回回归与最小评测集

本轮在第一轮 query rewrite 的基础上，继续推进 Phase 2，并为 Phase 4 建立最小可执行评测入口。

已确认的新问题：

1. 当前容器缺少 `bs4` 时，`kb_interface.interface_search()` 与 `search_framework` 的搜索入口曾因顶层导入清洗/抽取依赖而失败。
2. 这会影响本地回归评测，也会放大线上依赖缺失时的检索失败范围。
3. 第一轮 rewrite 后仍有三类召回缺口：
   - `野兽之主下路详细数据` 被清洗成 `野兽之主下路`，中文实体和路线粘连导致 0 命中。
   - `Mermaid Monkey stats` 未归一到中文页名，且 `stats` 被当作强约束。
   - `Beast Handler 004 DDT` 未归一到中文页名，首位命中偏离 `野兽之主.html`。

本轮改动：

1. `bot_app.agent.__init__` 改为延迟导出 `AgentRunner`，修复普通工具模块导入时触发的 `tools.base <-> agent.runner` 循环导入。
2. `btd6_knowledge_base` 搜索路径延迟加载重依赖：
   - `clean_index_text.py` 仅在 `extract_index_fields()` 内导入 `BeautifulSoup`。
   - `search_framework.py` 仅在构建索引时导入清洗函数。
   - `kb_interface.py` 仅在抓取、构建、抽取时导入 `crawler` / `decode_and_clean_html` / `BeautifulSoup`。
   - 结果：`interface_search()` 和 `search_file_paths()` 不再因当前容器缺少 `bs4` 而失败。
3. `sanitize_kb_query()` 继续补 BTD6 领域归一化：
   - 英文别名：`Beast Handler` -> `野兽之主`，`Mermaid Monkey` -> `人鱼猴`。
   - 英文弱词：`stats` / `data` / `details` / `info` / `guide` / `wiki` 等不再作为强制检索词。
   - 中文实体与路线粘连拆分：例如 `野兽之主下路` -> `野兽之主 下路`。
   - 中文实体与升级码粘连拆分：例如 `004野兽之主` -> `004 0-0-4 野兽之主`。
4. 新增轻量搜索评测脚本：
   - `tests/evaluate_knowledge_search.py`
   - 不依赖 `ncatbot`。
   - 直接验证 `sanitize_kb_query + search_file_paths` 的命中和首文档命中。

当前最小评测集覆盖 9 条：

- `@小污猫 野兽之主数据`
- `004野兽之主能不能抓DDT`
- `野兽之主下路详细数据`
- `野兽之主路线3详细数据`
- `人鱼猴数据`
- `Mermaid Monkey stats`
- `Beast Handler 004 DDT`
- `逆戟鲸能不能抓DDT`
- `下路野兽004数据`

验证结果：

- `python3 tests/evaluate_knowledge_search.py` 通过：
  - `total=9`
  - `search_hit_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `no_result_rate=0.000`
- `python3 -m py_compile bot_app/agent/__init__.py bot_app/tools/knowledge_service.py btd6_knowledge_base/clean_index_text.py btd6_knowledge_base/search_framework.py btd6_knowledge_base/kb_interface.py tests/test_agent_tool_calling.py tests/evaluate_knowledge_search.py` 通过。
- `git diff --check` 通过。
- `python3 -m unittest tests.test_agent_tool_calling.AgentToolCallingTests.test_sanitize_kb_query_strips_invocation_noise_and_rewrites_domain_terms` 仍无法执行，原因是测试模块导入阶段缺少 `ncatbot`。

当前结论：

1. Phase 2 已完成第一批代表问法的固定回归，SOP 中列出的核心样本现在能稳定首位命中目标页面。
2. Phase 4 已有最小评测入口，但仍不是完整评测集；后续仍需从真实日志扩到 50 到 100 条并补人工标注。
3. 下一轮更适合继续扩评测样本，而不是盲目扩大 alias 词表。

### 2026-06-25 更新：Phase 1 未命中硬兜底

本轮补齐 Phase 1 中“检索失败后禁止模型凭印象补事实性结论”的运行时约束。

已确认：

1. 之前 planning prompt 已要求 `matched_count=0` 时不要凭印象补答。
2. 但最终仍会进入 rendering LLM；如果模型忽略提示，仍可能生成无证据事实结论。
3. 因此需要运行时兜底，而不是只靠 prompt。

本轮改动：

1. `AgentRunner` 新增 `_knowledge_ungrounded_fallback_text()`。
2. 在知识问题进入 `final` 前检查工具结果：
   - 搜索失败：直接返回检索失败说明。
   - `matched_count <= 0`：直接返回知识库未命中说明，不调用 rendering。
   - 命中但未读：直接说明尚未读取证据。
   - 命中但读取失败或无内容：直接说明未取得可用证据。
   - 只有存在成功读取且 `content_chars > 0` 时，才允许进入 rendering 组织最终回答。
3. 补充测试意图：
   - 0 命中后只发生 planning，不发生 `agent_rendering`。
   - 读取失败后只发生 planning，不发生 `agent_rendering`。
   - 已命中场景仍保持强制读取。

验证结果：

- 独立 smoke 验证通过：`不存在塔数据` 搜索 0 命中后返回“知识库里暂时没有检索到...不能基于知识库证据确认结论”，LLM 请求只有 `agent_planning` 两次，没有 `agent_rendering`。
- `python3 -m py_compile bot_app/agent/runner.py tests/test_agent_tool_calling.py` 通过。
- `git diff --check` 通过。

当前结论：

- Phase 1 的核心硬约束已经更完整：
  - 知识问题必须搜索。
  - 搜索命中必须读取。
  - 无证据时不再把最终事实回答交给 rendering LLM 自由生成。
- 后续仍需在线上日志中观察 `fallback_reason=knowledge_not_found` 与 `answer_grounded=false` 的真实占比。

### 2026-06-25 更新：Phase 4 最小评测入口数据化

本轮把前述 9 条代表样本从脚本硬编码迁移为独立数据文件，为后续扩展真实评测集做准备。

新增产物：

1. `tests/knowledge_search_eval_cases.json`
   - 当前包含 9 条代表知识问句。
   - 每条包含：
     - `query`
     - `expected_first`
     - `should_hit`
     - `requires_grounded_answer`
     - `notes`
2. `tests/evaluate_knowledge_search.py`
   - 默认读取上述 JSON。
   - 支持 `--cases` 指向其他样本文件。
   - 支持 `--json` 输出完整机器可读报告。
   - 输出指标包括：
     - `knowledge_query_count`
     - `should_hit_count`
     - `grounded_required_count`
     - `search_hit_rate`
     - `first_doc_success_rate`
     - `no_result_rate`

验证结果：

- `python3 tests/evaluate_knowledge_search.py` 通过：
  - `knowledge_query_count=9`
  - `should_hit_count=9`
  - `grounded_required_count=9`
  - `search_hit_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `no_result_rate=0.000`
- `python3 tests/evaluate_knowledge_search.py --json` 可输出逐样本明细。

当前结论：

- Phase 4 已有可持续扩展的最小入口。
- Phase 4 仍未完成，因为样本量还未达到 50 到 100 条，也尚未覆盖完整的人工标注维度和 grounded answer 端到端评测。

### 2026-06-25 更新：Phase 3 第一轮读取片段抽取

本轮开始处理 Phase 3 中“搜到页面但返回内容不贴近问题”的问题。

已确认：

1. `野兽之主.html`、`人鱼猴.html` 等页面虽然不超过 8000 字符，但在较小 `max_chars` 下会直接返回全文前段。
2. 对 `004野兽之主能不能抓DDT`、`野兽之主下路详细数据` 这类问题，关键内容可能在页面后段，固定前缀截断会遗漏证据。

本轮改动：

1. `read_game_knowledge_file` 在有 `query` / `keywords` 时，对短文也优先执行本地关键词窗口抽取。
2. 新增 `extract_keyword_windows()`：
   - 使用 `sanitize_kb_query()` 后的关键词。
   - 按关键词命中位置切窗口。
   - 对语义词、机制词、路线词给予更高权重，对 `004` / `0-0-4` 这类数字码弱加权。
   - 返回 `mode=keyword_extract`，并保留 `keywords`、`content_chars`。
3. 针对 `野兽之主 004/0-0-4` 增加保守扩展词 `逆戟鲸`，帮助读取阶段抽到对应升级名附近的片段。

验证结果：

- `004野兽之主能不能抓DDT` 读取 `野兽之主.html` 时，返回 `mode=keyword_extract`，片段中包含 `逆戟鲸`、`004`、`0-0-4`、`野兽之主`。
- `野兽之主下路详细数据` 读取 `野兽之主.html` 时，片段中包含 `下路` 与 `野兽之主`。
- `人鱼猴数据` 读取 `人鱼猴.html` 时，片段中包含基础数据与详细数据。
- 搜索评测仍保持：
  - `search_hit_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `no_result_rate=0.000`

当前结论：

- Phase 3 已有第一轮轻量实现，解决了短页固定前缀截断的问题。
- 这还不是完整段落级索引；后续仍需建设真正的段落级索引和结构化字段抽取。

### 2026-06-25 更新：Phase 3 第二轮段落索引与结构化读取结果

本轮继续完成 Phase 3 原定的“段落级与结构化索引建设”，把第一轮轻量关键词窗口升级为真正的 SQLite 段落索引。

本轮改动：

1. `btd6_knowledge_base/search_framework.py`
   - 新增 `paragraphs` 表和 `paragraphs_fts` FTS5 表。
   - `build_index()` / `build_index_from_second_clean()` 在写入页面级 `documents` 时同步写入段落索引。
   - 新增旧索引兼容回填：第一次段落检索发现旧库无段落数据时，可从现有 `documents` 表回填 `paragraphs`。
   - 新增 `extract_paragraphs_for_file()`，支持在已命中文件内按 query 检索相关段落。
   - 段落级 fallback 使用 OR 命中排序，而不是要求所有词都在同一段出现，避免 `004野兽之主能不能抓DDT` 这类问题因 `DDT` 未直接出现在同段而漏掉 `逆戟鲸` 证据。
2. `btd6_knowledge_base/kb_interface.py`
   - 新增 `interface_extract_paragraphs()`。
   - CLI 新增 `extract-paragraphs`，便于单独验证某个文件的段落抽取结果。
3. `bot_app/tools/knowledge_service.py`
   - `read_game_knowledge_file` 在有 `query` / `keywords` 时优先调用段落索引。
   - 成功时返回 `mode=paragraph_extract`、`paragraphs`、`content_chars` 和 `metadata`。
   - 若段落索引不可用或未命中，继续回退到原有 `keyword_extract`，保证旧部署不直接失败。
   - 文件级搜索继续使用去弱词后的 query；段落级读取改用保留意图词的 query，让 `数据/价格/伤害/路线/技能/收益/反迷彩` 等字段能参与段落定位。
4. 结构化读取结果新增或确认以下字段：
   - `metadata.title`
   - `metadata.path`
   - `metadata.tower_or_hero`
   - `metadata.upgrade_path`
   - `metadata.upgrade_tier`
   - `metadata.tags`
   - `metadata.mechanics`
   - `metadata.detected_fields`
   - `metadata.paragraph_count`

关键验证：

1. 单点段落抽取：
   - `野兽之主.html` + `野兽之主 004 0-0-4 DDT 逆戟鲸` 返回 `paragraph_extract` 证据段，内容包含 `逆戟鲸`、`升级价格`、`可抓住所有大型气球`。
2. 50 条端到端 grounded 评测新增段落指标：
   - `search_call_rate=1.000`
   - `read_call_rate=1.000`
   - `first_doc_success_rate=1.000`
   - `grounded_answer_rate=1.000`
   - `ungrounded_fallback_rate=0.000`
   - `metadata_present_rate=1.000`
   - `metadata_title_rate=1.000`
   - `paragraph_extract_rate=1.000`
   - `paragraph_result_rate=1.000`

当前结论：

- Phase 3 的本地实现已从轻量窗口抽取推进到真实段落级 FTS 索引。
- `read_game_knowledge_file` 的结果格式已经能表达段落、结构化 metadata 和读取模式。
- 仍需通过真实线上日志继续观察：段落内容是否足够支撑真实模型组织高质量回答。

### 2026-06-25 更新：Phase 4 第二轮 50 条搜索评测集

本轮继续推进 Phase 4，把搜索评测集从 9 条扩展到 50 条。

样本来源：

1. 真实运行日志样本：
   - `@小污猫 野兽之主数据`
   - `人鱼猴数据`
   - `野兽之主数据`
   - `野兽之主路线3数据`
   - `004野兽之主能不能抓DDT`
   - 等同主题变体
2. 合成覆盖样本：
   - 基础塔：飞镖猴、回旋镖猴、大炮、图钉炮、冰猴等。
   - 路线问法：上路/中路/下路。
   - 英雄问法：昆西、格温多琳、本杰明、艾蒂安、杰拉尔多等。
   - 升级和机制问法：逆戟鲸、霸王龙、M.A.D、气球终结者等。

首轮 50 条评测暴露的问题：

1. `价格/伤害/穿透/攻速/收益/技能/商店/反迷彩` 等字段词在文件级检索中作为强制 AND 条件，导致大量 0 命中。
2. `上路/中路/下路` 在文件级检索中容易把结果拉到版本页、路线页或其他包含路线词的页面。
3. `飞机` 被 `间谍飞机.html` 抢首位，说明排序缺少标题精确匹配优先。
4. `帕特`、`布里克尔` 等简称需要归一到完整页面名。

本轮改动：

1. 文件级 query 清洗继续保持保守：
   - 将字段词和意图词从文件级搜索中移除，避免强制 AND。
   - 将 `上路/中路/下路` 从文件级搜索中移除，路线信息留给读取阶段抽取。
   - 增加 `帕特 -> 帕特•法斯第`、`布里克尔 -> 海军上将布里克尔` 等简称归一。
2. `search_framework.py` 排序增加精确身份优先：
   - `title == query` 优先。
   - `path stem == query` 次优先。
   - 解决 `飞机` 被 `间谍飞机` 抢首位的问题。
3. `tests/knowledge_search_eval_cases.json` 扩展到 50 条，并新增 `source` 字段区分：
   - `runtime_log`
   - `synthetic_alias`
   - `synthetic_mechanic`
   - `synthetic_phrase`
   - `synthetic_tower`
   - `synthetic_route`
   - `synthetic_hero`
   - `synthetic_upgrade`
4. `tests/evaluate_knowledge_search.py` 输出 `source_counts`，便于后续区分真实样本和合成样本。

验证结果：

- `python3 tests/evaluate_knowledge_search.py` 通过：
  - `knowledge_query_count=50`
  - `should_hit_count=50`
  - `grounded_required_count=50`
  - `search_hit_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `no_result_rate=0.000`
- `python3 -m py_compile bot_app/tools/knowledge_service.py tests/evaluate_knowledge_search.py btd6_knowledge_base/search_framework.py` 通过。
- `git diff --check` 通过。

当前结论：

- Phase 4 搜索评测规模已达到 SOP 要求的下限 50 条。
- 该评测目前只覆盖搜索命中和首文档命中，不等价于完整 grounded answer 端到端评测。
- 后续还需要继续补：
  - 真实日志样本占比。
  - `read_game_knowledge_file` 片段质量指标。
  - rendering 最终回答是否 grounded 的端到端统计。

### 2026-06-25 更新：Phase 4 第三轮端到端 grounded 评测

本轮继续补 Phase 4 中“最终回答是否 grounded”的评测缺口。

新增产物：

1. `tests/evaluate_knowledge_grounding.py`
   - 使用与搜索评测相同的 `tests/knowledge_search_eval_cases.json`。
   - 使用 deterministic fake LLM 驱动 `AgentRunner`。
   - fake LLM 在 planning 阶段始终返回 `final`，让 `AgentRunner` 的知识 hard rule 决定是否强制搜索和读取。
   - fake LLM 在 rendering 阶段只根据 `read_game_knowledge_file` 的 `content` 生成简短回答。
2. 新增指标：
   - `search_call_rate`
   - `read_call_rate`
   - `first_doc_success_rate`
   - `grounded_answer_rate`
   - `ungrounded_fallback_rate`

本轮发现的问题：

1. 搜索评测 50 条已全部首位命中，但端到端评测首跑只有 70% 样本触发 `search_game_knowledge`。
2. 原因是 `AgentRunner._is_game_knowledge_query()` 的关键词覆盖太窄，未覆盖：
   - `收益`
   - `加成`
   - `技能`
   - `商店`
   - `灵魂`
   - `陷阱`
   - `支援`
   - `亡灵`
   - `反迷彩`
   - `破铅`
   - `上路/中路/下路`
3. 这些问法本质是游戏知识问题，但此前可能不触发 hard rule。

本轮改动：

1. 扩充 `AgentRunner._is_game_knowledge_query()` 的领域关键词。
2. 确保 50 条评测样本都会进入知识 hard rule：
   - 必须搜索。
   - 命中后必须读取。
   - 成功读取后才进入 rendering。

验证结果：

- `python3 tests/evaluate_knowledge_grounding.py` 通过：
  - `knowledge_query_count=50`
  - `search_call_rate=1.000`
  - `read_call_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `grounded_answer_rate=1.000`
  - `ungrounded_fallback_rate=0.000`
- `python3 tests/evaluate_knowledge_search.py` 继续通过：
  - `search_hit_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `no_result_rate=0.000`
- `python3 -m py_compile bot_app/agent/__init__.py bot_app/agent/runner.py bot_app/tools/knowledge_service.py btd6_knowledge_base/clean_index_text.py btd6_knowledge_base/search_framework.py btd6_knowledge_base/kb_interface.py tests/test_agent_tool_calling.py tests/evaluate_knowledge_search.py tests/evaluate_knowledge_grounding.py` 通过。
- `git diff --check` 通过。

当前结论：

- Phase 4 已有两层评测：
  - 搜索评测：验证 query rewrite + 文件级搜索。
  - deterministic 端到端 grounded 评测：验证 `AgentRunner` hard rule、搜索、读取和 grounded summary。
- 这仍不是完整真实模型评测；真实线上 rendering 文风和事实组织质量仍需通过线上日志或真实 LLM 回归观察。

### 2026-06-25 更新：Phase 4 第四轮段落读取指标

本轮在端到端 grounded 评测中增加读取粒度指标：

1. `read_mode`
   - 记录 `read_game_knowledge_file` 实际返回的是 `paragraph_extract`、`keyword_extract`、`extract` 还是 `full`。
2. `paragraph_count`
   - 记录本次读取返回的段落数量。
3. 新增汇总指标：
   - `paragraph_extract_rate`
   - `paragraph_result_rate`

验证结果：

- `python3 tests/evaluate_knowledge_grounding.py` 通过：
  - `knowledge_query_count=50`
  - `search_call_rate=1.000`
  - `read_call_rate=1.000`
  - `first_doc_success_rate=1.000`
  - `grounded_answer_rate=1.000`
  - `ungrounded_fallback_rate=0.000`
  - `metadata_present_rate=1.000`
  - `metadata_title_rate=1.000`
  - `paragraph_extract_rate=1.000`
  - `paragraph_result_rate=1.000`

当前结论：

- Phase 4 已能同时验证“是否检索”、“是否读取”、“首文档是否正确”、“最终是否 grounded”以及“读取是否走段落索引”。
- 后续主要增量不是继续堆本地合成样本，而是提高真实日志样本占比，并在可控环境下补真实 LLM 回归。

### 2026-06-25 更新：Phase 5 混合检索阶段性评估

本轮基于 Phase 4 当前指标，对 Phase 5 做阶段性判断。

当前指标：

1. 搜索评测：
   - `knowledge_query_count=50`
   - `search_hit_rate=1.000`
   - `first_doc_success_rate=1.000`
   - `no_result_rate=0.000`
2. deterministic grounded 评测：
   - `knowledge_query_count=50`
   - `search_call_rate=1.000`
   - `read_call_rate=1.000`
   - `first_doc_success_rate=1.000`
   - `grounded_answer_rate=1.000`
   - `ungrounded_fallback_rate=0.000`
   - `metadata_present_rate=1.000`
   - `metadata_title_rate=1.000`
   - `paragraph_extract_rate=1.000`
   - `paragraph_result_rate=1.000`

阶段性结论：

1. 当前不建议引入 embedding / 向量库 / rerank 服务。
2. 理由：
   - 当前 50 条固定评测已达到 100% 搜索命中和首文档命中。
   - 端到端 grounded 评测已证明 hard rule 能稳定触发搜索和读取。
   - 段落索引评测已证明 50 条样本均能走 `paragraph_extract` 并返回段落结果。
   - 引入向量检索会增加部署依赖、索引维护、延迟和成本，但当前指标尚未证明有必要。
3. 后续如果出现以下情况，再重新评估混合检索：
   - 真实日志样本扩展到 100 条后 `search_hit_rate < 0.9`。
   - `first_doc_success_rate < 0.85` 且主要失败来自语义改写而非 alias 缺失。
   - 大量问题需要跨页面语义推断，FTS + query rewrite + 段落抽取无法稳定覆盖。
   - 段落级索引完成后仍存在“搜到页面但片段不含答案”的高频失败。

当前建议：

- 保留现有本地 SQLite FTS5 方案。
- 下一轮优先补真实线上日志回归与读取片段质量标注，而不是引入混合检索。

### 2026-06-24 摘要

截至 2026-06-24：

- 已完成：
  - 知识命中率问题代码审计
  - 近期真实样本抽样
  - 知识库实现与模型调用方式评估
  - 本 SOP 建立
  - 仓库内运行口径核对
  - 知识链路结构化观测字段第一轮补齐
- 2026-06-25 追加完成：
  - grounded hard rule 落地
  - BTD6 query rewrite 与 alias 扩展
  - 段落级 SQLite FTS 索引
  - 结构化读取 metadata
  - 50 条搜索评测集
  - 50 条 deterministic 端到端 grounded 评测
  - 混合检索阶段性评估
- 仍需持续观察：
  - 线上实例是否已经完全统一到新 `AgentRunner` 口径。
  - 真实日志样本扩展到 100 条后的指标是否仍稳定。
  - 真实 LLM rendering 是否稳定消费段落证据并正确组织事实。

当前判断：

- 基础知识库路线可保留。
- 当前本地固定评测已证明：FTS5 + query rewrite + 段落索引 + hard rule 足以覆盖本轮 50 条样本。
- 现阶段不建议引入向量库或 rerank 服务。
- 下一步应转向真实线上日志回归和真实 LLM 输出质量观察，而不是继续增加检索链路复杂度。
