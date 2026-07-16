from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class SessionLockManager:
    """Serialize a complete generate-deliver-commit transaction per session."""

    def __init__(self) -> None:
        self._entries: dict[str, _LockEntry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, session_id: str) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = _LockEntry(lock=asyncio.Lock())
                self._entries[session_id] = entry
            entry.users += 1

        try:
            async with entry.lock:
                yield
        finally:
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and not entry.lock.locked():
                    self._entries.pop(session_id, None)
