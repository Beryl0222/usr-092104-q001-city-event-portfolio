"""聚合仓储：从事件存储装载四类聚合，并以一次原子提交保存。"""
from __future__ import annotations

from src.aggregates import (
    EventApplication,
    PortfolioDecision,
    ResourceCommitment,
    ReviewOpinion,
)
from src.envelopes import Event
from src.store import EventStore


class Repository:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    def load_application(self, case_id: str) -> EventApplication:
        return EventApplication.replay(  # type: ignore[return-value]
            case_id, self.store.load_stream("event_application", case_id)
        )

    def load_resources(self, case_id: str) -> ResourceCommitment:
        agg_id = f"resource-{case_id}"
        return ResourceCommitment.replay(  # type: ignore[return-value]
            agg_id, self.store.load_stream("resource_commitment", agg_id)
        )

    def load_review(self, case_id: str) -> ReviewOpinion:
        agg_id = f"review-{case_id}"
        return ReviewOpinion.replay(  # type: ignore[return-value]
            agg_id, self.store.load_stream("review_opinion", agg_id)
        )

    def load_portfolio(self, year: int) -> PortfolioDecision:
        agg_id = f"annual-{year}"
        return PortfolioDecision.replay(  # type: ignore[return-value]
            agg_id, self.store.load_stream("portfolio_decision", agg_id)
        )

    def save(self, *aggregates) -> list[Event]:
        expected: dict[tuple[str, str], int] = {}
        new_events: list[Event] = []
        for agg in aggregates:
            events = agg.pull_events()
            if not events:
                continue
            key = (agg.aggregate_type, agg.id)
            expected[key] = events[0].version - 1
            new_events.extend(events)
        if new_events:
            self.store.append_many(new_events, expected)
        return new_events
