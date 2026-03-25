from __future__ import annotations

from pathlib import Path

from ncatbot.core import BotClient


class MessageSender:
    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def send_group_text(self, group_id: str, text: str, at_user_id: str | None = None) -> None:
        output_text = text
        if at_user_id and output_text and not output_text.startswith(" "):
            output_text = f" {output_text}"
        await self._bot.api.post_group_msg(group_id=group_id, text=output_text, at=at_user_id)

    async def send_group_image(
        self,
        group_id: str,
        image_path: str,
        text: str = "",
        at_user_id: str | None = None,
    ) -> None:
        resolved = Path(image_path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"image not found: {resolved}")

        caption = text.strip()
        if at_user_id:
            caption = f" {caption}" if caption else " "

        await self._bot.api.post_group_msg(
            group_id=group_id,
            text=caption or None,
            at=at_user_id,
            image=str(resolved),
        )
