"""三类角色的只读视图（从事件流投影重建）。

- applicant_view   申请方：补正理由、材料状态与决定版本（不暴露其他申办信息）；
- management_view  管理人员：年度赛事组合对场馆、公安运力、道路运力与财政的峰值压力；
- audit_view       审计人员：材料、会签、条件性批准、后续变更的完整可回放过程。

所有视图均为只读重建，不产生事件。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.aggregates import (
    EventApplication,
    PortfolioDecision,
    ResourceCommitment,
    ReviewOpinion,
)
from src.catalog import Category, Decision, EVIDENCE_ITEMS, INTERNATIONAL_EXTRA_EVIDENCE
from src.conflicts import CAPACITY_KINDS, EXCLUSIVE_KINDS
from src.repository import Repository

STATUS_CN = {
    "draft": "草稿",
    "filed": "已受理",
    "supplementing": "待补正",
    "reviewing": "审议中",
    "decided": "已决定",
    "reopened": "变更重审中",
    "withdrawn": "已撤回",
}

DECISION_CN = {
    "approved": "批准",
    "conditional_approval": "条件性批准",
    "rejected": "不予批准",
    "deferred": "暂缓",
}

PHASE_CN = {
    "operator_qualification": "运营主体资质",
    "project_grading": "项目级别",
    "admissibility": "分类准入证据",
    "benefit_forecast": "预测收益（仅供参考）",
    "venue": "场馆档期",
    "personnel": "人员占用",
    "transport": "交通保障",
    "security": "安全方案",
    "finance": "财政承诺",
    "commercial": "商业权益",
    "legacy": "赛后利用计划",
    "public_service": "公共服务",
    "deliberation": "会签表决",
}

ASPECT_CN = {"scale": "规模", "venue": "场地", "funding_source": "资金来源"}


# ---------------- 申请方视图 ----------------
@dataclass
class ApplicantView:
    case_id: str
    title: str
    category: str
    status: str
    amendment_no: int
    supplement_requests: list[dict[str, Any]]
    evidence_status: dict[str, Any]
    decision_versions: list[dict[str, Any]]
    advisory_forecasts: list[dict[str, Any]]
    open_change: dict[str, Any] | None
    timeline: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "category": self.category,
            "status_cn": STATUS_CN.get(self.status, self.status),
            "amendment_no": self.amendment_no,
            "supplement_requests": self.supplement_requests,
            "evidence_status": self.evidence_status,
            "decision_versions": self.decision_versions,
            "advisory_forecasts": self.advisory_forecasts,
            "open_change": self.open_change,
            "timeline": self.timeline,
        }


def build_applicant_view(repo: Repository, case_id: str) -> ApplicantView:
    app = repo.load_application(case_id)
    if not app.version:
        raise KeyError(f"申办 {case_id} 不存在")
    review = repo.load_review(case_id)

    required = list(EVIDENCE_ITEMS[app.category])
    if app.category == Category.INTERNATIONAL and app.international_nature:
        required = list(EVIDENCE_ITEMS[app.international_nature]) + list(INTERNATIONAL_EXTRA_EVIDENCE)
    submitted = {e["item"] for bucket in app.evidence.values() for e in bucket}

    supplement_requests = [
        {
            "request_id": r["request_id"],
            "requested_by": r["requested_by"],
            "reason": r["reason"],  # 补正理由对申请方可见
            "missing": list(r["missing"]),
            "requested_at": r["requested_at"].isoformat(),
            "submitted": sorted(set(r["covered"])),
            "outstanding": [m for m in r["missing"] if m not in r["covered"]],
        }
        for r in app.supplement_requests
    ]

    # 决定版本：从全局决定事件中逐版提取（申请方能看到的是结果与所附条件，不暴露他人讨论）
    decision_events = [
        e
        for e in repo.store.all_events()
        if e.event_type == "DECISION_ISSUED" and e.payload.get("case_id") == case_id
    ]
    decision_versions = []
    for e in decision_events:
        p = e.payload
        decision_versions.append(
            {
                "document_version": p["document_version"],
                "decision": p["decision"],
                "decision_cn": DECISION_CN.get(p["decision"], p["decision"]),
                "vote": dict(p["vote"]),
                "conditions": [
                    {
                        "condition_id": cid,
                        "description": review.conditions[cid]["description"],
                        "status": review.conditions[cid]["status"],
                    }
                    for cid in p.get("conditions", [])
                ],
                "note": p.get("note", ""),
                "issued_at": e.occurred_at.isoformat(),
            }
        )

    advisory_forecasts = [
        {
            "metric": f["forecast"]["metric"],
            "point_estimate": f["forecast"]["point_estimate"],
            "confidence_interval": [
                f["forecast"]["confidence_low"],
                f["forecast"]["confidence_high"],
            ],
            "methodology": f["forecast"]["methodology"],
            "advisory_only": True,
            "attached_at": f["attached_at"].isoformat(),
        }
        for f in app.forecasts
    ]

    open_change = next((c for c in app.changes if c["status"] == "open"), None)
    timeline = _case_timeline(repo, case_id, include_private=False)

    # 状态对申请方的呈现：有未关闭变更→重审中；已有决定→已决定
    if open_change is not None:
        display_status = "reopened"
    elif decision_versions:
        display_status = "decided"
    else:
        display_status = app.status

    return ApplicantView(
        case_id=case_id,
        title=app.title or "",
        category=app.category or "",
        status=display_status,
        amendment_no=app.amendment_no,
        supplement_requests=supplement_requests,
        evidence_status={
            "required": required,
            "submitted": sorted(submitted),
            "outstanding": [name for name in required if name not in submitted],
        },
        decision_versions=decision_versions,
        advisory_forecasts=advisory_forecasts,
        open_change={
            "change_no": open_change["change_no"],
            "aspects": open_change["aspects"],
            "justification": open_change["justification"],
            "declared_at": open_change["declared_at"].isoformat(),
        }
        if open_change
        else None,
        timeline=timeline,
    )


# ---------------- 管理人员：年度组合峰值压力 ----------------
@dataclass
class ResourcePressure:
    resource_id: str
    resource_kind: str
    peak_start: str
    peak_end: str
    peak_load: float
    capacity: float | None
    utilization: float | None  # peak_load / capacity
    concurrent_cases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_kind": self.resource_kind,
            "peak_window": {"start": self.peak_start, "end": self.peak_end},
            "peak_load": self.peak_load,
            "capacity": self.capacity,
            "utilization": round(self.utilization, 4) if self.utilization is not None else None,
            "concurrent_cases": self.concurrent_cases,
        }


@dataclass
class ManagementView:
    year: int
    included_cases: list[dict[str, Any]]
    resource_pressure: list[dict[str, Any]]
    fiscal_total_wan: float
    fiscal_breakdown: list[dict[str, Any]]
    public_services: list[dict[str, Any]]
    advisory: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "included_cases": self.included_cases,
            "peak_pressure": {
                "by_resource": self.resource_pressure,
                "overloaded": [
                    r["resource_id"]
                    for r in self.resource_pressure
                    if r["utilization"] is not None and r["utilization"] > 1
                ],
            },
            "fiscal": {
                "total_commitment_wan": self.fiscal_total_wan,
                "breakdown": self.fiscal_breakdown,
            },
            "public_services": self.public_services,
            "advisory_forecasts": self.advisory,
            "note": "峰值压力基于已批准（含条件性批准）赛事的实际占用与财政承诺；预测收益仅作参考材料，不构成审批依据。",
        }


def _approved_cases(repo: Repository) -> list[str]:
    latest: dict[str, str] = {}
    for e in repo.store.all_events():
        if e.event_type == "DECISION_ISSUED":
            latest[e.payload["case_id"]] = e.payload["decision"]
    return [cid for cid, d in latest.items() if d in (Decision.APPROVED, Decision.CONDITIONAL_APPROVAL)]


def build_management_view(repo: Repository, year: int) -> ManagementView:
    portfolio = repo.load_portfolio(year)
    approved = _approved_cases(repo)

    included: list[dict[str, Any]] = []
    windows_by_resource: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    capacities: dict[str, float] = {}
    kinds: dict[str, str] = {}
    fiscal_total = 0.0
    fiscal_breakdown: list[dict[str, Any]] = []
    public_services: list[dict[str, Any]] = []
    advisory_forecasts: list[dict[str, Any]] = []

    for cid in approved:
        app = repo.load_application(cid)
        if app.season != str(year):
            continue
        res = repo.load_resources(cid)
        decision = portfolio.decision_for(cid)
        included.append(
            {
                "case_id": cid,
                "title": app.title,
                "category": app.category,
                "peak_on_site": app.expected_crowd.peak_on_site if app.expected_crowd else None,
                "decision": decision["decision"] if decision else None,
            }
        )
        for occ in res.occupancies:
            windows_by_resource[occ.resource_id].append((occ, cid))
            kinds[occ.resource_id] = occ.resource_kind
            if occ.capacity is not None:
                capacities[occ.resource_id] = occ.capacity
        if res.finance:
            fiscal_total += res.finance.fiscal_amount_wan
            fiscal_breakdown.append(
                {
                    "case_id": cid,
                    "title": app.title,
                    "fiscal_amount_wan": res.finance.fiscal_amount_wan,
                    "nonfiscal_amount_wan": res.finance.nonfiscal_amount_wan,
                    "funding_source": res.finance.funding_source,
                }
            )
        for service in res.public_services.values():
            public_services.append(
                {"case_id": cid, **service.to_dict(), "protected": any(
                    p["service_id"] == service.service_id for p in repo.load_review(cid).protections
                )}
            )
        for f in app.forecasts:
            advisory_forecasts.append(
                {"case_id": cid, "title": app.title, **f["forecast"], "advisory_only": True}
            )

    pressures: list[dict[str, Any]] = []
    for resource_id, occs in sorted(windows_by_resource.items()):
        kind = kinds[resource_id]
        # sweep line：端点切分，每个区间内占用集合不变
        points: set[datetime] = set()
        for occ, _ in occs:
            points.add(occ.window.start)
            points.add(occ.window.end)
        timeline = sorted(points)

        best: tuple[float, datetime, datetime, list[str]] | None = None
        for start, end in zip(timeline, timeline[1:]):
            active = [(occ, cid) for occ, cid in occs if occ.window.start <= start < occ.window.end]
            if not active:
                continue
            if kind in CAPACITY_KINDS:
                load = sum(occ.load for occ, _ in active)
            else:
                # 独占资源（场馆/人员）：并行占用数即压力
                load = float(len(active))
            cases = sorted({cid for _, cid in active})
            if best is None or load > best[0]:
                best = (load, start, end, cases)
        if best is None:
            continue
        peak_load, peak_start, peak_end, cases = best
        capacity = capacities.get(resource_id)
        utilization = (peak_load / capacity) if capacity else None
        pressures.append(
            ResourcePressure(
                resource_id=resource_id,
                resource_kind=kind,
                peak_start=peak_start.isoformat(),
                peak_end=peak_end.isoformat(),
                peak_load=peak_load,
                capacity=capacity,
                utilization=utilization,
                concurrent_cases=cases,
            ).to_dict()
        )

    return ManagementView(
        year=year,
        included_cases=included,
        resource_pressure=pressures,
        fiscal_total_wan=fiscal_total,
        fiscal_breakdown=fiscal_breakdown,
        public_services=public_services,
        advisory={
            "forecasts": advisory_forecasts,
            "disclaimer": "预测材料带口径与置信范围，仅供有权部门参考，不自动替代任何决定。",
        },
    )


# ---------------- 审计视图 ----------------
def build_audit_view(repo: Repository, case_id: str) -> dict[str, Any]:
    """完整回放：原始信封 + 结构化时间线 + 现状快照。"""
    app = repo.load_application(case_id)
    if not app.version:
        raise KeyError(f"申办 {case_id} 不存在")
    res = repo.load_resources(case_id)
    review = repo.load_review(case_id)
    raw_events = repo.store.events_for_case(case_id)

    decision_events = [
        e
        for e in repo.store.all_events()
        if e.event_type == "DECISION_ISSUED" and e.payload.get("case_id") == case_id
    ]
    return {
        "case_id": case_id,
        "title": app.title,
        "replay": {
            "event_count": len(raw_events),
            "raw_envelopes": [e.to_dict() for e in raw_events],
            "timeline": _case_timeline(repo, case_id, include_private=True),
            "decision_versions": [
                {
                    "version": e.payload["document_version"],
                    "decision": e.payload["decision"],
                    "decision_cn": DECISION_CN[e.payload["decision"]],
                    "vote": e.payload["vote"],
                    "quorum": e.payload["quorum"],
                    "voting_members": e.payload["voting_members"],
                    "recused_members": e.payload["recused_members"],
                    "conditions": e.payload.get("conditions", []),
                    "issued_at": e.occurred_at.isoformat(),
                    "event_id": e.event_id,
                }
                for e in decision_events
            ],
            "reopened_rounds": [
                {
                    "round_no": r["round_no"],
                    "aspects": [ASPECT_CN.get(a, a) for a in r["aspects"]],
                    "phases": [PHASE_CN.get(p, p) for p in r["phases"]],
                    "justification": r["justification"],
                    "opened_at": r["opened_at"].isoformat(),
                    "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
                    "conclusions": r["conclusions"],
                }
                for r in review.rounds
            ],
        },
        "current_state": {
            "application_status": app.status,
            "amendment_no": app.amendment_no,
            "open_conflicts": [
                {
                    "kind": c["kind"],
                    "resource_id": c["resource_id"],
                    "window": c["window"],
                    "case_ids": c["case_ids"],
                    "severity": c["severity"],
                }
                for c in res.conflicts
                if c["status"] == "open"
            ],
            "conditions": {
                cid: {"description": c["description"], "status": c["status"]}
                for cid, c in review.conditions.items()
            },
            "recusals": [r["member"] for r in review.recusals],
            "dissents": [
                {"member": d["member"], "opinion": d["opinion"], "round": d["round"]}
                for d in review.dissents
            ],
            "public_service_protections": [
                {
                    "service_id": p["service_id"],
                    "replacement": p["replacement"],
                    "change_no": p["change_no"],
                }
                for p in review.protections
            ],
        },
    }


def _case_timeline(repo: Repository, case_id: str, *, include_private: bool) -> list[dict[str, Any]]:
    """把跨流事件整理为中文结构化时间线。include_private 控制回避/少数意见等审议内部材料。"""
    events = repo.store.events_for_case(case_id)
    timeline: list[dict[str, Any]] = []
    for e in events:
        p = e.payload
        et = e.event_type
        item: dict[str, Any] | None = {
            "at": e.occurred_at.isoformat(),
            "event_type": et,
            "aggregate_type": e.aggregate_type,
            "event_id": e.event_id,
        }
        if et == "APPLICATION_FILED":
            item["action"] = f"受理申办《{p['title']}》（{p['category']}，{p['project_level']}）"
        elif et == "APPLICATION_AMENDED":
            item["action"] = f"申办修订至第 {p['amendment_no']} 版：{p['reason']}"
            item["changed_sections"] = p["changed_sections"]
        elif et == "APPLICATION_WITHDRAWN":
            item["action"] = f"申办撤回：{p['reason']}"
        elif et == "SUPPLEMENT_REQUESTED":
            item["action"] = f"发出补正通知 {p['request_id']}：{p['reason']}"
            item["missing"] = p["missing"]
        elif et == "ADMISSIBILITY_EVIDENCE_SUBMITTED":
            item["action"] = f"提交准入证据 {len(p['items'])} 项（{p['category']}）"
        elif et == "BENEFIT_FORECAST_ATTACHED":
            item["action"] = "附预测收益材料（带口径与置信范围，仅供参考，不作审批依据）"
        elif et == "RESOURCE_DECLARED":
            item["action"] = f"申报资源占用 {len(p['occupancies'])} 项"
        elif et == "RESOURCE_REVISED":
            item["action"] = (
                f"第 {p['change_no']} 轮变更后修订资源占用：{p['reason']}"
                if p.get("change_no")
                else f"表决前资源协调修订：{p['reason']}"
            )
        elif et == "RESOURCE_CONFLICTED":
            item["action"] = f"表决前检出资源冲突 {len(p['conflicts'])} 项"
            item["conflicts"] = [
                {"resource_id": c["resource_id"], "window": c["window"], "severity": c["severity"]}
                for c in p["conflicts"]
            ]
        elif et == "CONFLICT_CHECK_RUN":
            item["action"] = f"执行组合资源冲突检测（覆盖 {p['cases_checked']} 个在档申办）"
        elif et == "RESOURCE_CONFLICT_RESOLVED":
            item["action"] = f"资源冲突处置：{p['resolution']}"
        elif et == "PUBLIC_SERVICE_COMMITTED":
            item["action"] = f"对外承诺公共服务：{p['service']['description']}"
        elif et == "PUBLIC_SERVICE_PROTECTED":
            item["action"] = f"变更中持续保障公共服务 {p['service_id']}：{p['replacement']}"
        elif et == "CONDITION_SET":
            item["action"] = f"设置批准条件 {len(p['conditions'])} 项"
        elif et == "CONDITION_SATISFIED":
            item["action"] = f"批准条件履行：{p['condition_id']}"
        elif et == "DECISION_ISSUED":
            item["action"] = (
                f"作出决定 v{p['document_version']}：{DECISION_CN.get(p['decision'], p['decision'])}"
            )
            item["vote"] = p["vote"]
            item["conditions"] = p.get("conditions", [])
        elif et == "CHANGE_DECLARED":
            item["action"] = (
                f"批准后实质变更（第 {p['change_no']} 轮）："
                + "、".join(ASPECT_CN.get(a, a) for a in p["aspects"])
            )
            item["justification"] = p["justification"]
        elif et == "CHANGE_REOPENED":
            item["action"] = "只重开受影响环节：" + "、".join(
                PHASE_CN.get(x, x) for x in p["phases"]
            )
        elif et == "CHANGE_CLOSED":
            item["action"] = f"第 {p['change_no']} 轮变更重审完结，决定升至 v{p['decision_version']}"
        elif et == "REVIEW_RESOLVED":
            item["action"] = f"第 {p['round_no']} 轮重开环节全部审议完结"
            item["conclusions"] = p["conclusions"]
        elif include_private and et == "RECUSAL_DECLARED":
            item["action"] = f"回避：{p['member']}（{p['reason']}）"
        elif include_private and et == "OPINION_SIGNED":
            item["action"] = f"会签：{p['department']} {p['member']}（第 {p.get('round', 0)} 轮）"
        elif include_private and et == "DISSENT_RECORDED":
            item["action"] = f"保留少数意见：{p['member']}：{p['opinion']}"
        else:
            # 非授权视图下，审议内部材料不出现在申请方时间线
            if et in ("RECUSAL_DECLARED", "OPINION_SIGNED", "DISSENT_RECORDED"):
                continue
            item["action"] = et
        timeline.append(item)
    return timeline
