from __future__ import annotations

try:
    from .plugin import Btd6BotPlugin
except ImportError:
    from plugin import Btd6BotPlugin

__all__ = ["Btd6BotPlugin"]
