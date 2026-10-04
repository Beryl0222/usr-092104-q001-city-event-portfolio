"""领域事件存储：信封与仓库既有约定（contracts/domain.schema.json）兼容。

- 每条事件携带 event_id / event_type / aggregate_type / aggregate_id /
  occurred_at / version / summary 七个基础字段，追加前用 ``src.validator``
  校验，保证与既有领域资料一致。
- ``version`` 按聚合（aggregate_type + aggregate_id）单调递增。
- ``correlation_id`` 把同一赛事的申办、会签、决定、变更事件串成一条链，
  供审计回放使用；``payload`` 携带业务内容。
- 存储为只追加（append-only）：可选落盘为 JSONL，每行一条事件。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from src.validator import validate_event


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventStore:
    """只追加的领域事件存储。"""

    def __init__(
        self,
        path: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._path = Path(path) if path else None
        self._events: list[dict] = []
        self._seq = 0
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    record = json.loads(line)
                    self._events.append(record)
                    self._seq = max(self._seq, record.get("seq", 0))

    def append(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        summary: str,
        payload: dict | None = None,
        correlation_id: str | None = None,
    ) -> dict:
        """追加一条事件并返回完整记录。信封不合规时拒绝写入。"""
        version = 1 + max(
            (e["version"] for e in self._events
             if e["aggregate_type"] == aggregate_type and e["aggregate_id"] == aggregate_id),
            default=0,
        )
        self._seq += 1
        record: dict[str, Any] = {
            "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": self._clock().isoformat(),
            "version": version,
            "summary": summary,
            "correlation_id": correlation_id,
            "payload": payload or {},
            "seq": self._seq,
        }
        errors = validate_event(record)
        if errors:
            raise ValueError(f"事件信封不合规：{errors}")
        self._events.append(record)
        if self._path:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def all(self) -> list[dict]:
        return list(self._events)

    def events_for(self, aggregate_type: str, aggregate_id: str) -> list[dict]:
        return [
            e for e in self._events
            if e["aggregate_type"] == aggregate_type and e["aggregate_id"] == aggregate_id
        ]

    def query(
        self,
        *,
        correlation_id: str | None = None,
        event_type: str | Iterable[str] | None = None,
    ) -> list[dict]:
        """按关联 id / 事件类型过滤，按发生顺序返回（审计回放用）。"""
        types = {event_type} if isinstance(event_type, str) else set(event_type or [])
        result = [
            e for e in self._events
            if (correlation_id is None or e.get("correlation_id") == correlation_id)
            and (not types or e["event_type"] in types)
        ]
        return sorted(result, key=lambda e: (e["occurred_at"], e["seq"]))
