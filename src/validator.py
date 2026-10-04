"""校验领域事件信封的基础字段。"""

REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")

#: 契约（contracts/domain.schema.json）要求这些字符串字段 minLength 为 1。
NON_EMPTY = ("event_id", "event_type", "aggregate_type", "aggregate_id", "summary")


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    for name in NON_EMPTY:
        if name in record and (not isinstance(record[name], str) or not record[name]):
            errors.append(f"{name} 必须是非空字符串")
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    return errors
