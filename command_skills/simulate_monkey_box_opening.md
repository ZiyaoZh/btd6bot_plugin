# simulate_monkey_box_opening 指令说明

## 用途
模拟 BTD6 开箱子小游戏并返回结果。

## 支持参数
- box_type: 木头 | 青铜 | 白银 | 黄金 | 钻石
- box_count: 正整数

## 调用模板
{
  "module": "llm_processor",
  "target": "command_processor",
  "intent": "simulate_monkey_box_opening",
  "arguments": {
    "box_type": "钻石",
    "box_count": 10
  },
  "reply": "",
  "reason": "用户希望模拟收集开箱"
}

## 返回重点
- command_result.data.report_path: 统计 Markdown 文档路径（一定有）
- command_result.image_path: 开箱拼图路径（仅 box_count<=50 时有）
- command_result.data.output_text: 模拟器终端输出摘要

## 注意
- 只需要 box_type 和 box_count 两个参数。
- box_count<=50 会生成图片并走 image 回复；大于 50 不发图片。
- box_type 非法或 box_count 非正整数会返回失败。
