"""赛事承办审议应用服务。

关键规则（需求逐条落地）：

- 竞技/职业/群众赛事使用各自的准入证据目录，国际赛事按项目性质挂靠并加涉外材料；
- 预测收益必须带口径与置信范围，且全程仅供有权部门参考，不参与任何自动放行；
- 同一资源的时间重叠在会签表决前检出并记录，未处置的阻断项不得表决；
- 回避成员不得会签；少数意见永久保留；补正请求与答复全程留痕；
- 决定分批准/条件性批准/不批准/暂缓，按文件版本逐版留存；
- 批准后规模/场地/资金来源实质变化只重开受影响环节，其他结论与条件继续有效；
- 已对外承诺的公共服务在变更中不得静默消失。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.aggregates import (
    EventApplication,
    PortfolioDecision,
    ResourceCommitment,
    ReviewOpinion,
)
from src.catalog import (
    Category,
    CHANGE_PHASES,
    ChangeAspect,
    Decision,
    EVIDENCE_ITEMS,
    INTERNATIONAL_EXTRA_EVIDENCE,
    Phase,
)
from src.conflicts import detect_conflicts, open_blocking_conflicts
from src.envelopes import Clock, IdGenerator, utc_now, uuid_id
from src.objects import (
    CrowdEstimate,
    FinancialCommitment,
    Forecast,
    Occupancy,
    PublicServicePlan,
)
from src.repository import Repository


class DomainError(RuntimeError):
    """业务规则拒绝。"""


# 各审议环节对应的会签部门（benefit_forecast 刻意不在此列：预测仅供参考，不设会签门槛）
PHASE_DEPARTMENTS: dict[str, str] = {
    Phase.OPERATOR_QUALIFICATION: "体育局",
    Phase.PROJECT_GRADING: "体育局",
    Phase.ADMISSIBILITY: "体育局",
    Phase.VENUE: "场馆管理单位",
    Phase.PERSONNEL: "人员保障部门",
    Phase.TRANSPORT: "公安交管局",
    Phase.SECURITY: "公安局",
    Phase.FINANCE: "财政局",
    Phase.COMMERCIAL: "商务部门",
    Phase.LEGACY: "体育局",
    Phase.PUBLIC_SERVICE: "体育局",
}

# 首次决定前必须完成会签的环节
BASELINE_REQUIRED_PHASES: frozenset[str] = frozenset(
    {
        Phase.OPERATOR_QUALIFICATION,
        Phase.ADMISSIBILITY,
        Phase.VENUE,
        Phase.PERSONNEL,
        Phase.TRANSPORT,
        Phase.SECURITY,
        Phase.FINANCE,
        Phase.COMMERCIAL,
        Phase.LEGACY,
    }
)


def required_evidence(category: str, international_nature: str | None = None) -> tuple[str, ...]:
    if category == Category.INTERNATIONAL:
        if international_nature not in (Category.COMPETITIVE, Category.PROFESSIONAL):
            raise DomainError("国际赛事须注明项目性质（竞技/职业）以确定准入证据")
        return EVIDENCE_ITEMS[international_nature] + INTERNATIONAL_EXTRA_EVIDENCE
    return EVIDENCE_ITEMS[category]


@dataclass(frozen=True)
class ConflictSignature:
    kind: str
    resource_id: str
    window_start: str
    window_end: str
    cases: tuple[str, ...]

    @classmethod
    def of(cls, conflict: dict[str, Any]) -> "ConflictSignature":
        return cls(
            kind=conflict["kind"],
            resource_id=conflict["resource_id"],
            window_start=conflict["window"]["start"],
            window_end=conflict["window"]["end"],
            cases=tuple(sorted(conflict["case_ids"])),
        )


class ReviewService:
    def __init__(
        self,
        repository: Repository,
        *,
        clock: Clock = utc_now,
        id_generator: IdGenerator = uuid_id,
    ) -> None:
        self.repo = repository
        self.now = clock
        self.new_id = id_generator

    # ---------- 申办与版本 ----------
    def file_application(
        self,
        case_id: str,
        *,
        title: str,
        category: str,
        season: str,
        operator: dict[str, Any],
        project_level: str,
        expected_crowd: CrowdEstimate,
        international_nature: str | None = None,
    ) -> None:
        app = self.repo.load_application(case_id)
        app.file(
            title=title,
            category=category,
            season=season,
            operator=operator,
            project_level=project_level,
            expected_crowd=expected_crowd,
            international_nature=international_nature,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(app)

    def amend_application(self, case_id: str, *, changes: dict[str, Any], reason: str, changed_sections: list[str]) -> None:
        """决定作出前的申办版本修订；决定作出后必须走实质变更流程。"""
        app = self._load_filed(case_id)
        if self._latest_decision(case_id) is not None:
            raise DomainError("已有正式决定；规模/场地/资金来源等实质变化须申报变更并重开受影响环节")
        app.amend(
            changes=changes,
            reason=reason,
            changed_sections=changed_sections,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(app)

    def withdraw_application(self, case_id: str, *, reason: str) -> None:
        app = self._load_filed(case_id)
        app.withdraw(reason=reason, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(app)

    # ---------- 补正 ----------
    def request_supplement(self, case_id: str, *, missing: list[str], reason: str, requested_by: str) -> str:
        app = self._load_filed(case_id)
        request_id = f"sup-{case_id}-{len(app.supplement_requests) + 1}"
        app.request_supplement(
            request_id=request_id,
            missing=missing,
            reason=reason,
            requested_by=requested_by,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(app)
        return request_id

    def submit_evidence(self, case_id: str, *, items: list[dict[str, str]], request_id: str | None = None) -> None:
        app = self._load_filed(case_id)
        category = app.category
        app.submit_evidence(
            category=category,
            items=items,
            request_id=request_id,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(app)

    # ---------- 预测材料（仅供参考） ----------
    def attach_forecast(self, case_id: str, forecast: Forecast) -> None:
        app = self._load_filed(case_id)
        app.attach_forecast(forecast=forecast, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(app)

    # ---------- 资源申报 ----------
    def declare_resources(
        self,
        case_id: str,
        *,
        occupancies: list[Occupancy],
        finance: FinancialCommitment,
        public_services: list[PublicServicePlan] | None = None,
        commercial_rights: dict[str, Any],
        legacy_plan: str,
    ) -> None:
        self._load_filed(case_id)
        res = self.repo.load_resources(case_id)
        res.declare(
            occupancies=occupancies,
            finance=finance,
            public_services=public_services or [],
            commercial_rights=commercial_rights,
            legacy_plan=legacy_plan,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(res)

    def commit_public_service(self, case_id: str, service: PublicServicePlan) -> None:
        self._load_filed(case_id)
        res = self.repo.load_resources(case_id)
        res.commit_public_service(service=service, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(res)

    def revise_resources(
        self,
        case_id: str,
        *,
        occupancies: list[Occupancy],
        reason: str,
        finance: FinancialCommitment | None = None,
        public_services: list[PublicServicePlan] | None = None,
        commercial_rights: dict[str, Any] | None = None,
        legacy_plan: str | None = None,
    ) -> None:
        """表决前的资源占用协调修订（如错峰、改期、追加运力）。

        与批准后的实质变更相区别：此处 change_no=0，不重开任何审议环节。
        未提供的材料沿用原申报；修订后须重新运行冲突检测。
        """
        if self._latest_decision(case_id) is not None:
            raise DomainError("已有正式决定；资源调整须随实质变更流程申报")
        self._load_filed(case_id)
        res = self.repo.load_resources(case_id)
        res.revise(
            occupancies=occupancies,
            reason=reason,
            change_no=0,
            finance=finance,
            public_services=public_services,
            commercial_rights=commercial_rights,
            legacy_plan=legacy_plan,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(res)

    # ---------- 冲突检测 ----------
    def run_conflict_check(self) -> list[dict[str, Any]]:
        """对全部在档且未退出的申办做时间重叠/运力超容量检测。

        新冲突写入相关申办的 resource_commitment 流；已消除的旧冲突自动登记消除。
        必须在会签表决前调用，阻断项未处置不得作出决定。
        """
        return self._reconcile_conflicts()

    def _reconcile_conflicts(self) -> list[dict[str, Any]]:
        """全量检测并把差异（新增/自动消除）写回各申办的资源流。"""
        occupancies_by_case = self._active_occupancies()
        found = detect_conflicts(occupancies_by_case)
        found_sigs = {ConflictSignature.of(c.to_dict()): c.to_dict() for c in found}

        # 所有在档申办，以及曾记录过冲突的申办（占用修订后需自动关闭旧冲突）
        touched_cases = set(occupancies_by_case)
        for cid in self.repo.store.cases():
            res0 = self.repo.load_resources(cid)
            if any(c["status"] == "open" for c in res0.conflicts):
                touched_cases.add(cid)

        aggregates: list[ResourceCommitment] = []
        all_found: list[dict[str, Any]] = []
        for cid in touched_cases:
            res = self.repo.load_resources(cid)
            # 自动消除：占用修订后原有重叠不再存在
            for recorded in res.conflicts:
                if recorded["status"] != "open":
                    continue
                if ConflictSignature.of(recorded) not in found_sigs:
                    res.resolve_conflicts(
                        conflict_indexes=[recorded["index"]],
                        resolution="自动消除",
                        note="占用修订后时间重叠不再存在",
                        event_id=self.new_id(),
                        occurred_at=self.now(),
                    )
            mine = [c for c in found if cid in c.case_ids]
            known = {ConflictSignature.of(c) for c in res.conflicts}
            fresh = [c.to_dict() for c in mine if ConflictSignature.of(c.to_dict()) not in known]
            if fresh:
                res.record_conflicts(conflicts=fresh, event_id=self.new_id(), occurred_at=self.now())
            # 无论有无冲突都留检测痕，保证"表决前显现过"可审计
            res.record_check_run(
                cases_checked=len(occupancies_by_case),
                event_id=self.new_id(),
                occurred_at=self.now(),
            )
            aggregates.append(res)
            all_found.extend(c.to_dict() for c in mine)

        # Repository.save 统一拉取未提交事件并原子落库
        self.repo.save(*aggregates)
        # 去重后返回本次检出的全部冲突（含此前已记录、本次仍存在的）
        unique: dict[ConflictSignature, dict[str, Any]] = {}
        for c in all_found:
            unique.setdefault(ConflictSignature.of(c), c)
        return list(unique.values())

    def resolve_conflict(self, case_id: str, *, conflict_indexes: list[int], resolution: str, note: str) -> None:
        res = self.repo.load_resources(case_id)
        res.resolve_conflicts(
            conflict_indexes=conflict_indexes,
            resolution=resolution,
            note=note,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(res)

    # ---------- 回避 / 会签 / 少数意见 ----------
    def declare_recusal(self, case_id: str, *, member: str, reason: str) -> None:
        self._load_filed(case_id)
        review = self.repo.load_review(case_id)
        review.recuse(member=member, reason=reason, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(review)

    def sign_opinion(self, case_id: str, *, member: str, department: str, position: str = "") -> None:
        self._load_filed(case_id)
        review = self.repo.load_review(case_id)
        review.sign(
            member=member,
            department=department,
            position=position,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(review)

    def record_dissent(self, case_id: str, *, member: str, opinion: str) -> None:
        review = self.repo.load_review(case_id)
        review.record_dissent(member=member, opinion=opinion, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(review)

    # ---------- 条件 ----------
    def set_conditions(self, case_id: str, conditions: list[dict[str, Any]]) -> None:
        review = self.repo.load_review(case_id)
        review.set_conditions(conditions=conditions, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(review)

    def satisfy_condition(self, case_id: str, *, condition_id: str, evidence_ref: str, note: str = "") -> None:
        review = self.repo.load_review(case_id)
        review.satisfy_condition(
            condition_id=condition_id,
            evidence_ref=evidence_ref,
            note=note,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        self.repo.save(review)

    # ---------- 表决与决定 ----------
    def issue_decision(
        self,
        year: int,
        case_id: str,
        *,
        decision: str,
        vote: dict[str, int],
        voting_members: list[str],
        quorum: int,
        note: str = "",
    ) -> None:
        app = self._load_filed(case_id)
        if app.withdrawn:
            raise DomainError("申办已撤回，不得作出决定")
        review = self.repo.load_review(case_id)
        if review.round_open:
            raise DomainError("存在尚未完结的重开环节，须先逐环节给出结论")

        self._assert_supplements_done(app)
        self._assert_evidence_complete(app)
        res = self.repo.load_resources(case_id)
        self._assert_resources_complete(res)
        self._assert_conflicts_clear(res)
        self._assert_signoff_coverage(review)
        self._assert_vote(decision, vote, voting_members, quorum, review)

        conditions = [cid for cid, c in review.conditions.items() if c["status"] == "open"]
        portfolio = self.repo.load_portfolio(year)
        prior = portfolio.decision_for(case_id)
        document_version = (prior["document_version"] + 1) if prior else 1
        portfolio.issue(
            case_id=case_id,
            decision=decision,
            document_version=document_version,
            vote=vote,
            quorum=quorum,
            voting_members=voting_members,
            recused_members=[r["member"] for r in review.recusals],
            conditions=conditions if decision == Decision.CONDITIONAL_APPROVAL else [],
            note=note,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        # 若本次决定对应的是一轮实质变更重审，关闭该轮次
        open_change = next((c for c in app.changes if c["status"] == "open"), None)
        if open_change is not None:
            app.close_change(
                change_no=open_change["change_no"],
                decision_version=document_version,
                event_id=self.new_id(),
                occurred_at=self.now(),
            )
        self.repo.save(portfolio, app)

    # ---------- 批准后实质变更 ----------
    def declare_post_decision_change(
        self,
        case_id: str,
        *,
        aspects: list[str],
        justification: str,
        occupancies: list[Occupancy],
        finance: FinancialCommitment | None = None,
        public_services: list[PublicServicePlan] | None = None,
        commercial_rights: dict[str, Any] | None = None,
        legacy_plan: str | None = None,
        protection_notes: dict[str, str] | None = None,
    ) -> None:
        app = self._load_filed(case_id)
        prior = self._latest_decision(case_id)
        if prior is None or prior["decision"] not in (
            Decision.APPROVED,
            Decision.CONDITIONAL_APPROVAL,
        ):
            raise DomainError("仅已批准（含条件性批准）的赛事可申报实质变更")
        for aspect in aspects:
            if aspect not in CHANGE_PHASES:
                raise DomainError(f"未知变更影响面：{aspect}")

        old_res = self.repo.load_resources(case_id)
        review = self.repo.load_review(case_id)
        change_no = len(app.changes) + 1
        app.declare_change(
            aspects=aspects,
            justification=justification,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )
        res = self.repo.load_resources(case_id)

        affected_phases = sorted({p for a in aspects for p in CHANGE_PHASES[a]})
        # 受影响环节的材料必须随变更重新提交，不得静默沿用旧承诺
        if Phase.VENUE in affected_phases and public_services is None:
            raise DomainError("场地变化：已对外承诺的公共服务安排须随场地变更重新确认，不得静默消失")
        if Phase.FINANCE in affected_phases and finance is None:
            raise DomainError("财政承诺受影响，须随变更重新提交，不得静默沿用旧承诺")
        if Phase.COMMERCIAL in affected_phases and commercial_rights is None:
            raise DomainError("商业权益环节受影响，须重新提交商业权益安排")
        if Phase.LEGACY in affected_phases and legacy_plan is None:
            raise DomainError("赛后利用环节受影响，须重新提交赛后利用计划")

        revised_services = self._protect_committed_services(
            old_res=old_res,
            review=review,
            public_services=public_services,
            venue_changed=Phase.VENUE in affected_phases,
            change_no=change_no,
            protection_notes=protection_notes or {},
        )

        res.revise(
            occupancies=occupancies,
            reason=justification,
            change_no=change_no,
            finance=finance,
            public_services=revised_services,
            commercial_rights=commercial_rights,
            legacy_plan=legacy_plan,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )

        review.reopen(
            phases=affected_phases,
            aspects=aspects,
            justification=justification,
            event_id=self.new_id(),
            occurred_at=self.now(),
        )

        # 先保存，再基于新占用跑冲突检测（检测会自行加载最新流）
        self.repo.save(app, res, review)
        self._refresh_conflicts_after_change(case_id)

    def resolve_reopened_round(self, case_id: str, *, conclusions: dict[str, str]) -> None:
        """逐环节给出重开结论；完结后由有权部门重新表决生成新版决定。"""
        review = self.repo.load_review(case_id)
        review.resolve_round(conclusions=conclusions, event_id=self.new_id(), occurred_at=self.now())
        self.repo.save(review)

    # ---------- 内部校验 ----------
    def _load_filed(self, case_id: str) -> EventApplication:
        app = self.repo.load_application(case_id)
        if not app.version:
            raise DomainError(f"申办 {case_id} 不存在")
        return app

    def _latest_decision(self, case_id: str) -> dict[str, Any] | None:
        # 决定按年份流存放；从全局事件中取该申办最新 DECISION_ISSUED
        decisions = [
            e
            for e in self.repo.store.all_events()
            if e.event_type == "DECISION_ISSUED" and e.payload.get("case_id") == case_id
        ]
        return decisions[-1].payload if decisions else None

    def _active_occupancies(self) -> dict[str, list[Occupancy]]:
        result: dict[str, list[Occupancy]] = {}
        for cid in self.repo.store.cases():
            app = self.repo.load_application(cid)
            if app.withdrawn:
                continue
            latest = self._latest_decision(cid)
            if latest and latest["decision"] in (Decision.REJECTED, Decision.DEFERRED):
                continue
            res = self.repo.load_resources(cid)
            if res.occupancies:
                result[cid] = res.occupancies
        return result

    def _assert_supplements_done(self, app: EventApplication) -> None:
        pending = app.open_supplement_requests()
        if pending:
            items = "、".join(pending[0]["missing"])
            raise DomainError(f"补正尚未完成（{pending[0]['request_id']}：{items}），不得进入表决")

    def _assert_evidence_complete(self, app: EventApplication) -> None:
        required = required_evidence(app.category, app.international_nature)
        submitted_names = {
            entry["item"] for entries in app.evidence.values() for entry in entries
        }
        missing = [name for name in required if name not in submitted_names]
        if missing:
            raise DomainError(f"{app.category} 准入证据不齐：{'、'.join(missing)}")

    def _assert_resources_complete(self, res: ResourceCommitment) -> None:
        if not res.declaration_history:
            raise DomainError("尚未申报场馆、人员、交通、安全、财政等占用材料")
        if res.finance is None:
            raise DomainError("缺少财政承诺")
        if res.commercial_rights is None:
            raise DomainError("缺少商业权益安排")
        if not res.legacy_plan:
            raise DomainError("缺少赛后利用计划")

    @staticmethod
    def _assert_conflicts_clear(res: ResourceCommitment) -> None:
        if not res.check_is_fresh:
            raise DomainError("尚未在最新资源材料上进行表决前冲突检测")
        blocking = open_blocking_conflicts(res.conflicts)
        if blocking:
            c = blocking[0]
            raise DomainError(
                f"资源时间重叠未处置：{c['resource_id']} {c['window']['start']}~{c['window']['end']}"
            )

    @staticmethod
    def _assert_signoff_coverage(review: ReviewOpinion) -> None:
        covered: dict[int, set[str]] = {}
        for sig in review.signatures:
            covered.setdefault(sig["round"], set()).add(sig["department"])

        missing = [
            PHASE_DEPARTMENTS[p] for p in BASELINE_REQUIRED_PHASES if PHASE_DEPARTMENTS[p] not in covered.get(0, set())
        ]
        if missing:
            raise DomainError(f"会签部门不齐：{'、'.join(sorted(set(missing)))}")
        for rnd in review.rounds:
            need = {PHASE_DEPARTMENTS[p] for p in rnd["phases"] if p in PHASE_DEPARTMENTS}
            have = covered.get(rnd["round_no"], set())
            gap = need - have
            if gap:
                raise DomainError(
                    f"第 {rnd['round_no']} 轮重开环节需重新会签：{'、'.join(sorted(gap))}"
                )

    @staticmethod
    def _assert_vote(
        decision: str,
        vote: dict[str, int],
        voting_members: list[str],
        quorum: int,
        review: ReviewOpinion,
    ) -> None:
        if len(voting_members) < quorum:
            raise DomainError(f"出席表决 {len(voting_members)} 人，未达法定人数 {quorum}")
        recused = {r["member"] for r in review.recusals}
        overlap = recused & set(voting_members)
        if overlap:
            raise DomainError(f"已回避成员不得参与表决：{'、'.join(sorted(overlap))}")
        if sum(vote.values()) != len(voting_members):
            raise DomainError("表决票数合计须与参与表决人数一致")
        yes = vote.get("赞成", 0)
        if decision in (Decision.APPROVED, Decision.CONDITIONAL_APPROVAL):
            if yes * 2 <= len(voting_members):
                raise DomainError("同意票未过半，不得作出批准决定")
        if decision == Decision.REJECTED and vote.get("反对", 0) * 2 <= len(voting_members):
            raise DomainError("反对票未过半，不得作出不批准决定")

    def _protect_committed_services(
        self,
        *,
        old_res: ResourceCommitment,
        review: ReviewOpinion,
        public_services: list[PublicServicePlan] | None,
        venue_changed: bool,
        change_no: int,
        protection_notes: dict[str, str],
    ) -> list[PublicServicePlan] | None:
        committed = [s for s in old_res.public_services.values() if s.externally_committed]
        if not committed or not venue_changed:
            # 非场地变更不触碰公共服务安排，原承诺继续有效
            return public_services
        if public_services is None:
            raise DomainError("已对外承诺的公共服务安排须随场地变更重新确认，不得静默消失")
        revised = {s.service_id: s for s in public_services}
        for service in committed:
            new_plan = revised.get(service.service_id)
            if new_plan is None:
                raise DomainError(
                    f"已对外承诺的公共服务 {service.service_id}（{service.description}）在变更中消失，必须保留或给出替代安排"
                )
            if venue_changed and not new_plan.replacement.strip():
                raise DomainError(
                    f"场地变化后须为已承诺公共服务 {service.service_id} 明确替代时段或场地"
                )
            if not new_plan.externally_committed:
                raise DomainError(f"公共服务 {service.service_id} 的对外承诺属性不得撤销")
            review.protect_public_service(
                service_id=service.service_id,
                replacement=new_plan.replacement or "维持原安排",
                change_no=change_no,
                note=protection_notes.get(service.service_id, "随实质变更重新核验并登记持续保障"),
                event_id=self.new_id(),
                occurred_at=self.now(),
            )
        return public_services

    def _refresh_conflicts_after_change(self, case_id: str) -> None:
        """变更后与全量检测对账：本申办与关联方流中的旧冲突都会自动关闭。"""
        self._reconcile_conflicts()
