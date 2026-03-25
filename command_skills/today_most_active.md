name: today_most_active
purpose: 查询今日群聊活跃榜
when_to_use: 用户问今日谁最活跃、活跃排行、TopN活跃用户
input:
- top_n: 整数，可选，默认3
output:
- rows: 每项含 user_id, nickname, card, message_count
example_queries:
- 今天谁最活跃
- 今日活跃榜前5
