"""年度赛事组合视图：场馆、财政与城市保障的峰值压力。

管理端用来回答"同一季度十几项赛事叠加后，城市在哪几天最吃紧"。
口径说明：
- 只统计已批准（含条件性批准）赛事的生效资源承诺；
- 场馆/健身时段按资源逐日叠加占用量，取峰值日；
- 安保、医疗、交通三类城市保障按类别跨资源逐日叠加，取峰值日；
- 财政补贴按赛事首个占用开始月份归集（归集口径，非支付口径）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable

from src.models import ApplicationStatus, Commitment, ResourceKind
from src.service import ReviewService

_SUPPORT_KINDS = (ResourceKind.SECURITY, ResourceKind.MEDICAL, ResourceKind.TRANSIT)
_MAX_DAYS = 400  # 单个占用窗口的展开上限，防止异常数据撑爆视图


def _days_in_window(start: str, end: str, year: int) -> Iterable[date]:
    begin = datetime.fromisoformat(start)
    # 窗口为 [start, end)，结束时刻所在日也算占用日
    finish = datetime.fromisoformat(end) - timedelta(microseconds=1)
    day = max(begin.date(), date(year, 1, 1))
    last = min(finish.date(), date(year, 12, 31))
    for offset in range(_MAX_DAYS):
        current = day + timedelta(days=offset)
        if current > last:
            break
        yield current


def _overlaps_year(start: str, end: str, year: int) -> bool:
    return datetime.fromisoformat(start).date() <= date(year, 12, 31) and datetime.fromisoformat(
        end
    ).date() >= date(year, 1, 1)


def _peak(bucket: dict[tuple[str, date], int], names: dict[str, str]) -> list[dict]:
    """把 (资源, 日期) → 占用量 的累加结果整理成每资源峰值清单。"""
    by_resource: dict[str, dict[date, int]] = defaultdict(dict)
    for (resource_id, day), quantity in bucket.items():
        by_resource[resource_id][day] = by_resource[resource_id].get(day, 0) + quantity
    peaks = []
    for resource_id, daily in by_resource.items():
        peak_day = max(daily, key=lambda d: (daily[d], d.isoformat()))
        peaks.append(
            {
                "resource_id": resource_id,
                "resource_name": names.get(resource_id, resource_id),
                "peak_day": peak_day.isoformat(),
                "peak_quantity": daily[peak_day],
            }
        )
    return sorted(peaks, key=lambda item: item["peak_quantity"], reverse=True)


def build_portfolio_view(service: ReviewService, year: int) -> dict:
    """管理端年度组合视图。"""
    approved = [
        app
        for app in service.applications.values()
        if app.status in (ApplicationStatus.APPROVED, ApplicationStatus.CONDITIONALLY_APPROVED)
    ]
    commitments = [
        c
        for c in service.commitments.values()
        if c.status == "active" and _overlaps_year(c.start, c.end, year)
    ]
    names = {app.id: app.name for app in approved}

    venue_bucket: dict[tuple[str, date], int] = defaultdict(int)
    fitness_bucket: dict[tuple[str, date], int] = defaultdict(int)
    support_bucket: dict[tuple[str, date], int] = defaultdict(int)
    support_day_apps: dict[tuple[str, date], set] = defaultdict(set)

    for commitment in commitments:
        for day in _days_in_window(commitment.start, commitment.end, year):
            if commitment.kind == ResourceKind.VENUE:
                venue_bucket[(commitment.resource_id, day)] += commitment.quantity
            elif commitment.kind == ResourceKind.PUBLIC_FITNESS_SLOT:
                fitness_bucket[(commitment.resource_id, day)] += commitment.quantity
            elif commitment.kind in _SUPPORT_KINDS:
                key = (commitment.kind.value, day)
                support_bucket[key] += commitment.quantity
                support_day_apps[key].add(names.get(commitment.application_id, commitment.application_id))

    # 已承诺的公共服务（如居民健身时段）同样占用资源，计入健身时段压力
    for psc in service.public_services.values():
        if psc.status != "active":
            continue
        for window in psc.windows:
            if not _overlaps_year(window["start"], window["end"], year):
                continue
            for day in _days_in_window(window["start"], window["end"], year):
                fitness_bucket[(window["resource_id"], day)] += window.get("quantity", 1)

    support_peaks = []
    for kind in _SUPPORT_KINDS:
        daily = {day: qty for (k, day), qty in support_bucket.items() if k == kind.value}
        if not daily:
            continue
        peak_day = max(daily, key=lambda d: (daily[d], d.isoformat()))
        support_peaks.append(
            {
                "kind": kind.value,
                "peak_day": peak_day.isoformat(),
                "peak_quantity": daily[peak_day],
                "events": sorted(support_day_apps[(kind.value, peak_day)]),
            }
        )

    fiscal_by_month: dict[str, dict] = {}
    events = []
    for app in approved:
        app_commitments = [c for c in commitments if c.application_id == app.id]
        if not app_commitments:
            continue
        window_start = min(c.start for c in app_commitments)
        window_end = max(c.end for c in app_commitments)
        subsidy = 0
        for decision in reversed(app.decisions):
            version = next((v for v in app.versions if v.number == decision.based_on_version), None)
            if version is not None:
                subsidy = version.sections.get("fiscal_commitment", {}).get("subsidy_amount", 0)
                break
        events.append(
            {
                "application_id": app.id,
                "name": app.name,
                "category": app.category.value,
                "window_start": window_start,
                "window_end": window_end,
                "subsidy_amount": subsidy,
            }
        )
        month = window_start[:7]
        entry = fiscal_by_month.setdefault(month, {"month": month, "subsidy_total": 0, "events": []})
        entry["subsidy_total"] += subsidy
        entry["events"].append(app.name)

    fiscal_months = sorted(fiscal_by_month.values(), key=lambda item: item["month"])
    peak_month = (
        max(fiscal_months, key=lambda item: item["subsidy_total"]) if fiscal_months else None
    )
    return {
        "year": year,
        "approved_events": sorted(events, key=lambda item: item["window_start"]),
        "venue_peaks": _peak(venue_bucket, {}),
        "support_peaks": support_peaks,
        "public_fitness_pressure": _peak(fitness_bucket, {}),
        "fiscal": {
            "by_month": fiscal_months,
            "peak_month": peak_month,
            "annual_total": sum(item["subsidy_total"] for item in fiscal_months),
        },
    }
