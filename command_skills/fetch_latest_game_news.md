# fetch_latest_game_news 指令说明

## 用途
刷新并获取 BTD6 最新活动资讯，内部使用 btd6_api_processer 的 update 模式。

## 调用模板
{
  "module": "llm_processor",
  "target": "command_processor",
  "intent": "fetch_latest_game_news",
  "arguments": {},
  "reply": "",
  "reason": "用户要求刷新最新资讯"
}

## 返回重点
- command_result.data.report: 更新报告全文
- command_result.data.generated_files: 本次刷新产出的文件列表

## 适用场景
- 用户说“刷新最新资讯”“更新活动信息”
- 用户要求先同步最新活动数据
