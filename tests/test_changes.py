"""批准后的变更：只重开受影响环节，公共服务不得静默消失。"""

import unittest

from src.models import DomainError
from tests.helpers import approve, file_complete, make_sections, make_service


def _scaled_sections(factor: float) -> dict:
    crowd = {"participants": int(8000 * factor), "spectators_peak": int(20000 * factor), "staff": 1500}
    return make_sections(expected_crowd=crowd)


class ChangeTest(unittest.TestCase):
    def test_minor_scale_change_files_without_reopen(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        result = service.request_change(app_id, "scale", "报名略增", _scaled_sections(1.1))
        self.assertFalse(result["substantial"])
        self.assertEqual(result["status"], "closed")
        archive = service.decision_file(app_id)
        self.assertEqual(archive["status"], "approved")
        self.assertEqual(archive["current_version"], 2)

    def test_substantial_scale_change_reopens_only_affected_stages(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        result = service.request_change(app_id, "scale", "规模扩大一倍", _scaled_sections(2.0))
        self.assertTrue(result["substantial"])
        self.assertEqual(
            set(result["affected_stages"]), {"resource", "safety", "transport"}
        )
        # 未受影响环节不能重复会签，旧会签继续有效
        with self.assertRaises(DomainError):
            service.sign_opinion(app_id, "fiscal", "财政局", "赵某", "concur")
        # 受影响环节未重签前不能收口
        with self.assertRaises(DomainError) as ctx:
            service.close_change(app_id, result["change_id"], "市体育局", "复核通过")
        self.assertEqual(len(ctx.exception.details["incomplete_stages"]), 3)
        # 重签受影响环节后收口，产生第 2 版决定
        for stage in result["affected_stages"]:
            service.sign_opinion(app_id, stage, "复核部门", "复核人", "concur")
        closed = service.close_change(app_id, result["change_id"], "市体育局", "安保与交通方案已按新规模复核")
        self.assertEqual(closed["status"], "approved")
        self.assertEqual(closed["decisions"][-1]["decision_version"], 2)
        self.assertEqual(closed["decisions"][-1]["change_id"], result["change_id"])

    def test_venue_change_reopens_legacy_and_rebases_commitments(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        moved = make_sections(
            resource_requests=[
                {
                    "resource_id": "venue-west-arena",
                    "kind": "venue",
                    "start": "2026-11-15T06:00:00+08:00",
                    "end": "2026-11-15T14:00:00+08:00",
                    "quantity": 1,
                }
            ]
        )
        result = service.request_change(app_id, "venue", "改至西体育馆", moved)
        self.assertEqual(
            set(result["affected_stages"]), {"resource", "safety", "transport", "legacy"}
        )
        for stage in result["affected_stages"]:
            service.sign_opinion(app_id, stage, "复核部门", "复核人", "concur")
        closed = service.close_change(app_id, result["change_id"], "市体育局", "新场地踏勘通过")
        active = [c for c in closed["commitments"] if c["status"] == "active"]
        released = [c for c in closed["commitments"] if c["status"] == "released"]
        self.assertEqual([c["resource_id"] for c in active], ["venue-west-arena"])
        self.assertTrue(any(c["resource_id"] == "venue-main-stadium" for c in released))

    def test_funding_source_change_reopens_fiscal_and_commercial(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        funded = make_sections(
            fiscal_commitment={"subsidy_amount": 2000000, "currency": "CNY", "source": "社会资本全额出资"}
        )
        result = service.request_change(app_id, "funding_source", "改为社会资本出资", funded)
        self.assertTrue(result["substantial"])
        self.assertEqual(set(result["affected_stages"]), {"fiscal", "commercial"})

    def test_public_service_cannot_disappear_silently(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        dropped = make_sections(public_services=[])  # 新版本不再包含已承诺的免费开放
        with self.assertRaises(DomainError) as ctx:
            service.request_change(app_id, "scale", "微调", {**dropped, "expected_crowd": {"participants": 8100, "spectators_peak": 20000, "staff": 1500}})
        self.assertIn("静默消失", str(ctx.exception))
        self.assertEqual(ctx.exception.details["services"], ["fitness-morning"])

    def test_public_service_revocation_requires_reason_and_authority(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        psc_id = service.decision_file(app_id)["public_services"][0]["id"]
        with self.assertRaises(DomainError):
            service.revoke_public_service(psc_id, "", "市政府常务会")
        result = service.revoke_public_service(psc_id, "场馆检修无法开放", "市体育局党组会")
        self.assertEqual(result["status"], "revoked")
        event = [e for e in service.store.all() if e["event_type"] == "PUBLIC_SERVICE_REVOKED"][0]
        self.assertEqual(event["payload"]["reason"], "场馆检修无法开放")

    def test_explicit_revocation_inside_change_is_recorded(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        dropped = make_sections(public_services=[])
        result = service.request_change(
            app_id,
            "scale",
            "规模微调并调整公共服务安排",
            dropped,
            revocations=[
                {"service_id": "fitness-morning", "reason": "场地档期调整", "authority": "市体育局"}
            ],
        )
        self.assertEqual(result["status"], "closed")  # 规模未超阈值，按一般变更备案
        archive = service.decision_file(app_id)
        self.assertEqual(archive["public_services"][0]["status"], "revoked")
        types = [e["event_type"] for e in service.store.query(correlation_id=app_id)]
        self.assertIn("PUBLIC_SERVICE_REVOKED", types)

    def test_change_requires_approved_status(self) -> None:
        service = make_service()
        app_id = file_complete(service)  # 尚未批准
        with self.assertRaises(DomainError):
            service.request_change(app_id, "scale", "变更", _scaled_sections(2.0))


if __name__ == "__main__":
    unittest.main()
