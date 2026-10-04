"""稳定枚举与准入证据目录。

这些名称同时出现在事件 payload 与对外视图中，属于跨服务交换边界，
调整时需同步 contracts/domain.schema.json 并保持向后兼容。
"""
from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    APPLICATION_FILED = "APPLICATION_FILED"
    APPLICATION_AMENDED = "APPLICATION_AMENDED"
    APPLICATION_WITHDRAWN = "APPLICATION_WITHDRAWN"
    SUPPLEMENT_REQUESTED = "SUPPLEMENT_REQUESTED"
    ADMISSIBILITY_EVIDENCE_SUBMITTED = "ADMISSIBILITY_EVIDENCE_SUBMITTED"
    BENEFIT_FORECAST_ATTACHED = "BENEFIT_FORECAST_ATTACHED"
    RESOURCE_DECLARED = "RESOURCE_DECLARED"
    RESOURCE_REVISED = "RESOURCE_REVISED"
    RESOURCE_CONFLICTED = "RESOURCE_CONFLICTED"
    CONFLICT_CHECK_RUN = "CONFLICT_CHECK_RUN"
    RESOURCE_CONFLICT_RESOLVED = "RESOURCE_CONFLICT_RESOLVED"
    PUBLIC_SERVICE_COMMITTED = "PUBLIC_SERVICE_COMMITTED"
    PUBLIC_SERVICE_PROTECTED = "PUBLIC_SERVICE_PROTECTED"
    RECUSAL_DECLARED = "RECUSAL_DECLARED"
    OPINION_SIGNED = "OPINION_SIGNED"
    DISSENT_RECORDED = "DISSENT_RECORDED"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"
    CONDITION_SET = "CONDITION_SET"
    CONDITION_SATISFIED = "CONDITION_SATISFIED"
    DECISION_ISSUED = "DECISION_ISSUED"
    CHANGE_DECLARED = "CHANGE_DECLARED"
    CHANGE_REOPENED = "CHANGE_REOPENED"
    CHANGE_CLOSED = "CHANGE_CLOSED"


class AggregateType(StrEnum):
    EVENT_APPLICATION = "event_application"
    RESOURCE_COMMITMENT = "resource_commitment"
    REVIEW_OPINION = "review_opinion"
    PORTFOLIO_DECISION = "portfolio_decision"


EVENT_TYPES = frozenset(t.value for t in EventType)
AGGREGATE_TYPES = frozenset(t.value for t in AggregateType)


class Category(StrEnum):
    """赛事项目级别分类：竞技、职业、群众采用各自的准入证据。"""

    COMPETITIVE = "competitive"  # 竞技赛事
    PROFESSIONAL = "professional"  # 职业联赛
    MASS = "mass"  # 群众路跑等群众赛事
    INTERNATIONAL = "international"  # 拟引进的国际赛事（按项目性质挂靠竞技/职业证据）


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    FILED = "filed"
    SUPPLEMENTING = "supplementing"
    REVIEWING = "reviewing"
    DECIDED = "decided"
    REOPENED = "reopened"
    WITHDRAWN = "withdrawn"


class Decision(StrEnum):
    APPROVED = "approved"  # 批准
    CONDITIONAL_APPROVAL = "conditional_approval"  # 条件性批准
    REJECTED = "rejected"  # 不予批准
    DEFERRED = "deferred"  # 暂缓（档期或资源无法协调）


# 审议环节：实质变更时只重开受影响环节，其余结论与条件继续有效
class Phase(StrEnum):
    OPERATOR_QUALIFICATION = "operator_qualification"  # 运营主体资质
    PROJECT_GRADING = "project_grading"  # 项目级别
    ADMISSIBILITY = "admissibility"  # 分类准入证据
    BENEFIT_FORECAST = "benefit_forecast"  # 预测收益（仅供有权部门参考）
    VENUE = "venue"  # 场馆档期
    PERSONNEL = "personnel"  # 人员占用
    TRANSPORT = "transport"  # 交通保障
    SECURITY = "security"  # 安全方案
    FINANCE = "finance"  # 财政承诺
    COMMERCIAL = "commercial"  # 商业权益
    LEGACY = "legacy"  # 赛后利用计划
    PUBLIC_SERVICE = "public_service"  # 居民日常健身等公共服务
    DELIBERATION = "deliberation"  # 会签表决


# 实质变更的影响面（变更申报 → 只重开这些环节）
class ChangeAspect(StrEnum):
    SCALE = "scale"  # 规模
    VENUE = "venue"  # 场地
    FUNDING_SOURCE = "funding_source"  # 资金来源


CHANGE_PHASES: dict[str, frozenset[str]] = {
    ChangeAspect.SCALE: frozenset(
        {
            Phase.ADMISSIBILITY,
            Phase.PERSONNEL,
            Phase.TRANSPORT,
            Phase.SECURITY,
            Phase.FINANCE,
            Phase.BENEFIT_FORECAST,
        }
    ),
    ChangeAspect.VENUE: frozenset(
        {
            Phase.VENUE,
            Phase.SECURITY,
            Phase.TRANSPORT,
            Phase.PUBLIC_SERVICE,
            Phase.LEGACY,
        }
    ),
    ChangeAspect.FUNDING_SOURCE: frozenset(
        {
            Phase.FINANCE,
            Phase.OPERATOR_QUALIFICATION,
            Phase.COMMERCIAL,
        }
    ),
}


# 各赛事类别的准入证据（材料项）。证据缺失只能补正或否决，
# 预测收益不得替代准入证据或有权部门决定。
EVIDENCE_ITEMS: dict[str, tuple[str, ...]] = {
    Category.COMPETITIVE: (
        "体育行政部门赛事级别认定",
        "竞赛规程与技术代表确认",
        "反兴奋剂承诺",
        "裁判员与技术官员配置",
    ),
    Category.PROFESSIONAL: (
        "联赛主体授权文件",
        "职业俱乐部参赛确认",
        "运动员工作合同合规证明",
        "商业权益排他性说明",
    ),
    Category.MASS: (
        "活动安全许可前置材料",
        "路线踏勘与医疗救援布点",
        "志愿服务与人群组织方案",
        "公众参与告知与熔断机制",
    ),
}

# 国际赛事按其项目性质挂靠竞技或职业证据，另需涉外材料
INTERNATIONAL_EXTRA_EVIDENCE: tuple[str, ...] = (
    "国际组织授权或备案",
    "涉外事务协调意见",
)

# 审议必备的资源/保障材料
RESOURCE_SECTIONS: tuple[str, ...] = (
    Phase.VENUE,
    Phase.PERSONNEL,
    Phase.TRANSPORT,
    Phase.SECURITY,
    Phase.FINANCE,
    Phase.COMMERCIAL,
    Phase.LEGACY,
)
