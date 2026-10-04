"""资源时间重叠检测：同一资源的占用时段在表决前显现。

检测对象包括三类占用：
1. 已批准赛事的生效资源承诺；
2. 在审赛事当前版本的资源请求；
3. 已对外承诺的公共服务（如居民日常健身时段）所占用的资源窗口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from src.models import Occupancy


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """两个 [start, end) 窗口是否相交。"""
    return _parse(a_start) < _parse(b_end) and _parse(b_start) < _parse(a_end)


def intersection(a_start: str, a_end: str, b_start: str, b_end: str) -> tuple[str, str]:
    """返回两个窗口的交集（调用前先用 overlaps 确认相交）。"""
    start = max(_parse(a_start), _parse(b_start))
    end = min(_parse(a_end), _parse(b_end))
    return start.isoformat(), end.isoformat()


def detect_overlaps(new: Occupancy, existing: Iterable[Occupancy]) -> list[tuple[Occupancy, str, str]]:
    """返回新占用与既有占用的重叠清单：[(既有占用, 重叠开始, 重叠结束)]。

    只统计同一 resource_id 且不同占用主体的重叠。
    """
    found: list[tuple[Occupancy, str, str]] = []
    for other in existing:
        if other.resource_id != new.resource_id or other.owner_id == new.owner_id:
            continue
        if overlaps(new.start, new.end, other.start, other.end):
            start, end = intersection(new.start, new.end, other.start, other.end)
            found.append((other, start, end))
    return found
