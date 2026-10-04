"""测试用构造辅助：快速走完受理→证据→资源→冲突→会签→表决的基线流程。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.catalog import Category, Decision, EVIDENCE_ITEMS, INTERNATIONAL_EXTRA_EVIDENCE
from src.objects import (
    CrowdEstimate,
    FinancialCommitment,
    Forecast,
    Occupancy,
    PublicServicePlan,
    TimeWindow,
)
from src.repository import Repository
from src.service import DomainError, ReviewService, required_evidence
from src.store import EventStore

TZ = timezone(timedelta(hours=8))

BASELINE_SIGNERS = [
    ("赵一", "体育局", "局长"),
    ("钱二", "场馆管理单位", "场长"),
    ("孙三", "人员保障部门", "主任"),
    ("李四", "公安交管局", "支队长"),
    ("周五", "公安局", "副局长"),
    ("吴六", "财政局", "处长"),
    ("郑七", "商务部门", "处长"),
]


def dt(y: int, mo: int, d: int, h: int = 8, minute: int = 0) -> datetime:
    return datetime(y, mo, d, h, minute, tzinfo=TZ)


def window(start: datetime, hours: float = 10) -> TimeWindow:
    return TimeWindow(start=start, end=start + timedelta(hours=hours))


def new_service() -> tuple[ReviewService, EventStore]:
    store = EventStore()
    return ReviewService(Repository(store)), store


def evidence_for(category: str, nature: str | None = None) -> list[dict[str, str]]:
    return [
        {"item": name, "doc_ref": f"DOC-{idx:02d}"}
        for idx, name in enumerate(required_evidence(category, nature), start=1)
    ]


def default_occupancies(day: datetime, *, venue: str = "市体育中心", police_load: float = 200):
    return [
        Occupancy(
            resource_id=venue,
            resource_kind="venue",
            window=window(day),
            label=f"{venue}比赛档期",
        ),
        Occupancy(
            resource_id="公安安保一组",
            resource_kind="police_capacity",
            window=window(day),
            load=police_load,
            capacity=500,
            label="赛日安保警力",
        ),
    ]


def default_finance(fiscal: float = 500) -> FinancialCommitment:
    return FinancialCommitment(
        fiscal_amount_wan=fiscal,
        nonfiscal_amount_wan=300,
        funding_source="市级体育产业引导资金+冠名赞助",
    )


def sample_forecast() -> Forecast:
    return Forecast(
        metric="住宿间夜数",
        point_estimate=8000,
        confidence_low=6200,
        confidence_high=9800,
        methodology="以近三届同规模赛事参赛人群×本地住宿转化率 0.62 估算，含外溢率假设",
        source="市体育局赛事专班测算",
    )


def file_case(
    svc: ReviewService,
    case_id: str,
    *,
    category: str = Category.MASS,
    title: str | None = None,
    season: str = "2026",
    nature: str | None = None,
    crowd: CrowdEstimate | None = None,
) -> None:
    svc.file_application(
        case_id,
        title=title or f"{case_id}测试赛事",
        category=category,
        season=season,
        operator={"name": f"{case_id}运营公司", "qualification_no": f"OP-{case_id}"},
        project_level="市级A类",
        expected_crowd=crowd or CrowdEstimate(peak_on_site=12000, cumulative=30000),
        international_nature=nature,
    )


def submit_baseline_evidence(svc: ReviewService, case_id: str, category: str, nature: str | None = None) -> None:
    svc.submit_evidence(case_id, items=evidence_for(category, nature))


def declare_default_resources(
    svc: ReviewService,
    case_id: str,
    day: datetime,
    *,
    occupancies=None,
    finance=None,
    public_services=None,
):
    svc.declare_resources(
        case_id,
        occupancies=occupancies if occupancies is not None else default_occupancies(day),
        finance=finance or default_finance(),
        public_services=public_services or [],
        commercial_rights={"title_sponsor": f"赞助-{case_id}", "exclusive_scope": "赛事冠名"},
        legacy_plan="赛后场馆向市民开放 30 天，并向社区学校提供 10 课时公益指导",
    )


def sign_all_baseline(svc: ReviewService, case_id: str) -> list[str]:
    members = []
    for member, department, position in BASELINE_SIGNERS:
        svc.sign_opinion(case_id, member=member, department=department, position=position)
        members.append(member)
    return members


def resolve_all_conflicts(svc: ReviewService, case_id: str) -> None:
    res = svc.repo.load_resources(case_id)
    open_idx = [c["index"] for c in res.conflicts if c["status"] == "open"]
    if open_idx:
        svc.resolve_conflict(
            case_id,
            conflict_indexes=open_idx,
            resolution="协调档期/追加运力",
            note="测试中统一协调解决",
        )


def approve(
    svc: ReviewService,
    case_id: str,
    *,
    year: int = 2026,
    members=None,
    decision: str = Decision.APPROVED,
    quorum: int = 5,
    note: str = "",
):
    members = members or sign_all_baseline(svc, case_id)
    svc.issue_decision(
        year,
        case_id,
        decision=decision,
        vote={"赞成": len(members)},
        voting_members=members,
        quorum=quorum,
        note=note,
    )


def build_approved_case(
    svc: ReviewService,
    case_id: str,
    day: datetime,
    *,
    category: str = Category.MASS,
    nature: str | None = None,
    occupancies=None,
    finance=None,
    public_services=None,
    committed_services=None,
    season: str = "2026",
    year: int = 2026,
) -> list[str]:
    """走完一个可批准案例的完整基线；返回表决成员名单。"""
    file_case(svc, case_id, category=category, nature=nature, season=season)
    submit_baseline_evidence(svc, case_id, category, nature)
    svc.attach_forecast(case_id, sample_forecast())
    declare_default_resources(
        svc,
        case_id,
        day,
        occupancies=occupancies,
        finance=finance,
        public_services=public_services,
    )
    for service in committed_services or []:
        svc.commit_public_service(case_id, service)
    svc.run_conflict_check()
    resolve_all_conflicts(svc, case_id)
    members = sign_all_baseline(svc, case_id)
    approve(svc, case_id, year=year, members=members)
    return members
