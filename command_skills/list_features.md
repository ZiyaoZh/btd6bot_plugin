name: list_features
purpose: 列出机器人可用功能
when_to_use: 用户询问你能做什么、功能有哪些、help/list
input:
- 无必须参数
output:
- feature_count: 功能总数
- features: 功能项列表（name/aliases/description/examples）
example_queries:
- 你有什么功能
- list
- help
