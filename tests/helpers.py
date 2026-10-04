"""测试共享的夹具：固定时钟、完整申办草稿、全流程会签。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import REQUIRED_STAGES
from src.service import ReviewService

CST = timezone(timedelta(hours=8))


class Clock:
    """每次调用前进一分钟的确定时钟，保证事件时间有序。"""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 10, 1, 9, 0, tzinfo=CST)

    def __call__(self) -> datetime:
        moment = self.current
        self.current += timedelta(minutes=1)
        return moment


def make_service() -> ReviewService:
    return ReviewService(clock=Clock())


def make_sections(**overrides) -> dict:
    """一份栏目齐全、证据齐全（群众赛事）的申办材料。"""
    sections = {
        "operator_qualification": {
            "license_no": "体运字-2026-001",
            "past_events": ["2025 城市半程马拉松"],
            "credit_status": "良好",
        },
        "event_level": "municipal",
        "expected_crowd": {"participants": 8000, "spectators_peak": 20000, "staff": 1500},
        "resource_requests": [
            {
                "resource_id": "venue-main-stadium",
                "kind": "venue",
                "start": "2026-11-15T06:00:00+08:00",
                "end": "2026-11-15T14:00:00+08:00",
                "quantity": 1,
                "note": "主体育场",
            },
            {
                "resource_id": "police-detachment-a",
                "kind": "security",
                "start": "2026-11-15T06:00:00+08:00",
                "end": "2026-11-15T14:00:00+08:00",
                "quantity": 300,
            },
        ],
        "transport_plan": {"summary": "地铁加开+接驳车", "road_control_windows": []},
        "safety_plan": {"summary": "分区安检与应急疏散", "medical_points": 6},
        "fiscal_commitment": {"subsidy_amount": 2000000, "currency": "CNY", "source": "市财政体育专项"},
        "commercial_rights": {"naming": "保留市级冠名权", "broadcast": "市级台转播"},
        "legacy_plan": {"summary": "赛后场地向市民开放", "public_opening_hours": "每日 6-22 时"},
        "public_services": [
            {
                "service_id": "fitness-morning",
                "description": "赛事当月主体育场早场对市民免费开放",
                "windows": [
                    {
                        "resource_id": "venue-main-stadium",
                        "kind": "public_fitness_slot",
                        "start": "2026-11-16T06:00:00+08:00",
                        "end": "2026-11-16T08:00:00+08:00",
                        "quantity": 1,
                    }
                ],
            }
        ],
        "benefit_forecast": {
            "amount_low": 5000000,
            "amount_high": 12000000,
            "confidence": 0.8,
            "methodology": "住宿、餐饮、交通直接消费口径，不含间接拉动",
            "assumptions": ["外地参赛者占比 35%"],
            "prepared_by": "某咨询机构",
        },
        "evidence": {
            "operator_license": "doc-license-1",
            "participant_scale_declaration": "doc-scale-1",
            "medical_support_plan": "doc-medical-1",
            "volunteer_plan": "doc-volunteer-1",
            "mass_activity_safety_permit": "doc-permit-1",
        },
    }
    sections.update(overrides)
    return sections


def make_draft(name: str = "城市半程马拉松", category: str = "mass", **section_overrides) -> dict:
    return {
        "name": name,
        "category": category,
        "applicant": {"org_id": "org-001", "legal_name": "某体育文化有限公司"},
        **make_sections(**section_overrides),
    }


def file_complete(service: ReviewService, name: str = "城市半程马拉松", **overrides) -> str:
    result = service.file_application(make_draft(name=name, **overrides))
    assert result["status"] == "in_review", result
    return result["application_id"]


def sign_all(service: ReviewService, application_id: str, stages=None) -> None:
    """以不同部门名义完成全部必经环节会签。"""
    for index, stage in enumerate(stages or REQUIRED_STAGES):
        service.sign_opinion(
            application_id,
            stage.value,
            department=f"会签部门{index}",
            signer=f"签署人{index}",
            position="concur",
        )


def approve(service: ReviewService, application_id: str, **kwargs) -> dict:
    sign_all(service, application_id)
    params = {"outcome": "approved", "decided_by": "市体育局", "rationale": "符合年度赛事布局"}
    params.update(kwargs)
    return service.issue_decision(application_id, **params)
