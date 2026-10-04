"""审计回放：从事件日志还原一项赛事的完整审议过程。

审计人员可以看到材料（申办/补件/变更版本）、会签、回避、冲突协调、
条件性批准、条件达成与后续变更，按发生顺序排列，并附中文解读。
"""

from __future__ import annotations

from src.service import ReviewService

_STAGE_LABELS = {
    "admission": "准入与资质",
    "resource": "场馆与人员占用",
    "safety": "安全方案",
    "transport": "交通保障",
    "fiscal": "财政承诺",
    "commercial": "商业权益",
    "legacy": "赛后利用",
}

_POSITION_LABELS = {
    "concur": "同意",
    "concur_with_conditions": "附条件同意",
    "dissent": "反对（少数意见）",
    "abstain": "弃权",
}

_OUTCOME_LABELS = {
    "approved": "批准",
    "conditionally_approved": "条件性批准",
    "rejected": "不予批准",
}

_KIND_LABELS = {
    "scale": "规模",
    "venue": "场地",
    "funding_source": "资金来源",
}


def _describe(event: dict) -> str:
    """把一条事件翻译成一行中文过程记录。"""
    payload = event.get("payload") or {}
    etype = event["event_type"]
    if etype == "APPLICATION_FILED":
        if payload.get("minor"):
            return f"一般变更备案，形成第 {payload.get('version')} 版材料"
        return f"提交申办材料（第 {payload.get('version')} 版）"
    if etype == "SUPPLEMENT_REQUESTED":
        return f"要求补正：{'、'.join(payload.get('missing', []))}"
    if etype == "SUPPLEMENT_PROVIDED":
        return f"收到补件（第 {payload.get('version')} 版）"
    if etype == "RECUSAL_DECLARED":
        scope = _STAGE_LABELS.get(payload.get("stage") or "", "全部环节")
        return f"回避登记：{payload.get('person')} 回避{scope}（{payload.get('reason')}）"
    if etype == "OPINION_SIGNED":
        stage = _STAGE_LABELS.get(payload.get("stage"), payload.get("stage"))
        position = _POSITION_LABELS.get(payload.get("position"), payload.get("position"))
        comment = payload.get("comment") or ""
        suffix = f"：{comment}" if comment else ""
        return (
            f"{payload.get('department')} 在「{stage}」环节会签：{position}"
            f"（{payload.get('signer')}，第 {payload.get('iteration')} 轮）{suffix}"
        )
    if etype == "RESOURCE_CONFLICTED":
        return (
            f"资源 {payload.get('resource_id')} 时段重叠：与 {payload.get('other_label')} "
            f"在 {payload.get('overlap_start')} 至 {payload.get('overlap_end')} 冲突"
        )
    if etype == "RESOURCE_CONFLICT_RESOLVED":
        return f"冲突协调完成（{payload.get('decided_by')}）：{payload.get('resolution')}"
    if etype == "RESOURCE_RESERVED":
        return f"资源占用生效：{payload.get('resource_id')}（{payload.get('start')} 起）"
    if etype == "RESOURCE_RELEASED":
        return f"资源占用释放：{payload.get('resource_id')}"
    if etype == "DECISION_ISSUED":
        outcome = _OUTCOME_LABELS.get(payload.get("outcome"), payload.get("outcome"))
        text = (
            f"第 {payload.get('decision_version')} 版决定：{outcome}"
            f"（决定部门：{payload.get('decided_by')}）"
        )
        conditions = payload.get("conditions") or []
        if conditions:
            text += "；条件：" + "、".join(c["description"] for c in conditions)
        minority = payload.get("minority_opinions") or []
        if minority:
            text += "；保留少数意见 " + str(len(minority)) + " 条"
        return text
    if etype == "CONDITION_FULFILLED":
        return f"条件达成登记：{payload.get('condition_id')}"
    if etype == "CHANGE_REQUESTED":
        kind = _KIND_LABELS.get(payload.get("kind"), payload.get("kind"))
        level = "实质变更" if payload.get("substantial") else "一般变更"
        return f"变更申报（{kind}，{level}）：{payload.get('description')}"
    if etype == "CHANGE_REOPENED":
        stages = "、".join(_STAGE_LABELS.get(s, s) for s in payload.get("affected_stages", []))
        return f"实质变更重开环节：{stages}（第 {payload.get('iteration')} 轮审议）"
    if etype == "PUBLIC_SERVICE_COMMITTED":
        return f"公共服务承诺生效：{payload.get('description')}"
    if etype == "PUBLIC_SERVICE_REVOKED":
        return (
            f"公共服务承诺撤销：{payload.get('service_id')}"
            f"（理由：{payload.get('reason')}；权限：{payload.get('authority')}）"
        )
    if etype == "APPLICATION_WITHDRAWN":
        return f"申办撤回：{payload.get('reason')}"
    return event.get("summary", etype)


def build_audit_trail(service: ReviewService, application_id: str) -> dict:
    """一项赛事的完整回放：材料、会签、条件性批准与后续变更。"""
    service._get(application_id)  # 不存在时抛 DomainError
    events = service.store.query(correlation_id=application_id)
    return {
        "application_id": application_id,
        "event_count": len(events),
        "timeline": [
            {
                "occurred_at": e["occurred_at"],
                "event_type": e["event_type"],
                "aggregate_type": e["aggregate_type"],
                "aggregate_id": e["aggregate_id"],
                "description": _describe(e),
            }
            for e in events
        ],
        "events": events,
    }
