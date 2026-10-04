"""承办审议服务：把申办、会签、决定、变更串成同一决策档案。

设计要点（对应专班要求）：
- 每次申办/补件/变更都形成决策档案的一个版本，栏目齐全才进入审议；
- 竞技、职业、群众赛事按各自准入证据校验，缺件进入"待补正"并给出中文补正理由；
- 预测收益只能作为带口径与置信范围的材料存档，决定必须由有权部门作出；
- 同一资源的时间重叠在表决前显现：存在未协调的冲突时不能作出决定；
- 回避登记、会签意见、少数意见全部留痕；
- 批准后规模/场地/资金来源发生实质变化时只重开受影响环节；
- 已对外承诺的公共服务只能经明示理由与权限撤销，不得静默消失。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from src.admissions import missing_evidence
from src.conflicts import detect_overlaps
from src.events import EventStore
from src.models import (
    CHANGE_IMPACT,
    REQUIRED_SECTIONS,
    REQUIRED_STAGES,
    SCALE_SUBSTANTIAL_RATIO,
    Application,
    ApplicationStatus,
    ApplicationVersion,
    ChangeKind,
    ChangeRequest,
    Commitment,
    Condition,
    Conflict,
    Decision,
    DecisionOutcome,
    DomainError,
    EventCategory,
    Occupancy,
    Opinion,
    OpinionPosition,
    PublicServiceCommitment,
    Recusal,
    ResourceKind,
    Stage,
)

#: 决定只能由有权部门作出；这些取值视为"由系统自动决定"，一律拒绝。
_NON_HUMAN_DECIDERS = {"system", "auto", "system-auto", "自动", "系统", "系统自动"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class ReviewService:
    """承办审议后端的核心服务。状态在内存中，全部变更同时写入事件日志。"""

    def __init__(
        self,
        store: EventStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.clock = clock or _utc_now
        self.store = store or EventStore(clock=self.clock)
        self.applications: dict[str, Application] = {}
        self.opinions: dict[str, list[Opinion]] = {}
        self.recusals: dict[str, list[Recusal]] = {}
        self.conflicts: dict[str, Conflict] = {}
        self.commitments: dict[str, Commitment] = {}
        self.public_services: dict[str, PublicServiceCommitment] = {}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _now(self) -> str:
        return self.clock().isoformat()

    def _get(self, application_id: str) -> Application:
        app = self.applications.get(application_id)
        if app is None:
            raise DomainError(f"申办不存在：{application_id}")
        return app

    @staticmethod
    def _validate_forecast(forecast: dict) -> None:
        """预测收益必须带口径与置信范围，否则不能作为材料存档。"""
        missing = [
            key
            for key in ("amount_low", "amount_high", "confidence", "methodology")
            if key not in forecast
        ]
        if missing:
            raise DomainError(
                "预测收益材料缺少口径或置信范围字段",
                {"missing": missing},
            )
        low, high = forecast["amount_low"], forecast["amount_high"]
        confidence = forecast["confidence"]
        if not (isinstance(low, (int, float)) and isinstance(high, (int, float)) and 0 <= low <= high):
            raise DomainError("预测收益置信区间不合法：需 0 ≤ 下限 ≤ 上限")
        if not (isinstance(confidence, (int, float)) and 0 < confidence <= 1):
            raise DomainError("预测收益置信度需在 (0, 1] 区间")
        if not str(forecast["methodology"]).strip():
            raise DomainError("预测收益必须说明测算口径（methodology）")

    @staticmethod
    def _validate_sections(sections: dict) -> None:
        missing = [key for key in REQUIRED_SECTIONS if key not in sections]
        if missing:
            raise DomainError("决策档案栏目不齐全", {"missing_sections": missing})
        requests = sections["resource_requests"]
        if not isinstance(requests, list) or not requests:
            raise DomainError("场馆与人员占用（resource_requests）至少一项")
        for idx, req in enumerate(requests):
            try:
                kind = ResourceKind(req["kind"])
                start = datetime.fromisoformat(req["start"])
                end = datetime.fromisoformat(req["end"])
            except (KeyError, ValueError) as exc:
                raise DomainError(f"资源占用第 {idx} 项格式不合法：{exc}") from exc
            if not req.get("resource_id"):
                raise DomainError(f"资源占用第 {idx} 项缺少 resource_id")
            if not start < end:
                raise DomainError(f"资源占用第 {idx} 项开始时间须早于结束时间")
            if not isinstance(req.get("quantity"), int) or req["quantity"] <= 0:
                raise DomainError(f"资源占用第 {idx} 项 quantity 须为正整数")
            req["kind"] = kind.value  # 归一化
        crowd = sections["expected_crowd"]
        if not isinstance(crowd, dict) or not crowd:
            raise DomainError("预计人群（expected_crowd）不能为空")
        for key, value in crowd.items():
            if not isinstance(value, int) or value < 0:
                raise DomainError(f"预计人群字段 {key} 须为非负整数")
        forecast = sections.get("benefit_forecast")
        if forecast is not None:
            ReviewService._validate_forecast(forecast)
        for svc in sections.get("public_services", []):
            if not svc.get("service_id") or not svc.get("description"):
                raise DomainError("公共服务承诺须含 service_id 与 description")
            for window in svc.get("windows", []):
                if not window.get("resource_id"):
                    raise DomainError("公共服务占用窗口须含 resource_id")
                w_start = datetime.fromisoformat(window["start"])
                w_end = datetime.fromisoformat(window["end"])
                if not w_start < w_end:
                    raise DomainError("公共服务占用窗口开始时间须早于结束时间")

    def _occupancies(self, exclude_application: str | None = None) -> list[Occupancy]:
        """当前全部占用：已批准承诺 + 其他在审申办 + 公共服务承诺窗口。"""
        found: list[Occupancy] = []
        for commitment in self.commitments.values():
            if commitment.status == "active" and commitment.application_id != exclude_application:
                found.append(Occupancy(
                    owner_id=commitment.application_id,
                    resource_id=commitment.resource_id,
                    kind=commitment.kind,
                    start=commitment.start,
                    end=commitment.end,
                    quantity=commitment.quantity,
                    label="已批准赛事占用",
                ))
        for other in self.applications.values():
            if other.id == exclude_application:
                continue
            if other.status not in (ApplicationStatus.IN_REVIEW, ApplicationStatus.CHANGE_UNDER_REVIEW):
                continue
            for req in other.current_version.sections["resource_requests"]:
                found.append(Occupancy(
                    owner_id=other.id,
                    resource_id=req["resource_id"],
                    kind=ResourceKind(req["kind"]),
                    start=req["start"],
                    end=req["end"],
                    quantity=req["quantity"],
                    label=f"在审申办：{other.name}",
                ))
        for psc in self.public_services.values():
            if psc.status != "active":
                continue
            for window in psc.windows:
                found.append(Occupancy(
                    owner_id=psc.id,
                    resource_id=window["resource_id"],
                    kind=ResourceKind(window.get("kind", ResourceKind.PUBLIC_FITNESS_SLOT.value)),
                    start=window["start"],
                    end=window["end"],
                    quantity=window.get("quantity", 1),
                    label=f"公共服务承诺：{psc.description}",
                ))
        return found

    def _recheck_conflicts(self, app: Application) -> None:
        """以申办当前版本重新检测重叠；旧的未决冲突标记为已被取代。"""
        for conflict in self.conflicts.values():
            if conflict.status == "open" and app.id in conflict.owners:
                conflict.status = "superseded"
        version = app.current_version
        existing = self._occupancies(exclude_application=app.id)
        for idx, req in enumerate(version.sections["resource_requests"]):
            new = Occupancy(
                owner_id=app.id,
                resource_id=req["resource_id"],
                kind=ResourceKind(req["kind"]),
                start=req["start"],
                end=req["end"],
                quantity=req["quantity"],
                label=f"本申办第 {version.number} 版",
            )
            for other, o_start, o_end in detect_overlaps(new, existing):
                conflict = Conflict(
                    id=_new_id("cfl"),
                    resource_id=req["resource_id"],
                    owners=(app.id, other.owner_id),
                    overlap_start=o_start,
                    overlap_end=o_end,
                    status="open",
                    created_at=self._now(),
                )
                self.conflicts[conflict.id] = conflict
                self.store.append(
                    "RESOURCE_CONFLICTED",
                    "resource_commitment",
                    f"resreq-{app.id}-v{version.number}-{idx}",
                    f"资源 {req['resource_id']} 时段重叠：{app.name} 与 {other.label}",
                    payload={
                        "conflict_id": conflict.id,
                        "resource_id": req["resource_id"],
                        "owners": list(conflict.owners),
                        "other_label": other.label,
                        "overlap_start": o_start,
                        "overlap_end": o_end,
                    },
                    correlation_id=app.id,
                )

    def _open_conflicts_for(self, application_id: str) -> list[Conflict]:
        return [
            c for c in self.conflicts.values()
            if c.status == "open" and application_id in c.owners
        ]

    def _stage_complete(self, app: Application, stage: Stage) -> bool:
        required = app.stage_iterations.get(stage, 1)
        return any(
            op.stage == stage
            and op.iteration >= required
            and op.position in (OpinionPosition.CONCUR, OpinionPosition.CONCUR_WITH_CONDITIONS)
            for op in self.opinions.get(app.id, [])
        )

    def _incomplete_stages(self, app: Application) -> list[Stage]:
        return [s for s in REQUIRED_STAGES if not self._stage_complete(app, s)]

    def _check_decider(self, decided_by: str, rationale: str) -> None:
        if not decided_by or not decided_by.strip():
            raise DomainError("决定必须记载有权部门（decided_by）")
        if decided_by.strip().lower() in _NON_HUMAN_DECIDERS:
            raise DomainError("预测收益等材料不能自动替代有权部门作出决定")
        if not rationale or not rationale.strip():
            raise DomainError("决定必须记载理由（rationale）")

    def _register_version(self, app: Application, sections: dict, change_id: str | None) -> ApplicationVersion:
        missing = missing_evidence(app.category, sections.get("evidence"))
        version = ApplicationVersion(
            number=len(app.versions) + 1,
            submitted_at=self._now(),
            sections=sections,
            missing_evidence=missing,
            change_id=change_id,
        )
        app.versions.append(version)
        return version

    # ------------------------------------------------------------------
    # 申办与补件
    # ------------------------------------------------------------------
    def file_application(self, draft: dict) -> dict:
        """受理申办。证据不齐进入待补正，补正理由对申请方可见。"""
        try:
            category = EventCategory(draft["category"])
        except (KeyError, ValueError) as exc:
            raise DomainError(f"赛事类别不合法：{draft.get('category')!r}") from exc
        applicant = draft.get("applicant") or {}
        if not applicant.get("org_id") or not applicant.get("legal_name"):
            raise DomainError("运营主体须含 org_id 与 legal_name")
        if not draft.get("name"):
            raise DomainError("申办须含赛事名称 name")
        sections = {key: draft[key] for key in draft if key not in ("name", "category", "applicant")}
        self._validate_sections(sections)

        app = Application(
            id=_new_id("app"),
            name=draft["name"],
            category=category,
            applicant=applicant,
            status=ApplicationStatus.IN_REVIEW,
            created_at=self._now(),
            stage_iterations={stage: 1 for stage in REQUIRED_STAGES},
        )
        self.applications[app.id] = app
        version = self._register_version(app, sections, change_id=None)
        self.store.append(
            "APPLICATION_FILED",
            "event_application",
            app.id,
            f"受理申办：{app.name}（第 {version.number} 版）",
            payload={
                "version": version.number,
                "category": category.value,
                "applicant": applicant,
                "missing_evidence": version.missing_evidence,
                "sections": sections,
            },
            correlation_id=app.id,
        )
        if version.missing_evidence:
            app.status = ApplicationStatus.SUPPLEMENT_REQUIRED
            self.store.append(
                "SUPPLEMENT_REQUESTED",
                "event_application",
                app.id,
                f"要求补正：{'、'.join(version.missing_evidence)}",
                payload={"version": version.number, "missing": version.missing_evidence},
                correlation_id=app.id,
            )
        else:
            self._recheck_conflicts(app)
        return self.decision_file(app.id)

    def submit_version(self, application_id: str, sections: dict) -> dict:
        """提交新一版材料（补件或主动修订）。批准后的修订须走变更流程。"""
        app = self._get(application_id)
        if app.status not in (ApplicationStatus.SUPPLEMENT_REQUIRED, ApplicationStatus.IN_REVIEW):
            raise DomainError("当前状态不允许直接提交新版本；批准后请使用变更流程")
        self._validate_sections(sections)
        was_supplement = app.status == ApplicationStatus.SUPPLEMENT_REQUIRED
        version = self._register_version(app, sections, change_id=None)
        event_type = "SUPPLEMENT_PROVIDED" if was_supplement else "APPLICATION_FILED"
        self.store.append(
            event_type,
            "event_application",
            app.id,
            f"收到第 {version.number} 版材料：{app.name}",
            payload={
                "version": version.number,
                "missing_evidence": version.missing_evidence,
                "sections": sections,
            },
            correlation_id=app.id,
        )
        if version.missing_evidence:
            app.status = ApplicationStatus.SUPPLEMENT_REQUIRED
            self.store.append(
                "SUPPLEMENT_REQUESTED",
                "event_application",
                app.id,
                f"要求补正：{'、'.join(version.missing_evidence)}",
                payload={"version": version.number, "missing": version.missing_evidence},
                correlation_id=app.id,
            )
        else:
            app.status = ApplicationStatus.IN_REVIEW
            self._recheck_conflicts(app)
        return self.decision_file(app.id)

    def withdraw(self, application_id: str, reason: str) -> dict:
        app = self._get(application_id)
        if app.status in (ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN):
            raise DomainError("申办已终结，不能撤回")
        app.status = ApplicationStatus.WITHDRAWN
        for commitment in self.commitments.values():
            if commitment.application_id == app.id and commitment.status == "active":
                commitment.status = "released"
                self.store.append(
                    "RESOURCE_RELEASED",
                    "resource_commitment",
                    commitment.id,
                    f"撤回释放资源：{commitment.resource_id}",
                    payload={"application_id": app.id, "resource_id": commitment.resource_id},
                    correlation_id=app.id,
                )
        for conflict in self._open_conflicts_for(app.id):
            conflict.status = "superseded"
        self.store.append(
            "APPLICATION_WITHDRAWN",
            "event_application",
            app.id,
            f"申办撤回：{app.name}",
            payload={"reason": reason},
            correlation_id=app.id,
        )
        return self.decision_file(app.id)

    # ------------------------------------------------------------------
    # 回避与会签
    # ------------------------------------------------------------------
    def declare_recusal(self, application_id: str, person: str, reason: str, stage: str | None = None) -> dict:
        """登记回避关系。回避关系本身留痕，被回避人此后的会签无效。"""
        app = self._get(application_id)
        if not person or not reason:
            raise DomainError("回避登记须含人员与理由")
        scope = Stage(stage) if stage else None
        recusal = Recusal(person=person, reason=reason, stage=scope, declared_at=self._now())
        self.recusals.setdefault(app.id, []).append(recusal)
        self.store.append(
            "RECUSAL_DECLARED",
            "review_opinion",
            _new_id("recusal"),
            f"回避登记：{person}（{scope.value if scope else '全部环节'}）",
            payload={
                "application_id": app.id,
                "person": person,
                "reason": reason,
                "stage": scope.value if scope else None,
            },
            correlation_id=app.id,
        )
        return {"application_id": app.id, "person": person, "stage": stage, "reason": reason}

    def _recused(self, app: Application, person: str, stage: Stage) -> str | None:
        for recusal in self.recusals.get(app.id, []):
            if recusal.person == person and (recusal.stage is None or recusal.stage == stage):
                return recusal.reason
        return None

    def sign_opinion(
        self,
        application_id: str,
        stage: str,
        department: str,
        signer: str,
        position: str,
        comment: str = "",
    ) -> dict:
        """会签一个环节。少数意见（反对）同样入档保留。"""
        app = self._get(application_id)
        if app.status not in (ApplicationStatus.IN_REVIEW, ApplicationStatus.CHANGE_UNDER_REVIEW):
            raise DomainError("当前状态不能会签")
        stage_enum = Stage(stage)
        position_enum = OpinionPosition(position)
        if app.status == ApplicationStatus.CHANGE_UNDER_REVIEW and stage_enum not in app.reopened_stages:
            raise DomainError("变更审议中，只能会签被重开的环节", {"reopened": [s.value for s in app.reopened_stages]})
        if stage_enum == Stage.ADMISSION and app.current_version.missing_evidence:
            raise DomainError(
                "准入证据未齐，不能会签准入环节",
                {"missing": app.current_version.missing_evidence},
            )
        reason = self._recused(app, signer, stage_enum)
        if reason is not None:
            raise DomainError(f"{signer} 已登记回避（{reason}），会签无效")
        required = app.stage_iterations.get(stage_enum, 1)
        if any(
            op.stage == stage_enum and op.department == department and op.iteration >= required
            for op in self.opinions.get(app.id, [])
        ):
            raise DomainError(f"{department} 已在第 {required} 轮会签 {stage_enum.value} 环节")
        opinion = Opinion(
            stage=stage_enum,
            department=department,
            signer=signer,
            position=position_enum,
            comment=comment,
            iteration=app.iteration,
            signed_at=self._now(),
        )
        self.opinions.setdefault(app.id, []).append(opinion)
        record = self.store.append(
            "OPINION_SIGNED",
            "review_opinion",
            _new_id("op"),
            f"{department} 会签 {stage_enum.value}：{position_enum.value}",
            payload={
                "application_id": app.id,
                "stage": stage_enum.value,
                "department": department,
                "signer": signer,
                "position": position_enum.value,
                "comment": comment,
                "iteration": opinion.iteration,
            },
            correlation_id=app.id,
        )
        return {"opinion_event": record["event_id"], "stage": stage_enum.value, "iteration": opinion.iteration}

    # ------------------------------------------------------------------
    # 冲突协调
    # ------------------------------------------------------------------
    def open_conflicts(self, application_id: str) -> list[dict]:
        self._get(application_id)
        return [
            {
                "conflict_id": c.id,
                "resource_id": c.resource_id,
                "owners": list(c.owners),
                "overlap_start": c.overlap_start,
                "overlap_end": c.overlap_end,
            }
            for c in self._open_conflicts_for(application_id)
        ]

    def resolve_conflict(self, conflict_id: str, resolution: str, decided_by: str) -> dict:
        """登记协调结果。冲突协调完毕前不能表决。"""
        conflict = self.conflicts.get(conflict_id)
        if conflict is None:
            raise DomainError(f"冲突不存在：{conflict_id}")
        if conflict.status != "open":
            raise DomainError("冲突已处理")
        if not resolution or not decided_by:
            raise DomainError("协调结果须含方案与协调人")
        conflict.status = "resolved"
        conflict.resolution = resolution
        conflict.resolved_by = decided_by
        self.store.append(
            "RESOURCE_CONFLICT_RESOLVED",
            "resource_commitment",
            conflict.id,
            f"冲突已协调：{conflict.resource_id}",
            payload={
                "conflict_id": conflict.id,
                "resource_id": conflict.resource_id,
                "owners": list(conflict.owners),
                "resolution": resolution,
                "decided_by": decided_by,
            },
            correlation_id=conflict.owners[0],
        )
        return {"conflict_id": conflict.id, "status": conflict.status}

    # ------------------------------------------------------------------
    # 决定
    # ------------------------------------------------------------------
    def _minority_opinions(self, app: Application) -> list[dict]:
        result = []
        for op in self.opinions.get(app.id, []):
            required = app.stage_iterations.get(op.stage, 1)
            if op.iteration >= required and op.position == OpinionPosition.DISSENT:
                result.append({
                    "stage": op.stage.value,
                    "department": op.department,
                    "signer": op.signer,
                    "comment": op.comment,
                    "signed_at": op.signed_at,
                })
        return result

    def _issue_decision(
        self,
        app: Application,
        outcome: DecisionOutcome,
        decided_by: str,
        rationale: str,
        conditions: list[dict] | None,
        change_id: str | None,
    ) -> Decision:
        condition_objs = [
            Condition(id=f"cond-{app.id}-{len(app.decisions) + 1}-{idx + 1}", description=item["description"])
            for idx, item in enumerate(conditions or [])
        ]
        decision = Decision(
            id=f"dec-{app.id}-{len(app.decisions) + 1}",
            decision_version=len(app.decisions) + 1,
            outcome=outcome,
            decided_by=decided_by,
            rationale=rationale,
            conditions=condition_objs,
            minority_opinions=self._minority_opinions(app),
            forecast_snapshot=app.current_version.sections.get("benefit_forecast"),
            based_on_version=app.current_version.number,
            issued_at=self._now(),
            change_id=change_id,
        )
        app.decisions.append(decision)
        self.store.append(
            "DECISION_ISSUED",
            "portfolio_decision",
            decision.id,
            f"第 {decision.decision_version} 版决定：{outcome.value}（{app.name}）",
            payload={
                "application_id": app.id,
                "decision_version": decision.decision_version,
                "outcome": outcome.value,
                "decided_by": decided_by,
                "rationale": rationale,
                "conditions": [{"id": c.id, "description": c.description} for c in condition_objs],
                "minority_opinions": decision.minority_opinions,
                "forecast_snapshot": decision.forecast_snapshot,
                "based_on_version": decision.based_on_version,
                "change_id": change_id,
            },
            correlation_id=app.id,
        )
        return decision

    def _activate_commitments(self, app: Application) -> None:
        version = app.current_version
        for idx, req in enumerate(version.sections["resource_requests"]):
            commitment = Commitment(
                id=f"rc-{app.id}-v{version.number}-{idx}",
                application_id=app.id,
                resource_id=req["resource_id"],
                kind=ResourceKind(req["kind"]),
                start=req["start"],
                end=req["end"],
                quantity=req["quantity"],
                status="active",
            )
            self.commitments[commitment.id] = commitment
            self.store.append(
                "RESOURCE_RESERVED",
                "resource_commitment",
                commitment.id,
                f"资源占用生效：{commitment.resource_id}",
                payload={
                    "application_id": app.id,
                    "resource_id": commitment.resource_id,
                    "kind": commitment.kind.value,
                    "start": commitment.start,
                    "end": commitment.end,
                    "quantity": commitment.quantity,
                },
                correlation_id=app.id,
            )
        for svc in version.sections.get("public_services", []):
            psc = PublicServiceCommitment(
                id=f"psc-{app.id}-{svc['service_id']}",
                application_id=app.id,
                service_id=svc["service_id"],
                description=svc["description"],
                windows=svc.get("windows", []),
            )
            self.public_services[psc.id] = psc
            self.store.append(
                "PUBLIC_SERVICE_COMMITTED",
                "public_service_commitment",
                psc.id,
                f"公共服务承诺生效：{svc['description']}",
                payload={
                    "application_id": app.id,
                    "service_id": svc["service_id"],
                    "description": svc["description"],
                    "windows": psc.windows,
                },
                correlation_id=app.id,
            )

    def issue_decision(
        self,
        application_id: str,
        outcome: str,
        decided_by: str,
        rationale: str,
        conditions: list[dict] | None = None,
    ) -> dict:
        """表决并作出决定。预测收益只作材料，不能替代有权部门。"""
        app = self._get(application_id)
        if app.status != ApplicationStatus.IN_REVIEW:
            raise DomainError("当前状态不能作出决定", {"status": app.status.value})
        outcome_enum = DecisionOutcome(outcome)
        self._check_decider(decided_by, rationale)
        if app.current_version.missing_evidence:
            raise DomainError("准入证据未齐，不能表决", {"missing": app.current_version.missing_evidence})
        incomplete = self._incomplete_stages(app)
        if incomplete:
            raise DomainError(
                "尚有环节未完成会签",
                {"incomplete_stages": [s.value for s in incomplete]},
            )
        open_conflicts = self._open_conflicts_for(app.id)
        if open_conflicts:
            raise DomainError(
                "存在未协调的资源时段重叠，须在表决前完成协调",
                {"open_conflicts": [c.id for c in open_conflicts]},
            )
        if outcome_enum == DecisionOutcome.CONDITIONALLY_APPROVED and not conditions:
            raise DomainError("条件性批准必须列明条件")
        for item in conditions or []:
            if not item.get("description"):
                raise DomainError("条件须含 description")

        decision = self._issue_decision(app, outcome_enum, decided_by, rationale, conditions, change_id=None)
        if outcome_enum in (DecisionOutcome.APPROVED, DecisionOutcome.CONDITIONALLY_APPROVED):
            self._activate_commitments(app)
            app.status = (
                ApplicationStatus.APPROVED
                if outcome_enum == DecisionOutcome.APPROVED
                else ApplicationStatus.CONDITIONALLY_APPROVED
            )
        else:
            app.status = ApplicationStatus.REJECTED
            for conflict in self._open_conflicts_for(app.id):
                conflict.status = "superseded"
        return self.decision_file(app.id)

    def fulfill_condition(self, application_id: str, condition_id: str, evidence_note: str = "") -> dict:
        app = self._get(application_id)
        for decision in reversed(app.decisions):
            for condition in decision.conditions:
                if condition.id == condition_id:
                    if condition.fulfilled:
                        raise DomainError("条件已登记达成")
                    condition.fulfilled = True
                    condition.fulfilled_at = self._now()
                    condition.evidence_note = evidence_note
                    all_done = all(c.fulfilled for c in decision.conditions)
                    if all_done and app.status == ApplicationStatus.CONDITIONALLY_APPROVED:
                        app.status = ApplicationStatus.APPROVED
                    self.store.append(
                        "CONDITION_FULFILLED",
                        "portfolio_decision",
                        decision.id,
                        f"条件达成：{condition.description}",
                        payload={
                            "application_id": app.id,
                            "condition_id": condition.id,
                            "evidence_note": evidence_note,
                            "all_conditions_fulfilled": all_done,
                        },
                        correlation_id=app.id,
                    )
                    return {"condition_id": condition_id, "fulfilled": True, "all_conditions_fulfilled": all_done}
        raise DomainError(f"条件不存在：{condition_id}")

    # ------------------------------------------------------------------
    # 批准后的变更
    # ------------------------------------------------------------------
    def _is_substantial(self, app: Application, kind: ChangeKind, new_sections: dict) -> bool:
        current = app.current_version.sections
        if kind == ChangeKind.SCALE:
            old = sum(current["expected_crowd"].values())
            new = sum(new_sections["expected_crowd"].values())
            return abs(new - old) / max(old, 1) > SCALE_SUBSTANTIAL_RATIO
        if kind == ChangeKind.VENUE:
            def venues(sections: dict) -> set[str]:
                return {
                    r["resource_id"]
                    for r in sections["resource_requests"]
                    if r["kind"] == ResourceKind.VENUE.value
                }
            return venues(current) != venues(new_sections)
        if kind == ChangeKind.FUNDING_SOURCE:
            return current["fiscal_commitment"].get("source") != new_sections["fiscal_commitment"].get("source")
        return True

    def request_change(
        self,
        application_id: str,
        kind: str,
        description: str,
        new_sections: dict,
        revocations: list[dict] | None = None,
    ) -> dict:
        """批准后申报变更。实质变化只重开受影响环节；

        新版本中消失的公共服务承诺必须在 revocations 中明示理由与权限，
        否则视为静默消失而拒绝。
        """
        app = self._get(application_id)
        if app.status not in (ApplicationStatus.APPROVED, ApplicationStatus.CONDITIONALLY_APPROVED):
            raise DomainError("只有已批准的申办才能申报变更", {"status": app.status.value})
        kind_enum = ChangeKind(kind)
        self._validate_sections(new_sections)

        # 公共服务静默消失检查
        committed = {
            psc.service_id: psc
            for psc in self.public_services.values()
            if psc.application_id == app.id and psc.status == "active"
        }
        kept = {svc["service_id"] for svc in new_sections.get("public_services", [])}
        disappearing = set(committed) - kept
        revocation_map = {item["service_id"]: item for item in (revocations or [])}
        silent = disappearing - set(revocation_map)
        if silent:
            raise DomainError(
                "已对外承诺的公共服务不得静默消失，须逐项明示撤销理由与权限",
                {"services": sorted(silent)},
            )
        for service_id in disappearing:
            item = revocation_map[service_id]
            if not item.get("reason") or not item.get("authority"):
                raise DomainError(f"撤销公共服务 {service_id} 须含理由（reason）与权限（authority）")

        substantial = self._is_substantial(app, kind_enum, new_sections)
        # 资源占用本身发生变化时，无论变更类型都必须重开资源环节
        resources_changed = (
            app.current_version.sections["resource_requests"] != new_sections["resource_requests"]
        )
        substantial = substantial or resources_changed
        affected = list(CHANGE_IMPACT[kind_enum]) if substantial else []
        if resources_changed and Stage.RESOURCE not in affected:
            affected.append(Stage.RESOURCE)
        change = ChangeRequest(
            id=_new_id("chg"),
            kind=kind_enum,
            description=description,
            substantial=substantial,
            affected_stages=affected,
            status="open",
            requested_at=self._now(),
        )
        app.changes.append(change)
        self.store.append(
            "CHANGE_REQUESTED",
            "event_application",
            app.id,
            f"变更申报（{kind_enum.value}）：{description}",
            payload={
                "change_id": change.id,
                "kind": kind_enum.value,
                "description": description,
                "substantial": substantial,
            },
            correlation_id=app.id,
        )
        version = self._register_version(app, new_sections, change_id=change.id)

        # 明示撤销的公共服务：留痕后生效（一般变更与实质变更一致处理）
        for service_id in disappearing:
            item = revocation_map[service_id]
            self.revoke_public_service(
                committed[service_id].id,
                reason=item["reason"],
                authority=item["authority"],
            )

        if not substantial:
            change.status = "closed"
            self.store.append(
                "APPLICATION_FILED",
                "event_application",
                app.id,
                f"一般变更备案（第 {version.number} 版）：{app.name}",
                payload={"version": version.number, "change_id": change.id, "minor": True, "sections": new_sections},
                correlation_id=app.id,
            )
            return {"change_id": change.id, "substantial": False, "status": change.status}

        app.iteration += 1
        for stage in change.affected_stages:
            app.stage_iterations[stage] = app.iteration
        app.reopened_stages = set(change.affected_stages)
        app.status = ApplicationStatus.CHANGE_UNDER_REVIEW
        change.status = "reopened"
        self._recheck_conflicts(app)
        self.store.append(
            "CHANGE_REOPENED",
            "event_application",
            app.id,
            f"实质变更重开环节：{'、'.join(s.value for s in change.affected_stages)}",
            payload={
                "change_id": change.id,
                "kind": kind_enum.value,
                "affected_stages": [s.value for s in change.affected_stages],
                "iteration": app.iteration,
                "version": version.number,
            },
            correlation_id=app.id,
        )
        return {
            "change_id": change.id,
            "substantial": True,
            "status": change.status,
            "affected_stages": [s.value for s in change.affected_stages],
        }

    def close_change(
        self,
        application_id: str,
        change_id: str,
        decided_by: str,
        rationale: str,
        outcome: str = "approved",
        conditions: list[dict] | None = None,
    ) -> dict:
        """受影响环节重新会签完毕后，就变更作出新版决定并换绑资源承诺。"""
        app = self._get(application_id)
        change = next((c for c in app.changes if c.id == change_id), None)
        if change is None:
            raise DomainError(f"变更不存在：{change_id}")
        if change.status != "reopened":
            raise DomainError("变更不在重开状态")
        outcome_enum = DecisionOutcome(outcome)
        if outcome_enum == DecisionOutcome.REJECTED:
            raise DomainError("变更审议不支持直接否决；请由申请方撤回或再次变更")
        self._check_decider(decided_by, rationale)
        incomplete = [s for s in change.affected_stages if not self._stage_complete(app, s)]
        if incomplete:
            raise DomainError(
                "受影响环节尚未重新会签完毕",
                {"incomplete_stages": [s.value for s in incomplete]},
            )
        open_conflicts = self._open_conflicts_for(app.id)
        if open_conflicts:
            raise DomainError(
                "存在未协调的资源时段重叠，须在表决前完成协调",
                {"open_conflicts": [c.id for c in open_conflicts]},
            )
        if outcome_enum == DecisionOutcome.CONDITIONALLY_APPROVED and not conditions:
            raise DomainError("条件性批准必须列明条件")

        self._issue_decision(app, outcome_enum, decided_by, rationale, conditions, change_id=change.id)

        # 换绑资源承诺：新版本不再占用的释放，新增占用（含数量变化）的生效
        version = app.current_version
        wanted = {
            (r["resource_id"], r["start"], r["end"], r["quantity"])
            for r in version.sections["resource_requests"]
        }
        for commitment in list(self.commitments.values()):
            if commitment.application_id != app.id or commitment.status != "active":
                continue
            if (commitment.resource_id, commitment.start, commitment.end, commitment.quantity) not in wanted:
                commitment.status = "released"
                self.store.append(
                    "RESOURCE_RELEASED",
                    "resource_commitment",
                    commitment.id,
                    f"变更释放资源：{commitment.resource_id}",
                    payload={"application_id": app.id, "resource_id": commitment.resource_id, "change_id": change.id},
                    correlation_id=app.id,
                )
        active_keys = {
            (c.resource_id, c.start, c.end, c.quantity)
            for c in self.commitments.values()
            if c.application_id == app.id and c.status == "active"
        }
        for idx, req in enumerate(version.sections["resource_requests"]):
            key = (req["resource_id"], req["start"], req["end"], req["quantity"])
            if key in active_keys:
                continue
            commitment = Commitment(
                id=f"rc-{app.id}-v{version.number}-{idx}",
                application_id=app.id,
                resource_id=req["resource_id"],
                kind=ResourceKind(req["kind"]),
                start=req["start"],
                end=req["end"],
                quantity=req["quantity"],
                status="active",
            )
            self.commitments[commitment.id] = commitment
            self.store.append(
                "RESOURCE_RESERVED",
                "resource_commitment",
                commitment.id,
                f"变更后资源占用生效：{commitment.resource_id}",
                payload={
                    "application_id": app.id,
                    "resource_id": commitment.resource_id,
                    "kind": commitment.kind.value,
                    "start": commitment.start,
                    "end": commitment.end,
                    "quantity": commitment.quantity,
                    "change_id": change.id,
                },
                correlation_id=app.id,
            )
        # 新版本新增的公共服务承诺生效
        existing_services = {
            psc.service_id
            for psc in self.public_services.values()
            if psc.application_id == app.id and psc.status == "active"
        }
        for svc in version.sections.get("public_services", []):
            if svc["service_id"] in existing_services:
                continue
            psc = PublicServiceCommitment(
                id=f"psc-{app.id}-{svc['service_id']}",
                application_id=app.id,
                service_id=svc["service_id"],
                description=svc["description"],
                windows=svc.get("windows", []),
            )
            self.public_services[psc.id] = psc
            self.store.append(
                "PUBLIC_SERVICE_COMMITTED",
                "public_service_commitment",
                psc.id,
                f"公共服务承诺生效：{svc['description']}",
                payload={
                    "application_id": app.id,
                    "service_id": svc["service_id"],
                    "description": svc["description"],
                    "windows": psc.windows,
                    "change_id": change.id,
                },
                correlation_id=app.id,
            )

        change.status = "closed"
        app.reopened_stages = set()
        app.status = (
            ApplicationStatus.APPROVED
            if outcome_enum == DecisionOutcome.APPROVED
            else ApplicationStatus.CONDITIONALLY_APPROVED
        )
        return self.decision_file(app.id)

    # ------------------------------------------------------------------
    # 公共服务承诺
    # ------------------------------------------------------------------
    def revoke_public_service(self, commitment_id: str, reason: str, authority: str) -> dict:
        """撤销已承诺的公共服务：必须明示理由与权限，全程留痕。"""
        psc = self.public_services.get(commitment_id)
        if psc is None:
            raise DomainError(f"公共服务承诺不存在：{commitment_id}")
        if psc.status != "active":
            raise DomainError("公共服务承诺已撤销")
        if not reason or not reason.strip() or not authority or not authority.strip():
            raise DomainError("撤销公共服务承诺必须明示理由（reason）与权限（authority）")
        psc.status = "revoked"
        psc.revoked_reason = reason
        psc.revoked_by = authority
        self.store.append(
            "PUBLIC_SERVICE_REVOKED",
            "public_service_commitment",
            psc.id,
            f"公共服务承诺撤销：{psc.description}",
            payload={
                "application_id": psc.application_id,
                "service_id": psc.service_id,
                "reason": reason,
                "authority": authority,
            },
            correlation_id=psc.application_id,
        )
        for conflict in self.conflicts.values():
            if conflict.status == "open" and psc.id in conflict.owners:
                conflict.status = "superseded"
        return {"commitment_id": psc.id, "status": psc.status}

    # ------------------------------------------------------------------
    # 视图
    # ------------------------------------------------------------------
    def decision_file(self, application_id: str) -> dict:
        """完整决策档案：申办版本、会签、回避、冲突、决定、变更、承诺。"""
        app = self._get(application_id)
        current = app.current_version
        opinions = [
            {
                "stage": op.stage.value,
                "department": op.department,
                "signer": op.signer,
                "position": op.position.value,
                "comment": op.comment,
                "iteration": op.iteration,
                "signed_at": op.signed_at,
            }
            for op in self.opinions.get(app.id, [])
        ]
        return {
            "application_id": app.id,
            "name": app.name,
            "category": app.category.value,
            "applicant": app.applicant,
            "status": app.status.value,
            "iteration": app.iteration,
            "current_version": current.number,
            "missing_evidence": current.missing_evidence,
            "sections": current.sections,
            "versions": [
                {
                    "number": v.number,
                    "submitted_at": v.submitted_at,
                    "missing_evidence": v.missing_evidence,
                    "change_id": v.change_id,
                }
                for v in app.versions
            ],
            "opinions": opinions,
            "recusals": [
                {
                    "person": r.person,
                    "reason": r.reason,
                    "stage": r.stage.value if r.stage else None,
                    "declared_at": r.declared_at,
                }
                for r in self.recusals.get(app.id, [])
            ],
            "open_conflicts": self.open_conflicts(app.id),
            "decisions": [
                {
                    "id": d.id,
                    "decision_version": d.decision_version,
                    "outcome": d.outcome.value,
                    "decided_by": d.decided_by,
                    "rationale": d.rationale,
                    "conditions": [
                        {"id": c.id, "description": c.description, "fulfilled": c.fulfilled}
                        for c in d.conditions
                    ],
                    "minority_opinions": d.minority_opinions,
                    "forecast_snapshot": d.forecast_snapshot,
                    "based_on_version": d.based_on_version,
                    "issued_at": d.issued_at,
                    "change_id": d.change_id,
                }
                for d in app.decisions
            ],
            "changes": [
                {
                    "id": c.id,
                    "kind": c.kind.value,
                    "description": c.description,
                    "substantial": c.substantial,
                    "affected_stages": [s.value for s in c.affected_stages],
                    "status": c.status,
                }
                for c in app.changes
            ],
            "commitments": [
                {
                    "id": c.id,
                    "resource_id": c.resource_id,
                    "kind": c.kind.value,
                    "start": c.start,
                    "end": c.end,
                    "quantity": c.quantity,
                    "status": c.status,
                }
                for c in self.commitments.values()
                if c.application_id == app.id
            ],
            "public_services": [
                {
                    "id": p.id,
                    "service_id": p.service_id,
                    "description": p.description,
                    "status": p.status,
                    "revoked_reason": p.revoked_reason,
                    "revoked_by": p.revoked_by,
                }
                for p in self.public_services.values()
                if p.application_id == app.id
            ],
        }

    def applicant_view(self, application_id: str) -> dict:
        """申请方视图：补正理由、决定版本、条件与公共服务状态。"""
        app = self._get(application_id)
        supplement_requests = [
            {
                "requested_at": e["occurred_at"],
                "version": e["payload"].get("version"),
                "missing": e["payload"].get("missing", []),
            }
            for e in self.store.query(correlation_id=app.id, event_type="SUPPLEMENT_REQUESTED")
        ]
        return {
            "application_id": app.id,
            "name": app.name,
            "status": app.status.value,
            "current_version": app.current_version.number,
            "missing_evidence": app.current_version.missing_evidence,
            "supplement_requests": supplement_requests,
            "decisions": [
                {
                    "decision_version": d.decision_version,
                    "outcome": d.outcome.value,
                    "rationale": d.rationale,
                    "issued_at": d.issued_at,
                    "conditions": [
                        {"id": c.id, "description": c.description, "fulfilled": c.fulfilled}
                        for c in d.conditions
                    ],
                }
                for d in app.decisions
            ],
            "changes": [
                {"id": c.id, "kind": c.kind.value, "substantial": c.substantial, "status": c.status}
                for c in app.changes
            ],
            "public_services": [
                {
                    "service_id": p.service_id,
                    "description": p.description,
                    "status": p.status,
                    "revoked_reason": p.revoked_reason,
                }
                for p in self.public_services.values()
                if p.application_id == app.id
            ],
        }
