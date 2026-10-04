"""追加式事件存储与并发控制。

内存实现（接口可替换为持久化实现）：每个聚合一条流，事件按 version 严格递增；
所有事件同时进入全局日志。审议相关事件在 payload.case_id 中携带申办编号，
便于 CaseFile 跨四条聚合流完整回放。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.envelopes import Event

StreamKey = tuple[str, str]


class ConcurrentModificationError(RuntimeError):
    """流版本与预期不符（乐观并发失败）。"""


class EventStore:
    def __init__(self) -> None:
        self._streams: dict[StreamKey, list[Event]] = defaultdict(list)
        self._global_log: list[Event] = []

    def append(self, event: Event, expected_version: int) -> None:
        """在版本 expected_version（0 表示新流）之后追加事件。"""
        self.append_many([event], {self._key(event): expected_version})

    @staticmethod
    def _key(event: Event) -> StreamKey:
        return (event.aggregate_type, event.aggregate_id)

    def stream_version(self, aggregate_type: str, aggregate_id: str) -> int:
        return len(self._streams[(aggregate_type, aggregate_id)])

    def load_stream(self, aggregate_type: str, aggregate_id: str) -> list[Event]:
        return list(self._streams[(aggregate_type, aggregate_id)])

    def all_events(self) -> list[Event]:
        return list(self._global_log)

    def events_for_case(self, case_id: str) -> list[Event]:
        """按发生顺序回放一个申办档案（跨聚合流）的全部事件。"""
        return [e for e in self._global_log if e.payload.get("case_id") == case_id]

    def cases(self) -> list[str]:
        seen: list[str] = []
        for e in self._global_log:
            cid = e.payload.get("case_id")
            if cid and cid not in seen:
                seen.append(cid)
        return seen

    def append_many(
        self,
        events: Iterable[Event],
        expected_versions: dict[StreamKey, int],
    ) -> None:
        """批量提交：先统一校验版本与序号，全部通过后才写入（简单事务边界）。"""
        events = list(events)
        touched: dict[StreamKey, int] = {}
        for event in events:
            key = self._key(event)
            current = touched.get(key)
            if current is None:
                current = len(self._streams[key])
                if key in expected_versions and current != expected_versions[key]:
                    raise ConcurrentModificationError(
                        f"流 {key} 版本 {current} 与预期 {expected_versions[key]} 不一致"
                    )
                if key not in expected_versions and current != 0:
                    raise ConcurrentModificationError(f"未登记的既有流 {key} 禁止隐式追加")
            if event.version != current + 1:
                raise ConcurrentModificationError(
                    f"事件 version={event.version} 应为 {current + 1}"
                )
            touched[key] = current + 1
        for event in events:
            self._streams[self._key(event)].append(event)
            self._global_log.append(event)
