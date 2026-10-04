"""表决前资源冲突检测：同一资源的时间重叠必须在表决前显现。

规则：

- 独占资源（场馆档期、关键人员等 resource_kind in EXCLUSIVE_KINDS）：
  不同申办在同一 resource_id 上时间窗重叠即冲突；
- 可配额资源（公安运力、道路运力）：用扫描线求任意时刻并发占用合计，
  超过容量即冲突（覆盖三项及以上赛事同时叠加的情形）；
- 占用居民日常健身时段（public_use_displaced）作为提示项单独列出，
  须在公共服务安排中回应。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.objects import Occupancy

EXCLUSIVE_KINDS = frozenset({"venue", "personnel"})
CAPACITY_KINDS = frozenset({"police_capacity", "road_capacity"})


@dataclass
class Conflict:
    kind: str  # exclusive_overlap / capacity_overload / public_use_displaced
    resource_id: str
    window: dict[str, str]
    case_ids: list[str]
    detail: str
    severity: str = "block"  # block 必须在表决前处置；notice 需说明但不阻断
    load_total: float | None = None
    capacity: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "resource_id": self.resource_id,
            "window": self.window,
            "case_ids": list(self.case_ids),
            "detail": self.detail,
            "severity": self.severity,
        }
        if self.load_total is not None:
            data["load_total"] = self.load_total
            data["capacity"] = self.capacity
        return data


def _intersection(a: Occupancy, b: Occupancy) -> dict[str, str]:
    start = max(a.window.start, b.window.start)
    end = min(a.window.end, b.window.end)
    return {"start": start.isoformat(), "end": end.isoformat()}


def detect_conflicts(
    cases: dict[str, list[Occupancy]],
    *,
    excluded_cases: frozenset[str] = frozenset(),
) -> list[Conflict]:
    """cases: case_id -> 该申办当前生效的资源占用清单。

    excluded_cases 中的申办（如已撤回、已否决）不参与比对。
    """
    active = {cid: occ for cid, occ in cases.items() if cid not in excluded_cases}
    conflicts: list[Conflict] = []
    case_ids = sorted(active)

    # 1) 独占互斥：两两时间窗重叠
    for i, cid_a in enumerate(case_ids):
        for cid_b in case_ids[i + 1 :]:
            for occ_a in active[cid_a]:
                if occ_a.resource_kind not in EXCLUSIVE_KINDS:
                    continue
                for occ_b in active[cid_b]:
                    if occ_b.resource_id != occ_a.resource_id:
                        continue
                    if not occ_a.window.overlaps(occ_b.window):
                        continue
                    conflicts.append(
                        Conflict(
                            kind="exclusive_overlap",
                            resource_id=occ_a.resource_id,
                            window=_intersection(occ_a, occ_b),
                            case_ids=[cid_a, cid_b],
                            detail=(
                                f"{occ_a.label or occ_a.resource_id} 与"
                                f"{occ_b.label or occ_b.resource_id} 时间重叠"
                            ),
                        )
                    )

    # 2) 可配额资源：扫描线求每个区间的并发占用合计（跨申办 + 同申办多时段）
    by_resource: dict[str, list[tuple[Occupancy, str]]] = {}
    for cid in case_ids:
        for occ in active[cid]:
            if occ.resource_kind in CAPACITY_KINDS:
                by_resource.setdefault(occ.resource_id, []).append((occ, cid))
    for resource_id, occs in by_resource.items():
        points = sorted({p for occ, _ in occs for p in (occ.window.start, occ.window.end)})
        for start, end in zip(points, points[1:]):
            current = [(occ, cid) for occ, cid in occs if occ.window.start <= start < occ.window.end]
            if not current:
                continue
            total = sum(occ.load for occ, _ in current)
            capacities = [occ.capacity for occ, _ in current if occ.capacity is not None]
            capacity = max(capacities) if capacities else None
            if capacity is None or total <= capacity:
                continue
            involved = sorted({cid for _, cid in current})
            conflicts.append(
                Conflict(
                    kind="capacity_overload",
                    resource_id=resource_id,
                    window={"start": start.isoformat(), "end": end.isoformat()},
                    case_ids=involved,
                    detail=f"并发占用合计 {total:g} 超过保障容量 {capacity:g}",
                    load_total=total,
                    capacity=capacity,
                )
            )

    # 3) 居民日常健身时段被占用 → 提示（须在公共服务安排中回应）
    for cid in case_ids:
        for occ in active[cid]:
            if occ.public_use_displaced:
                conflicts.append(
                    Conflict(
                        kind="public_use_displaced",
                        resource_id=occ.resource_id,
                        window=occ.window.to_dict(),
                        case_ids=[cid],
                        detail=f"{occ.label or occ.resource_id} 占用居民日常健身时段，须明确替代安排",
                        severity="notice",
                    )
                )
    return conflicts


def open_blocking_conflicts(recorded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从聚合已记录的冲突中筛出未处置的阻断项。"""
    return [c for c in recorded if c["status"] == "open" and c["severity"] == "block"]
