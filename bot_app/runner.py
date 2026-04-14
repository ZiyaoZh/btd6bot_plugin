from __future__ import annotations

import re
import traceback
from pathlib import Path
from uuid import uuid4

from ncatbot.core import BotClient
from ncatbot.event.qq import GroupMessageEvent

from .command_processor import CommandProcessor
from .db_manager import BotDatabase
from .llm_client import LLMClient, LLMRequestError
from .llm_processor import LLMMessageProcessor
from .logging_utils import configure_flow_logger, log_flow
from .memory import GroupChatMemory
from .receiver import GroupMessageReceiver
from .sender import MessageSender
from .settings import load_settings
from .tool_selector import ToolSelector


def create_bot() -> BotClient:
    settings = load_settings()
    configure_flow_logger(settings.flow_log_file)
    db = BotDatabase(settings.db_file)

    bot = BotClient()
    receiver = GroupMessageReceiver(settings, db=db)
    sender = MessageSender(bot)
    memory = GroupChatMemory(history_turns=settings.history_turns)
    llm = LLMClient(settings=settings)
    llm_processor = LLMMessageProcessor(llm=llm, memory=memory, settings=settings)
    tool_selector = ToolSelector()
    command_processor = CommandProcessor(
        db=db,
        manual_file=settings.manual_file,
        command_guide_dir=settings.command_guide_dir,
    )
    bot_enabled = True

    @bot.group_event()
    async def on_group_message(msg: GroupMessageEvent):
        nonlocal bot_enabled
        trace_id = uuid4().hex[:12]
        log_flow(
            stage="receiver.raw_message",
            trace_id=trace_id,
            raw_message=msg.raw_message,
            group_id=str(msg.group_id),
            user_id=str(msg.user_id),
        )

        if receiver.should_track_message(msg):
            receiver.sync_message_to_db(msg)
            log_flow(stage="db.synced", trace_id=trace_id, group_id=str(msg.group_id), user_id=str(msg.user_id))

        if not receiver.should_accept(msg):
            log_flow(stage="receiver.rejected", trace_id=trace_id, reason="message filtered")
            return

        admin_control = receiver.parse_admin_control(msg)
        if admin_control["matched"]:
            log_flow(stage="receiver.admin_control", trace_id=trace_id, control=admin_control)

            if not admin_control["authorized"]:
                await sender.send_group_text(
                    group_id=str(msg.group_id),
                    text=f"该指令仅限群主/管理员在带@{settings.bot_name}时使用",
                    at_user_id=str(msg.user_id),
                )
                log_flow(
                    stage="sender.sent",
                    trace_id=trace_id,
                    reply=f"该指令仅限群主/管理员在带@{settings.bot_name}时使用",
                )
                return

            if admin_control["action"] == "stop":
                bot_enabled = False
                await sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已终止机器人对话处理",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已终止机器人对话处理")
                return

            if admin_control["action"] == "start":
                bot_enabled = True
                await sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已启动机器人对话处理",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已启动机器人对话处理")
                return

            if admin_control["action"] == "proactive_on":
                receiver.set_proactive_chat_enabled(True)
                _persist_env_bool(settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", True)
                await sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已开启主动接话",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已开启主动接话")
                return

            if admin_control["action"] == "proactive_off":
                receiver.set_proactive_chat_enabled(False)
                _persist_env_bool(settings.env_file, "BOT_PROACTIVE_CHAT_ENABLED", False)
                await sender.send_group_text(
                    group_id=str(msg.group_id),
                    text="已关闭主动接话，仅响应@消息",
                    at_user_id=str(msg.user_id),
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply="已关闭主动接话，仅响应@消息")
                return

        if not bot_enabled:
            log_flow(stage="receiver.rejected", trace_id=trace_id, reason="bot is stopped")
            return

        text = receiver.normalize_text(msg.raw_message or "")
        if not text:
            log_flow(stage="receiver.rejected", trace_id=trace_id, reason="empty text after normalize")
            return

        if text in {"/reset", "#reset", "重置会话"}:
            llm_processor.reset_user_context(str(msg.group_id), str(msg.user_id))
            log_flow(stage="memory.reset", trace_id=trace_id, group_id=str(msg.group_id), user_id=str(msg.user_id))
            await sender.send_group_text(
                group_id=str(msg.group_id),
                text="你的会话上下文已重置",
                at_user_id=str(msg.user_id),
            )
            log_flow(stage="sender.sent", trace_id=trace_id, reply="你的会话上下文已重置")
            return

        event_payload = receiver.build_event_payload(msg, text)
        log_flow(stage="receiver.event_payload", trace_id=trace_id, event_payload=event_payload)

        try:
            intent = await llm_processor.plan_intent(event_payload, trace_id=trace_id)
            log_flow(stage="judge.route", trace_id=trace_id, target=intent["target"], intent=intent)

            # 判官模块: 用 target 字段决定交给聊天模块还是指令模块。
            if intent["target"] == "noop":
                log_flow(stage="judge.noop", trace_id=trace_id, reason=intent.get("reason", ""))
                return
            if intent["target"] in {"tool_selector", "command_processor"}:
                if intent["target"] == "tool_selector":
                    guide_bundle = await tool_selector.load_tool_guides(intent, event_payload, command_processor)
                    log_flow(stage="tool_selector.bundle", trace_id=trace_id, guide_bundle=guide_bundle)
                    tool_history: list[dict] = [{"intent": intent, "command_result": guide_bundle}]
                else:
                    command_result = await command_processor.execute(intent, event_payload)
                    log_flow(stage="command.result", trace_id=trace_id, command_result=command_result)
                    tool_history = [{"intent": intent, "command_result": command_result}]

                final_reply = ""

                for round_index in range(settings.max_tool_rounds):
                    decision = await llm_processor.decide_next_tool_step(
                        event_payload,
                        tool_history,
                        trace_id=trace_id,
                    )
                    log_flow(
                        stage="tool.decision",
                        trace_id=trace_id,
                        round=round_index + 1,
                        decision=decision,
                    )

                    if decision["decision"] == "final":
                        final_reply = str(decision.get("final_reply", "") or "").strip()
                        break

                    next_intent = decision["intent"]
                    if next_intent.get("target") == "tool_selector":
                        guide_bundle = await tool_selector.load_tool_guides(next_intent, event_payload, command_processor)
                        log_flow(stage="tool_selector.bundle", trace_id=trace_id, guide_bundle=guide_bundle)
                        tool_history.append({"intent": next_intent, "command_result": guide_bundle})
                    else:
                        command_result = await command_processor.execute(next_intent, event_payload)
                        log_flow(stage="command.result", trace_id=trace_id, command_result=command_result)
                        tool_history.append({"intent": next_intent, "command_result": command_result})

                if not final_reply:
                    final_reply = await llm_processor.render_command_result(
                        event_payload,
                        tool_history[-1]["intent"],
                        tool_history[-1]["command_result"],
                        trace_id=trace_id,
                    )

                last_result = tool_history[-1].get("command_result", {}) if tool_history else {}
                response_type = str(last_result.get("response_type", "text") or "text").strip().lower()
                image_path = str(last_result.get("image_path", "") or "").strip()
            else:
                final_reply = await llm_processor.chat_reply(event_payload, trace_id=trace_id)
                response_type = "text"
                image_path = ""

            reply_for_memory = final_reply
            total_tokens = llm.pop_trace_total_tokens(trace_id)
            final_reply = _append_token_cost_line(reply_for_memory, total_tokens, settings.max_reply_chars)

            if intent["target"] in {"tool_selector", "command_processor"}:
                await llm_processor.record_tool_turn(event_payload, reply_for_memory, trace_id=trace_id)

            if response_type == "image" and image_path:
                await sender.send_group_image(
                    group_id=event_payload["group_id"],
                    image_path=image_path,
                    text=final_reply,
                    at_user_id=event_payload["user_id"],
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply=final_reply, image_path=image_path)
            else:
                await sender.send_group_text(
                    group_id=event_payload["group_id"],
                    text=final_reply,
                    at_user_id=event_payload["user_id"],
                )
                log_flow(stage="sender.sent", trace_id=trace_id, reply=final_reply)
        except Exception as exc:
            log_flow(
                stage="pipeline.error",
                trace_id=trace_id,
                error_type=type(exc).__name__,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            if isinstance(exc, LLMRequestError):
                log_flow(
                    stage="pipeline.silent_drop",
                    trace_id=trace_id,
                    reason="llm_request_failed_after_retries",
                )
                return

            public_error = f"请求处理失败: {type(exc).__name__}"
            await sender.send_group_text(
                group_id=str(msg.group_id),
                text=public_error,
                at_user_id=str(msg.user_id),
            )
            log_flow(stage="sender.sent", trace_id=trace_id, reply=public_error)

    return bot


def _append_token_cost_line(text: str, total_tokens: int, max_chars: int) -> str:
    base = (text or "").strip()
    cost_line = f"本次花费{max(0, int(total_tokens))}tokens"
    combined = f"{base}\n{cost_line}" if base else cost_line

    if len(combined) <= max_chars:
        return combined

    kept_cost = f"\n{cost_line}"
    head_limit = max(0, max_chars - len(kept_cost))
    trimmed = base[:head_limit].rstrip()
    return f"{trimmed}{kept_cost}" if trimmed else cost_line[:max_chars]


def _persist_env_bool(env_file: str, key: str, enabled: bool) -> None:
    path = Path(env_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "true" if enabled else "false"

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> None:
    bot = create_bot()
    bot.run()


if __name__ == "__main__":
    main()
