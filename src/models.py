"""城市赛事承办审议的领域模型：枚举、数据结构与领域错误。

本模块只放稳定词汇，不含业务流程。所有状态变化都通过
``src.service.ReviewService`` 完成并追加领域事件（见 ``src.events``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DomainError(Exception):
    """业务规则被拒绝时抛出。``details`` 携带给申请方或管理端的说明。"""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EventCategory(str, Enum):
    """赛事类别：竞技、职业、群众，各自对应一套准入证据。"""

    COMPETITIVE = "competitive"
    PROFESSIONAL = "professional"
    MASS = "mass"


class EventLevel(str, Enum):
    """项目级别。"""

    INTERNATIONAL = "international"
    NATIONAL = "national"
    PROVINCIAL = "provincial"
    MUNICIPAL = "municipal"


class ResourceKind(str, Enum):
    """被占用资源的类别。居民日常健身时段也视为一种资源，

    这样赛事占用与已承诺的公共服务可以在同一套冲突检测中显现。"""

    VENUE = "venue"
    SECURITY = "security"
    MEDICAL = "medical"
    TRANSIT = "transit"
    PUBLIC_FITNESS_SLOT = "public_fitness_slot"


class Stage(str, Enum):
    """审议环节。变更时只重开受影响的环节，其余环节的会签继续有效。"""

    ADMISSION = "admission"  # 准入与资质
    RESOURCE = "resource"  # 场馆与人员占用
    SAFETY = "safety"  # 安全方案
    TRANSPORT = "transport"  # 交通保障
    FISCAL = "fiscal"  # 财政承诺
    COMMERCIAL = "commercial"  # 商业权益
    LEGACY = "legacy"  # 赛后利用


#: 作出决定前必须完成会签的环节。
REQUIRED_STAGES: tuple[Stage, ...] = (
    Stage.ADMISSION,
    Stage.RESOURCE,
    Stage.SAFETY,
    Stage.TRANSPORT,
    Stage.FISCAL,
    Stage.COMMERCIAL,
    Stage.LEGACY,
)


class ChangeKind(str, Enum):
    """批准后的实质变化类型。"""

    SCALE = "scale"  # 规模
    VENUE = "venue"  # 场地
    FUNDING_SOURCE = "funding_source"  # 资金来源


#: 实质变化 → 需要重开的环节。未列出的环节保持原会签有效。
CHANGE_IMPACT: dict[ChangeKind, tuple[Stage, ...]] = {
    ChangeKind.SCALE: (Stage.RESOURCE, Stage.SAFETY, Stage.TRANSPORT),
    ChangeKind.VENUE: (Stage.RESOURCE, Stage.SAFETY, Stage.TRANSPORT, Stage.LEGACY),
    ChangeKind.FUNDING_SOURCE: (Stage.FISCAL, Stage.COMMERCIAL),
}

#: 规模变化超过该比例才视为实质变化（其余按一般变更备案，不重开环节）。
SCALE_SUBSTANTIAL_RATIO = 0.2


class ApplicationStatus(str, Enum):
    SUPPLEMENT_REQUIRED = "supplement_required"  # 待补正
    IN_REVIEW = "in_review"  # 审议中
    APPROVED = "approved"
    CONDITIONALLY_APPROVED = "conditionally_approved"
    REJECTED = "rejected"
    CHANGE_UNDER_REVIEW = "change_under_review"  # 变更审议中
    WITHDRAWN = "withdrawn"


class DecisionOutcome(str, Enum):
    APPROVED = "approved"
    CONDITIONALLY_APPROVED = "conditionally_approved"
    REJECTED = "rejected"


class OpinionPosition(str, Enum):
    CONCUR = "concur"  # 同意
    CONCUR_WITH_CONDITIONS = "concur_with_conditions"  # 附条件同意
    DISSENT = "dissent"  # 反对（作为少数意见保留）
    ABSTAIN = "abstain"  # 弃权


#: 决策档案中每次申办版本必须齐全的栏目。
REQUIRED_SECTIONS: tuple[str, ...] = (
    "operator_qualification",  # 运营主体资质
    "event_level",  # 项目级别
    "expected_crowd",  # 预计人群
    "resource_requests",  # 场馆与人员占用
    "transport_plan",  # 交通保障
    "safety_plan",  # 安全方案
    "fiscal_commitment",  # 财政承诺
    "commercial_rights",  # 商业权益
    "legacy_plan",  # 赛后利用计划
)


@dataclass
class ApplicationVersion:
    """一版申办材料，即决策档案的一个版本。"""

    number: int
    submitted_at: str
    sections: dict
    missing_evidence: list[str] = field(default_factory=list)
    change_id: str | None = None  # 由哪次变更引入（初次申办为 None）


@dataclass
class Opinion:
    stage: Stage
    department: str
    signer: str
    position: OpinionPosition
    comment: str
    iteration: int  # 第几轮审议（变更重开后递增）
    signed_at: str


@dataclass
class Recusal:
    """回避登记：回避关系本身需要留痕。"""

    person: str
    reason: str
    stage: Stage | None  # None 表示整项赛事回避
    declared_at: str


@dataclass
class Condition:
    id: str
    description: str
    fulfilled: bool = False
    fulfilled_at: str | None = None
    evidence_note: str = ""


@dataclass
class Decision:
    id: str
    decision_version: int  # 决定版本，申请方可见
    outcome: DecisionOutcome
    decided_by: str  # 有权部门，预测收益不能替代
    rationale: str
    conditions: list[Condition]
    minority_opinions: list[dict]  # 少数意见随决定一并保留
    forecast_snapshot: dict | None  # 作为材料的预测收益快照
    based_on_version: int
    issued_at: str
    change_id: str | None = None


@dataclass
class ChangeRequest:
    id: str
    kind: ChangeKind
    description: str
    substantial: bool
    affected_stages: list[Stage]
    status: str  # open / reopened / closed
    requested_at: str


@dataclass
class Application:
    id: str
    name: str
    category: EventCategory
    applicant: dict
    status: ApplicationStatus
    created_at: str
    versions: list[ApplicationVersion] = field(default_factory=list)
    iteration: int = 1
    # 每个环节要求的最短会签轮次：变更重开时被影响的环节会提升到当前轮次，
    # 未受影响的环节保持原轮次，旧会签继续有效。
    stage_iterations: dict[Stage, int] = field(default_factory=dict)
    reopened_stages: set[Stage] = field(default_factory=set)
    decisions: list[Decision] = field(default_factory=list)
    changes: list[ChangeRequest] = field(default_factory=list)

    @property
    def current_version(self) -> ApplicationVersion:
        return self.versions[-1]


@dataclass
class Occupancy:
    """一次资源占用（申办请求、已生效承诺或公共服务承诺）。"""

    owner_id: str  # 申请 id 或公共服务承诺 id
    resource_id: str
    kind: ResourceKind
    start: str  # ISO 8601
    end: str
    quantity: int
    label: str = ""


@dataclass
class Conflict:
    id: str
    resource_id: str
    owners: tuple[str, str]
    overlap_start: str
    overlap_end: str
    status: str  # open / resolved / superseded
    created_at: str
    resolution: str = ""
    resolved_by: str = ""


@dataclass
class Commitment:
    """批准后生效的资源占用承诺。"""

    id: str
    application_id: str
    resource_id: str
    kind: ResourceKind
    start: str
    end: str
    quantity: int
    status: str  # active / released


@dataclass
class PublicServiceCommitment:
    """对外承诺的公共服务。只能经明示理由与权限撤销，不得静默消失。"""

    id: str
    application_id: str
    service_id: str
    description: str
    windows: list[dict]
    status: str = "active"  # active / revoked
    revoked_reason: str = ""
    revoked_by: str = ""
