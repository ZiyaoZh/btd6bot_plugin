name: script_manual_query
aliases:
- manual_query
purpose: 基于脚本程序说明书回答问题
when_to_use: 用户问程序怎么用、配置方式、参数含义、报错处理、安装步骤
input:
- query: 用户问题文本
output:
- manual_full_text: 完整说明书内容
- query: 原问题
important:
- 结合 extra_prompt 指示，优先依据说明书作答，不要编造
example_queries:
- 这个程序怎么启动
