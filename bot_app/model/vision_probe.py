from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_IMAGE_PATHS = (
    Path("image_samples/8E6A80EA82136530C3E24400ED12557A.png"),
    Path("image_samples/7D22F1F2E89707D664C29E5BBDD844D5.png"),
)
TRANSIENT_HTTP_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ImageDescriptor:
    path: str
    mime_type: str
    byte_count: int
    sha256: str


@dataclass
class ProbeResult:
    name: str
    passed: bool
    latency_ms: int
    attempts: int
    status_code: int | None = None
    response_model: str = ""
    finish_reason: str = ""
    content_present: bool = False
    tool_call_present: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class ProbeReport:
    api_base_url: str
    model: str
    images: list[ImageDescriptor]
    probes: list[ProbeResult]

    @property
    def passed(self) -> bool:
        return bool(self.probes) and all(item.passed for item in self.probes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "api_base_url": self.api_base_url,
            "model": self.model,
            "images": [asdict(image) for image in self.images],
            "probes": [asdict(probe) for probe in self.probes],
        }


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def describe_image(path: Path) -> ImageDescriptor:
    raw = path.read_bytes()
    return ImageDescriptor(
        path=str(path),
        mime_type=_mime_type(path),
        byte_count=len(raw),
        sha256=sha256(raw).hexdigest(),
    )


def image_content(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{_mime_type(path)};base64,{encoded}"},
    }


def build_probe_payloads(
    model: str, image_paths: list[Path]
) -> list[tuple[str, dict[str, Any]]]:
    first, second = image_paths
    single = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "简要描述这张图片中可见的主要内容。"},
                    image_content(first),
                ],
            }
        ],
    }
    multiple = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "比较图片 1 和图片 2 的主要视觉内容，并指出一项差异。",
                    },
                    {"type": "text", "text": "图片 1："},
                    image_content(first),
                    {"type": "text", "text": "图片 2："},
                    image_content(second),
                ],
            }
        ],
    }
    tool_name = "report_visible_image"
    with_tool = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "观察图片，然后必须调用工具报告结果。"},
                    image_content(first),
                ],
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "报告已成功观察图片，并给出简短视觉摘要。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "observed": {"type": "boolean", "const": True},
                            "summary": {"type": "string"},
                        },
                        "required": ["observed", "summary"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
    }
    return [
        ("single_image_data_url", single),
        ("two_image_comparison", multiple),
        ("image_with_tool_call", with_tool),
    ]


def run_probe(
    *,
    api_base_url: str,
    api_key: str,
    model: str,
    image_paths: list[Path],
    timeout: float,
    max_attempts: int,
    opener: Callable[..., Any] = request.urlopen,
) -> ProbeReport:
    descriptors = [describe_image(path) for path in image_paths]
    results = [
        _execute_probe(
            name=name,
            payload=payload,
            url=f"{api_base_url.rstrip('/')}/chat/completions",
            api_key=api_key,
            timeout=timeout,
            max_attempts=max_attempts,
            opener=opener,
        )
        for name, payload in build_probe_payloads(model, image_paths)
    ]
    return ProbeReport(
        api_base_url=api_base_url, model=model, images=descriptors, probes=results
    )


def _execute_probe(
    *,
    name: str,
    payload: dict[str, Any],
    url: str,
    api_key: str,
    timeout: float,
    max_attempts: int,
    opener: Callable[..., Any],
) -> ProbeResult:
    started = time.monotonic()
    expects_tool = name == "image_with_tool_call"
    last_error = ""
    status_code: int | None = None
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        req = request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with opener(req, timeout=timeout) as response:
                status_code = int(getattr(response, "status", 200))
                data = json.loads(response.read().decode("utf-8"))
            return _result_from_response(
                name=name,
                data=data,
                expects_tool=expects_tool,
                started=started,
                attempt=attempt,
                status_code=status_code,
            )
        except error.HTTPError as exc:
            status_code = exc.code
            last_error = f"HTTP {exc.code}: {_safe_error_detail(exc.read())}"
            if exc.code not in TRANSIENT_HTTP_STATUS:
                break
        except (
            error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < attempts:
            time.sleep(min(0.5 * attempt, 2.0))
    return ProbeResult(
        name=name,
        passed=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        attempts=min(attempt, attempts),
        status_code=status_code,
        error=last_error or "unknown probe failure",
    )


def _result_from_response(
    *,
    name: str,
    data: dict[str, Any],
    expects_tool: bool,
    started: float,
    attempt: int,
    status_code: int,
) -> ProbeResult:
    choice = data["choices"][0]
    message = choice["message"]
    content_present = bool(str(message.get("content") or "").strip())
    tool_calls = message.get("tool_calls")
    tool_call_present = isinstance(tool_calls, list) and bool(tool_calls)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    passed = tool_call_present if expects_tool else content_present
    error_message = ""
    if not passed:
        error_message = (
            "expected tool_calls in assistant message"
            if expects_tool
            else "expected non-empty assistant content"
        )
    return ProbeResult(
        name=name,
        passed=passed,
        latency_ms=int((time.monotonic() - started) * 1000),
        attempts=attempt,
        status_code=status_code,
        response_model=str(data.get("model") or ""),
        finish_reason=str(choice.get("finish_reason") or ""),
        content_present=content_present,
        tool_call_present=tool_call_present,
        usage=usage,
        error=error_message,
    )


def _safe_error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\n", " ").strip()
    return text[:500]


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    try:
        return mapping[suffix]
    except KeyError as exc:
        raise ValueError(f"unsupported image extension: {suffix or '<none>'}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe OpenAI-compatible vision and tool-call support."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--image", type=Path, action="append", dest="images")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    api_key = os.getenv("LLM_API_KEY", "").strip()
    api_base_url = os.getenv("LLM_API_BASE_URL", "").strip()
    model = (
        os.getenv("LLM_MODELS", "").split(",")[0].strip()
        or os.getenv("LLM_MODEL", "").strip()
    )
    if not api_key or not api_base_url or not model:
        parser.error(
            "LLM_API_KEY, LLM_API_BASE_URL and LLM_MODEL/LLM_MODELS are required"
        )

    image_paths = args.images or list(DEFAULT_IMAGE_PATHS)
    if len(image_paths) != 2:
        parser.error("exactly two --image paths are required")
    missing = [str(path) for path in image_paths if not path.is_file()]
    if missing:
        parser.error(f"image file not found: {', '.join(missing)}")

    report = run_probe(
        api_base_url=api_base_url,
        api_key=api_key,
        model=model,
        image_paths=image_paths,
        timeout=args.timeout or float(os.getenv("LLM_REQUEST_TIMEOUT", "30")),
        max_attempts=max(1, args.max_attempts),
    )
    rendered = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
