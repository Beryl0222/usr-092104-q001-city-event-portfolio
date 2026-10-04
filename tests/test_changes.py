"""批准后实质变更：只重开受影响环节、公共服务不得静默消失、新版决定与条件延续。"""
from __future__ import annotations

import unittest

from src.catalog import CHANGE_PHASES, ChangeAspect, Decision, Phase
from src.objects import FinancialCommitment, Occupancy, PublicServicePlan
from src.service import DomainError
from tests._helpers import (
    approve,
    build_approved_case,
    default_finance,
    default_occupancies,
    dt,
    new_service,
    resolve_all_conflicts,
    window,
)


def committed_morning_slot() -> PublicServicePlan:
    return PublicServicePlan(
        service_id="PS-MORNING",
        description="主馆工作日 6:00-8:00 市民晨练时段",
        window=window(dt(2026, 10, 18, 6), 2),
        replacement="",
        externally_committed=True,
    )


def sign_round(svc, case_id, phases):
    """按受影响环节所需部门重新会签一轮。"""
    from src.service import PHASE_DEPARTMENTS

    departments = {PHASE_DEPARTMENTS[p] for p in phases if p in PHASE_DEPARTMENTS}
    deputies = {
        "体育局": ("王局长", "局长"),
        "场馆管理单位": ("李场长", "场长"),
        "人员保障部门": ("赵主任", "主任"),
        "公安交管局": ("钱支队", "支队长"),
        "公安局": ("孙局长", "副局长"),
        "财政局": ("周处长", "处长"),
        "商务部门": ("吴处长", "处长"),
    }
    members = []
    for dep in departments:
        name, pos = deputies[dep]
        svc.sign_opinion(case_id, member=name, department=dep, position=pos)
        members.append(name)
    return members


def close_change_round(svc, case_id, phases, members, year=2026):
    conclusions = {p: f"{p} 环节复核通过" for p in phases}
    svc.resolve_reopened_round(case_id, conclusions=conclusions)
    approve(svc, case_id, year=year, members=members, quorum=3)


class PostDecisionChangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc, _ = new_service()
        self.day = dt(2026, 10, 18, 8)
        build_approved_case(
            self.svc,
            "MAR",
            self.day,
            committed_services=[committed_morning_slot()],
        )

    def test_venue_change_reopens_only_affected_phases(self) -> None:
        review = self.svc.repo.load_review("MAR")
        affected = sorted({p for a in (ChangeAspect.VENUE,) for p in CHANGE_PHASES[a]})

        new_day = dt(2026, 11, 2, 8)
        self.svc.declare_post_decision_change(
            "MAR",
            aspects=[ChangeAspect.VENUE],
            justification="因引进国际田径赛，主馆让档，改至城东体育场",
            occupancies=default_occupancies(new_day, venue="城东体育场"),
            public_services=[
                PublicServicePlan(
                    service_id="PS-MORNING",
                    description="主馆工作日 6:00-8:00 市民晨练时段",
                    window=window(dt(2026, 11, 2, 6), 2),
                    replacement="城东体育场副场 6:00-8:00 等量开放",
                    externally_committed=True,
                )
            ],
            legacy_plan="改至城东体育场后，赛后 30 天面向城东社区开放并提供公益课时",
        )
        review = self.svc.repo.load_review("MAR")
        self.assertEqual([r["phases"] for r in review.rounds], [affected])
        # 未受影响的环节（财政、商业）不在重开列表
        self.assertNotIn(Phase.FINANCE, affected)
        self.assertNotIn(Phase.COMMERCIAL, affected)
        # 保护登记已生成
        self.assertEqual(len(review.protections), 1)
        self.assertIn("城东体育场副场", review.protections[0]["replacement"])

        members = sign_round(self.svc, "MAR", affected)
        close_change_round(self.svc, "MAR", affected, members)

        portfolio = self.svc.repo.load_portfolio(2026)
        self.assertEqual(portfolio.decision_for("MAR")["document_version"], 2)
        review = self.svc.repo.load_review("MAR")
        self.assertFalse(review.round_open)
        self.assertEqual(self.svc.repo.load_application("MAR").changes[0]["status"], "closed")

    def test_committed_service_cannot_silently_disappear(self) -> None:
        new_day = dt(2026, 11, 3, 8)
        legacy = "赛后开放计划不变，随新场地落实"
        # 完全不提交公共服务安排
        with self.assertRaisesRegex(DomainError, "公共服务安排须随场地变更重新确认"):
            self.svc.declare_post_decision_change(
                "MAR",
                aspects=[ChangeAspect.VENUE],
                justification="改场",
                occupancies=default_occupancies(new_day, venue="城北馆"),
                legacy_plan=legacy,
            )
        # 提交了清单但漏掉已承诺服务
        with self.assertRaisesRegex(DomainError, "在变更中消失"):
            self.svc.declare_post_decision_change(
                "MAR",
                aspects=[ChangeAspect.VENUE],
                justification="改场",
                occupancies=default_occupancies(new_day, venue="城北馆"),
                legacy_plan=legacy,
                public_services=[
                    PublicServicePlan(
                        service_id="PS-OTHER",
                        description="其他服务",
                        replacement="x",
                        externally_committed=True,
                    )
                ],
            )
        # 保留了服务但场地变了却不给替代安排
        with self.assertRaisesRegex(DomainError, "明确替代时段或场地"):
            self.svc.declare_post_decision_change(
                "MAR",
                aspects=[ChangeAspect.VENUE],
                justification="改场",
                occupancies=default_occupancies(new_day, venue="城北馆"),
                legacy_plan=legacy,
                public_services=[
                    PublicServicePlan(
                        service_id="PS-MORNING",
                        description="晨练",
                        replacement="  ",
                        externally_committed=True,
                    )
                ],
            )

    def test_funding_change_does_not_touch_public_service_but_requires_finance(self) -> None:
        affected = sorted({p for a in (ChangeAspect.FUNDING_SOURCE,) for p in CHANGE_PHASES[a]})
        # 未重新提交财政承诺即被拒
        with self.assertRaisesRegex(DomainError, "财政承诺受影响"):
            self.svc.declare_post_decision_change(
                "MAR",
                aspects=[ChangeAspect.FUNDING_SOURCE],
                justification="拟引入社会资本替代部分财政资金",
                occupancies=default_occupancies(self.day),
            )
        self.svc.declare_post_decision_change(
            "MAR",
            aspects=[ChangeAspect.FUNDING_SOURCE],
            justification="拟引入社会资本替代部分财政资金",
            occupancies=default_occupancies(self.day),
            finance=FinancialCommitment(
                fiscal_amount_wan=100,
                nonfiscal_amount_wan=700,
                funding_source="社会资本主办+财政购买公共服务",
            ),
            commercial_rights={"title_sponsor": "新主赞助商", "exclusive_scope": "冠名+场地广告"},
        )
        review = self.svc.repo.load_review("MAR")
        self.assertEqual([r["phases"] for r in review.rounds], [affected])
        # 非场地变更不生成公共服务保护动作，原承诺在资源状态中继续存在
        self.assertEqual(review.protections, [])
        res = self.svc.repo.load_resources("MAR")
        self.assertIn("PS-MORNING", res.public_services)

        members = sign_round(self.svc, "MAR", affected)
        close_change_round(self.svc, "MAR", affected, members)
        self.assertEqual(self.svc.repo.load_resources("MAR").finance.fiscal_amount_wan, 100)

    def test_scale_change_triggers_new_conflict_and_blocks_revote(self) -> None:
        # 先批准另一场赛事占用同一运力
        other_day = dt(2026, 10, 25, 8)
        build_approved_case(
            self.svc,
            "BIG",
            other_day,
            occupancies=[
                Occupancy("公安安保一组", "police_capacity", window(other_day), load=450, capacity=500),
            ],
        )
        # MAR 规模扩大到与 BIG 同日且警力需求 300（450+300>500）
        big_day = dt(2026, 10, 25, 8)
        self.svc.declare_post_decision_change(
            "MAR",
            aspects=[ChangeAspect.SCALE],
            justification="报名超额，预计峰值由 1.2 万升至 2.6 万",
            occupancies=default_occupancies(big_day, police_load=300),
            finance=default_finance(700),
        )
        res = self.svc.repo.load_resources("MAR")
        blocking = [c for c in res.conflicts if c["status"] == "open" and c["severity"] == "block"]
        self.assertTrue(blocking)
        affected = sorted({p for a in (ChangeAspect.SCALE,) for p in CHANGE_PHASES[a]})
        members = sign_round(self.svc, "MAR", affected)
        self.svc.resolve_reopened_round("MAR", conclusions={p: "复核" for p in affected})
        with self.assertRaisesRegex(DomainError, "资源时间重叠未处置"):
            approve(self.svc, "MAR", year=2026, members=members, quorum=3)
        # 协调追加运力后处置冲突，方可重新表决
        resolve_all_conflicts(self.svc, "MAR")
        resolve_all_conflicts(self.svc, "BIG")
        approve(self.svc, "MAR", year=2026, members=members, quorum=3)
        self.assertEqual(self.svc.repo.load_portfolio(2026).decision_for("MAR")["document_version"], 2)


if __name__ == "__main__":
    unittest.main()
