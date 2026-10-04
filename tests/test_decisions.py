"""会签表决规则：回避、法定人数、票数、少数意见保留、决定版本与条件性批准。"""
from __future__ import annotations

import unittest

from src.catalog import Decision
from src.service import DomainError
from tests._helpers import (
    BASELINE_SIGNERS,
    approve,
    build_approved_case,
    dt,
    new_service,
    sign_all_baseline,
)


class DecisionTest(unittest.TestCase):
    def test_recused_member_cannot_sign_or_vote(self) -> None:
        svc, _ = new_service()
        from tests._helpers import file_case, submit_baseline_evidence, declare_default_resources
        from tests._helpers import resolve_all_conflicts

        file_case(svc, "RC2")
        submit_baseline_evidence(svc, "RC2", "mass")
        declare_default_resources(svc, "RC2", dt(2026, 5, 11))
        svc.run_conflict_check()
        resolve_all_conflicts(svc, "RC2")
        svc.declare_recusal("RC2", member="周五", reason="其配偶为承办单位股东")
        with self.assertRaisesRegex(ValueError, "已回避"):
            svc.sign_opinion("RC2", member="周五", department="公安局")
        # 公安局由不具回避关系的他人代会签
        for m, dep, pos in BASELINE_SIGNERS:
            if m != "周五":
                svc.sign_opinion("RC2", member=m, department=dep, position=pos)
        svc.sign_opinion("RC2", member="陈八", department="公安局", position="安保处长")
        members = [m for m, _, _ in BASELINE_SIGNERS if m != "周五"] + ["陈八"]
        # 回避者若混入表决仍被拒
        with self.assertRaisesRegex(DomainError, "回避成员不得参与表决"):
            svc.issue_decision(
                2026,
                "RC2",
                decision=Decision.APPROVED,
                vote={"赞成": len(members) + 1},
                voting_members=members + ["周五"],
                quorum=5,
            )
        approve(svc, "RC2", members=members, quorum=5)

    def test_quorum_enforced(self) -> None:
        svc, _ = new_service()
        build_approved_case(svc, "Q0", dt(2026, 5, 12))
        from tests._helpers import file_case, submit_baseline_evidence, declare_default_resources
        from tests._helpers import resolve_all_conflicts

        file_case(svc, "Q1")
        submit_baseline_evidence(svc, "Q1", "mass")
        declare_default_resources(svc, "Q1", dt(2026, 5, 13))
        svc.run_conflict_check()
        resolve_all_conflicts(svc, "Q1")
        members = sign_all_baseline(svc, "Q1")
        # 会签齐备，但出席人数达不到更高的法定人数
        with self.assertRaisesRegex(DomainError, "法定人数"):
            approve(svc, "Q1", members=members, quorum=10)

    def test_dissent_is_retained_but_does_not_block(self) -> None:
        svc, _ = new_service()
        from tests._helpers import (
            file_case,
            submit_baseline_evidence,
            declare_default_resources,
            resolve_all_conflicts,
        )

        file_case(svc, "DS")
        submit_baseline_evidence(svc, "DS", "mass")
        declare_default_resources(svc, "DS", dt(2026, 5, 14))
        svc.run_conflict_check()
        resolve_all_conflicts(svc, "DS")
        members = sign_all_baseline(svc, "DS")
        # 少数意见在表决前保留，不阻断多数通过
        svc.record_dissent("DS", member="吴六", opinion="财政承诺占季度盘子比例偏高，建议压减")
        approve(svc, "DS", members=members, note="多数同意批准，保留少数意见")
        review = svc.repo.load_review("DS")
        self.assertEqual(len(review.dissents), 1)
        self.assertEqual(review.dissents[0]["member"], "吴六")
        self.assertEqual(review.dissents[0]["opinion"], "财政承诺占季度盘子比例偏高，建议压减")

    def test_conditional_approval_requires_conditions(self) -> None:
        svc, _ = new_service()
        members = build_approved_case(svc, "CA", dt(2026, 5, 15))
        with self.assertRaisesRegex(ValueError, "条件性批准必须附带条件"):
            svc.issue_decision(
                2026,
                "CA",
                decision=Decision.CONDITIONAL_APPROVAL,
                vote={"赞成": len(members)},
                voting_members=members,
                quorum=5,
            )

    def test_decision_versions_accumulate(self) -> None:
        svc, _ = new_service()
        members = build_approved_case(svc, "DV", dt(2026, 5, 16))
        portfolio = svc.repo.load_portfolio(2026)
        self.assertEqual(portfolio.decision_for("DV")["document_version"], 1)

    def test_missing_signoff_department_blocks(self) -> None:
        svc, _ = new_service()
        from tests._helpers import file_case, submit_baseline_evidence, declare_default_resources
        from tests._helpers import resolve_all_conflicts

        file_case(svc, "SG")
        submit_baseline_evidence(svc, "SG", "mass")
        declare_default_resources(svc, "SG", dt(2026, 5, 17))
        svc.run_conflict_check()
        resolve_all_conflicts(svc, "SG")
        # 只会签体育局，缺财政、公安等
        svc.sign_opinion("SG", member="赵一", department="体育局", position="局长")
        with self.assertRaisesRegex(DomainError, "会签部门不齐"):
            approve(svc, "SG", members=["赵一"], quorum=1)


if __name__ == "__main__":
    unittest.main()
