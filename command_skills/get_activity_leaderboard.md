# get_activity_leaderboard 指令说明

## 用途
获取指定类型活动排行榜，输出图片，内部使用 btd6_api_processer 的 leaderboard image 模式。

## 支持参数
- leaderboard_type: race | boss-standard | boss-elite

## 调用模板
{
  "module": "llm_processor",
  "target": "command_processor",
  "intent": "get_activity_leaderboard",
  "arguments": {
    "leaderboard_type": "boss-elite"
  },
  "reply": "",
  "reason": "用户要看精英 boss 排行榜"
}

## 返回重点
- command_result.response_type = image
- command_result.image_path: 要发送的图片绝对路径
- command_result.data.report: 生成摘要

## 注意
- 排行榜图片发送依赖 sender 的图片发送能力。
- 若 image_path 为空，说明生成失败，需要提示用户稍后重试。
