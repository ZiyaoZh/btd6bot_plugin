from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ACTIVITY_BRIEF_EXECUTION_TIMEOUT_SECONDS = 180.0
ACTIVITY_BRIEF_RUN_TIMEOUT_MARGIN_SECONDS = 60.0


@dataclass(frozen=True)
class AppSettings:
    env_file: str
    target_group_id: str
    target_group_ids: tuple[str, ...]
    api_key: str
    api_base_url: str = "https://api.openai.com/v1"
    models: tuple[str, ...] = ("gpt-4o-mini",)
    system_prompt: str = "你是一个友好、简洁、乐于助人的群聊机器人。"
    router_tool_hints_prompt: str = ""
    request_timeout: int = 30
    llm_max_retries: int = 20
    history_turns: int = 6
    max_reply_chars: int = 1200
    trigger_prefix: str = ""
    bot_qq: str = ""
    bot_name: str = "机器人"
    manual_file: str = "manuals/program_manual.md"
    max_tool_rounds: int = 4
    proactive_chat_enabled: bool = True
    db_file: str = "data/bot_data.db"
    flow_log_file: str = "logs/network.log"
    context_compact_max_chars: int = 12000
    context_compact_keep_recent_turns: int = 6
    context_compact_summary_max_chars: int = 1200
    context_max_input_tokens: int = 24000
    context_image_token_reserve: int = 2048
    context_group_max_chars: int = 4000
    context_group_max_age_seconds: int = 86400
    memory_persistence_enabled: bool = True
    memory_retention_days: int = 90
    context_compact_model: str = ""
    context_compact_api_base_url: str = ""
    context_compact_api_key: str = ""
    recent_group_window_size: int = 10
    tool_timeout_seconds: float = 30.0
    knowledge_max_read_chars: int = 350000
    max_tool_result_chars: int = 400000
    run_timeout_seconds: float = 120.0
    image_gen_api_base_url: str = ""
    image_gen_api_key: str = ""
    image_gen_model: str = ""
    image_gen_request_timeout: float = 180.0
    image_gen_max_retries: int = 2
    image_gen_retry_delay_seconds: float = 2.0
    image_gen_use_proxy: bool = False
    image_gen_max_bytes: int = 20 * 1024 * 1024
    artifact_root: str = "data/artifacts"
    artifact_activity_ttl_seconds: float = 600.0
    artifact_image_ttl_seconds: float = 7 * 86_400.0
    artifact_default_ttl_seconds: float = 86_400.0
    artifact_archive_retention_seconds: float = 7 * 86_400.0
    vision_enabled: bool = False
    vision_group_ids: tuple[str, ...] = ()
    vision_cache_root: str = "data/chat_media"
    vision_download_timeout_seconds: float = 10.0
    vision_max_image_bytes: int = 10 * 1024 * 1024
    vision_max_pixels: int = 20_000_000
    vision_max_dimension: int = 2048
    vision_cache_ttl_seconds: float = 6 * 3600.0
    vision_cache_capacity_bytes: int = 512 * 1024 * 1024
    vision_current_image_limit: int = 4
    vision_history_image_limit: int = 2
    vision_total_image_limit: int = 4
    vision_image_detail: str = "auto"
    vision_summary_enabled: bool = True
    vision_summary_image_limit: int = 4
    vision_summary_max_chars: int = 160

    def is_vision_enabled_for_group(self, group_id: str) -> bool:
        if not self.vision_enabled:
            return False
        allowed_group_ids = self.vision_group_ids or self.target_group_ids
        return str(group_id) in allowed_group_ids


