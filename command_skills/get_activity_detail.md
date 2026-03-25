# get_activity_detail 指令说明

## 用途
获取指定活动类型的最新详情，内部使用 btd6_api_processer 的 detail 模式。

## 支持参数
- activity_type: race | boss | odyssey | daily

## 调用模板
{
  "module": "llm_processor",
  "target": "command_processor",
  "intent": "get_activity_detail",
  "arguments": {
    "activity_type": "race"
  },
  "reply": "",
  "reason": "用户要看竞速活动详情"
}

## 返回重点
- command_result.data.report: 详情文本
- command_result.data.generated_files: 详情文件路径

## 注意
- activity_type 取值不合法会返回失败。
- 若用户说“竞速/BOSS/征程/每日挑战”，先映射到标准值再调用。
