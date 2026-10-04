"""领域事件信封。

与仓库既有约定保持一致：事件必备 event_id / event_type / aggregate_type /
aggregate_id / occurred_at / version / summary 七项（见
contracts/domain.schema.json）。业务负载统一放在 ``payload`` 中，属于信封的
additionalProperties，旧消费者可忽略。

为保证审议记录可复现，应用服务通过 Clock / IdGenerator 注入时间与编号，
测试中可固定两者。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from src.catalog import AGGREGATE_TYPES, EVENT_TYPES

REQUIRED_FIELDS = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)


class IdGenerator(Protocol):
    def __call__(self) -> str: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_id() -> str:
    return uuid4().hex


@dataclass
class Event:
    """一条不可变的领域事件。"""

    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    version: int
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    causation_id: str | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为既有信封格式（+ payload 扩展字段）。"""
        record: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at.isoformat(),
            "version": self.version,
            "summary": self.summary,
            "payload": self.payload,
        }
        if self.causation_id is not None:
            record["causation_id"] = self.causation_id
        if self.correlation_id is not None:
            record["correlation_id"] = self.correlation_id
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "Event":
        return cls(
            event_id=record["event_id"],
            event_type=record["event_type"],
            aggregate_type=record["aggregate_type"],
            aggregate_id=record["aggregate_id"],
            occurred_at=datetime.fromisoformat(record["occurred_at"]),
            version=record["version"],
            summary=record["summary"],
            payload=dict(record.get("payload", {})),
            causation_id=record.get("causation_id"),
            correlation_id=record.get("correlation_id"),
        )


def validate_envelope(record: dict[str, Any]) -> list[str]:
    """信封校验，兼容 src.validator.validate_event 的基础检查并补充登记目录。"""
    errors: list[str] = []
    for name in REQUIRED_FIELDS:
        if name not in record:
            errors.append(f"缺少字段：{name}")
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    if "event_type" in record and record["event_type"] not in EVENT_TYPES:
        errors.append(f"event_type 未登记：{record['event_type']}")
    if "aggregate_type" in record and record["aggregate_type"] not in AGGREGATE_TYPES:
        errors.append(f"aggregate_type 未登记：{record['aggregate_type']}")
    return errors
