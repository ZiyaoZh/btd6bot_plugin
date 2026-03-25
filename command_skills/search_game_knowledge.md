# search_game_knowledge

## 用途

## 检索前处理
- 先将原始查询拆分成关键词，查询时空格隔开，不支持原始问题直接查询。
- 系统会自动过滤弱信息词（如“详情”“说明”“机制”“模式”等）后再检索，以提升命中质量。
- 返回结果里会同时保留原始 query 与过滤后的 query。
## 指令名
## 参数
- top_n: int，可选，返回前 N 个匹配文件路径，默认 5，范围建议 1-20。
- query: str，可选，检索关键词，支持多关键词，空格隔开。

## 返回
- command_result.data.matched_paths: string[]，按相关性排序的文件路径列表。
- command_result.data.matched_count: int，命中数量。

## 路径约定
- 返回路径默认位于 btd6_knowledge_base/wiki_data_second_cleaned/btd6。

## 示例
- {"module":"llm_processor","target":"command_processor","intent":"search_game_knowledge","arguments":{"top_n":5,"query":"dart"},"reply":"","reason":"检索游戏知识"}
