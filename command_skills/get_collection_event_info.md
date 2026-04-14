# get_collection_event_info 指令说明

## 用途
获取收集活动轮换信息，内部使用 btd6_api_processer 的 collection-event 模式，并返回可直接发送的图片。

## 支持参数
- only_upcoming: true | false（可选）

## 调用模板
{
  "module": "llm_processor",
  "target": "command_processor",
  "intent": "get_collection_event_info",
  "arguments": {
    "only_upcoming": "true"
  },
  "reply": "",
  "reason": "用户要看接下来的收集活动轮换图"
}

## 返回重点
- command_result.response_type: image
- command_result.image_path: 收集活动轮换图路径
- command_result.data.json_path: 对应 JSON 文件路径

## 注意
- only_upcoming=true 表示仅返回当前及未来轮换。
- 图片缺失时会返回失败，请提示用户稍后重试。
