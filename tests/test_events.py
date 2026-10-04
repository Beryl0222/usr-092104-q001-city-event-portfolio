"""事件信封与仓库既有契约的兼容性。"""

import json
import unittest
from pathlib import Path

from src.events import EventStore
from src.validator import validate_event
from tests.helpers import Clock, approve, file_complete, make_service

SCHEMA = json.loads(
    (Path(__file__).parents[1] / "contracts" / "domain.schema.json").read_text(encoding="utf-8")
)


class EventCompatibilityTest(unittest.TestCase):
    def test_emitted_events_match_envelope_and_contract_enums(self) -> None:
        service = make_service()
        app_id = file_complete(service)
        approve(service, app_id)
        event_types = set(SCHEMA["properties"]["event_type"]["enum"])
        aggregate_types = set(SCHEMA["properties"]["aggregate_type"]["enum"])
        events = service.store.all()
        self.assertGreater(len(events), 5)
        for event in events:
            self.assertEqual(validate_event(event), [], event)
            self.assertIn(event["event_type"], event_types)
            self.assertIn(event["aggregate_type"], aggregate_types)
            self.assertEqual(event["correlation_id"], app_id)

    def test_original_sample_still_valid(self) -> None:
        sample = json.loads(
            (Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8")
        )
        self.assertEqual(validate_event(sample), [])
        self.assertIn(sample["event_type"], set(SCHEMA["properties"]["event_type"]["enum"]))

    def test_version_increments_per_aggregate(self) -> None:
        store = EventStore(clock=Clock())
        first = store.append("APPLICATION_FILED", "event_application", "app-x", "第一版")
        second = store.append("SUPPLEMENT_REQUESTED", "event_application", "app-x", "补正")
        other = store.append("OPINION_SIGNED", "review_opinion", "op-1", "会签")
        self.assertEqual((first["version"], second["version"], other["version"]), (1, 2, 1))

    def test_jsonl_persistence_roundtrip(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            store = EventStore(path=path, clock=Clock())
            store.append("APPLICATION_FILED", "event_application", "app-1", "受理")
            reloaded = EventStore(path=path, clock=Clock())
            self.assertEqual(len(reloaded.all()), 1)
            self.assertEqual(reloaded.all()[0]["aggregate_id"], "app-1")

    def test_invalid_envelope_rejected(self) -> None:
        store = EventStore(clock=Clock())
        with self.assertRaises(ValueError):
            store.append("APPLICATION_FILED", "event_application", "", "缺少聚合 id")


if __name__ == "__main__":
    unittest.main()
