# Vision Phase 1: Proxy Compatibility Probe

## Goal

在修改生产消息链路前，验证当前 OpenAI-compatible 代理是否同时支持图片输入和原生工具调用。

## Changes

- 新增 `bot_app.model.vision_probe` 独立 CLI。
- 使用两张仓库内非敏感样本执行 Base64 单图、双图比较、图片与强制工具调用三项探针。
- 报告仅保留图片摘要、响应结构、usage、耗时和脱敏错误；不记录密钥、Base64 或完整 payload。
- 新增 `VP-01` 至 `VP-04` 单元测试，覆盖 payload、门槛判定、脱敏和瞬态重试。

## Boundary

本阶段不修改 `EventPayload`、数据库、Harness、会话记忆或生产 LLM 请求。探针全部通过只是进入第二阶段渠道事件契约改造的前置条件。

## Verification

- `python3 plugins/btd6bot_plugin/tests/test_vision_probe.py -v`
- 真实代理：`PYTHONPATH=plugins/btd6bot_plugin python3 -m bot_app.model.vision_probe`

2026-08-19 对 `https://llmapi.paratera.com` 的 `AWS-GPT-5.6-Luna` 实测三项全部通过，均为 HTTP 200 且一次成功：

| Probe | Latency | Prompt tokens | Result |
|---|---:|---:|---|
| Single image data URL | 16.5s | 1,403 | non-empty content |
| Two-image comparison | 10.8s | 6,704 | non-empty content |
| Image with forced tool call | 16.0s | 2,553 | `finish_reason=tool_calls` |

脱敏报告写入被 Git 忽略的 `data/vision-probe-report.json`。多图 prompt token 增长明显，后续上下文装配必须实施图片数量和总请求预算。
