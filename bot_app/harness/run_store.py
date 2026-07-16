from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RunRecord:
    trace_id: str
    session_id: str
    status: str
    started_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemoryRunStore:
    """Bounded lifecycle store for diagnostics and delivery consistency."""

    def __init__(self, max_records: int = 1000) -> None:
        self._max_records = max(10, int(max_records))
        self._records: OrderedDict[str, RunRecord] = OrderedDict()

    def start(self, trace_id: str, session_id: str) -> None:
        now = _utc_now()
        self._records[trace_id] = RunRecord(
            trace_id=trace_id,
            session_id=session_id,
            status="queued",
            started_at=now,
            updated_at=now,
        )
        self._trim()

    def transition(self, trace_id: str, status: str, **metadata: Any) -> None:
        record = self._records.get(trace_id)
        if record is None:
            return
        record.status = status
        record.updated_at = _utc_now()
        record.metadata.update(metadata)
        self._records.move_to_end(trace_id)

    def get(self, trace_id: str) -> RunRecord | None:
        return self._records.get(trace_id)

    def _trim(self) -> None:
        while len(self._records) > self._max_records:
            self._records.popitem(last=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
