from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class AppSettings:
    env_file: str
    target_group_id: str
    target_group_ids: Tuple[str, ...]
    api_key: str
    api_base_url: str = "https://api.openai.com/v1"
    models: Tuple[str, ...] = ("gpt-4o-mini",)
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
    command_guide_dir: str = "command_skills"
    max_tool_rounds: int = 4
    proactive_chat_enabled: bool = True
    db_file: str = "data/bot_data.db"
    flow_log_file: str = "logs/network.log"
    context_compact_max_chars: int = 100000
    context_compact_keep_recent_turns: int = 10
    context_compact_summary_max_chars: int = 360
    context_compact_model: str = ""
    context_compact_api_base_url: str = ""
    context_compact_api_key: str = ""


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


def _parse_models(raw: str) -> Tuple[str, ...]:
    items = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not items:
        return ("gpt-4o-mini",)
    # Keep order and remove duplicates.
    return tuple(dict.fromkeys(items))


def load_settings(env_path: str = ".env") -> AppSettings:
    _load_env_file(Path(env_path))

    base_dir = Path(env_path).resolve().parent

    target_group_id = os.getenv("BOT_TARGET_GROUP_ID", "").strip()
    if not target_group_id:
        raise ValueError("缺少环境变量 BOT_TARGET_GROUP_ID")

    target_group_ids_raw = os.getenv("BOT_TARGET_GROUP_IDS", "").strip()
    parsed_group_ids = [gid.strip() for gid in target_group_ids_raw.split(",") if gid.strip()]
    if not parsed_group_ids:
        parsed_group_ids = [target_group_id]

    if target_group_id not in parsed_group_ids:
        parsed_group_ids.insert(0, target_group_id)

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少环境变量 LLM_API_KEY")

    api_base_url = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    models_raw = os.getenv("LLM_MODELS", "").strip()
    if not models_raw:
        # Backward compatible: old single-model env var can also be comma-separated.
        models_raw = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
    models = _parse_models(models_raw)
    prompt_file_raw = os.getenv("LLM_SYSTEM_PROMPT_FILE", os.getenv("SYSTEM_PROMPT_FILE", "")).strip()
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

    router_hints_file_raw = os.getenv("LLM_ROUTER_TOOL_HINTS_FILE", "prompts/router_tool_hints.md").strip()
    router_tool_hints_prompt = ""
    if router_hints_file_raw:
        router_hints_path = Path(router_hints_file_raw)
        if not router_hints_path.is_absolute():
            router_hints_path = base_dir / router_hints_path
        if router_hints_path.exists():
            router_tool_hints_prompt = router_hints_path.read_text(encoding="utf-8").strip()

    request_timeout = int(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
    llm_max_retries = int(os.getenv("LLM_MAX_RETRIES", "20"))
    history_turns = int(os.getenv("LLM_HISTORY_TURNS", "6"))
    max_reply_chars = int(os.getenv("LLM_MAX_REPLY_CHARS", "1200"))
    trigger_prefix = os.getenv("BOT_TRIGGER_PREFIX", "").strip()
    bot_qq = os.getenv("BOT_QQ", "").strip()
    bot_name = os.getenv("BOT_NAME", "机器人").strip() or "机器人"
    manual_file = os.getenv("BOT_MANUAL_FILE", "manuals/program_manual.md").strip() or "manuals/program_manual.md"
    command_guide_dir = os.getenv("BOT_COMMAND_GUIDE_DIR", "command_skills").strip() or "command_skills"
    max_tool_rounds = int(os.getenv("BOT_MAX_TOOL_ROUNDS", "4"))
    proactive_chat_enabled = os.getenv("BOT_PROACTIVE_CHAT_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    db_file = os.getenv("BOT_DB_FILE", "data/bot_data.db").strip() or "data/bot_data.db"
    flow_log_file = os.getenv("BOT_FLOW_LOG_FILE", "logs/network.log").strip() or "logs/network.log"
    context_compact_max_chars = int(os.getenv("LLM_CONTEXT_COMPACT_MAX_CHARS", "100000"))
    context_compact_keep_recent_turns = int(os.getenv("LLM_CONTEXT_COMPACT_KEEP_RECENT_TURNS", "10"))
    context_compact_summary_max_chars = int(os.getenv("LLM_CONTEXT_COMPACT_SUMMARY_MAX_CHARS", "360"))
    context_compact_model = os.getenv("LLM_CONTEXT_COMPACT_MODEL", "").strip()
    context_compact_api_base_url = os.getenv("LLM_CONTEXT_COMPACT_API_BASE_URL", "").strip()
    context_compact_api_key = os.getenv("LLM_CONTEXT_COMPACT_API_KEY", "").strip()

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
        command_guide_dir=command_guide_dir,
        max_tool_rounds=max(1, max_tool_rounds),
        proactive_chat_enabled=proactive_chat_enabled,
        db_file=db_file,
        flow_log_file=flow_log_file,
        context_compact_max_chars=max(1000, context_compact_max_chars),
        context_compact_keep_recent_turns=max(1, context_compact_keep_recent_turns),
        context_compact_summary_max_chars=max(120, context_compact_summary_max_chars),
        context_compact_model=context_compact_model,
        context_compact_api_base_url=context_compact_api_base_url,
        context_compact_api_key=context_compact_api_key,
    )
