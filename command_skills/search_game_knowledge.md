# search_game_knowledge

## 用途

## 检索前处理
- query 优先传用户原始问题，不要让大模型自行压缩成少量关键词。
- 系统会自动过滤唤醒词、弱信息词，并扩展中英文别名、路线、升级码、机制词后检索。
- 返回结果里会同时保留原始 query、过滤后的 query、query_plan、候选分数和置信度。
## 指令名
## 参数
- top_n: int，可选，返回前 N 个匹配文件路径，默认 5，范围建议 1-20。
- query: str，可选，用户原始问题。仅当用户明确给出关键词列表时才传关键词。

## 返回
- command_result.data.matched_paths: string[]，按相关性排序的文件路径列表。
- command_result.data.matched_count: int，命中数量。
- command_result.data.candidate_details: object[]，候选分数、置信度、命中原因。
- command_result.data.query_plan: object，服务端生成的查询变体和实体/机制识别结果。

## 路径约定
- 返回路径默认位于 btd6_knowledge_base/wiki_data_second_cleaned/btd6。

## 示例
- {"module":"llm_processor","target":"command_processor","intent":"search_game_knowledge","arguments":{"top_n":5,"query":"飞镖猴能不能破铅"},"reply":"","reason":"检索游戏知识"}
