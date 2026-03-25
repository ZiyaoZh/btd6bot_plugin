name: summarize_recent_messages
aliases:
- recent_chat_summary
- 群聊总结
- 总结最近消息
purpose: 总结最近的群聊消息，提炼话题与结论
when_to_use: 用户要求总结最近群聊内容、回顾讨论重点、提取待办事项
input:
- top_n: 最近消息条数，默认 30，建议 5-200
output:
- top_n: 实际读取条数
- message_count: 实际命中消息数
- messages: 最近消息列表（含 display_name/text/created_at）
important:
- 基于 messages 进行总结，不要编造未出现的内容
- 优先提炼主要话题、共识分歧、可执行待办
example_queries:
- 总结最近群聊
- 帮我回顾最近50条消息在说什么
