# Vision Phase 4: Bounded Media Cache

## 变更类型

feat

## 内容

- 新增 `ChatMediaStore`，为 QQ 图片提供流式限量下载、URL/重定向校验、Pillow 解码与归一化、SHA-256 去重、TTL 和容量清理。
- 当前接受消息前台缓存，目标群其他消息后台尽力缓存；失败不阻断消息处理。
- 缓存结果回写 `message_media` 的 MIME、尺寸、哈希、路径、状态和过期时间。
- 新增默认关闭的视觉总开关及缓存安全边界配置。

## 四位一体

- Code: `bot_app/media_store.py`, `bot_app/application.py`, `bot_app/receiver.py`, `bot_app/db_manager.py`, `bot_app/settings.py`
- Test: `tests/test_vision_media_store.py` (`VC-01` 至 `VC-06`)
- Docs: `README.md` 识图兼容性探针章节
- Skill: 不涉及 NcatBot 公共 Skill API，现有 `framework-dev` 流程无需修改

## 边界

本阶段不构造 Chat Completions `image_url` 内容段、不改变 Harness 工具循环、不升级短期记忆，也不生成视觉摘要。开关启用后只建立安全缓存；模型读取真实像素属于下一阶段。

## 验证

- `python3 tests/test_vision_media_store.py -v`
- `python3 tests/test_vision_event_contract.py -v`
- `python3 tests/test_agent_tool_calling.py -v`
- `ruff check` 与 `ruff format --check`（本次触及文件）
