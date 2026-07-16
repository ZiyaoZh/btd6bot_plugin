from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent.types import ToolHandler, ToolSpec
from ..protocol import CommandResultPayload

DEFAULT_RESPONSE_TYPE = "text"


@dataclass(frozen=True)
class ConcreteTool:
    spec: ToolSpec
    handler: ToolHandler
    execution_timeout_seconds: float | None = None


@dataclass
class ToolDependencies:
    db: Any
    plugin_root: Path
    manual_file: Path
    kb_root: Path
    api_processor_root: Path
    chat_monkey_root: Path
    subprocess_runner: Any = None
    manual_text_cache: str = ""
    image_gen_api_base_url: str = ""
    image_gen_api_key: str = ""
    image_gen_model: str = ""
    image_gen_request_timeout: float = 180.0
    image_gen_max_retries: int = 2
    image_gen_retry_delay_seconds: float = 2.0
    image_gen_use_proxy: bool = False
    image_gen_max_bytes: int = 20 * 1024 * 1024

    @classmethod
    def create(
        cls,
        *,
        db: Any,
        plugin_root: Path,
        manual_file: str | Path,
        subprocess_runner: Any = None,
        image_gen_api_base_url: str = "",
        image_gen_api_key: str = "",
        image_gen_model: str = "",
        image_gen_request_timeout: float = 180.0,
        image_gen_max_retries: int = 2,
        image_gen_retry_delay_seconds: float = 2.0,
        image_gen_use_proxy: bool = False,
        image_gen_max_bytes: int = 20 * 1024 * 1024,
    ) -> ToolDependencies:
        root = plugin_root.resolve()
        resolved_manual_file = _resolve_under_root(root, manual_file)
        return cls(
            db=db,
            plugin_root=root,
            manual_file=resolved_manual_file,
            kb_root=root / "btd6_knowledge_base",
            api_processor_root=root / "btd6_api_processor",
            chat_monkey_root=root / "ChatMonkeyTD",
            subprocess_runner=subprocess_runner,
            image_gen_api_base_url=str(image_gen_api_base_url or "").strip().rstrip("/"),
            image_gen_api_key=str(image_gen_api_key or "").strip(),
            image_gen_model=str(image_gen_model or "").strip(),
            image_gen_request_timeout=max(1.0, float(image_gen_request_timeout)),
            image_gen_max_retries=max(0, min(5, int(image_gen_max_retries))),
            image_gen_retry_delay_seconds=max(
                0.0,
                min(60.0, float(image_gen_retry_delay_seconds)),
            ),
            image_gen_use_proxy=bool(image_gen_use_proxy),
            image_gen_max_bytes=max(1024, int(image_gen_max_bytes)),
        )


def _resolve_under_root(plugin_root: Path, path: str | Path) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target.resolve()
    return (plugin_root / target).resolve()


def object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def ok_result(
    *,
    module: str,
    command: str,
    data: dict[str, Any] | None = None,
    message: str = "工具执行完成",
    extra_prompt: str = "",
    response_type: str = DEFAULT_RESPONSE_TYPE,
    image_path: str = "",
) -> CommandResultPayload:
    return {
        "module": module,
        "ok": True,
        "command": command,
        "data": data or {},
        "message": message,
        "extra_prompt": extra_prompt,
        "response_type": response_type,
        "image_path": image_path,
    }


def fail_result(
    *,
    module: str,
    command: str,
    data: dict[str, Any] | None = None,
    message: str = "工具执行失败，请稍后重试。",
    extra_prompt: str = "",
    response_type: str = DEFAULT_RESPONSE_TYPE,
    image_path: str = "",
) -> CommandResultPayload:
    return {
        "module": module,
        "ok": False,
        "command": command,
        "data": data or {},
        "message": message,
        "extra_prompt": extra_prompt,
        "response_type": response_type,
        "image_path": image_path,
    }


def image_result(
    *,
    module: str,
    command: str,
    image_path: str,
    data: dict[str, Any] | None = None,
    message: str = "图片已生成",
    extra_prompt: str = "",
) -> CommandResultPayload:
    return ok_result(
        module=module,
        command=command,
        data=data,
        message=message,
        extra_prompt=extra_prompt,
        response_type="image",
        image_path=image_path,
    )


def normalize_result(
    result: CommandResultPayload,
    *,
    default_module: str,
    default_command: str,
) -> CommandResultPayload:
    response_type = str(
        result.get("response_type", DEFAULT_RESPONSE_TYPE) or DEFAULT_RESPONSE_TYPE
    )
    return {
        "module": str(result.get("module", default_module) or default_module),
        "ok": bool(result.get("ok", False)),
        "command": str(result.get("command", default_command) or default_command),
        "data": result.get("data", {}) or {},
        "message": str(result.get("message", "") or ""),
        "extra_prompt": str(result.get("extra_prompt", "") or ""),
        "response_type": response_type.lower(),
        "image_path": str(result.get("image_path", "") or ""),
    }
