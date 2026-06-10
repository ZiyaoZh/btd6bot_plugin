# 2026-06-10 图片发送与开箱 PNG 修复

## 背景

排查发现排行榜、收集轮换、开箱工具能返回 `response_type=image` 和 `image_path`，但发送层使用 `post_group_msg(image=...)`。NcatBot 示例中 `post_group_msg` 为纯文本接口，图文应使用 `MessageArray` / `post_group_array_msg` 或图片发送 sugar API。

小游戏开箱模拟原先输出 `webp`，对 QQ 图片发送兼容性不如 `png` 稳定。

## 变更

- `bot_app/sender.py`: `send_group_image` 改用 `MessageArray` 构造 at、caption、image，并通过 `post_group_array_msg` 发送。
- `bot_app/command_processor.py`: 开箱模拟生成的 bot 图片路径改为 `.png`。
- `ChatMonkeyTD/main.py`: 小游戏 CLI 默认 `--output` 改为 `output/open_result.png`。
- `tests/test_agent_tool_calling.py`: 增加 sender 单测，确保图片发送走富文本接口而不是 `post_group_msg`。

## 验证

- `python -m unittest tests.test_agent_tool_calling`
- `python main.py 钻石 1 --report output/_codex_test_open_png.md --output output/_codex_test_open_png.png`

## 四位一体

- Code: 已修复发送层和小游戏输出格式。
- Test: 已补 sender 行为测试。
- Docs: 未修改用户文档；行为保持为“图片回复”，仅修复内部发送实现和图片格式。
- Skill: 未修改 `.agents/skills/`；本次为插件内部 bug 修复。
