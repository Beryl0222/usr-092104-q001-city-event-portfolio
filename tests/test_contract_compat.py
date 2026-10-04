"""契约向后兼容：既有样例、信封校验、schema 与代码目录一致。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.catalog import EVENT_TYPES, AGGREGATE_TYPES
from src.envelopes import Event, validate_envelope
from src.validator import validate_event

ROOT = Path(__file__).parents[1]


class ContractCompatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))
        self.sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))

    def test_original_sample_still_valid(self) -> None:
        # 既有基础校验不能破坏
        self.assertEqual(validate_event(self.sample), [])
        # 新信封校验对既有样例同样通过
        self.assertEqual(validate_envelope(self.sample), [])

    def test_original_five_events_remain_registered(self) -> None:
        for name in (
            "APPLICATION_FILED",
            "RESOURCE_CONFLICTED",
            "OPINION_SIGNED",
            "DECISION_ISSUED",
            "CHANGE_REOPENED",
        ):
            self.assertIn(name, self.schema["properties"]["event_type"]["enum"])
            self.assertIn(name, EVENT_TYPES)

    def test_code_catalog_matches_schema(self) -> None:
        self.assertEqual(
            EVENT_TYPES,
            set(self.schema["properties"]["event_type"]["enum"]),
        )
        self.assertEqual(
            AGGREGATE_TYPES,
            set(self.schema["properties"]["aggregate_type"]["enum"]),
        )

    def test_envelope_rejects_unknown_event(self) -> None:
        bad = dict(self.sample, event_type="SOMETHING_ELSE")
        self.assertTrue(any("未登记" in e for e in validate_envelope(bad)))

    def test_event_roundtrip_keeps_envelope_fields(self) -> None:
        event = Event(
            event_id="e1",
            event_type="APPLICATION_FILED",
            aggregate_type="event_application",
            aggregate_id="c1",
            occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            version=1,
            summary="x",
            payload={"case_id": "c1"},
        )
        record = event.to_dict()
        self.assertEqual(validate_envelope(record), [])
        again = Event.from_dict(record)
        self.assertEqual(again.event_id, event.event_id)
        self.assertEqual(again.payload, {"case_id": "c1"})


if __name__ == "__main__":
    unittest.main()
