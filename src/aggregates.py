"""事件溯源聚合：从事件流还原审议状态。

四类聚合与契约一一对应：

- event_application  : 申办版本、主体资质、项目级别、预计人群、准入证据、预测材料、变更申报
- resource_commitment: 场馆/人员/运力占用、财政承诺、商业权益、赛后利用、公共服务
- review_opinion     : 回避、会签、少数意见、环节重开、条件与公共服务保护
- portfolio_decision : 决定版本（批准/条件性批准/不批准/暂缓）

命令方法在聚合内完成不变量校验并产生事件；服务负责跨聚合一次性提交。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.catalog import (
    ApplicationStatus,
    Category,
    ChangeAspect,
    Decision,
    EventType,
    Phase,
)
from src.envelopes import Event
from src.objects import (
    CrowdEstimate,
    FinancialCommitment,
    Forecast,
    Occupancy,
    PublicServicePlan,
)


class Aggregate:
    aggregate_type = ""

    def __init__(self, aggregate_id: str) -> None:
        self.id = aggregate_id
        self.version = 0
        self._uncommitted: list[Event] = []

    @classmethod
    def replay(cls, aggregate_id: str, events: list[Event]) -> "Aggregate":
        obj = cls(aggregate_id)
        for event in events:
            obj.apply(event)
        return obj

    def pull_events(self) -> list[Event]:
        events = self._uncommitted
        self._uncommitted = []
        return events

    def _raise(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        event = Event(
            event_id=event_id,
            event_type=event_type,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.id,
            occurred_at=occurred_at,
            version=self.version + 1,
            summary=summary,
            payload=payload,
        )
        self.apply(event)
        self._uncommitted.append(event)
        return event

    # 由子类实现
    def apply(self, event: Event) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError


class EventApplication(Aggregate):
    aggregate_type = "event_application"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.title: str | None = None
        self.category: str | None = None
        self.international_nature: str | None = None  # 国际赛事按竞技/职业挂靠准入证据
        self.season: str | None = None
        self.operator: dict[str, Any] | None = None
        self.project_level: str | None = None
        self.expected_crowd: CrowdEstimate | None = None
        self.status: str = ApplicationStatus.DRAFT
        # 申办版本：0=首次申报，之后每次修订 +1
        self.amendment_no = 0
        self.amendments: list[dict[str, Any]] = []
        self.evidence: dict[str, list[dict[str, str]]] = {}
        self.supplement_requests: list[dict[str, Any]] = []
        self.forecasts: list[dict[str, Any]] = []
        self.changes: list[dict[str, Any]] = []
        self.review_started = False
        self.withdrawn = False

    # ---- 命令 ----
    def file(
        self,
        *,
        title: str,
        category: str,
        season: str,
        operator: dict[str, Any],
        project_level: str,
        expected_crowd: CrowdEstimate,
        event_id: str,
        occurred_at: datetime,
        international_nature: str | None = None,
    ) -> Event:
        if self.version:
            raise ValueError("申办已存在，不得重复申报")
        if category not in {c.value for c in Category}:
            raise ValueError(f"未知赛事类别：{category}")
        if category == Category.INTERNATIONAL and international_nature not in (
            Category.COMPETITIVE,
            Category.PROFESSIONAL,
        ):
            raise ValueError("国际赛事须注明项目性质（竞技/职业）以确定准入证据")
        if not operator.get("name") or not operator.get("qualification_no"):
            raise ValueError("运营主体名称与资质编号必填")
        payload = {
            "case_id": self.id,
            "title": title,
            "category": category,
            "international_nature": international_nature,
            "season": season,
            "operator": dict(operator),
            "project_level": project_level,
            "expected_crowd": expected_crowd.to_dict(),
        }
        return self._raise(
            EventType.APPLICATION_FILED, f"申办受理：{title}", payload, event_id, occurred_at
        )

    def amend(
        self,
        *,
        changes: dict[str, Any],
        reason: str,
        changed_sections: list[str],
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        """申办版本修订。批准后只允许通过 change 流程，服务层据此拦截。"""
        if not self.version:
            raise ValueError("尚未申报，无从修订")
        if self.withdrawn:
            raise ValueError("已撤回的申办不得修订")
        if not changes:
            raise ValueError("修订内容为空")
        payload = {
            "case_id": self.id,
            "amendment_no": self.amendment_no + 1,
            "changed_sections": changed_sections,
            "reason": reason,
            "changes": changes,
        }
        return self._raise(
            EventType.APPLICATION_AMENDED,
            f"申办修订第 {self.amendment_no + 1} 版：{reason}",
            payload,
            event_id,
            occurred_at,
        )

    def withdraw(self, *, reason: str, event_id: str, occurred_at: datetime) -> Event:
        if not self.version:
            raise ValueError("尚未申报")
        if self.withdrawn:
            raise ValueError("申办已撤回")
        return self._raise(
            EventType.APPLICATION_WITHDRAWN,
            "申办撤回",
            {"case_id": self.id, "reason": reason},
            event_id,
            occurred_at,
        )

    def request_supplement(
        self,
        *,
        request_id: str,
        missing: list[str],
        reason: str,
        requested_by: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        if not missing:
            raise ValueError("补正清单为空")
        return self._raise(
            EventType.SUPPLEMENT_REQUESTED,
            f"发出补正通知：{request_id}",
            {
                "case_id": self.id,
                "request_id": request_id,
                "missing": list(missing),
                "reason": reason,
                "requested_by": requested_by,
            },
            event_id,
            occurred_at,
        )

    def submit_evidence(
        self,
        *,
        category: str,
        items: list[dict[str, str]],
        request_id: str | None,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        if not items or any(not i.get("item") or not i.get("doc_ref") for i in items):
            raise ValueError("每项准入证据需注明材料项与文件编号")
        return self._raise(
            EventType.ADMISSIBILITY_EVIDENCE_SUBMITTED,
            f"提交{category}准入证据 {len(items)} 项",
            {
                "case_id": self.id,
                "category": category,
                "request_id": request_id,
                "items": [dict(i) for i in items],
            },
            event_id,
            occurred_at,
        )

    def attach_forecast(
        self, *, forecast: Forecast, event_id: str, occurred_at: datetime
    ) -> Event:
        # Forecast 构造时已强制口径与置信范围；事件中再次显式标注仅供参考
        data = forecast.to_dict()
        data["advisory_only"] = True
        return self._raise(
            EventType.BENEFIT_FORECAST_ATTACHED,
            f"附预测收益材料（口径：{forecast.metric}）",
            {"case_id": self.id, "forecast": data},
            event_id,
            occurred_at,
        )

    def declare_change(
        self,
        *,
        aspects: list[str],
        justification: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        valid = {a.value for a in ChangeAspect}
        if not aspects or any(a not in valid for a in aspects):
            raise ValueError("变更影响面必须是 scale/venue/funding_source 的非空子集")
        if any(c.get("status") == "open" for c in self.changes):
            raise ValueError("上一轮实质变更尚未审议完结，不得叠加申报")
        return self._raise(
            EventType.CHANGE_DECLARED,
            f"批准后实质变更申报：{'、'.join(aspects)}",
            {
                "case_id": self.id,
                "change_no": len(self.changes) + 1,
                "aspects": list(aspects),
                "justification": justification,
                "status": "open",
            },
            event_id,
            occurred_at,
        )

    def mark_review_started(self) -> None:
        self.review_started = True

    def close_change(self, *, change_no: int, decision_version: int, event_id: str, occurred_at: datetime) -> Event:
        """重开环节审议完结并作出新版决定后关闭本轮变更。"""
        target = next((c for c in self.changes if c["change_no"] == change_no), None)
        if target is None:
            raise ValueError("变更轮次不存在")
        if target["status"] != "open":
            raise ValueError("变更轮次已关闭")
        return self._raise(
            EventType.CHANGE_CLOSED,
            f"第 {change_no} 轮实质变更重审完结（决定 v{decision_version}）",
            {
                "case_id": self.id,
                "change_no": change_no,
                "decision_version": decision_version,
            },
            event_id,
            occurred_at,
        )

    # ---- 回放 ----
    def apply(self, event: Event) -> None:
        self.version += 1
        p = event.payload
        et = event.event_type
        if et == EventType.APPLICATION_FILED:
            self.title = p["title"]
            self.category = p["category"]
            self.international_nature = p.get("international_nature")
            self.season = p["season"]
            self.operator = dict(p["operator"])
            self.project_level = p["project_level"]
            self.expected_crowd = CrowdEstimate.from_dict(p["expected_crowd"])
            self.status = ApplicationStatus.FILED
        elif et == EventType.APPLICATION_AMENDED:
            self.amendment_no = p["amendment_no"]
            self.amendments.append(dict(p))
            changes = p["changes"]
            if "title" in changes:
                self.title = changes["title"]
            if "category" in changes:
                self.category = changes["category"]
            if "international_nature" in changes:
                self.international_nature = changes["international_nature"]
            if "operator" in changes:
                self.operator = dict(changes["operator"])
            if "project_level" in changes:
                self.project_level = changes["project_level"]
            if "expected_crowd" in changes:
                self.expected_crowd = CrowdEstimate.from_dict(changes["expected_crowd"])
        elif et == EventType.APPLICATION_WITHDRAWN:
            self.withdrawn = True
            self.status = ApplicationStatus.WITHDRAWN
        elif et == EventType.SUPPLEMENT_REQUESTED:
            self.supplement_requests.append(
                {
                    "request_id": p["request_id"],
                    "missing": list(p["missing"]),
                    "reason": p["reason"],
                    "requested_by": p["requested_by"],
                    "requested_at": event.occurred_at,
                    "covered": [],
                }
            )
            self.status = ApplicationStatus.SUPPLEMENTING
        elif et == EventType.ADMISSIBILITY_EVIDENCE_SUBMITTED:
            bucket = self.evidence.setdefault(p["category"], [])
            for item in p["items"]:
                bucket.append(
                    {"item": item["item"], "doc_ref": item["doc_ref"], "request_id": p.get("request_id")}
                )
            if p.get("request_id"):
                for req in self.supplement_requests:
                    if req["request_id"] == p["request_id"]:
                        req["covered"].extend(i["item"] for i in p["items"])
            if not self.open_supplement_requests():
                self.status = (
                    ApplicationStatus.REVIEWING if self.review_started else ApplicationStatus.FILED
                )
        elif et == EventType.BENEFIT_FORECAST_ATTACHED:
            self.forecasts.append({"forecast": dict(p["forecast"]), "attached_at": event.occurred_at})
        elif et == EventType.CHANGE_DECLARED:
            self.changes.append(
                {
                    "change_no": p["change_no"],
                    "aspects": list(p["aspects"]),
                    "justification": p["justification"],
                    "declared_at": event.occurred_at,
                    "status": "open",
                }
            )
            self.status = ApplicationStatus.REOPENED
        elif et == EventType.CHANGE_CLOSED:
            for change in self.changes:
                if change["change_no"] == p["change_no"]:
                    change["status"] = "closed"
                    change["closed_at"] = event.occurred_at
                    change["decision_version"] = p["decision_version"]
            self.status = ApplicationStatus.DECIDED

    def open_supplement_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.supplement_requests if not self._request_covered(r)]

    @staticmethod
    def _request_covered(request: dict[str, Any]) -> bool:
        return all(item in request["covered"] for item in request["missing"])


class ResourceCommitment(Aggregate):
    aggregate_type = "resource_commitment"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.case_id = aggregate_id.removeprefix("resource-")
        self.occupancies: list[Occupancy] = []
        self.finance: FinancialCommitment | None = None
        self.public_services: dict[str, PublicServicePlan] = {}
        self.commercial_rights: dict[str, Any] | None = None
        self.legacy_plan: str | None = None
        self.declaration_history: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []
        self.last_declaration_at: datetime | None = None
        self.last_check_at: datetime | None = None
        self.check_count = 0

    @property
    def check_is_fresh(self) -> bool:
        """最近一次资源申报/修订之后必须跑过冲突检测，重叠才不致静默躲过表决。"""
        return self.last_check_at is not None and (
            self.last_declaration_at is None or self.last_check_at >= self.last_declaration_at
        )

    def record_check_run(self, *, cases_checked: int, event_id: str, occurred_at: datetime) -> Event:
        return self._raise(
            EventType.CONFLICT_CHECK_RUN,
            f"组合内资源冲突检测（覆盖 {cases_checked} 个在档申办）",
            {"case_id": self.case_id, "cases_checked": cases_checked},
            event_id,
            occurred_at,
        )

    def declare(
        self,
        *,
        occupancies: list[Occupancy],
        finance: FinancialCommitment | None,
        public_services: list[PublicServicePlan],
        commercial_rights: dict[str, Any] | None,
        legacy_plan: str | None,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        if self.declaration_history:
            raise ValueError("资源已申报，修订请使用 revise")
        self._validate_occupancies(occupancies)
        payload: dict[str, Any] = {
            "case_id": self.case_id,
            "occupancies": [o.to_dict() for o in occupancies],
            "finance": finance.to_dict() if finance else None,
            "public_services": [s.to_dict() for s in public_services],
            "commercial_rights": commercial_rights,
            "legacy_plan": legacy_plan,
        }
        return self._raise(
            EventType.RESOURCE_DECLARED, "申报场馆/人员/交通/安全/财政等占用", payload, event_id, occurred_at
        )

    def revise(
        self,
        *,
        occupancies: list[Occupancy],
        reason: str,
        change_no: int,
        finance: FinancialCommitment | None = None,
        public_services: list[PublicServicePlan] | None = None,
        commercial_rights: dict[str, Any] | None = None,
        legacy_plan: str | None = None,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        if not self.declaration_history:
            raise ValueError("尚未申报资源占用")
        self._validate_occupancies(occupancies)
        payload: dict[str, Any] = {
            "case_id": self.case_id,
            "change_no": change_no,
            "reason": reason,
            "occupancies": [o.to_dict() for o in occupancies],
            "finance": finance.to_dict() if finance else None,
            "public_services": [s.to_dict() for s in public_services] if public_services is not None else None,
            "commercial_rights": commercial_rights,
            "legacy_plan": legacy_plan,
        }
        return self._raise(
            EventType.RESOURCE_REVISED,
            f"资源占用修订（变更第 {change_no} 轮）：{reason}"
            if change_no > 0
            else f"表决前资源协调修订：{reason}",
            payload,
            event_id,
            occurred_at,
        )

    def record_conflicts(self, *, conflicts: list[dict[str, Any]], event_id: str, occurred_at: datetime) -> Event:
        return self._raise(
            EventType.RESOURCE_CONFLICTED,
            f"表决前发现 {len(conflicts)} 项资源时间重叠",
            {
                "case_id": self.case_id,
                "conflicts": conflicts,
            },
            event_id,
            occurred_at,
        )

    def resolve_conflicts(
        self, *, conflict_indexes: list[int], resolution: str, note: str, event_id: str, occurred_at: datetime
    ) -> Event:
        open_idx = {i for i, c in enumerate(self.conflicts) if c["status"] == "open"}
        if not set(conflict_indexes) <= open_idx:
            raise ValueError("只能解决处于未决状态的冲突")
        return self._raise(
            EventType.RESOURCE_CONFLICT_RESOLVED,
            "资源冲突已处置",
            {
                "case_id": self.case_id,
                "conflict_indexes": list(conflict_indexes),
                "resolution": resolution,
                "note": note,
            },
            event_id,
            occurred_at,
        )

    def commit_public_service(
        self, *, service: PublicServicePlan, event_id: str, occurred_at: datetime
    ) -> Event:
        if not service.externally_committed:
            raise ValueError("仅登记已对外承诺的公共服务")
        return self._raise(
            EventType.PUBLIC_SERVICE_COMMITTED,
            f"对外承诺公共服务：{service.service_id}",
            {"case_id": self.case_id, "service": service.to_dict()},
            event_id,
            occurred_at,
        )

    @staticmethod
    def _validate_occupancies(occupancies: list[Occupancy]) -> None:
        if not occupancies:
            raise ValueError("至少申报一项资源占用")
        for o in occupancies:
            if o.capacity is not None and o.load > o.capacity:
                raise ValueError(f"资源 {o.resource_id} 单场占用 {o.load} 超过容量 {o.capacity}")

    def apply(self, event: Event) -> None:
        self.version += 1
        p = event.payload
        et = event.event_type
        if et in (EventType.RESOURCE_DECLARED, EventType.RESOURCE_REVISED):
            self.declaration_history.append({"event_type": et, "at": event.occurred_at, "payload": p})
            self.last_declaration_at = event.occurred_at
            self.occupancies = [Occupancy.from_dict(o) for o in p["occupancies"]]
            if p.get("finance"):
                self.finance = FinancialCommitment.from_dict(p["finance"])
            if p.get("public_services") is not None:
                for s in p["public_services"]:
                    plan = PublicServicePlan.from_dict(s)
                    self.public_services[plan.service_id] = plan
            if p.get("commercial_rights") is not None:
                self.commercial_rights = p["commercial_rights"]
            if p.get("legacy_plan") is not None:
                self.legacy_plan = p["legacy_plan"]
        elif et == EventType.RESOURCE_CONFLICTED:
            base = len(self.conflicts)
            for offset, c in enumerate(p["conflicts"]):
                record = dict(c)
                record["index"] = base + offset
                record["status"] = "open"
                record["detected_at"] = event.occurred_at
                self.conflicts.append(record)
        elif et == EventType.RESOURCE_CONFLICT_RESOLVED:
            for idx in p["conflict_indexes"]:
                self.conflicts[idx]["status"] = "resolved"
                self.conflicts[idx]["resolution"] = p["resolution"]
                self.conflicts[idx]["resolution_note"] = p["note"]
        elif et == EventType.PUBLIC_SERVICE_COMMITTED:
            plan = PublicServicePlan.from_dict(p["service"])
            self.public_services[plan.service_id] = plan
        elif et == EventType.CONFLICT_CHECK_RUN:
            self.last_check_at = event.occurred_at
            self.check_count += 1


class ReviewOpinion(Aggregate):
    aggregate_type = "review_opinion"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.case_id = aggregate_id.removeprefix("review-")
        self.recusals: list[dict[str, Any]] = []
        self.signatures: list[dict[str, Any]] = []
        self.dissents: list[dict[str, Any]] = []
        self.rounds: list[dict[str, Any]] = []  # 重开轮次（不含首轮）
        self.conditions: dict[str, dict[str, Any]] = {}
        self.protections: list[dict[str, Any]] = []

    @property
    def current_round(self) -> int:
        return len(self.rounds)

    @property
    def round_open(self) -> bool:
        return bool(self.rounds and self.rounds[-1]["resolved_at"] is None)

    def recuse(self, *, member: str, reason: str, event_id: str, occurred_at: datetime) -> Event:
        if any(r["member"] == member for r in self.recusals):
            raise ValueError(f"{member} 已声明回避")
        return self._raise(
            EventType.RECUSAL_DECLARED,
            f"回避声明：{member}",
            {"case_id": self.case_id, "member": member, "reason": reason},
            event_id,
            occurred_at,
        )

    def sign(self, *, member: str, department: str, position: str, event_id: str, occurred_at: datetime) -> Event:
        if any(r["member"] == member for r in self.recusals):
            raise ValueError(f"{member} 已回避，不得参与会签")
        if any(s["member"] == member and s["round"] == self.current_round for s in self.signatures):
            raise ValueError(f"{member} 在本轮已会签")
        return self._raise(
            EventType.OPINION_SIGNED,
            f"会签：{department} {member}",
            {
                "case_id": self.case_id,
                "member": member,
                "department": department,
                "position": position,
                "round": self.current_round,
            },
            event_id,
            occurred_at,
        )

    def record_dissent(self, *, member: str, opinion: str, event_id: str, occurred_at: datetime) -> Event:
        """少数意见必须保留：不阻断决定，但随档案永久可查。"""
        if not opinion.strip():
            raise ValueError("少数意见内容为空")
        return self._raise(
            EventType.DISSENT_RECORDED,
            f"保留少数意见：{member}",
            {"case_id": self.case_id, "member": member, "opinion": opinion, "round": self.current_round},
            event_id,
            occurred_at,
        )

    def reopen(
        self,
        *,
        phases: list[str],
        aspects: list[str],
        justification: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        if self.round_open:
            raise ValueError("上一轮重开环节尚未审议完结")
        valid = {p.value for p in Phase}
        if not phases or any(p not in valid for p in phases):
            raise ValueError("重开环节非法")
        return self._raise(
            EventType.CHANGE_REOPENED,
            f"重开受影响环节：{'、'.join(phases)}",
            {
                "case_id": self.case_id,
                "round_no": len(self.rounds) + 1,
                "phases": list(phases),
                "aspects": list(aspects),
                "justification": justification,
            },
            event_id,
            occurred_at,
        )

    def resolve_round(
        self, *, conclusions: dict[str, str], event_id: str, occurred_at: datetime
    ) -> Event:
        if not self.round_open:
            raise ValueError("当前没有待完结的重开环节")
        phases = self.rounds[-1]["phases"]
        if set(conclusions) != set(phases):
            raise ValueError("须对全部重开环节逐一给出结论，不得遗漏")
        return self._raise(
            EventType.REVIEW_RESOLVED,
            f"第 {len(self.rounds)} 轮重开环节审议完结",
            {
                "case_id": self.case_id,
                "round_no": len(self.rounds),
                "conclusions": dict(conclusions),
            },
            event_id,
            occurred_at,
        )

    def set_conditions(
        self, *, conditions: list[dict[str, Any]], event_id: str, occurred_at: datetime
    ) -> Event:
        if not conditions:
            raise ValueError("条件性批准至少附带一项条件")
        for c in conditions:
            if c["id"] in self.conditions:
                raise ValueError(f"条件编号重复：{c['id']}")
            if c["owner_phase"] not in {p.value for p in Phase}:
                raise ValueError("条件责任环节非法")
        return self._raise(
            EventType.CONDITION_SET,
            f"设置批准条件 {len(conditions)} 项",
            {"case_id": self.case_id, "conditions": [dict(c) for c in conditions]},
            event_id,
            occurred_at,
        )

    def satisfy_condition(
        self, *, condition_id: str, evidence_ref: str, note: str, event_id: str, occurred_at: datetime
    ) -> Event:
        if condition_id not in self.conditions:
            raise ValueError("条件不存在")
        if self.conditions[condition_id]["status"] == "satisfied":
            raise ValueError("条件已满足")
        return self._raise(
            EventType.CONDITION_SATISFIED,
            f"批准条件已履行：{condition_id}",
            {
                "case_id": self.case_id,
                "condition_id": condition_id,
                "evidence_ref": evidence_ref,
                "note": note,
            },
            event_id,
            occurred_at,
        )

    def protect_public_service(
        self,
        *,
        service_id: str,
        replacement: str,
        change_no: int,
        note: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        return self._raise(
            EventType.PUBLIC_SERVICE_PROTECTED,
            f"公共服务持续保障登记：{service_id}",
            {
                "case_id": self.case_id,
                "service_id": service_id,
                "replacement": replacement,
                "change_no": change_no,
                "note": note,
            },
            event_id,
            occurred_at,
        )

    def apply(self, event: Event) -> None:
        self.version += 1
        p = event.payload
        et = event.event_type
        if et == EventType.RECUSAL_DECLARED:
            self.recusals.append({"member": p["member"], "reason": p["reason"], "at": event.occurred_at})
        elif et == EventType.OPINION_SIGNED:
            self.signatures.append(
                {
                    "member": p["member"],
                    "department": p["department"],
                    "position": p.get("position", ""),
                    "round": p.get("round", 0),
                    "at": event.occurred_at,
                }
            )
        elif et == EventType.DISSENT_RECORDED:
            self.dissents.append(
                {"member": p["member"], "opinion": p["opinion"], "round": p.get("round", 0), "at": event.occurred_at}
            )
        elif et == EventType.CHANGE_REOPENED:
            self.rounds.append(
                {
                    "round_no": p["round_no"],
                    "phases": list(p["phases"]),
                    "aspects": list(p["aspects"]),
                    "justification": p["justification"],
                    "opened_at": event.occurred_at,
                    "resolved_at": None,
                    "conclusions": None,
                }
            )
        elif et == EventType.REVIEW_RESOLVED:
            for rnd in self.rounds:
                if rnd["round_no"] == p["round_no"]:
                    rnd["resolved_at"] = event.occurred_at
                    rnd["conclusions"] = dict(p["conclusions"])
        elif et == EventType.CONDITION_SET:
            for c in p["conditions"]:
                self.conditions[c["id"]] = {
                    "id": c["id"],
                    "description": c["description"],
                    "owner_phase": c["owner_phase"],
                    "required_by": c.get("required_by"),
                    "status": "open",
                    "round": len(self.rounds),
                    "set_at": event.occurred_at,
                }
        elif et == EventType.CONDITION_SATISFIED:
            self.conditions[p["condition_id"]]["status"] = "satisfied"
            self.conditions[p["condition_id"]]["satisfied_at"] = event.occurred_at
            self.conditions[p["condition_id"]]["evidence_ref"] = p["evidence_ref"]
        elif et == EventType.PUBLIC_SERVICE_PROTECTED:
            self.protections.append(
                {
                    "service_id": p["service_id"],
                    "replacement": p["replacement"],
                    "change_no": p["change_no"],
                    "note": p["note"],
                    "at": event.occurred_at,
                }
            )


class PortfolioDecision(Aggregate):
    """年度赛事组合的决定流（每年一条聚合流，如 annual-2026）。"""

    aggregate_type = "portfolio_decision"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.year = aggregate_id.removeprefix("annual-")
        self.decisions: list[dict[str, Any]] = []

    def issue(
        self,
        *,
        case_id: str,
        decision: str,
        document_version: int,
        vote: dict[str, int],
        quorum: int,
        voting_members: list[str],
        recused_members: list[str],
        conditions: list[str],
        note: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Event:
        if decision not in {d.value for d in Decision}:
            raise ValueError("决定类型非法")
        if decision == Decision.CONDITIONAL_APPROVAL and not conditions:
            raise ValueError("条件性批准必须附带条件")
        prior = [d for d in self.decisions if d["case_id"] == case_id]
        expected_version = (prior[-1]["document_version"] + 1) if prior else 1
        if document_version != expected_version:
            raise ValueError(f"决定版本应为 {expected_version}")
        if sum(vote.values()) != len(voting_members):
            raise ValueError("表决票数须与实际参与表决人数一致（回避者不计）")
        return self._raise(
            EventType.DECISION_ISSUED,
            f"{case_id} 决定版本 v{document_version}：{decision}",
            {
                "case_id": case_id,
                "portfolio_id": self.id,
                "decision": decision,
                "document_version": document_version,
                "vote": dict(vote),
                "quorum": quorum,
                "voting_members": list(voting_members),
                "recused_members": list(recused_members),
                "conditions": list(conditions),
                "note": note,
            },
            event_id,
            occurred_at,
        )

    def apply(self, event: Event) -> None:
        self.version += 1
        if event.event_type == EventType.DECISION_ISSUED:
            p = event.payload
            self.decisions.append(
                {
                    "case_id": p["case_id"],
                    "decision": p["decision"],
                    "document_version": p["document_version"],
                    "vote": dict(p["vote"]),
                    "quorum": p["quorum"],
                    "voting_members": list(p["voting_members"]),
                    "recused_members": list(p["recused_members"]),
                    "conditions": list(p["conditions"]),
                    "note": p.get("note", ""),
                    "issued_at": event.occurred_at,
                }
            )

    def decision_for(self, case_id: str) -> dict[str, Any] | None:
        matches = [d for d in self.decisions if d["case_id"] == case_id]
        return matches[-1] if matches else None
