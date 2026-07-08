# 常见用户问题对应工具选择

- 功能列表、你会什么、有哪些能力 -> `list_features`
- 今天谁最活跃、活跃榜、群活跃排行 -> `today_most_active`
- 总结最近群聊、整理最近讨论 -> `summarize_recent_messages`
- 脚本功能怎么用、脚本如何配置、程序文档问答、刷收集、刷黑框、脚本运行问题 -> `script_manual_query`
- 查游戏机制、某塔资料、回合信息、游戏知识检索 -> 先调用 `search_game_knowledge`，命中后再调用 `read_game_knowledge_file`
- 竞速/BOSS/征程/每日挑战详情 -> `get_activity_detail`
- 竞速/BOSS活动排行榜 -> `get_activity_leaderboard`
- 收集活动/收集轮换查询 -> `get_collection_event_info`
- 当前公开资料、新闻公告、产品服务信息、教程攻略、一般网页查证、其他工具未命中或失败后的补充查证 -> `search_web`
- insta、收集开箱、开箱模拟、抽猴子箱子、模拟开箱结果 -> `simulate_monkey_box_opening`

# 选择优先级建议

1. 用户明确提到“最新活动/资讯/排行榜”时优先选择对应的活动工具，不要输出能力类别名。
2. 用户明确提到“群活跃/谁最活跃”时优先 `today_most_active`。
3. 用户问题涉及“脚本程序本身怎么用”时优先 `script_manual_query`。
4. 用户问题是“BTD6百科知识解释”时，必须先 `search_game_knowledge`，命中后继续 `read_game_knowledge_file`。
5. 如果知识库 matched_count=0、知识库读取失败、活动工具失败，或用户问题需要当前公开网页资料、新闻公告、产品服务信息、教程攻略、一般资料查证，可以调用 `search_web` 作为备用路径；联网结果不足时必须说明不确定。
6. `search_web` 只能用于公开网页查证，不得用于违法违规、规避监管、侵犯隐私、侵权盗版、危险行为或其他法律法规禁止事项的操作性指导；遇到这类请求应拒绝并给出合法合规替代方向。
7. 用户问题涉及“开箱模拟/收集开箱结果”时优先 `simulate_monkey_box_opening`。