def _read_prompt_from_file(path: Path) -> str:
    if not path.exists():
        raise ValueError(f"系统提示词文件不存在: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"系统提示词文件为空: {path}")
    return text


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _parse_models(raw: str) -> tuple[str, ...]:
    items = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not items:
        return ("gpt-4o-mini",)
    # Keep order and remove duplicates.
    return tuple(dict.fromkeys(items))


def _parse_group_ids(raw: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(part.strip() for part in (raw or "").split(",") if part.strip())
    )


def calculate_image_gen_execution_timeout(
    request_timeout: float,
    max_retries: int,
    retry_delay_seconds: float,
) -> float:
    retries = max(0, int(max_retries))
    attempts = retries + 1
    retry_wait = max(0.0, float(retry_delay_seconds)) * (2**retries - 1)
    return attempts * max(1.0, float(request_timeout)) + retry_wait + 10.0


def load_settings(env_path: str = ".env") -> AppSettings:
    _load_env_file(Path(env_path))

    base_dir = Path(env_path).resolve().parent

    target_group_id = os.getenv("BOT_TARGET_GROUP_ID", "").strip()
    if not target_group_id:
        raise ValueError("缺少环境变量 BOT_TARGET_GROUP_ID")

    target_group_ids_raw = os.getenv("BOT_TARGET_GROUP_IDS", "").strip()
    parsed_group_ids = [
        gid.strip() for gid in target_group_ids_raw.split(",") if gid.strip()
    ]
    if not parsed_group_ids:
        parsed_group_ids = [target_group_id]

    if target_group_id not in parsed_group_ids:
        parsed_group_ids.insert(0, target_group_id)

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少环境变量 LLM_API_KEY")

    api_base_url = (
        os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    )
    models_raw = os.getenv("LLM_MODELS", "").strip()
    if not models_raw:
        # Backward compatible: old single-model env var can also be comma-separated.
        models_raw = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
    models = _parse_models(models_raw)
    prompt_file_raw = os.getenv(
        "LLM_SYSTEM_PROMPT_FILE", os.getenv("SYSTEM_PROMPT_FILE", "")
    ).strip()
    if prompt_file_raw:
        prompt_path = Path(prompt_file_raw)
        if not prompt_path.is_absolute():
            prompt_path = base_dir / prompt_path
        system_prompt = _read_prompt_from_file(prompt_path)
    else:
        system_prompt = os.getenv(
            "LLM_SYSTEM_PROMPT",
            os.getenv("SYSTEM_PROMPT", "你是一个友好、简洁、乐于助人的群聊机器人。"),
        ).strip()

    router_hints_file_raw = os.getenv(
        "LLM_ROUTER_TOOL_HINTS_FILE", "prompts/router_tool_hints.md"
    ).strip()
    router_tool_hints_prompt = ""
    if router_hints_file_raw:
        router_hints_path = Path(router_hints_file_raw)
        if not router_hints_path.is_absolute():
            router_hints_path = base_dir / router_hints_path
        if router_hints_path.exists():
            router_tool_hints_prompt = router_hints_path.read_text(
                encoding="utf-8"
            ).strip()

    request_timeout = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
    llm_max_retries = int(os.getenv("LLM_MAX_RETRIES", "20"))
    history_turns = int(os.getenv("LLM_HISTORY_TURNS", "6"))
    max_reply_chars = int(os.getenv("LLM_MAX_REPLY_CHARS", "1200"))
    trigger_prefix = os.getenv("BOT_TRIGGER_PREFIX", "").strip()
    bot_qq = os.getenv("BOT_QQ", "").strip()
    bot_name = os.getenv("BOT_NAME", "机器人").strip() or "机器人"
    manual_file = (
        os.getenv("BOT_MANUAL_FILE", "manuals/program_manual.md").strip()
        or "manuals/program_manual.md"
    )
    max_tool_rounds = int(os.getenv("BOT_MAX_TOOL_ROUNDS", "4"))
    proactive_chat_enabled = os.getenv(
        "BOT_PROACTIVE_CHAT_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    db_file = os.getenv("BOT_DB_FILE", "data/bot_data.db").strip() or "data/bot_data.db"
    flow_log_file = (
        os.getenv("BOT_FLOW_LOG_FILE", "logs/network.log").strip() or "logs/network.log"
    )
    context_compact_max_chars = int(os.getenv("LLM_CONTEXT_COMPACT_MAX_CHARS", "12000"))
    context_compact_keep_recent_turns = int(
        os.getenv("LLM_CONTEXT_COMPACT_KEEP_RECENT_TURNS", "6")
    )
    context_compact_summary_max_chars = int(
        os.getenv("LLM_CONTEXT_COMPACT_SUMMARY_MAX_CHARS", "1200")
    )
    context_compact_model = os.getenv("LLM_CONTEXT_COMPACT_MODEL", "").strip()
    context_compact_api_base_url = os.getenv(
        "LLM_CONTEXT_COMPACT_API_BASE_URL", ""
    ).strip()
    context_compact_api_key = os.getenv("LLM_CONTEXT_COMPACT_API_KEY", "").strip()
    recent_group_window_size = int(os.getenv("BOT_RECENT_GROUP_WINDOW_SIZE", "10"))
    tool_timeout_seconds = float(os.getenv("BOT_TOOL_TIMEOUT_SECONDS", "30"))
    knowledge_max_read_chars = int(os.getenv("BOT_KNOWLEDGE_MAX_READ_CHARS", "350000"))
    max_tool_result_chars = int(os.getenv("BOT_MAX_TOOL_RESULT_CHARS", "400000"))
    run_timeout_seconds = float(os.getenv("BOT_RUN_TIMEOUT_SECONDS", "120"))
    run_timeout_seconds = max(
        run_timeout_seconds,
        ACTIVITY_BRIEF_EXECUTION_TIMEOUT_SECONDS
        + ACTIVITY_BRIEF_RUN_TIMEOUT_MARGIN_SECONDS,
    )
    image_gen_api_base_url = os.getenv("IMAGE_GEN_API_BASE_URL", "").strip().rstrip("/")
    image_gen_api_key = os.getenv("IMAGE_GEN_API_KEY", "").strip()
    image_gen_model = os.getenv("IMAGE_GEN_MODEL", "").strip()
    image_gen_request_timeout = max(
        1.0,
        float(os.getenv("IMAGE_GEN_REQUEST_TIMEOUT", "180")),
    )
    image_gen_max_retries = max(
        0,
        min(5, int(os.getenv("IMAGE_GEN_MAX_RETRIES", "2"))),
    )
    image_gen_retry_delay_seconds = max(
        0.0,
        min(60.0, float(os.getenv("IMAGE_GEN_RETRY_DELAY_SECONDS", "2"))),
    )
    image_gen_use_proxy = os.getenv("IMAGE_GEN_USE_PROXY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    image_gen_max_bytes = int(os.getenv("IMAGE_GEN_MAX_BYTES", str(20 * 1024 * 1024)))
    artifact_root = (
        os.getenv("BOT_ARTIFACT_ROOT", "data/artifacts").strip() or "data/artifacts"
    )
    artifact_activity_ttl_seconds = max(
        1.0,
        float(os.getenv("BOT_ARTIFACT_ACTIVITY_TTL_SECONDS", "600")),
    )
    artifact_image_ttl_seconds = max(
        1.0,
        float(os.getenv("BOT_ARTIFACT_IMAGE_TTL_SECONDS", str(7 * 86_400))),
    )
    artifact_default_ttl_seconds = max(
        1.0,
        float(os.getenv("BOT_ARTIFACT_DEFAULT_TTL_SECONDS", "86400")),
    )
    artifact_archive_retention_seconds = max(
        0.0,
        float(
            os.getenv(
                "BOT_ARTIFACT_ARCHIVE_RETENTION_SECONDS",
                str(7 * 86_400),
            )
        ),
    )
    vision_enabled = os.getenv("LLM_VISION_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    vision_group_ids = _parse_group_ids(os.getenv("LLM_VISION_GROUP_IDS", ""))
    unknown_vision_group_ids = set(vision_group_ids).difference(parsed_group_ids)
    if unknown_vision_group_ids:
        unknown = ", ".join(sorted(unknown_vision_group_ids))
        raise ValueError(f"LLM_VISION_GROUP_IDS 包含非目标群: {unknown}")
    vision_cache_root = (
        os.getenv("LLM_VISION_CACHE_ROOT", "data/chat_media").strip()
        or "data/chat_media"
    )
    vision_download_timeout_seconds = max(
        0.1, float(os.getenv("LLM_VISION_DOWNLOAD_TIMEOUT_SECONDS", "10"))
    )
    vision_max_image_bytes = max(
        1024, int(os.getenv("LLM_VISION_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
    )
    vision_max_pixels = max(1, int(os.getenv("LLM_VISION_MAX_PIXELS", "20000000")))
    vision_max_dimension = max(64, int(os.getenv("LLM_VISION_MAX_DIMENSION", "2048")))
    vision_cache_ttl_seconds = max(
        1.0, float(os.getenv("LLM_VISION_CACHE_TTL_SECONDS", str(6 * 3600)))
    )
    vision_cache_capacity_bytes = max(
        vision_max_image_bytes,
        int(os.getenv("LLM_VISION_CACHE_CAPACITY_BYTES", str(512 * 1024 * 1024))),
    )
    vision_current_image_limit = max(
        0, int(os.getenv("LLM_VISION_CURRENT_IMAGE_LIMIT", "4"))
    )
    vision_history_image_limit = max(
        0, int(os.getenv("LLM_VISION_HISTORY_IMAGE_LIMIT", "2"))
    )
    vision_total_image_limit = max(
        0, int(os.getenv("LLM_VISION_TOTAL_IMAGE_LIMIT", "4"))
    )
    vision_image_detail = os.getenv("LLM_VISION_IMAGE_DETAIL", "auto").strip().lower()
    if vision_image_detail not in {"auto", "low", "high"}:
        raise ValueError("LLM_VISION_IMAGE_DETAIL 必须是 auto、low 或 high")
    vision_summary_enabled = os.getenv(
        "LLM_VISION_SUMMARY_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    vision_summary_image_limit = max(
        0, int(os.getenv("LLM_VISION_SUMMARY_IMAGE_LIMIT", "4"))
    )
    vision_summary_max_chars = max(
        40, int(os.getenv("LLM_VISION_SUMMARY_MAX_CHARS", "160"))
    )

    if all((image_gen_api_base_url, image_gen_api_key, image_gen_model)):
        image_execution_timeout = calculate_image_gen_execution_timeout(
            image_gen_request_timeout,
            image_gen_max_retries,
            image_gen_retry_delay_seconds,
        )
        run_timeout_seconds = max(run_timeout_seconds, image_execution_timeout + 60.0)

    return AppSettings(
        env_file=str(Path(env_path).resolve()),
        target_group_id=target_group_id,
        target_group_ids=tuple(dict.fromkeys(parsed_group_ids)),
        api_key=api_key,
        api_base_url=api_base_url,
        models=models,
        system_prompt=system_prompt,
        router_tool_hints_prompt=router_tool_hints_prompt,
        request_timeout=request_timeout,
        llm_max_retries=max(0, llm_max_retries),
        history_turns=history_turns,
        max_reply_chars=max_reply_chars,
        trigger_prefix=trigger_prefix,
        bot_qq=bot_qq,
        bot_name=bot_name,
        manual_file=manual_file,
        max_tool_rounds=max(1, max_tool_rounds),
        proactive_chat_enabled=proactive_chat_enabled,
        db_file=db_file,
        flow_log_file=flow_log_file,
        context_compact_max_chars=max(1000, context_compact_max_chars),
        context_compact_keep_recent_turns=max(1, context_compact_keep_recent_turns),
        context_compact_summary_max_chars=max(120, context_compact_summary_max_chars),
        context_max_input_tokens=max(
            1024, int(os.getenv("LLM_CONTEXT_MAX_INPUT_TOKENS", "24000"))
        ),
        context_image_token_reserve=max(
            1, int(os.getenv("LLM_CONTEXT_IMAGE_TOKEN_RESERVE", "2048"))
        ),
        context_group_max_chars=max(
            500, int(os.getenv("BOT_GROUP_CONTEXT_MAX_CHARS", "4000"))
        ),
        context_group_max_age_seconds=max(
            60, int(os.getenv("BOT_GROUP_CONTEXT_MAX_AGE_SECONDS", "86400"))
        ),
        memory_persistence_enabled=os.getenv(
            "BOT_MEMORY_PERSISTENCE_ENABLED", "true"
        ).lower()
        in {"true", "1", "yes", "on"},
        memory_retention_days=max(1, int(os.getenv("BOT_MEMORY_RETENTION_DAYS", "90"))),
        context_compact_model=context_compact_model,
        context_compact_api_base_url=context_compact_api_base_url,
        context_compact_api_key=context_compact_api_key,
        recent_group_window_size=max(1, min(recent_group_window_size, 20)),
        tool_timeout_seconds=max(0.1, tool_timeout_seconds),
        knowledge_max_read_chars=max(500, knowledge_max_read_chars),
        max_tool_result_chars=max(1000, max_tool_result_chars),
        run_timeout_seconds=max(1.0, run_timeout_seconds),
        image_gen_api_base_url=image_gen_api_base_url,
        image_gen_api_key=image_gen_api_key,
        image_gen_model=image_gen_model,
        image_gen_request_timeout=image_gen_request_timeout,
        image_gen_max_retries=image_gen_max_retries,
        image_gen_retry_delay_seconds=image_gen_retry_delay_seconds,
        image_gen_use_proxy=image_gen_use_proxy,
        image_gen_max_bytes=max(1024, image_gen_max_bytes),
        artifact_root=artifact_root,
        artifact_activity_ttl_seconds=artifact_activity_ttl_seconds,
        artifact_image_ttl_seconds=artifact_image_ttl_seconds,
        artifact_default_ttl_seconds=artifact_default_ttl_seconds,
        artifact_archive_retention_seconds=artifact_archive_retention_seconds,
        vision_enabled=vision_enabled,
        vision_group_ids=vision_group_ids,
        vision_cache_root=vision_cache_root,
        vision_download_timeout_seconds=vision_download_timeout_seconds,
        vision_max_image_bytes=vision_max_image_bytes,
        vision_max_pixels=vision_max_pixels,
        vision_max_dimension=vision_max_dimension,
        vision_cache_ttl_seconds=vision_cache_ttl_seconds,
        vision_cache_capacity_bytes=vision_cache_capacity_bytes,
        vision_current_image_limit=vision_current_image_limit,
        vision_history_image_limit=vision_history_image_limit,
        vision_total_image_limit=vision_total_image_limit,
        vision_image_detail=vision_image_detail,
        vision_summary_enabled=vision_summary_enabled,
        vision_summary_image_limit=vision_summary_image_limit,
        vision_summary_max_chars=vision_summary_max_chars,
    )
