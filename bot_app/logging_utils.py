from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_LOGGER_NAME = "bot.flow"
_FLOW_LOG_MAX_BYTES = 10 * 1024 * 1024
_FLOW_LOG_BACKUP_COUNT = 10
_LOG_TIMEZONE = timezone(timedelta(hours=8))
_DATA_IMAGE_URL_RE = re.compile(
    r"data:(image/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)",
    re.IGNORECASE,
)
_HTTP_URL_RE = re.compile(r"https?://[^\s\]\[<>{}\"']+", re.IGNORECASE)


def configure_flow_logger(log_file: str) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    path = Path(log_file).resolve()
    current_file = ""
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            current_file = str(Path(handler.baseFilename).resolve())
            break

    if current_file == str(path) and logger.handlers:
        return logger

    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        path,
        maxBytes=_FLOW_LOG_MAX_BYTES,
        backupCount=_FLOW_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_flow(stage: str, trace_id: str, **fields: Any) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        return

    payload = {
        "ts": datetime.now(_LOG_TIMEZONE).isoformat(),
        "trace_id": trace_id,
        "stage": stage,
        **{k: sanitize_log_value(v) for k, v in fields.items()},
    }
    logger.info(json.dumps(payload, ensure_ascii=False))


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    return str(value)


def sanitize_log_value(value: Any) -> Any:
    """Remove image bodies and signed URL secrets from structured log fields."""
    if isinstance(value, dict):
        return {str(key): sanitize_log_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_log_value(item) for item in value]
    jsonable = _to_jsonable(value)
    if isinstance(jsonable, str):
        return _sanitize_log_text(jsonable)
    return jsonable


def _sanitize_log_text(value: str) -> str:
    def redact_data_url(match: re.Match[str]) -> str:
        mime_type, encoded = match.groups()
        try:
            binary = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return "[redacted invalid image data URL]"
        digest = hashlib.sha256(binary).hexdigest()
        return f"[redacted {mime_type.lower()} bytes={len(binary)} sha256={digest}]"

    redacted = _DATA_IMAGE_URL_RE.sub(redact_data_url, value)

    def strip_url_secrets(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            return "[redacted invalid URL]"
        if not parsed.query and not parsed.fragment:
            return raw_url
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    return _HTTP_URL_RE.sub(strip_url_secrets, redacted)
