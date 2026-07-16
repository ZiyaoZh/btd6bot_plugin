from __future__ import annotations

from pathlib import Path

from ncatbot.core import BotClient
from ncatbot.event.qq import GroupMessageEvent

from .bootstrap import build_application
from .settings import load_settings


def create_bot() -> BotClient:
    settings = load_settings()
    bot = BotClient()
    application = build_application(
        settings=settings,
        bot=bot,
        plugin_root=Path(__file__).resolve().parent.parent,
    )

    @bot.group_event()
    async def on_group_message(msg: GroupMessageEvent):
        await application.handle_group_message(msg)

    return bot


def main() -> None:
    create_bot().run()


if __name__ == "__main__":
    main()
