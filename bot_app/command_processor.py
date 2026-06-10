from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .db_manager import BotDatabase
from .protocol import CommandResultPayload, EventPayload, IntentPayload


class CommandProcessor:
    """Extensible command module. Add new command handlers in _handle_* methods."""

    def __init__(self, db: BotDatabase, manual_file: str, command_guide_dir: str) -> None:
        self._db = db
        self._runtime_root_dir = Path(__file__).resolve().parent.parent

        manual_path = Path(manual_file)
        self._manual_file = manual_path if manual_path.is_absolute() else self._runtime_root_dir / manual_path

        guide_dir_path = Path(command_guide_dir)
        self._command_guide_dir = (
            guide_dir_path if guide_dir_path.is_absolute() else self._runtime_root_dir / guide_dir_path
        )

        self._kb_root_dir = self._runtime_root_dir / "btd6_knowledge_base"
        self._kb_db_path = self._kb_root_dir / "search_index.db"
        self._kb_corpus_dir = self._kb_root_dir / "wiki_data_second_cleaned" / "btd6"
        self._kb_legacy_corpus_dir = self._kb_root_dir / "wiki_data_cleaned" / "btd6"
        self._kb_alias_file = self._kb_root_dir / "aliases.json"
        self._api_processor_root = self._runtime_root_dir / "btd6_api_processor"
        self._api_processor_cli = self._api_processor_root / "btd6_cli.py"
        self._api_processor_output_dir = self._api_processor_root / "output"
        self._chat_monkey_root = self._runtime_root_dir / "ChatMonkeyTD"
        self._chat_monkey_main = self._chat_monkey_root / "main.py"
        self._chat_monkey_output_dir = self._chat_monkey_root / "output"
        self._manual_text_cache = ""

    async def execute(self, intent: IntentPayload, event: EventPayload) -> CommandResultPayload:
        command_name = intent.get("intent", "unknown")
        arguments = intent.get("arguments", {}) or {}

        if command_name in {"list", "help", "list_features"}:
            return self._handle_list_features()

        if command_name in {"view_command_guide", "command_guide", "read_command_guide"}:
            return self._handle_view_command_guide(arguments)

        if command_name in {"script_manual_query", "manual_query", "manual_qa", "doc_query"}:
            return self._handle_script_manual_query(event, arguments)

        if command_name == "today_most_active":
            return self._handle_today_most_active(event, arguments)

        if command_name in {"summarize_recent_messages", "recent_chat_summary", "群聊总结"}:
            return self._handle_summarize_recent_messages(event, arguments)

        if command_name in {"search_game_knowledge", "game_kb_search", "btd6_kb_search"}:
            return self._handle_search_game_knowledge(event, arguments)

        if command_name in {"read_game_knowledge_file", "read_kb_file", "kb_read"}:
            return self._handle_read_game_knowledge_file(arguments)

        if command_name in {"get_activity_detail", "activity_detail", "game_activity_detail"}:
            return self._handle_get_activity_detail(arguments)

        if command_name in {"get_activity_leaderboard", "activity_leaderboard", "game_leaderboard"}:
            return self._handle_get_activity_leaderboard(arguments)

        if command_name in {"get_collection_event_info", "collection_event", "collection_schedule"}:
            return self._handle_get_collection_event_info(arguments)

        if command_name in {"simulate_monkey_box_opening", "open_monkey_box", "game_box_opening"}:
            return self._handle_simulate_monkey_box_opening(arguments)

        return {
            "module": "command_processor",
            "ok": False,
            "command": command_name,
            "data": {},
            "message": f"暂不支持该指令: {command_name}",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }

    def _handle_today_most_active(self, event: EventPayload, arguments: dict) -> CommandResultPayload:
        top_n_raw = arguments.get("top_n", 3)
        try:
            top_n = int(top_n_raw)
        except (TypeError, ValueError):
            top_n = 3

        rows = self._db.get_today_most_active(event["group_id"], top_n=top_n)
        if not rows:
            return {
                "module": "command_processor",
                "ok": True,
                "command": "today_most_active",
                "data": {"top_n": top_n, "rows": []},
                "message": "今天还没有可统计的群聊消息。",
                "extra_prompt": "",
            }

        return {
            "module": "command_processor",
            "ok": True,
            "command": "today_most_active",
            "data": {"top_n": top_n, "rows": rows},
            "message": "统计完成",
            "extra_prompt": "",
        }

    def _handle_list_features(self) -> CommandResultPayload:
        features = [
            {
                "name": "list_features",
                "aliases": ["list", "help"],
                "description": "查看机器人当前支持的所有功能",
                "examples": ["list", "功能列表", "命令列表"],
            },
            {
                "name": "today_most_active",
                "aliases": ["今日活跃榜", "谁最活跃"],
                "description": "统计今天群里最活跃的成员",
                "arguments": {"top_n": "返回前 N 名，默认 3"},
                "examples": ["今天谁最活跃", "今日活跃榜前5"],
            },
            {
                "name": "summarize_recent_messages",
                "aliases": ["recent_chat_summary", "群聊总结", "总结最近消息"],
                "description": "总结最近的群聊消息",
                "arguments": {"top_n": "读取最近 N 条消息并总结，默认 30"},
                "examples": ["总结最近群聊", "群聊总结 最近50条"],
            },
            {
                "name": "script_manual_query",
                "aliases": ["manual_query", "说明书", "文档查询", "程序怎么用", "脚本说明书"],
                "description": "检索脚本程序说明书并基于说明书回答问题",
                "arguments": {"query": "问题关键词或完整问题"},
                "examples": ["这个脚本程序怎么启动", "刷收集怎么用"],
            },
            {
                "name": "search_game_knowledge",
                "aliases": ["game_kb_search", "btd6_kb_search", "检索游戏知识"],
                "description": "查看 BTD6 游戏知识",
                "arguments": {"top_n": "返回前 N 个文件路径，默认 5", "query": "检索问题，缺省时使用用户原话"},
                "examples": ["检索游戏知识 top 5", "查找猴子塔升级机制相关资料"],
            },
            {
                "name": "get_activity_detail",
                "aliases": ["activity_detail", "活动详情"],
                "description": "获取指定活动详情（detail 模式）",
                "arguments": {"activity_type": "race|boss|odyssey|daily"},
                "examples": ["获取竞速活动详情", "获取 boss 活动详情"],
            },
            {
                "name": "get_activity_leaderboard",
                "aliases": ["activity_leaderboard", "活动排行榜"],
                "description": "获取指定活动排行榜（leaderboard image 模式）",
                "arguments": {"leaderboard_type": "race|boss-standard|boss-elite"},
                "examples": ["获取竞速排行榜", "获取精英 boss 排行榜"],
            },
            {
                "name": "get_collection_event_info",
                "aliases": ["collection_event", "collection_schedule", "收集活动", "收集轮换"],
                "description": "获取收集活动轮换信息并发送图片（collection-event 模式）",
                "arguments": {"only_upcoming": "true|false，可选，true 表示仅当前和未来轮换"},
                "examples": ["查询收集活动", "看一下收集轮换图", "获取 upcoming 收集活动"],
            },
            {
                "name": "simulate_monkey_box_opening",
                "aliases": ["open_monkey_box", "game_box_opening", "开箱模拟", "收集开箱"],
                "description": "模拟 BTD6 开箱并返回统计文档，箱子数量 <=50 时额外返回图片",
                "arguments": {"box_type": "木头|青铜|白银|黄金|钻石", "box_count": "正整数"},
                "examples": ["开 10 个钻石箱", "模拟青铜箱 30 个"],
            },
            {
                "name": "chat",
                "aliases": ["闲聊", "问答"],
                "description": "普通对话能力，由大模型直接回复",
            },
        ]

        return {
            "module": "command_processor",
            "ok": True,
            "command": "list_features",
            "data": {
                "feature_count": len(features),
                "features": features,
            },
            "message": "功能列表已整理完成",
            "extra_prompt": "",
            "response_type": "text",
            "image_path": "",
        }

    def _handle_get_activity_detail(self, arguments: dict) -> CommandResultPayload:
        raw_type = str(arguments.get("activity_type", "") or arguments.get("detail_type", "") or "").strip().lower()
        alias_map = {
            "竞速": "race",
            "race": "race",
            "boss": "boss",
            "boss活动": "boss",
            "征程": "odyssey",
            "odyssey": "odyssey",
            "每日挑战": "daily",
            "daily": "daily",
        }
        detail_type = alias_map.get(raw_type, raw_type)
        if detail_type not in {"race", "boss", "odyssey", "daily"}:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_activity_detail",
                "data": {"mode": "detail", "activity_type": raw_type},
                "message": "activity_type 仅支持 race、boss、odyssey、daily。",
                "extra_prompt": "请提示用户给出合法活动类型。",
                "response_type": "text",
                "image_path": "",
            }

        ok, report, files, error_message = self._run_btd6_api_cli(mode="detail", detail_type=detail_type)
        if not ok:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_activity_detail",
                "data": {"mode": "detail", "activity_type": detail_type, "generated_files": []},
                "message": error_message,
                "extra_prompt": "请说明详情获取失败并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        return {
            "module": "command_processor",
            "ok": True,
            "command": "get_activity_detail",
            "data": {
                "mode": "detail",
                "activity_type": detail_type,
                "generated_files": files,
                "report": report,
            },
            "message": f"{detail_type} 活动详情获取完成",
            "extra_prompt": "请优先基于 report 回答，并提炼关键规则和时间信息。",
            "response_type": "text",
            "image_path": "",
        }

    def _handle_get_activity_leaderboard(self, arguments: dict) -> CommandResultPayload:
        raw_type = str(arguments.get("leaderboard_type", "") or arguments.get("activity_type", "") or "").strip().lower()
        alias_map = {
            "竞速": "race",
            "race": "race",
            "boss": "boss-standard",
            "boss-standard": "boss-standard",
            "普通boss": "boss-standard",
            "boss-elite": "boss-elite",
            "精英boss": "boss-elite",
        }
        leaderboard_type = alias_map.get(raw_type, raw_type)
        if leaderboard_type not in {"race", "boss-standard", "boss-elite"}:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_activity_leaderboard",
                "data": {"mode": "leaderboard", "leaderboard_type": raw_type},
                "message": "leaderboard_type 仅支持 race、boss-standard、boss-elite。",
                "extra_prompt": "请提示用户给出合法排行榜类型。",
                "response_type": "text",
                "image_path": "",
            }

        ok, report, files, error_message = self._run_btd6_api_cli(
            mode="leaderboard",
            leaderboard_type=leaderboard_type,
            leaderboard_format="image",
        )
        if not ok:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_activity_leaderboard",
                "data": {"mode": "leaderboard", "leaderboard_type": leaderboard_type, "generated_files": []},
                "message": error_message,
                "extra_prompt": "请说明排行榜获取失败并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        image_path = ""
        for path_str in files:
            if path_str.lower().endswith(".png"):
                image_path = path_str
                break

        if not image_path:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_activity_leaderboard",
                "data": {"mode": "leaderboard", "leaderboard_type": leaderboard_type, "generated_files": files},
                "message": "排行榜图片未生成成功。",
                "extra_prompt": "请说明未找到图片文件，并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        return {
            "module": "command_processor",
            "ok": True,
            "command": "get_activity_leaderboard",
            "data": {
                "mode": "leaderboard",
                "leaderboard_type": leaderboard_type,
                "generated_files": files,
                "report": report,
            },
            "message": f"{leaderboard_type} 排行榜图片已生成",
            "extra_prompt": "请给出一条简短说明，随后发送图片。",
            "response_type": "image",
            "image_path": image_path,
        }

    def _handle_get_collection_event_info(self, arguments: dict) -> CommandResultPayload:
        raw_only_upcoming = str(arguments.get("only_upcoming", "") or "").strip().lower()
        only_upcoming = raw_only_upcoming in {"1", "true", "yes", "on", "upcoming", "仅未来", "未来"}

        ok, report, files, error_message = self._run_btd6_api_cli(
            mode="collection-event",
            collection_only_upcoming=only_upcoming,
        )
        if not ok:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_collection_event_info",
                "data": {
                    "mode": "collection-event",
                    "only_upcoming": only_upcoming,
                    "generated_files": [],
                },
                "message": error_message,
                "extra_prompt": "请说明收集活动查询失败并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        image_path = ""
        json_path = ""
        for path_str in files:
            lower = path_str.lower()
            if lower.endswith(".png") and not image_path:
                image_path = path_str
            if lower.endswith(".json") and not json_path:
                json_path = path_str

        if not image_path:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "get_collection_event_info",
                "data": {
                    "mode": "collection-event",
                    "only_upcoming": only_upcoming,
                    "generated_files": files,
                    "report": report,
                },
                "message": "收集活动图片未生成成功。",
                "extra_prompt": "请说明未找到收集活动图片，并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        return {
            "module": "command_processor",
            "ok": True,
            "command": "get_collection_event_info",
            "data": {
                "mode": "collection-event",
                "only_upcoming": only_upcoming,
                "generated_files": files,
                "json_path": json_path,
                "report": report,
            },
            "message": "收集活动轮换图片已生成",
            "extra_prompt": "请给出一条简短说明，随后发送收集活动轮换图。",
            "response_type": "image",
            "image_path": image_path,
        }

    def _handle_simulate_monkey_box_opening(self, arguments: dict) -> CommandResultPayload:
        raw_box_type = str(arguments.get("box_type", "") or arguments.get("box", "") or "").strip().lower()
        box_alias_map = {
            "木头": "木头",
            "wood": "木头",
            "青铜": "青铜",
            "bronze": "青铜",
            "白银": "白银",
            "silver": "白银",
            "黄金": "黄金",
            "gold": "黄金",
            "钻石": "钻石",
            "diamond": "钻石",
        }
        box_type = box_alias_map.get(raw_box_type, "")
        if not box_type:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "simulate_monkey_box_opening",
                "data": {
                    "box_type": raw_box_type,
                    "box_count": arguments.get("box_count", ""),
                },
                "message": "box_type 仅支持 木头、青铜、白银、黄金、钻石。",
                "extra_prompt": "请提示用户给出合法箱子类型。",
                "response_type": "text",
                "image_path": "",
            }

        raw_box_count = arguments.get("box_count", arguments.get("count", ""))
        try:
            box_count = int(raw_box_count)
        except (TypeError, ValueError):
            box_count = 0

        if box_count <= 0:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "simulate_monkey_box_opening",
                "data": {
                    "box_type": box_type,
                    "box_count": raw_box_count,
                },
                "message": "box_count 必须是正整数。",
                "extra_prompt": "请提示用户提供正确的开箱数量。",
                "response_type": "text",
                "image_path": "",
            }

        ok, report_path, image_path, output_text, error_message = self._run_chat_monkey_simulator(
            box_type=box_type,
            box_count=box_count,
        )
        if not ok:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "simulate_monkey_box_opening",
                "data": {
                    "box_type": box_type,
                    "box_count": box_count,
                    "report_path": "",
                    "image_path": "",
                },
                "message": error_message,
                "extra_prompt": "请说明开箱模拟失败并建议稍后重试。",
                "response_type": "text",
                "image_path": "",
            }

        generated_files = [report_path]
        if image_path:
            generated_files.append(image_path)

        response_type = "image" if image_path else "text"
        message = "开箱模拟完成，已生成统计与拼图" if image_path else "开箱模拟完成，已生成统计文档"
        extra_prompt = (
            "请先简要概括本次开箱结果，再发送开箱拼图。"
            if image_path
            else "请基于输出文本简要总结本次开箱统计结果。"
        )

        return {
            "module": "command_processor",
            "ok": True,
            "command": "simulate_monkey_box_opening",
            "data": {
                "box_type": box_type,
                "box_count": box_count,
                "report_path": report_path,
                "generated_files": generated_files,
                "output_text": output_text,
            },
            "message": message,
            "extra_prompt": extra_prompt,
            "response_type": response_type,
            "image_path": image_path,
        }

    def _run_chat_monkey_simulator(
        self,
        box_type: str,
        box_count: int,
    ) -> tuple[bool, str, str, str, str]:
        if not self._chat_monkey_main.exists():
            return False, "", "", "", "未找到 ChatMonkeyTD/main.py。"

        self._chat_monkey_output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = (self._chat_monkey_output_dir / f"bot_open_result_{stamp}.md").resolve()
        image_path = (self._chat_monkey_output_dir / f"bot_open_result_{stamp}.png").resolve()

        cmd = [
            sys.executable,
            str(self._chat_monkey_main.name),
            box_type,
            str(box_count),
            "--report",
            str(report_path),
        ]
        if box_count <= 50:
            cmd.extend(["--output", str(image_path)])

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._chat_monkey_root),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "", "", "", "开箱模拟超时，请稍后重试。"
        except Exception as exc:
            return False, "", "", "", f"执行开箱模拟失败: {type(exc).__name__}"

        if proc.returncode != 0:
            err_text = (proc.stderr or proc.stdout or "").strip()
            err_text = err_text.splitlines()[-1] if err_text else "未知错误"
            return False, "", "", "", f"开箱模拟执行失败: {err_text}"

        if not report_path.exists():
            return False, "", "", "", "开箱模拟执行成功但未找到统计文档。"

        final_image_path = ""
        if box_count <= 50:
            if not image_path.exists():
                return False, "", "", "", "开箱模拟执行成功但未找到拼图文件。"
            final_image_path = str(image_path)

        output_text = "\n".join(
            [
                (proc.stdout or "").strip(),
                (proc.stderr or "").strip(),
            ]
        ).strip()

        return True, str(report_path), final_image_path, output_text, ""

    def _run_btd6_api_cli(
        self,
        mode: str,
        detail_type: str = "",
        leaderboard_type: str = "",
        leaderboard_format: str = "markdown",
        collection_only_upcoming: bool = False,
    ) -> tuple[bool, str, list[str], str]:
        if not self._api_processor_cli.exists():
            return False, "", [], "未找到 btd6_api_processor/btd6_cli.py。"

        self._api_processor_output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = self._api_processor_output_dir / f"bot_{mode}_{stamp}.md"

        cmd = [
            sys.executable,
            str(self._api_processor_cli.name),
            "--mode",
            mode,
            "--output",
            str(output_file.relative_to(self._api_processor_root)),
        ]

        if mode == "detail" and detail_type:
            cmd.extend(["--detail-types", detail_type])

        if mode == "leaderboard":
            cmd.extend(["--leaderboard-type", leaderboard_type or "race", "--leaderboard-format", leaderboard_format])

        if mode == "collection-event" and collection_only_upcoming:
            cmd.append("--only-upcoming")

        env = os.environ.copy()
        nk_api_key = os.getenv("NK_API_KEY", "").strip()
        if nk_api_key:
            env["NK_API_KEY"] = nk_api_key

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._api_processor_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "", [], "获取游戏资讯超时，请稍后重试。"
        except Exception as exc:
            return False, "", [], f"执行游戏资讯工具失败: {type(exc).__name__}"

        if proc.returncode != 0:
            err_text = (proc.stderr or proc.stdout or "").strip()
            err_text = err_text.splitlines()[-1] if err_text else "未知错误"
            return False, "", [], f"游戏资讯工具执行失败: {err_text}"

        if not output_file.exists():
            return False, "", [], "工具执行成功但未找到输出文件。"

        report = output_file.read_text(encoding="utf-8", errors="ignore").strip()
        files = self._extract_generated_files(report)
        return True, report, files, ""

    def _extract_generated_files(self, report: str) -> list[str]:
        files: list[str] = []
        for line in (report or "").splitlines():
            text = line.strip()
            if text.startswith("# 文件:"):
                raw = text.split(":", 1)[1].strip()
                if raw:
                    files.append(str((self._api_processor_root / raw).resolve()))
                continue

            if text.startswith("# JSON:"):
                raw = text.split(":", 1)[1].strip()
                if raw:
                    files.append(str((self._api_processor_root / raw).resolve()))
                continue

            if text.startswith("# 图片:"):
                raw = text.split(":", 1)[1].strip()
                if raw:
                    files.append(str((self._api_processor_root / raw).resolve()))
                continue

            match = re.search(r"->\s*([\w./-]+\.(?:md|png|json))\s*$", text)
            if match:
                files.append(str((self._api_processor_root / match.group(1)).resolve()))

        deduped = []
        seen: set[str] = set()
        for p in files:
            if p in seen:
                continue
            seen.add(p)
            deduped.append(p)
        return deduped

    def _handle_view_command_guide(self, arguments: dict) -> CommandResultPayload:
        command_name = self._extract_guide_command(arguments)
        if not command_name:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "view_command_guide",
                "data": {
                    "requested_command": "",
                    "available_guides": self._list_command_guides(),
                    "guide_text": "",
                },
                "message": "缺少参数 command，请指定要查看的指令教程。",
                "extra_prompt": "请提示用户先给出 command 参数，例如 script_manual_query。",
            }

        guide_path = self._command_guide_dir / f"{command_name}.md"
        if not guide_path.exists():
            return {
                "module": "command_processor",
                "ok": False,
                "command": "view_command_guide",
                "data": {
                    "requested_command": command_name,
                    "available_guides": self._list_command_guides(),
                    "guide_text": "",
                },
                "message": f"未找到指令教程: {command_name}",
                "extra_prompt": "请根据 available_guides 给出最接近的可用指令建议。",
            }

        guide_text = guide_path.read_text(encoding="utf-8").strip()
        return {
            "module": "command_processor",
            "ok": True,
            "command": "view_command_guide",
            "data": {
                "requested_command": command_name,
                "guide_file": str(guide_path),
                "guide_text": guide_text,
            },
            "message": "已读取指令教程",
            "extra_prompt": (
                "你拿到了本地指令教程。"
                "如果还需要继续完成任务，可继续输出下一条 command_processor 指令。"
                "如果信息已足够，请直接输出最终答复。"
            ),
        }

    def _handle_script_manual_query(self, event: EventPayload, arguments: dict) -> CommandResultPayload:
        query = str(arguments.get("query", "") or "").strip() or event["text"]
        manual_text = self._load_manual_text()

        if not manual_text:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "script_manual_query",
                "data": {"query": query, "manual_file": str(self._manual_file), "manual_full_text": ""},
                "message": "未找到说明书内容，请先补充说明书文件。",
                "extra_prompt": "直接说明说明书缺失，并引导管理员补充 BOT_MANUAL_FILE 指向的文档。",
            }

        return {
            "module": "command_processor",
            "ok": True,
            "command": "script_manual_query",
            "data": {
                "query": query,
                "manual_file": str(self._manual_file),
                "manual_full_text": manual_text,
            },
            "message": "说明书检索完成",
            "extra_prompt": (
                "你现在要扮演程序说明书问答助手。"
                "请优先依据 command_result.data.manual_full_text 回答用户问题。"
                "如果说明书仍不足以回答，请明确说明“说明书未覆盖该细节”，并给出可执行下一步。"
                "不要凭空编造。"
            ),
        }

    def _handle_summarize_recent_messages(self, event: EventPayload, arguments: dict) -> CommandResultPayload:
        top_n_raw = arguments.get("top_n", 30)
        try:
            top_n = int(top_n_raw)
        except (TypeError, ValueError):
            top_n = 30
        top_n = max(5, min(top_n, 200))

        rows = self._db.get_recent_group_messages(event["group_id"], limit=top_n)
        if not rows:
            return {
                "module": "command_processor",
                "ok": True,
                "command": "summarize_recent_messages",
                "data": {"top_n": top_n, "message_count": 0, "messages": []},
                "message": "最近没有可用于总结的群聊消息。",
                "extra_prompt": "直接说明最近缺少可总结的消息。",
            }

        compact_messages = []
        for row in rows:
            display_name = str(row.get("sender_card", "") or "").strip() or str(row.get("sender_nickname", "") or "").strip()
            compact_messages.append(
                {
                    "display_name": display_name,
                    "text": str(row.get("raw_message", "") or "").strip(),
                    "created_at": str(row.get("created_at", "") or ""),
                }
            )

        return {
            "module": "command_processor",
            "ok": True,
            "command": "summarize_recent_messages",
            "data": {
                "top_n": top_n,
                "message_count": len(compact_messages),
                "messages": compact_messages,
            },
            "message": "最近群聊消息已提取完成",
            "extra_prompt": (
                "请基于 messages 生成群聊总结。"
                "总结重点包括: 主要话题、共识/分歧、待办事项。"
                "不要逐条复述，不要编造消息里不存在的内容。"
            ),
        }

    def _handle_search_game_knowledge(self, event: EventPayload, arguments: dict) -> CommandResultPayload:
        raw_query = str(arguments.get("query", "") or "").strip() or event["text"]
        query = self._sanitize_kb_query(raw_query)
        top_n_raw = arguments.get("top_n", 5)
        try:
            top_n = int(top_n_raw)
        except (TypeError, ValueError):
            top_n = 5
        top_n = max(1, min(top_n, 20))

        try:
            kb_dir = str(self._kb_root_dir.resolve())
            if kb_dir not in sys.path:
                sys.path.insert(0, kb_dir)

            from kb_interface import interface_search  # type: ignore

            alias_file = self._kb_alias_file if self._kb_alias_file.exists() else None
            paths = interface_search(
                query=query,
                top_n=top_n,
                db_path=self._kb_db_path,
                corpus_dir=self._kb_corpus_dir,
                alias_file=alias_file,
            )
        except Exception as exc:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "search_game_knowledge",
                "data": {
                    "raw_query": raw_query,
                    "query": query,
                    "top_n": top_n,
                    "matched_paths": [],
                },
                "message": f"游戏知识检索失败: {type(exc).__name__}",
                "extra_prompt": "请简要说明检索失败，并建议稍后重试或检查知识库索引文件。",
            }

        matched_paths = [str(p) for p in paths]
        return {
            "module": "command_processor",
            "ok": True,
            "command": "search_game_knowledge",
            "data": {
                "raw_query": raw_query,
                "query": query,
                "top_n": top_n,
                "matched_count": len(matched_paths),
                "matched_paths": matched_paths,
            },
            "message": "游戏知识检索完成",
            "extra_prompt": (
                "你已经拿到候选文件路径。"
                "请先从 matched_paths 中选择最可能有答案的 1 个文件，再调用 read_game_knowledge_file 读取内容。"
                "若 matched_count=0，明确说明未检索到匹配文件，并建议用户换关键词。"
            ),
        }

    def _handle_read_game_knowledge_file(self, arguments: dict) -> CommandResultPayload:
        raw_path = str(arguments.get("file_path", "") or "").strip()
        keywords_query = self._build_extract_keywords_query(arguments)
        max_chars_raw = arguments.get("max_chars", 8000)
        try:
            max_chars = int(max_chars_raw)
        except (TypeError, ValueError):
            max_chars = 8000
        max_chars = max(500, min(max_chars, 20000))

        if not raw_path:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "read_game_knowledge_file",
                "data": {"file_path": "", "content": ""},
                "message": "缺少参数 file_path。",
                "extra_prompt": "请先从 search_game_knowledge 的 matched_paths 中选择一个 file_path。",
            }

        safe_path = self._resolve_safe_kb_path(raw_path)
        if safe_path is None:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "read_game_knowledge_file",
                "data": {"file_path": raw_path, "content": ""},
                "message": "拒绝读取该路径：超出知识库允许范围。",
                "extra_prompt": "仅允许读取 btd6_knowledge_base/wiki_data_second_cleaned/btd6 目录内文件。",
            }

        if not safe_path.exists() or not safe_path.is_file():
            return {
                "module": "command_processor",
                "ok": False,
                "command": "read_game_knowledge_file",
                "data": {"file_path": str(safe_path), "content": ""},
                "message": "目标文件不存在。",
                "extra_prompt": "请改用 search_game_knowledge 返回的路径。",
            }

        if safe_path.suffix.lower() not in {".html", ".htm", ".md", ".txt"}:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "read_game_knowledge_file",
                "data": {"file_path": str(safe_path), "content": ""},
                "message": "文件类型不允许读取。",
                "extra_prompt": "仅支持读取 html/htm/md/txt 文件。",
            }

        try:
            text = safe_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            return {
                "module": "command_processor",
                "ok": False,
                "command": "read_game_knowledge_file",
                "data": {"file_path": str(safe_path), "content": ""},
                "message": f"读取知识库文件失败: {type(exc).__name__}",
                "extra_prompt": "请说明读取失败并建议更换文件重试。",
            }

        content = self._extract_readable_content(text)

        if len(content) > 8000:
            if not keywords_query:
                return {
                    "module": "command_processor",
                    "ok": False,
                    "command": "read_game_knowledge_file",
                    "data": {
                        "file_path": str(safe_path),
                        "content": "",
                        "mode": "extract",
                        "keywords": [],
                    },
                    "message": "文档过长，缺少关键词，无法执行文本块提取。",
                    "extra_prompt": (
                        "该文档长度超过 8000 字符，不再返回整篇内容。"
                        "请补充 keywords（可用用户问题关键词）后重试 read_game_knowledge_file。"
                    ),
                }

            try:
                kb_dir = str(self._kb_root_dir.resolve())
                if kb_dir not in sys.path:
                    sys.path.insert(0, kb_dir)

                from kb_interface import interface_extract  # type: ignore

                extract_result = interface_extract(
                    file_name=str(safe_path),
                    keywords_query=keywords_query,
                    corpus_dir=self._kb_corpus_dir,
                    max_chars=max_chars,
                )
            except Exception as exc:
                return {
                    "module": "command_processor",
                    "ok": False,
                    "command": "read_game_knowledge_file",
                    "data": {
                        "file_path": str(safe_path),
                        "content": "",
                        "mode": "extract",
                        "keywords": self._split_keywords_for_view(keywords_query),
                    },
                    "message": f"关键词提取失败: {type(exc).__name__}",
                    "extra_prompt": "请更换关键词重试，或选择其他候选文件。",
                }

            extracted_text = str(extract_result.get("text", "") or "").strip()
            return {
                "module": "command_processor",
                "ok": True,
                "command": "read_game_knowledge_file",
                "data": {
                    "file_path": str(extract_result.get("file", str(safe_path))),
                    "max_chars": int(extract_result.get("max_chars", max_chars) or max_chars),
                    "content": extracted_text,
                    "mode": "extract",
                    "keywords": extract_result.get("keywords", self._split_keywords_for_view(keywords_query)),
                    "chars": int(extract_result.get("chars", len(extracted_text)) or len(extracted_text)),
                },
                "message": "知识库长文已按关键词提取相关文本块",
                "extra_prompt": (
                    "这是从长文中提取的相关文本块。"
                    "请优先基于 content 回答。"
                    "若信息不足，更换更简单的关键词或换文件继续提取。"
                ),
            }

        content = content[:max_chars]

        return {
            "module": "command_processor",
            "ok": True,
            "command": "read_game_knowledge_file",
            "data": {
                "file_path": str(safe_path),
                "max_chars": max_chars,
                "content": content,
                "mode": "full",
            },
            "message": "知识库文件读取完成",
            "extra_prompt": (
                "请优先依据 content 回答用户问题。"
                "若内容不足，请再次从 matched_paths 选择其他文件读取，不要编造。"
            ),
        }

    def _build_extract_keywords_query(self, arguments: dict) -> str:
        for key in ("keywords", "query", "keyword"):
            value = arguments.get(key, "")
            if isinstance(value, list):
                merged = " ".join(str(item).strip() for item in value if str(item).strip())
            else:
                merged = str(value or "").strip()
            if merged:
                return merged
        return ""

    def _split_keywords_for_view(self, keywords_query: str) -> list[str]:
        parts = re.split(r"[\s,，、;；|+/]+", keywords_query or "")
        return [p.strip() for p in parts if p.strip()]

    def _load_manual_text(self) -> str:
        if self._manual_text_cache:
            return self._manual_text_cache

        if not self._manual_file.exists():
            return ""

        text = self._manual_file.read_text(encoding="utf-8").strip()
        self._manual_text_cache = text
        return text

    def _extract_guide_command(self, arguments: dict) -> str:
        # Be tolerant to common LLM argument key variations.
        for key in ("command", "command_name", "intent", "name", "topic"):
            value = str(arguments.get(key, "") or "").strip()
            if value:
                return value
        return ""

    def _resolve_safe_kb_path(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate

        try:
            resolved = candidate.resolve(strict=False)
            allowed_roots = {
                self._kb_corpus_dir.resolve(strict=False),
                self._kb_legacy_corpus_dir.resolve(strict=False),
            }
        except Exception:
            return None

        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            return None
        return resolved

    def _extract_readable_content(self, text: str) -> str:
        # Strip script/style-heavy HTML to reduce noise before sending to LLM.
        stripped = text
        try:
            from bs4 import BeautifulSoup  # type: ignore

            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            stripped = soup.get_text("\n", strip=True)
        except Exception:
            stripped = text

        return "\n".join(line.strip() for line in stripped.splitlines() if line.strip())

    def _sanitize_kb_query(self, query: str) -> str:
        # Remove weak intent words to keep retrieval focused on real entities/mechanics.
        weak_terms = (
            "详情",
            "详细",
            "说明",
            "介绍",
            "机制",
            "信息",
            "讲解",
            "告诉我",
            "说说",
            "看下",
            "看一下",
            "帮我",
            "请问",
            "一下",
            "是什么",
            "怎么",
            "如何",
            "有关",
            "相关",
        )

        cleaned = query
        for term in weak_terms:
            cleaned = cleaned.replace(term, " ")

        cleaned = re.sub(r"[，。,.!?！？；;:/\\|]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or query.strip()

    def _list_command_guides(self) -> list[str]:
        if not self._command_guide_dir.exists():
            return []
        return sorted([p.stem for p in self._command_guide_dir.glob("*.md") if p.is_file()])
