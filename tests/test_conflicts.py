"""表决前冲突显现：场馆档期互斥、公安运力超容量（含三方叠加）、占用修订后自动消除。"""
from __future__ import annotations

import unittest

from src.catalog import Category
from src.objects import Occupancy
from src.service import DomainError
from tests._helpers import (
    approve,
    build_approved_case,
    declare_default_resources,
    default_finance,
    default_occupancies,
    dt,
    file_case,
    new_service,
    resolve_all_conflicts,
    sign_all_baseline,
    submit_baseline_evidence,
    window,
)


def prepare(svc, case_id, day, *, occupancies, finance=None):
    file_case(svc, case_id, category=Category.MASS)
    submit_baseline_evidence(svc, case_id, Category.MASS)
    declare_default_resources(
        svc, case_id, day, occupancies=occupancies, finance=finance or default_finance()
    )


class ConflictTest(unittest.TestCase):
    def test_venue_overlap_blocks_vote_until_resolved(self) -> None:
        svc, _ = new_service()
        day = dt(2026, 7, 10)
        prepare(svc, "RUN-A", day, occupancies=default_occupancies(day, venue="市体育中心"))
        prepare(svc, "LEAGUE-B", day, occupancies=default_occupancies(day, venue="市体育中心"))

        found = svc.run_conflict_check()
        exclusive = [c for c in found if c["kind"] == "exclusive_overlap"]
        self.assertEqual(len(exclusive), 1)
        self.assertEqual(exclusive[0]["case_ids"], ["LEAGUE-B", "RUN-A"])

        members = sign_all_baseline(svc, "RUN-A")
        with self.assertRaisesRegex(DomainError, "资源时间重叠未处置"):
            approve(svc, "RUN-A", members=members)

        resolve_all_conflicts(svc, "RUN-A")
        resolve_all_conflicts(svc, "LEAGUE-B")
        approve(svc, "RUN-A", members=members)

    def test_police_capacity_three_way_overload_detected(self) -> None:
        svc, _ = new_service()
        day = dt(2026, 9, 19)
        for cid, load in (("P1", 400), ("P2", 300), ("P3", 250)):
            prepare(
                svc,
                cid,
                day,
                occupancies=[
                    Occupancy("公安安保一组", "police_capacity", window(day), load=load, capacity=500),
                ],
            )
        found = svc.run_conflict_check()
        triple = [c for c in found if set(c["case_ids"]) == {"P1", "P2", "P3"}]
        self.assertTrue(triple, "三方同时叠加的峰值必须被检出")
        self.assertEqual(triple[0]["load_total"], 950)
        self.assertEqual(triple[0]["capacity"], 500)

    def test_disjoint_days_no_conflict(self) -> None:
        svc, _ = new_service()
        prepare(svc, "D1", dt(2026, 4, 1), occupancies=default_occupancies(dt(2026, 4, 1)))
        prepare(svc, "D2", dt(2026, 4, 8), occupancies=default_occupancies(dt(2026, 4, 8)))
        self.assertEqual(svc.run_conflict_check(), [])

    def test_conflict_auto_clears_after_pre_vote_revision(self) -> None:
        svc, _ = new_service()
        day = dt(2026, 8, 15)
        prepare(svc, "BASE", day, occupancies=default_occupancies(day, venue="体育馆X"))
        prepare(svc, "NEW", day, occupancies=default_occupancies(day, venue="体育馆X"))
        found = svc.run_conflict_check()
        self.assertTrue(any(c["kind"] == "exclusive_overlap" for c in found))

        # 表决前协调改期（会前修订，不触发环节重开）
        new_day = dt(2026, 8, 22)
        svc.revise_resources(
            "NEW",
            occupancies=default_occupancies(new_day, venue="体育馆X"),
            reason="与已申报赛事撞档，协调改期",
        )
        self.assertEqual(svc.run_conflict_check(), [])
        res = svc.repo.load_resources("NEW")
        self.assertTrue(all(c["status"] == "resolved" for c in res.conflicts))
        self.assertEqual(res.conflicts[0]["resolution"], "自动消除")

    def test_withdrawn_case_leaves_conflict_pool(self) -> None:
        svc, _ = new_service()
        day = dt(2026, 7, 1)
        prepare(svc, "KEEP", day, occupancies=default_occupancies(day))
        prepare(svc, "GONE", day, occupancies=default_occupancies(day))
        self.assertTrue(svc.run_conflict_check())  # 默认同场馆，撤回前确实冲突
        svc.withdraw_application("GONE", reason="申办单位主动退出")
        self.assertEqual(svc.run_conflict_check(), [])


if __name__ == "__main__":
    unittest.main()
