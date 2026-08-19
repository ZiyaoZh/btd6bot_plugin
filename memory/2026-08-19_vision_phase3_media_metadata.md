# Vision Phase 3: Media Metadata

## Goal

让结构化图片元数据可靠进入 SQLite 和最近群聊上下文，并解除纯图片消息的空文本拦截，为后续有界缓存与多模态请求装配提供稳定数据源。

## Changes

- 新增 `message_media` 表，按消息行和原始段位置幂等保存图片源元数据、处理状态及后续缓存/摘要字段。
- `record_group_message` 在消息事务内写入图片元数据，不保存二进制或 Base64。
- 最近群聊查询改为包含有效文字或媒体的消息，并按段顺序返回媒体记录。
- 纯图片消息通过入口内容检查，当前和历史上下文均提供图片数量占位。
- 最近群聊装配按 `message_id` 排除当前消息，避免当前图片重复占用后续多模态预算。
- 新增 `VM-01` 至 `VM-04` 测试，覆盖纯图片接受、媒体幂等/联查、数据库迁移和当前消息去重。

## Boundary

本阶段不下载图片、不写本地缓存、不校验格式、不计算 SHA-256、不生成视觉摘要，也不向 Chat Completions 提交 `image_url`。`processing_status` 保持 `metadata_only`，缓存及真正的像素识别属于下一阶段。

## Verification

- `python3 tests/test_vision_event_contract.py -v`
- `python3 tests/test_agent_tool_calling.py -v`
- `ruff check bot_app tests/test_vision_event_contract.py --ignore SIM103`
- `ruff format --check bot_app tests/test_vision_event_contract.py`
