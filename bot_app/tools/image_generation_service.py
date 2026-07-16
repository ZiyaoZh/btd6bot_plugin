from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import requests


class ImageGenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int | None = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = attempts


def generate_image_file(
    *,
    prompt: str,
    api_base_url: str,
    api_key: str,
    model: str,
    output_dir: Path,
    timeout: float,
    max_bytes: int,
    max_retries: int = 2,
    retry_delay_seconds: float = 2.0,
    use_proxy: bool = True,
) -> Path:
    retries = max(0, int(max_retries))
    base_delay = max(0.0, float(retry_delay_seconds))
    session: requests.Session | None = None
    post = requests.post
    get = requests.get
    if not use_proxy:
        session = requests.Session()
        session.trust_env = False
        post = session.post
        get = session.get
    try:
        for attempt in range(1, retries + 2):
            try:
                return _generate_image_file_once(
                    prompt=prompt,
                    api_base_url=api_base_url,
                    api_key=api_key,
                    model=model,
                    output_dir=output_dir,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    post=post,
                    get=get,
                )
            except ImageGenerationError as exc:
                exc.attempts = attempt
                if not exc.retryable or attempt > retries:
                    raise
                delay = min(base_delay * (2 ** (attempt - 1)), 60.0)
                if delay > 0:
                    time.sleep(delay)
        raise AssertionError("unreachable image generation retry state")
    finally:
        if session is not None:
            session.close()


def _generate_image_file_once(
    *,
    prompt: str,
    api_base_url: str,
    api_key: str,
    model: str,
    output_dir: Path,
    timeout: float,
    max_bytes: int,
    post: Any,
    get: Any,
) -> Path:
    endpoint = _generation_endpoint(api_base_url)
    try:
        response = post(
            endpoint,
            json={"model": model, "prompt": prompt, "n": 1},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise ImageGenerationError(
            "AI 生图服务请求超时，请稍后重试。",
            code="request_timeout",
            retryable=True,
        ) from exc
    except requests.RequestException as exc:
        raise ImageGenerationError(
            "暂时无法连接 AI 生图服务，请稍后重试。",
            code="request_failed",
            retryable=True,
        ) from exc

    if not response.ok:
        raise _http_error(response.status_code)

    try:
        payload = response.json()
    except ValueError as exc:
        raise ImageGenerationError(
            "AI 生图服务返回了无法解析的数据。",
            code="invalid_json",
            retryable=True,
        ) from exc

    image_bytes = _extract_image_bytes(
        payload,
        timeout=timeout,
        max_bytes=max_bytes,
        get=get,
    )
    extension = _detect_image_extension(image_bytes)
    if not extension:
        raise ImageGenerationError(
            "AI 生图服务返回的内容不是支持的图片格式。",
            code="unsupported_image",
            retryable=True,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"ai_image_{timestamp}_{uuid4().hex[:8]}.{extension}"
    destination = (output_dir / filename).resolve()
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(image_bytes)
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _generation_endpoint(api_base_url: str) -> str:
    base_url = str(api_base_url or "").strip().rstrip("/")
    if base_url.endswith("/images/generations"):
        return base_url
    return f"{base_url}/images/generations"


def _extract_image_bytes(
    payload: Any,
    *,
    timeout: float,
    max_bytes: int,
    get: Any,
) -> bytes:
    if not isinstance(payload, dict):
        raise ImageGenerationError(
            "AI 生图服务返回的数据结构无效。",
            code="invalid_response",
            retryable=True,
        )
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ImageGenerationError(
            "AI 生图服务没有返回图片。",
            code="missing_image",
            retryable=True,
        )

    image = data[0]
    encoded = str(image.get("b64_json", "") or "").strip()
    if encoded:
        return _decode_base64_image(encoded, max_bytes=max_bytes)

    image_url = str(image.get("url", "") or "").strip()
    if image_url.startswith("data:"):
        _, separator, encoded_data = image_url.partition(",")
        if not separator or ";base64" not in image_url[: image_url.index(",")]:
            raise ImageGenerationError(
                "AI 生图服务返回了无效的图片数据。",
                code="invalid_base64",
                retryable=True,
            )
        return _decode_base64_image(encoded_data, max_bytes=max_bytes)
    if image_url:
        return _download_image(
            image_url,
            timeout=timeout,
            max_bytes=max_bytes,
            get=get,
        )

    raise ImageGenerationError(
        "AI 生图服务没有返回可用的图片内容。",
        code="missing_image",
        retryable=True,
    )


def _decode_base64_image(encoded: str, *, max_bytes: int) -> bytes:
    compact = "".join(str(encoded or "").split())
    max_encoded_chars = ((max_bytes + 2) // 3) * 4
    if len(compact) > max_encoded_chars:
        raise ImageGenerationError(
            "AI 生图服务返回的图片过大。",
            code="image_too_large",
        )
    try:
        image_bytes = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenerationError(
            "AI 生图服务返回了无效的 Base64 图片。",
            code="invalid_base64",
            retryable=True,
        ) from exc
    _validate_image_size(image_bytes, max_bytes=max_bytes)
    return image_bytes


def _download_image(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    get: Any,
) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageGenerationError(
            "AI 生图服务返回了无效的图片地址。",
            code="invalid_image_url",
        )
    try:
        with get(url, stream=True, timeout=timeout) as response:
            if not response.ok:
                raise ImageGenerationError(
                    "AI 生图服务返回的图片下载失败。",
                    code="image_download_failed",
                    status_code=response.status_code,
                    retryable=(
                        response.status_code == 429 or response.status_code >= 500
                    ),
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ImageGenerationError(
                        "AI 生图服务返回的图片过大。",
                        code="image_too_large",
                    )
                chunks.append(chunk)
    except ImageGenerationError:
        raise
    except requests.Timeout as exc:
        raise ImageGenerationError(
            "下载 AI 生成图片超时，请稍后重试。",
            code="download_timeout",
            retryable=True,
        ) from exc
    except requests.RequestException as exc:
        raise ImageGenerationError(
            "下载 AI 生成图片失败，请稍后重试。",
            code="image_download_failed",
            retryable=True,
        ) from exc
    image_bytes = b"".join(chunks)
    _validate_image_size(image_bytes, max_bytes=max_bytes)
    return image_bytes


def _validate_image_size(image_bytes: bytes, *, max_bytes: int) -> None:
    if not image_bytes:
        raise ImageGenerationError(
            "AI 生图服务返回了空图片。",
            code="empty_image",
            retryable=True,
        )
    if len(image_bytes) > max_bytes:
        raise ImageGenerationError(
            "AI 生图服务返回的图片过大。",
            code="image_too_large",
        )


def _detect_image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        return "webp"
    return ""


def _http_error(status_code: int) -> ImageGenerationError:
    if status_code in {401, 403}:
        return ImageGenerationError(
            "AI 生图服务鉴权失败，请联系管理员检查配置。",
            code="authentication_failed",
            status_code=status_code,
        )
    if status_code == 429:
        return ImageGenerationError(
            "AI 生图服务当前繁忙，请稍后重试。",
            code="rate_limited",
            status_code=status_code,
            retryable=True,
        )
    if 400 <= status_code < 500:
        return ImageGenerationError(
            "AI 生图请求被服务拒绝，请调整描述后重试。",
            code="request_rejected",
            status_code=status_code,
        )
    return ImageGenerationError(
        "AI 生图服务暂时不可用，请稍后重试。",
        code="service_unavailable",
        status_code=status_code,
        retryable=True,
    )
