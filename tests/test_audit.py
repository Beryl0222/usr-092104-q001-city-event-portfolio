"""审计回放：从一项赛事还原材料、会签、条件性批准与变更的完整过程。"""

import unittest

from src.audit import build_audit_trail
from tests.helpers import approve, file_complete, make_sections, make_service


class AuditTrailTest(unittest.TestCase):
    def _full_lifecycle(self):
        service = make_service()
        app_id = file_complete(service, name="审计样例赛事")
        # 回避 + 会签（含一条少数意见）+ 条件性批准
        service.declare_recusal(app_id, "张某", "亲属任职申请方", stage="commercial")
        from tests.helpers import REQUIRED_STAGES

        for index, stage in enumerate(REQUIRED_STAGES):
            service.sign_opinion(app_id, stage.value, f"部门{index}", f"签署人{index}", "concur")
        # 财政局另签一条反对意见，作为少数意见保留
        service.sign_opinion(app_id, "fiscal", "财政局", "李某", "dissent", comment="补贴偏高")
        service.issue_decision(
            app_id,
            "conditionally_approved",
            "市体育局",
            "附条件可行",
            conditions=[{"description": "提交安保细化方案"}],
        )
        # 实质变更：规模扩大 → 重开三个环节 → 第二版决定
        bigger = make_sections(
            expected_crowd={"participants": 20000, "spectators_peak": 50000, "staff": 3000}
        )
        change = service.request_change(app_id, "scale", "规模扩大", bigger)
        for stage in change["affected_stages"]:
            service.sign_opinion(app_id, stage, "复核部门", "复核人", "concur")
        service.close_change(app_id, change["change_id"], "市体育局", "复核通过")
        return service, app_id

    def test_replay_covers_materials_signoff_conditional_approval_and_change(self) -> None:
        service, app_id = self._full_lifecycle()
        trail = build_audit_trail(service, app_id)
        types = [e["event_type"] for e in trail["events"]]
        # 关键节点齐全且有序
        self.assertLess(types.index("APPLICATION_FILED"), types.index("OPINION_SIGNED"))
        self.assertLess(types.index("OPINION_SIGNED"), types.index("DECISION_ISSUED"))
        self.assertLess(types.index("DECISION_ISSUED"), types.index("CHANGE_REQUESTED"))
        self.assertLess(types.index("CHANGE_REQUESTED"), types.index("CHANGE_REOPENED"))
        self.assertEqual(types.count("DECISION_ISSUED"), 2)  # 条件性批准 + 变更后新版决定
        self.assertIn("RECUSAL_DECLARED", types)
        # 少数意见留在第二版决定里仍可见（第一版决定的 payload）
        first_decision = [e for e in trail["events"] if e["event_type"] == "DECISION_ISSUED"][0]
        self.assertEqual(first_decision["payload"]["outcome"], "conditionally_approved")
        self.assertEqual(first_decision["payload"]["minority_opinions"][0]["department"], "财政局")

    def test_timeline_is_chinese_and_ordered(self) -> None:
        service, app_id = self._full_lifecycle()
        trail = build_audit_trail(service, app_id)
        self.assertEqual(trail["event_count"], len(trail["timeline"]))
        occurred = [item["occurred_at"] for item in trail["timeline"]]
        self.assertEqual(occurred, sorted(occurred))
        text = "\n".join(item["description"] for item in trail["timeline"])
        for keyword in ("提交申办材料", "会签", "条件性批准", "变更", "重开环节", "回避登记"):
            self.assertIn(keyword, text)

    def test_events_from_other_applications_not_mixed(self) -> None:
        service, app_id = self._full_lifecycle()
        other_sections = make_sections(
            resource_requests=[
                {
                    "resource_id": "venue-east-gym",
                    "kind": "venue",
                    "start": "2026-12-01T06:00:00+08:00",
                    "end": "2026-12-01T14:00:00+08:00",
                    "quantity": 1,
                }
            ]
        )
        other = file_complete(service, name="另一项赛事", **other_sections)
        approve(service, other)
        trail = build_audit_trail(service, app_id)
        self.assertTrue(
            all(e["correlation_id"] == app_id for e in trail["events"])
        )


if __name__ == "__main__":
    unittest.main()
