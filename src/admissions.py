"""分类准入证据规则：竞技、职业、群众赛事各自需要提交的准入材料。

证据不齐的申办不会被拒绝受理，而是进入"待补正"状态，
缺失项以中文理由返回给申请方（补正理由），补件后继续审议。
"""

from __future__ import annotations

from src.models import EventCategory

#: 所有类别都要提交的基础证据。
COMMON_EVIDENCE: tuple[str, ...] = ("operator_license",)

#: 各类别专属的准入证据。
CATEGORY_EVIDENCE: dict[EventCategory, tuple[str, ...]] = {
    EventCategory.COMPETITIVE: (
        "competition_rules_approval",  # 竞赛规程核准
        "venue_standard_cert",  # 场地器材标准认证
        "referee_team_qualification",  # 裁判团队资质
        "anti_doping_plan",  # 反兴奋剂方案
    ),
    EventCategory.PROFESSIONAL: (
        "league_authorization",  # 联赛/联盟授权文件
        "club_participation_agreement",  # 俱乐部参赛协议
        "broadcast_plan",  # 转播与商业开发方案
        "athlete_insurance",  # 运动员保险凭证
    ),
    EventCategory.MASS: (
        "participant_scale_declaration",  # 参与人数规模申报
        "medical_support_plan",  # 医疗保障方案
        "volunteer_plan",  # 志愿者组织方案
        "mass_activity_safety_permit",  # 大型群众性活动安全许可
    ),
}

#: 证据键 → 中文名称，用于生成申请方可读的补正理由。
EVIDENCE_LABELS: dict[str, str] = {
    "operator_license": "运营主体资质证照",
    "competition_rules_approval": "竞赛规程核准文件",
    "venue_standard_cert": "场地器材标准认证",
    "referee_team_qualification": "裁判团队资质证明",
    "anti_doping_plan": "反兴奋剂方案",
    "league_authorization": "联赛/联盟授权文件",
    "club_participation_agreement": "俱乐部参赛协议",
    "broadcast_plan": "转播与商业开发方案",
    "athlete_insurance": "运动员保险凭证",
    "participant_scale_declaration": "参与人数规模申报",
    "medical_support_plan": "医疗保障方案",
    "volunteer_plan": "志愿者组织方案",
    "mass_activity_safety_permit": "大型群众性活动安全许可",
}


def required_evidence(category: EventCategory) -> tuple[str, ...]:
    return COMMON_EVIDENCE + CATEGORY_EVIDENCE[category]


def missing_evidence(category: EventCategory, evidence: dict | None) -> list[str]:
    """返回缺失证据的中文名称列表（即补正理由）。"""
    provided = {k for k, v in (evidence or {}).items() if v}
    return [
        EVIDENCE_LABELS[key]
        for key in required_evidence(category)
        if key not in provided
    ]
