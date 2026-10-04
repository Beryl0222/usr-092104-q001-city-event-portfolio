"""领域值对象与共享类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TimeWindow:
    """资源占用时间窗，半开区间 [start, end)。"""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("时间窗结束时间必须晚于开始时间")

    def overlaps(self, other: "TimeWindow") -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "TimeWindow":
        return cls(start=datetime.fromisoformat(data["start"]), end=datetime.fromisoformat(data["end"]))


@dataclass(frozen=True)
class Occupancy:
    """一项资源占用：场馆档期 / 人员时段 / 公安运力 / 交通保障。

    capacity_kind 非空时表示对可配额资源（如公安运力、道路运力）的占用，
    load 为占用量，capacity 为该资源在本时间窗内的保障容量。
    场馆档期等独占资源 capacity_kind 为 None，占用即互斥。
    """

    resource_id: str
    resource_kind: str  # venue / personnel / police_capacity / road_capacity
    window: TimeWindow
    load: float = 0.0
    capacity: float | None = None
    label: str = ""
    public_use_displaced: bool = False  # 是否占用居民日常健身时段

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "window": self.window.to_dict(),
            "load": self.load,
            "capacity_kind": None,
            "label": self.label,
            "public_use_displaced": self.public_use_displaced,
        }
        if self.capacity is not None:
            data["capacity"] = self.capacity
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Occupancy":
        return cls(
            resource_id=data["resource_id"],
            resource_kind=data["resource_kind"],
            window=TimeWindow.from_dict(data["window"]),
            load=float(data.get("load", 0.0)),
            capacity=data.get("capacity"),
            label=data.get("label", ""),
            public_use_displaced=data.get("public_use_displaced", False),
        )


@dataclass(frozen=True)
class Forecast:
    """预测收益材料：必须带口径与置信范围，仅供有权部门参考。

    预测结果在任何流程中都不自动生成批准结论。
    """

    metric: str  # lodging_nights / consumption / attendances ...
    point_estimate: float
    confidence_low: float
    confidence_high: float
    methodology: str  # 口径说明：样本、模型、假设
    confidence_level: float = 0.95
    source: str = ""

    def __post_init__(self) -> None:
        if not self.methodology.strip():
            raise ValueError("预测材料必须说明计算口径")
        if not (self.confidence_low <= self.point_estimate <= self.confidence_high):
            raise ValueError("置信范围必须覆盖点估计值")
        if not 0 < self.confidence_level < 1:
            raise ValueError("置信水平应在 (0, 1) 之间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "point_estimate": self.point_estimate,
            "confidence_low": self.confidence_low,
            "confidence_high": self.confidence_high,
            "confidence_level": self.confidence_level,
            "methodology": self.methodology,
            "source": self.source,
            "advisory_only": True,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Forecast":
        return cls(
            metric=data["metric"],
            point_estimate=float(data["point_estimate"]),
            confidence_low=float(data["confidence_low"]),
            confidence_high=float(data["confidence_high"]),
            methodology=data["methodology"],
            confidence_level=float(data.get("confidence_level", 0.95)),
            source=data.get("source", ""),
        )


@dataclass(frozen=True)
class FinancialCommitment:
    """财政承诺：金额单位万元，区分财政资金与社会资本。"""

    fiscal_amount_wan: float
    nonfiscal_amount_wan: float
    funding_source: str
    note: str = ""

    @property
    def total_wan(self) -> float:
        return self.fiscal_amount_wan + self.nonfiscal_amount_wan

    def to_dict(self) -> dict[str, Any]:
        return {
            "fiscal_amount_wan": self.fiscal_amount_wan,
            "nonfiscal_amount_wan": self.nonfiscal_amount_wan,
            "funding_source": self.funding_source,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FinancialCommitment":
        return cls(
            fiscal_amount_wan=float(data["fiscal_amount_wan"]),
            nonfiscal_amount_wan=float(data.get("nonfiscal_amount_wan", 0.0)),
            funding_source=data["funding_source"],
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class PublicServicePlan:
    """对居民日常健身等公共服务的安排。

    已对外承诺的公共服务在后续变更中不得静默消失：若替代安排缺失，
    相关变更不得通过（见 service 中的保护校验）。
    """

    service_id: str
    description: str
    window: TimeWindow | None = None
    replacement: str = ""  # 替代开放时段/场地说明；为空表示维持原服务
    externally_committed: bool = False  # 是否已对外承诺

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "service_id": self.service_id,
            "description": self.description,
            "replacement": self.replacement,
            "externally_committed": self.externally_committed,
        }
        if self.window is not None:
            data["window"] = self.window.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublicServicePlan":
        return cls(
            service_id=data["service_id"],
            description=data["description"],
            window=TimeWindow.from_dict(data["window"]) if data.get("window") else None,
            replacement=data.get("replacement", ""),
            externally_committed=data.get("externally_committed", False),
        )


@dataclass(frozen=True)
class CrowdEstimate:
    """预计人群：峰值与累计，均为申报口径。"""

    peak_on_site: int
    cumulative: int
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_on_site": self.peak_on_site,
            "cumulative": self.cumulative,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CrowdEstimate":
        return cls(
            peak_on_site=int(data["peak_on_site"]),
            cumulative=int(data["cumulative"]),
            note=data.get("note", ""),
        )
