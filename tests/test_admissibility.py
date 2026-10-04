"""分类准入证据、补正流程与预测材料的参考性约束。"""
from __future__ import annotations

import unittest

from src.catalog import Category, Decision
from src.objects import Forecast
from src.service import DomainError
from tests._helpers import (
    approve,
    build_approved_case,
    declare_default_resources,
    dt,
    evidence_for,
    file_case,
    new_service,
    resolve_all_conflicts,
    sign_all_baseline,
    submit_baseline_evidence,
)


class AdmissibilityTest(unittest.TestCase):
    def test_each_category_uses_own_evidence_set(self) -> None:
        svc, _ = new_service()
        file_case(svc, "C-MASS", category=Category.MASS)
        file_case(svc, "C-PRO", category=Category.PROFESSIONAL)
        file_case(svc, "C-COMP", category=Category.COMPETITIVE)
        # 竞技证据不能满足群众赛事
        svc.submit_evidence("C-MASS", items=evidence_for(Category.COMPETITIVE))
        declare_default_resources(svc, "C-MASS", dt(2026, 5, 1))
        members = sign_all_baseline(svc, "C-MASS")
        with self.assertRaisesRegex(DomainError, "准入证据不齐"):
            approve(svc, "C-MASS", members=members)

    def test_international_requires_nature_and_extra_evidence(self) -> None:
        svc, _ = new_service()
        with self.assertRaisesRegex(ValueError, "项目性质"):
            file_case(svc, "C-INT", category=Category.INTERNATIONAL)
        file_case(svc, "C-INT", category=Category.INTERNATIONAL, nature=Category.COMPETITIVE)
        # 只交竞技证据、缺涉外材料仍不齐
        svc.submit_evidence("C-INT", items=evidence_for(Category.COMPETITIVE))
        declare_default_resources(svc, "C-INT", dt(2026, 6, 1))
        members = sign_all_baseline(svc, "C-INT")
        with self.assertRaisesRegex(DomainError, "国际组织授权"):
            approve(svc, "C-INT", members=members)

    def test_supplement_request_blocks_then_unblocks_vote(self) -> None:
        svc, _ = new_service()
        file_case(svc, "C-SUP")
        request_id = svc.request_supplement(
            "C-SUP",
            missing=["活动安全许可前置材料"],
            reason="路线安保等级证明缺失，无法进入安全会签",
            requested_by="公安局",
        )
        declare_default_resources(svc, "C-SUP", dt(2026, 5, 2))
        members = sign_all_baseline(svc, "C-SUP")
        with self.assertRaisesRegex(DomainError, "补正尚未完成"):
            approve(svc, "C-SUP", members=members)
        # 补正答复对应通知编号
        svc.submit_evidence(
            "C-SUP",
            request_id=request_id,
            items=[{"item": "活动安全许可前置材料", "doc_ref": "DOC-SUP-1"}],
        )
        # 其余群众赛事证据仍需补齐
        with self.assertRaisesRegex(DomainError, "准入证据不齐"):
            approve(svc, "C-SUP", members=members)
        submit_baseline_evidence(svc, "C-SUP", Category.MASS)
        svc.run_conflict_check()
        resolve_all_conflicts(svc, "C-SUP")
        approve(svc, "C-SUP", members=members)

    def test_forecast_must_carry_methodology_and_interval(self) -> None:
        with self.assertRaisesRegex(ValueError, "计算口径"):
            Forecast(
                metric="消费",
                point_estimate=1,
                confidence_low=0,
                confidence_high=2,
                methodology="  ",
            )
        with self.assertRaisesRegex(ValueError, "置信范围"):
            Forecast(
                metric="消费",
                point_estimate=9,
                confidence_low=0,
                confidence_high=2,
                methodology="口径说明",
            )

    def test_forecast_never_substitutes_for_evidence_or_decision(self) -> None:
        svc, _ = new_service()
        file_case(svc, "C-FC")
        # 只交预测，不交任何准入证据
        from tests._helpers import sample_forecast

        svc.attach_forecast("C-FC", sample_forecast())
        declare_default_resources(svc, "C-FC", dt(2026, 5, 3))
        members = sign_all_baseline(svc, "C-FC")
        with self.assertRaisesRegex(DomainError, "准入证据不齐"):
            approve(svc, "C-FC", members=members)

    def test_amend_after_decision_rejected(self) -> None:
        svc, _ = new_service()
        build_approved_case(svc, "C-A", dt(2026, 5, 4))
        with self.assertRaisesRegex(DomainError, "实质变化须申报变更"):
            svc.amend_application(
                "C-A", changes={"expected_crowd": None}, reason="想改", changed_sections=["crowd"]
            )


if __name__ == "__main__":
    unittest.main()
