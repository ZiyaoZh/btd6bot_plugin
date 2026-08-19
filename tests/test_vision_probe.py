from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from bot_app.model.vision_probe import build_probe_payloads, run_probe


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class VisionProbeTests(unittest.TestCase):
    def _images(self, root: Path) -> list[Path]:
        paths = [root / "first.png", root / "second.png"]
        paths[0].write_bytes(b"first-image")
        paths[1].write_bytes(b"second-image")
        return paths

    def test_builds_multimodal_and_forced_tool_payloads(self) -> None:
        """VP-01: 三种探针均使用图文 content，工具探针强制函数调用。"""
        with tempfile.TemporaryDirectory() as directory:
            payloads = build_probe_payloads(
                "vision-model", self._images(Path(directory))
            )

        self.assertEqual(
            [name for name, _ in payloads],
            ["single_image_data_url", "two_image_comparison", "image_with_tool_call"],
        )
        self.assertEqual(
            sum(
                part["type"] == "image_url"
                for part in payloads[1][1]["messages"][0]["content"]
            ),
            2,
        )
        self.assertEqual(
            payloads[2][1]["tool_choice"]["function"]["name"], "report_visible_image"
        )

    def test_requires_tool_call_for_combined_probe(self) -> None:
        """VP-02: 图片与 tools 组合仅在响应包含 tool_calls 时通过。"""
        responses = [
            _Response(
                {
                    "model": "m",
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "ok"}}
                    ],
                    "usage": {"total_tokens": 1},
                }
            ),
            _Response(
                {
                    "model": "m",
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "ok"}}
                    ],
                    "usage": {"total_tokens": 1},
                }
            ),
            _Response(
                {
                    "model": "m",
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "text only"}}
                    ],
                    "usage": {"total_tokens": 1},
                }
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            report = run_probe(
                api_base_url="https://proxy.example/v1",
                api_key="secret",
                model="m",
                image_paths=self._images(Path(directory)),
                timeout=1,
                max_attempts=1,
                opener=lambda *_args, **_kwargs: responses.pop(0),
            )

        self.assertFalse(report.passed)
        self.assertFalse(report.probes[-1].passed)
        self.assertIn("tool_calls", report.probes[-1].error)

    def test_report_excludes_base64_and_api_key(self) -> None:
        """VP-03: 探针报告只保留图片摘要，不泄露 Base64 或 API key。"""
        response = {
            "model": "m",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "ok",
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {
                                    "name": "report_visible_image",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"total_tokens": 2},
        }
        with tempfile.TemporaryDirectory() as directory:
            report = run_probe(
                api_base_url="https://proxy.example/v1",
                api_key="top-secret",
                model="m",
                image_paths=self._images(Path(directory)),
                timeout=1,
                max_attempts=1,
                opener=lambda *_args, **_kwargs: _Response(response),
            )
        rendered = json.dumps(report.to_dict())

        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("base64", rendered)
        self.assertTrue(report.passed)

    def test_retries_transient_http_error(self) -> None:
        """VP-04: 临时 HTTP 错误会在次数预算内重试并记录 attempts。"""
        calls = 0
        success = {
            "model": "m",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "ok", "tool_calls": [{"id": "1"}]},
                }
            ],
            "usage": {"total_tokens": 2},
        }

        def opener(req, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPError(
                    req.full_url, 503, "temporary", {}, io.BytesIO(b"unavailable")
                )
            return _Response(success)

        with tempfile.TemporaryDirectory() as directory:
            report = run_probe(
                api_base_url="https://proxy.example/v1",
                api_key="secret",
                model="m",
                image_paths=self._images(Path(directory)),
                timeout=1,
                max_attempts=2,
                opener=opener,
            )

        self.assertTrue(report.passed)
        self.assertEqual(report.probes[0].attempts, 2)


if __name__ == "__main__":
    unittest.main()
