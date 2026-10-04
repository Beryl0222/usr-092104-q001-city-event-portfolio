"""条件性批准与存储乐观并发。"""
from __future__ import annotations

import unittest

from src.catalog import Decision, Phase
from src.store import ConcurrentModificationError, EventStore
from tests._helpers import (
    build_approved_case,
    declare_default_resources,
    dt,
    file_case,
    new_service,
    resolve_all_conflicts,
    sign_all_baseline,
    submit_baseline_evidence,
)


class ConditionalApprovalTest(unittest.TestCase):
    def test_conditions_follow_version_and_can_be_satisfied(self) -> None:
        svc, _ = new_service()
        file_case(svc, "COND")
        submit_baseline_evidence(svc, "COND", "mass")
        declare_default_resources(svc, "COND", dt(2026, 5, 30))
        svc.run_conflict_check()
        resolve_all_conflicts(svc, "COND")
        members = sign_all_baseline(svc, "COND")
        svc.set_conditions(
            "COND",
            [
                {
                    "id": "K-1",
                    "description": "开赛前 30 日提交医疗救援布点终稿",
                    "owner_phase": Phase.SECURITY,
                    "required_by": "2026-09-01",
                },
                {
                    "id": "K-2",
                    "description": "财政绩效目标随资金下达同步公开",
                    "owner_phase": Phase.FINANCE,
                },
            ],
        )
        svc.issue_decision(
            2026,
            "COND",
            decision=Decision.CONDITIONAL_APPROVAL,
            vote={"赞成": 6, "反对": 1},
            voting_members=members,
            quorum=5,
            note="两项条件赛前办结",
        )
        portfolio = svc.repo.load_portfolio(2026)
        d = portfolio.decision_for("COND")
        self.assertEqual(d["decision"], Decision.CONDITIONAL_APPROVAL)
        self.assertEqual(set(d["conditions"]), {"K-1", "K-2"})

        # 履行一项条件
        svc.satisfy_condition("COND", condition_id="K-1", evidence_ref="MED-PLAN-FINAL", note="终稿已备案")
        review = svc.repo.load_review("COND")
        self.assertEqual(review.conditions["K-1"]["status"], "satisfied")
        self.assertEqual(review.conditions["K-2"]["status"], "open")
        with self.assertRaisesRegex(ValueError, "条件已满足"):
            svc.satisfy_condition("COND", condition_id="K-1", evidence_ref="X")


class StoreConcurrencyTest(unittest.TestCase):
    def test_optimistic_version_guards_stream(self) -> None:
        from src.envelopes import Event

        store = EventStore()
        e1 = Event(
            event_id="1",
            event_type="APPLICATION_FILED",
            aggregate_type="event_application",
            aggregate_id="X",
            occurred_at=dt(2026, 1, 1),
            version=1,
            summary="s",
            payload={"case_id": "X"},
        )
        store.append(e1, expected_version=0)
        e2 = Event(
            event_id="2",
            event_type="APPLICATION_AMENDED",
            aggregate_type="event_application",
            aggregate_id="X",
            occurred_at=dt(2026, 1, 2),
            version=2,
            summary="s",
            payload={"case_id": "X"},
        )
        # 过期的预期版本被拒
        with self.assertRaises(ConcurrentModificationError):
            store.append(e2, expected_version=0)
        store.append(e2, expected_version=1)
        # version 必须连续
        bad = Event(
            event_id="3",
            event_type="APPLICATION_WITHDRAWN",
            aggregate_type="event_application",
            aggregate_id="X",
            occurred_at=dt(2026, 1, 3),
            version=9,
            summary="s",
            payload={"case_id": "X"},
        )
        with self.assertRaises(ConcurrentModificationError):
            store.append(bad, expected_version=2)

    def test_case_events_replay_in_order(self) -> None:
        svc, store = new_service()
        build_approved_case(svc, "R1", dt(2026, 4, 4))
        events = store.events_for_case("R1")
        self.assertGreater(len(events), 5)
        versions = [(e.aggregate_type, e.version) for e in events]
        # 每条流内部 version 严格递增
        per_stream: dict[str, int] = {}
        for agg_type, ver in versions:
            assert per_stream.get(agg_type, 0) < ver
            per_stream[agg_type] = ver


if __name__ == "__main__":
    unittest.main()
