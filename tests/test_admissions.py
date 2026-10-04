"""分类准入证据与补件流程。"""

import unittest

from src.models import DomainError
from tests.helpers import make_draft, make_sections, make_service


class AdmissionTest(unittest.TestCase):
    def test_mass_event_missing_evidence_goes_to_supplement(self) -> None:
        service = make_service()
        draft = make_draft(evidence={"operator_license": "doc-1"})  # 缺群众赛事专属证据
        result = service.file_application(draft)
        self.assertEqual(result["status"], "supplement_required")
        self.assertIn("大型群众性活动安全许可", result["missing_evidence"])

        view = service.applicant_view(result["application_id"])
        self.assertEqual(len(view["supplement_requests"]), 1)
        self.assertIn("医疗保障方案", view["supplement_requests"][0]["missing"])

    def test_each_category_has_own_evidence(self) -> None:
        service = make_service()
        # 职业赛事只交群众赛事的证据，仍缺 league_authorization 等
        draft = make_draft(category="professional")
        result = service.file_application(draft)
        self.assertEqual(result["status"], "supplement_required")
        self.assertIn("联赛/联盟授权文件", result["missing_evidence"])
        # 竞技赛事缺反兴奋剂方案
        result2 = service.file_application(make_draft(name="竞技测试赛", category="competitive"))
        self.assertIn("反兴奋剂方案", result2["missing_evidence"])

    def test_supplement_then_in_review(self) -> None:
        service = make_service()
        result = service.file_application(make_draft(evidence={"operator_license": "doc-1"}))
        app_id = result["application_id"]
        again = service.submit_version(app_id, make_sections())  # 证据齐全
        self.assertEqual(again["status"], "in_review")
        self.assertEqual(again["current_version"], 2)
        event_types = [e["event_type"] for e in service.store.query(correlation_id=app_id)]
        self.assertIn("SUPPLEMENT_REQUESTED", event_types)
        self.assertIn("SUPPLEMENT_PROVIDED", event_types)

    def test_forecast_must_carry_methodology_and_confidence_range(self) -> None:
        service = make_service()
        # 缺口径
        bad = make_sections(benefit_forecast={"amount_low": 1, "amount_high": 2, "confidence": 0.9})
        with self.assertRaises(DomainError) as ctx:
            service.file_application(make_draft(**bad))
        self.assertIn("口径", str(ctx.exception))
        # 区间倒置
        bad2 = make_sections(
            benefit_forecast={
                "amount_low": 10,
                "amount_high": 2,
                "confidence": 0.9,
                "methodology": "直接消费口径",
            }
        )
        with self.assertRaises(DomainError):
            service.file_application(make_draft(**bad2))

    def test_missing_sections_rejected(self) -> None:
        service = make_service()
        draft = make_draft()
        del draft["safety_plan"]
        with self.assertRaises(DomainError) as ctx:
            service.file_application(draft)
        self.assertIn("safety_plan", ctx.exception.details["missing_sections"])


if __name__ == "__main__":
    unittest.main()
