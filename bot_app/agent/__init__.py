from __future__ import annotations

from .types import AgentResponse

__all__ = ["Agent", "AgentResponse", "AgentRunner"]


def __getattr__(name: str) -> object:
    if name in {"Agent", "AgentRunner"}:
        from .runner import Agent

        return Agent
    raise AttributeError(name)
