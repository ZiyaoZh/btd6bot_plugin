from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOGGER_NAME = "bot.flow"
_FLOW_LOG_MAX_BYTES = 10 * 1024 * 1024
_FLOW_LOG_BACKUP_COUNT = 10


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
        "ts": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "stage": stage,
        **{k: _to_jsonable(v) for k, v in fields.items()},
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
