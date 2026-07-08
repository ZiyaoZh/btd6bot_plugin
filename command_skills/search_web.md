# search_web

## 用途
联网搜索公开网页，作为其他工具无结果、失败、信息可能过时，或用户需要当前公开资料时的备用证据路径。

适合：
- 当前公开资料、新闻公告、产品/服务信息、教程攻略、一般知识补充、资料来源查证。
- 本地知识库或专用工具无结果、失败、过时，或片段不足。
- 用户的问题不属于已有专用工具，但可以通过公开网页候选辅助回答。

不适合：
- 已有专用工具或本地资料给出可靠结果时重复联网。
- 需要登录、私有群聊或非公开网页内容。
- 违法违规、规避监管、侵犯隐私、侵权盗版、危险行为或其他法律法规禁止事项的操作性指导。

## 指令名
- search_web

## 参数
- query: str，必填。搜索关键词或用户原问题；需要时加入主题、时间、地区、来源类型、产品名、版本号等限定词。
- top_n: int，可选。返回前 N 个网页候选，默认 5，范围 1-8。
- timeout_seconds: number，可选。请求超时时间，默认 6 秒，范围 2-10 秒。

## 返回
- command_result.data.result_count: int，网页候选数量。
- command_result.data.results: object[]，每项包含 rank、title、url、source_domain、snippet。
- command_result.data.provider: 搜索后端标识。

## 使用要求
- 最终回答只能依据 results 中的 title、snippet、url，不要把联网搜索当成已阅读全文。
- 涉及时效性信息时，说明信息可能随时间变化。
- 如果 result_count=0 或摘要不能支撑结论，应明确说不确定，并建议换关键词或指定来源。
- 涉及法律、医疗、金融、安全等高风险主题时，只能提供一般信息，并提醒以官方渠道或专业人士意见为准。
- 如果用户请求涉及违法违规、规避监管、侵犯隐私、侵权盗版、危险行为或其他法律法规禁止事项，不得提供操作性指导、规避方法或可执行步骤；应简要拒绝并提供合法合规的替代建议。

## 示例
- {"module":"llm_processor","target":"command_processor","intent":"search_web","arguments":{"query":"BTD6 46.0 patch notes nerf tower","top_n":5},"reply":"","reason":"知识库没有最新版本资料，联网补充查证"}
- {"module":"llm_processor","target":"command_processor","intent":"search_web","arguments":{"query":"BTD6 DDT counter guide","top_n":5},"reply":"","reason":"知识库未命中开放策略问题，联网搜索攻略资料"}
- {"module":"llm_processor","target":"command_processor","intent":"search_web","arguments":{"query":"某产品 2026 官方公告 价格","top_n":5},"reply":"","reason":"用户需要当前公开资料，联网查证"}
