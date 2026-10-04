"""会签、回避、少数意见、条件性批准与"预测收益只是材料"的约束。"""

import unittest

from src.models import DomainError, REQUIRED_STAGES
from tests.helpers import approve, file_complete, make_service, sign_all


class DecisionTest(unittest.TestCase):
    def test_full_flow_approves_and_activates_commitments(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        result = approve(service, app_id)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(len(result["commitments"]), 2)
        self.assertTrue(all(c["status"] == "active" for c in result["commitments"]))
        self.assertEqual(len(result["public_services"]), 1)
        types = [e["event_type"] for e in service.store.query(correlation_id=app_id)]
        for expected in ("APPLICATION_FILED", "OPINION_SIGNED", "DECISION_ISSUED", "RESOURCE_RESERVED", "PUBLIC_SERVICE_COMMITTED"):
            self.assertIn(expected, types)

    def test_decision_requires_all_stages_signed(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        with self.assertRaises(DomainError) as ctx:
            service.issue_decision(app_id, "approved", "市体育局", "理由")
        self.assertEqual(len(ctx.exception.details["incomplete_stages"]), len(REQUIRED_STAGES))

    def test_decision_must_come_from_authorized_department(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        sign_all(service, app_id)
        for decider in ("", "system", "系统自动"):
            with self.assertRaises(DomainError, msg=decider):
                service.issue_decision(app_id, "approved", decider, "理由")
        with self.assertRaises(DomainError):
            service.issue_decision(app_id, "approved", "市体育局", "  ")

    def test_recusal_blocks_signing_and_is_recorded(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        service.declare_recusal(app_id, "张某", "其配偶为申请方高管", stage="commercial")
        with self.assertRaises(DomainError) as ctx:
            service.sign_opinion(app_id, "commercial", "商务局", "张某", "concur")
        self.assertIn("回避", str(ctx.exception))
        # 其他环节不受该回避影响
        service.sign_opinion(app_id, "safety", "公安局", "张某", "concur")
        archive = service.decision_file(app_id)
        self.assertEqual(archive["recusals"][0]["person"], "张某")

    def test_minority_opinion_preserved_in_decision(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        sign_all(service, app_id)
        service.sign_opinion(app_id, "fiscal", "财政局", "李某", "dissent", comment="补贴超出年度预算承受力")
        result = service.issue_decision(app_id, "approved", "市体育局", "综合考量后批准")
        minority = result["decisions"][0]["minority_opinions"]
        self.assertEqual(len(minority), 1)
        self.assertEqual(minority[0]["department"], "财政局")
        # 少数意见随 DECISION_ISSUED 事件一并留痕
        event = [e for e in service.store.all() if e["event_type"] == "DECISION_ISSUED"][0]
        self.assertEqual(event["payload"]["minority_opinions"][0]["signer"], "李某")

    def test_conditional_approval_and_fulfillment(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        sign_all(service, app_id)
        with self.assertRaises(DomainError):
            service.issue_decision(app_id, "conditionally_approved", "市体育局", "理由")  # 无条件清单
        result = service.issue_decision(
            app_id,
            "conditionally_approved",
            "市体育局",
            "压缩规模后可行",
            conditions=[{"description": "赛前 30 日提交细化安保方案"}],
        )
        self.assertEqual(result["status"], "conditionally_approved")
        condition_id = result["decisions"][0]["conditions"][0]["id"]
        done = service.fulfill_condition(app_id, condition_id, "方案已备案")
        self.assertTrue(done["all_conditions_fulfilled"])
        self.assertEqual(service.decision_file(app_id)["status"], "approved")

    def test_duplicate_signature_rejected(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        service.sign_opinion(app_id, "safety", "公安局", "王某", "concur")
        with self.assertRaises(DomainError):
            service.sign_opinion(app_id, "safety", "公安局", "王某", "concur")

    def test_forecast_kept_as_material_snapshot(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        result = approve(service, app_id)
        snapshot = result["decisions"][0]["forecast_snapshot"]
        self.assertEqual(snapshot["methodology"], "住宿、餐饮、交通直接消费口径，不含间接拉动")
        self.assertEqual(snapshot["confidence"], 0.8)


if __name__ == "__main__":
    unittest.main()
