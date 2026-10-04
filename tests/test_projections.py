"""三类角色视图：申请方、管理人员（年度组合峰值压力）、审计回放。"""
from __future__ import annotations

import unittest

from src.catalog import Category, ChangeAspect, Decision
from src.objects import Occupancy, PublicServicePlan
from src.projections import build_applicant_view, build_audit_view, build_management_view
from tests._helpers import (
    build_approved_case,
    default_finance,
    default_occupancies,
    dt,
    new_service,
    sample_forecast,
    window,
)
from tests.test_changes import close_change_round, committed_morning_slot, sign_round
from src.catalog import CHANGE_PHASES


class ApplicantViewTest(unittest.TestCase):
    def test_sees_supplement_reasons_and_decision_versions(self) -> None:
        svc, _ = new_service()
        build_approved_case(svc, "AV", dt(2026, 5, 20))
        rid = svc.request_supplement(
            "AV",
            missing=["志愿者保险凭证"],
            reason="保险额度与峰值人群不匹配",
            requested_by="公安局",
        )
        svc.submit_evidence(
            "AV",
            request_id=rid,
            items=[{"item": "志愿者保险凭证", "doc_ref": "DOC-INS-9"}],
        )
        view = build_applicant_view(svc.repo, "AV").to_dict()
        self.assertEqual(view["status_cn"], "已决定")
        reasons = [r["reason"] for r in view["supplement_requests"]]
        self.assertIn("保险额度与峰值人群不匹配", reasons)
        self.assertEqual(view["decision_versions"][0]["decision_cn"], "批准")
        # 预测材料显式标注仅供参考
        self.assertTrue(all(f["advisory_only"] for f in view["advisory_forecasts"]))
        # 申请方时间线不出现其他申办信息与内部讨论
        timeline_text = str(view["timeline"])
        self.assertNotIn("RECUSAL", timeline_text)

    def test_cannot_see_other_cases(self) -> None:
        svc, _ = new_service()
        build_approved_case(svc, "ONE", dt(2026, 5, 21))
        build_approved_case(svc, "TWO", dt(2026, 5, 22))
        view = build_applicant_view(svc.repo, "ONE").to_dict()
        blob = str(view)
        self.assertNotIn("TWO", blob)


class ManagementViewTest(unittest.TestCase):
    def test_peak_pressure_and_fiscal_aggregation(self) -> None:
        svc, _ = new_service()
        day = dt(2026, 9, 20, 8)
        # 三场赛事同日：警力 200+200+150=550 > 容量 500；两场共用同一场馆不同时段不互斥则无冲突
        build_approved_case(
            svc,
            "M1",
            day,
            occupancies=[
                Occupancy("公安安保一组", "police_capacity", window(day), load=200, capacity=500),
            ],
            finance=default_finance(400),
        )
        build_approved_case(
            svc,
            "M2",
            day,
            occupancies=[
                Occupancy("公安安保一组", "police_capacity", window(day), load=200, capacity=500),
            ],
            finance=default_finance(400),
        )
        build_approved_case(
            svc,
            "M3",
            day,
            occupancies=[
                Occupancy("公安安保一组", "police_capacity", window(day), load=150, capacity=500),
            ],
            finance=default_finance(300),
        )
        # 批准前冲突必须处置；三场均批准说明各自冲突已被人工处置（追加运力的行政决定），
        # 但年度峰值压力视图仍如实呈现 550/500 的超载峰值，供管理人员复核。
        view = build_management_view(svc.repo, 2026).to_dict()
        police = [r for r in view["peak_pressure"]["by_resource"] if r["resource_id"] == "公安安保一组"]
        self.assertEqual(len(police), 1)
        self.assertEqual(police[0]["peak_load"], 550)
        self.assertGreater(police[0]["utilization"], 1)
        self.assertIn("公安安保一组", view["peak_pressure"]["overloaded"])
        self.assertEqual(view["fiscal"]["total_commitment_wan"], 1100)
        self.assertEqual(len(view["included_cases"]), 3)
        # 预测材料只作为参考附在管理视图中
        self.assertIn("disclaimer", view["advisory_forecasts"])

    def test_only_approved_cases_count(self) -> None:
        svc, _ = new_service()
        build_approved_case(svc, "IN1", dt(2026, 6, 1))
        view = build_management_view(svc.repo, 2026).to_dict()
        self.assertEqual([c["case_id"] for c in view["included_cases"]], ["IN1"])


class AuditViewTest(unittest.TestCase):
    def test_full_replay_includes_materials_signoffs_conditions_and_changes(self) -> None:
        svc, _ = new_service()
        day = dt(2026, 10, 9, 8)
        build_approved_case(
            svc,
            "AUD",
            day,
            committed_services=[committed_morning_slot()],
        )
        # 场地实质变更并完成重审
        new_day = dt(2026, 11, 7, 8)
        svc.declare_post_decision_change(
            "AUD",
            aspects=[ChangeAspect.VENUE],
            justification="让档国际赛事",
            occupancies=default_occupancies(new_day, venue="城南基地"),
            public_services=[
                PublicServicePlan(
                    service_id="PS-MORNING",
                    description="主馆工作日晨练时段",
                    window=window(dt(2026, 11, 7, 6), 2),
                    replacement="城南基地田径场等量开放",
                    externally_committed=True,
                )
            ],
            legacy_plan="改至城南基地后，赛后 30 天面向城南片区学校与社区开放",
        )
        affected = sorted({p for a in (ChangeAspect.VENUE,) for p in CHANGE_PHASES[a]})
        members = sign_round(svc, "AUD", affected)
        close_change_round(svc, "AUD", affected, members)

        view = build_audit_view(svc.repo, "AUD")
        # 决定有两个版本
        versions = [d["version"] for d in view["replay"]["decision_versions"]]
        self.assertEqual(versions, [1, 2])
        # 重开轮次可回放
        self.assertEqual(len(view["replay"]["reopened_rounds"]), 1)
        self.assertIn("场馆档期", view["replay"]["reopened_rounds"][0]["phases"])
        self.assertNotIn("财政承诺", view["replay"]["reopened_rounds"][0]["phases"])
        # 原始信封完整保留且可通过信封校验
        from src.envelopes import validate_envelope

        for raw in view["replay"]["raw_envelopes"]:
            self.assertEqual(validate_envelope(raw), [])
        # 公共服务保护与时间线均可查
        protections = view["current_state"]["public_service_protections"]
        self.assertEqual(protections[0]["replacement"], "城南基地田径场等量开放")
        actions = [t.get("action", "") for t in view["replay"]["timeline"]]
        self.assertTrue(any("只重开受影响环节" in a for a in actions))
        self.assertTrue(any("变更重审完结" in a for a in actions))


if __name__ == "__main__":
    unittest.main()
