"""资源时间重叠：同一资源的冲突必须在表决前显现。"""

import unittest

from src.models import DomainError
from tests.helpers import approve, file_complete, make_draft, make_sections, make_service, sign_all


def _overlap_requests(resource_id: str = "venue-main-stadium") -> list[dict]:
    return [
        {
            "resource_id": resource_id,
            "kind": "venue",
            "start": "2026-11-15T08:00:00+08:00",  # 与默认草稿 06:00-14:00 重叠
            "end": "2026-11-15T18:00:00+08:00",
            "quantity": 1,
        }
    ]


class ConflictTest(unittest.TestCase):
    def test_overlap_detected_between_pending_applications(self) -> None:
        service = make_service()
        file_complete(service, name="赛事甲")
        second = service.file_application(
            make_draft(name="赛事乙", resource_requests=_overlap_requests())
        )
        conflicts = second["open_conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["resource_id"], "venue-main-stadium")
        types = [e["event_type"] for e in service.store.all()]
        self.assertIn("RESOURCE_CONFLICTED", types)

    def test_decision_blocked_until_conflict_resolved(self) -> None:
        service = make_service()
        file_complete(service, name="赛事甲")
        second = service.file_application(
            make_draft(name="赛事乙", resource_requests=_overlap_requests())
        )
        app_b = second["application_id"]
        sign_all(service, app_b)
        with self.assertRaises(DomainError) as ctx:
            service.issue_decision(app_b, "approved", "市体育局", "布局合理")
        self.assertIn("重叠", str(ctx.exception))
        # 协调后可以表决
        conflict_id = second["open_conflicts"][0]["conflict_id"]
        service.resolve_conflict(conflict_id, "乙改至次日 06:00 前进场布置", "市赛事协调办")
        result = service.issue_decision(app_b, "approved", "市体育局", "冲突已协调")
        self.assertEqual(result["status"], "approved")

    def test_conflict_with_public_fitness_commitment(self) -> None:
        service = make_service()
        app_a = file_complete(service, name="赛事甲")
        approve(service, app_a)  # 甲的公共服务承诺占用主体育场 11-16 早场
        # 乙申请同一体育场 11-16 全天，撞上已承诺的市民健身时段
        requests = [
            {
                "resource_id": "venue-main-stadium",
                "kind": "public_fitness_slot",
                "start": "2026-11-16T05:00:00+08:00",
                "end": "2026-11-16T12:00:00+08:00",
                "quantity": 1,
            }
        ]
        second = service.file_application(make_draft(name="赛事乙", resource_requests=requests))
        self.assertEqual(len(second["open_conflicts"]), 1)

    def test_new_version_supersedes_old_conflict(self) -> None:
        service = make_service()
        file_complete(service, name="赛事甲")
        second = service.file_application(
            make_draft(name="赛事乙", resource_requests=_overlap_requests())
        )
        app_b = second["application_id"]
        self.assertEqual(len(service.open_conflicts(app_b)), 1)
        # 乙改期到不重叠的窗口
        moved = make_sections(
            resource_requests=[
                {
                    "resource_id": "venue-main-stadium",
                    "kind": "venue",
                    "start": "2026-11-22T06:00:00+08:00",
                    "end": "2026-11-22T14:00:00+08:00",
                    "quantity": 1,
                }
            ]
        )
        service.submit_version(app_b, moved)
        self.assertEqual(service.open_conflicts(app_b), [])

    def test_non_overlapping_same_resource_is_fine(self) -> None:
        service = make_service()
        file_complete(service, name="赛事甲")
        later = make_sections(
            resource_requests=[
                {
                    "resource_id": "venue-main-stadium",
                    "kind": "venue",
                    "start": "2026-11-15T15:00:00+08:00",
                    "end": "2026-11-15T20:00:00+08:00",
                    "quantity": 1,
                }
            ]
        )
        second = service.file_application(make_draft(name="赛事乙", **later))
        self.assertEqual(second["open_conflicts"], [])


if __name__ == "__main__":
    unittest.main()
