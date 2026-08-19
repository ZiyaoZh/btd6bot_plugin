# Vision Phase 2: Event Contract

## Goal

让 QQ 图片以结构化元数据进入渠道无关事件契约，为后续媒体持久化、缓存和多模态上下文装配提供稳定输入。

## Changes

- `EventPayload` 新增可选 `message_id` 和 `images`，保持既有调用方兼容。
- 新增 `ImagePayload`，记录原消息段位置、URL、file ID、文件名、字节数和 `sub_type`。
- `GroupMessageReceiver` 使用 `MessageArray.to_list()` 提取图片，保留图文原始顺序，不解析 CQ 图片码。
- 事件日志中的图片只保留数量、段位置、字节数和 `sub_type`，不记录 URL、file ID 或文件名。
- 新增 `VE-01` 至 `VE-04` 测试，覆盖顺序、字段归一化、事件注入、异常降级和日志脱敏。
- README 记录事件契约和当前阶段边界。

## Boundary

本阶段不下载图片、不写媒体表、不缓存文件、不改变最近群聊查询，也不构造多模态 LLM payload。现有应用仍会拒绝文本归一化后为空的纯图片消息；该行为将在上下文与媒体链路接入阶段调整。

## Verification

- `python3 tests/test_vision_event_contract.py -v`
- `python3 tests/test_agent_tool_calling.py -v`
- `ruff check bot_app/protocol.py bot_app/receiver.py tests/test_vision_event_contract.py --ignore SIM103`
- `ruff format --check bot_app/protocol.py bot_app/receiver.py tests/test_vision_event_contract.py`
