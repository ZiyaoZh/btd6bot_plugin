from __future__ import annotations

from .types import AgentResponse

__all__ = ["AgentResponse", "AgentRunner"]


def __getattr__(name: str) -> object:
    if name == "AgentRunner":
        from .runner import AgentRunner

        return AgentRunner
    raise AttributeError(name)
