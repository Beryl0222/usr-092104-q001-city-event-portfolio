"""年度赛事组合对场馆、财政与城市保障的峰值压力。"""

import unittest

from src.portfolio import build_portfolio_view
from tests.helpers import approve, file_complete, make_draft, make_sections, make_service


def _second_event_sections() -> dict:
    return make_sections(
        resource_requests=[
            {
                "resource_id": "venue-main-stadium",
                "kind": "venue",
                "start": "2026-11-15T10:00:00+08:00",  # 与默认事件同日叠加
                "end": "2026-11-15T22:00:00+08:00",
                "quantity": 1,
            },
            {
                "resource_id": "police-detachment-b",
                "kind": "security",
                "start": "2026-11-15T10:00:00+08:00",
                "end": "2026-11-15T22:00:00+08:00",
                "quantity": 200,
            },
        ],
        fiscal_commitment={"subsidy_amount": 1500000, "currency": "CNY", "source": "市财政体育专项"},
        public_services=[],
    )


class PortfolioTest(unittest.TestCase):
    def _two_approved_events(self):
        service = make_service()
        first = file_complete(service, name="赛事甲")
        approve(service, first)
        second = service.file_application(make_draft(name="赛事乙", **_second_event_sections()))
        app_b = second["application_id"]
        # 乙与甲的场馆时段重叠，先协调再批准
        conflict_id = second["open_conflicts"][0]["conflict_id"]
        service.resolve_conflict(conflict_id, "错峰使用同一体育场", "市赛事协调办")
        approve(service, app_b)
        return service

    def test_venue_peak_stacks_same_day_events(self) -> None:
        service = self._two_approved_events()
        view = build_portfolio_view(service, 2026)
        peak = view["venue_peaks"][0]
        self.assertEqual(peak["resource_id"], "venue-main-stadium")
        self.assertEqual(peak["peak_day"], "2026-11-15")
        self.assertEqual(peak["peak_quantity"], 2)  # 两场赛事同日占用

    def test_support_peak_aggregates_across_resources(self) -> None:
        service = self._two_approved_events()
        view = build_portfolio_view(service, 2026)
        security = [p for p in view["support_peaks"] if p["kind"] == "security"][0]
        self.assertEqual(security["peak_quantity"], 500)  # 300 + 200 两路警力
        self.assertEqual(security["peak_day"], "2026-11-15")
        self.assertEqual(len(security["events"]), 2)

    def test_fiscal_by_month_and_total(self) -> None:
        service = self._two_approved_events()
        view = build_portfolio_view(service, 2026)
        fiscal = view["fiscal"]
        self.assertEqual(fiscal["annual_total"], 3500000)
        self.assertEqual(fiscal["peak_month"]["month"], "2026-11")
        self.assertEqual(fiscal["peak_month"]["subsidy_total"], 3500000)

    def test_pending_and_rejected_events_excluded(self) -> None:
        service = make_service()
        file_complete(service, name="在审赛事")  # 未批准，不计入
        view = build_portfolio_view(service, 2026)
        self.assertEqual(view["approved_events"], [])
        self.assertEqual(view["venue_peaks"], [])

    def test_public_fitness_pressure_visible(self) -> None:
        service = make_service()
        app_id = file_complete(service, name="占用健身时段的赛事")
        approve(service, app_id)
        view = build_portfolio_view(service, 2026)
        fitness = view["public_fitness_pressure"]
        self.assertEqual(fitness[0]["resource_id"], "venue-main-stadium")
        self.assertEqual(fitness[0]["peak_day"], "2026-11-16")  # 公共服务承诺窗口


if __name__ == "__main__":
    unittest.main()
