# Vision Phase 9: Single-group Gray Acceptance

## 变更类型

feat

## 内容

- 新增 `LLM_VISION_GROUP_IDS` 灰度群白名单；留空时保持原有的全部目标群语义。
- 白名单必须是 `BOT_TARGET_GROUP_IDS` 的子集，非法群号在启动时拒绝。
- 图片缓存、多模态请求装配和视觉摘要统一执行群级开关判定，非灰度群保持纯文本请求且不下载图片。
- 生产环境开启 `LLM_VISION_ENABLED=true`，仅对白名单群 `849240239` 生效；另一目标群 `960225891` 保持非视觉链路。

## 四位一体

- Code: `bot_app/settings.py`, `bot_app/application.py`, `bot_app/model/multimodal.py`, `bot_app/visual_summary.py`
- Test: `tests/test_vision_media_store.py` (`VC-05`), `tests/test_vision_multimodal_context.py` (`VX-08`), `tests/test_vision_visual_summary.py` (`VS-09`)
- Docs: `README.md` 增加灰度群白名单配置说明
- Skill: 不涉及 NcatBot 公共 Skill API，现有 `framework-dev` 流程无需修改

## 真实代理探针

2026-08-19 对 `https://llmapi.paratera.com` 的 `AWS-GPT-5.6-Luna` 重新执行三项探针，最终全部通过：

| Probe | Latency | Attempts | Prompt tokens | Result |
|---|---:|---:|---:|---|
| Single image data URL | 21.6s | 1 | 1,403 | HTTP 200, non-empty content |
| Two-image comparison | 10.3s | 1 | 6,704 | HTTP 200, non-empty content |
| Image with forced tool call | 42.3s | 2 | 2,553 | HTTP 200, `finish_reason=tool_calls` |

工具组合探针发生一次瞬态失败后重试成功。兼容性门槛通过，但灰度期需要继续观察代理错误率和尾延迟。脱敏报告位于被 Git 忽略的 `data/vision-probe-report.json`。

## 生产灰度状态

- 使用 `docker compose up -d --build --no-deps ncatbot` 重建应用容器，未重启已健康运行的 SSH 代理。
- QQ 登录、NapCat WebSocket、插件加载均成功；观察时容器重启次数为 0。
- 启动基线下图片缓存为空，短观察窗口内灰度群没有新图片消息，因此尚无真实 QQ 图片 token、端到端延迟或缓存增长样本。
- 非灰度群 `960225891` 在启动后收到 1 条含图消息：媒体元数据状态保持 `metadata_only`，没有缓存事件、缓存文件或视觉 LLM 请求，实际验证了群级隔离。
- 宿主机配置的 SSH 私钥路径当前不可读，完整 `verify_deploy.sh` 仍会在 SSH 凭据门禁失败；现有 `ssh-proxy` 容器持续健康，不影响本次仅重建应用容器。

## 验证

- `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py' -v`: 119 tests passed
- 聚焦 Ruff 检查通过（保留项目既有 best-effort `BLE001` 例外）
- `ruff format` 通过（本阶段 Python 变更文件）
- 真实代理三项探针最终通过
- Compose 配置有效；`ncatbot` 与 `ssh-proxy` 均为 running，代理为 healthy

## 后续观察门槛

收到灰度群真实图片样本后，检查 `media.cache_completed`、`llm.request`、`pipeline.finalized` 和代理错误事件，记录图片 token、端到端延迟、缓存字节、成功率及普通文本对照。没有真实群消息样本前，不将 QQ 端到端视觉验收标记为完成。
