# 城市赛事承办审议后端

把同一季度的职业联赛、群众路跑、拟引进国际赛事等申办，纳入同一套**事件溯源（event sourcing）**的承办审议决策档案：
申办版本、运营主体资质、项目级别、预计人群、场馆与人员占用、交通保障、安全方案、财政承诺、商业权益、赛后利用计划全部可回放；
兼容仓库既有的领域事件信封约定。

## 目录

- `contracts/domain.schema.json`：领域事件信封与稳定枚举（仅追加扩展，原有 5 个事件、4 类聚合保持不变）。
- `data/sample.json`：原始中文联调样例，仍然有效。
- `src/`
  - `envelopes.py`：事件信封（event_id/event_type/aggregate_type/aggregate_id/occurred_at/version/summary + payload），与 `validator.py` 兼容。
  - `catalog.py`：稳定枚举、四类赛事分类准入证据目录、审议环节、实质变更→受影响环节映射。
  - `objects.py`：时间窗、资源占用、预测收益（强制口径+置信范围）、财政承诺、公共服务安排等值对象。
  - `store.py`：追加式事件存储，按聚合流维护连续 version，乐观并发，跨流按 `case_id` 回放。
  - `aggregates.py`：`event_application` / `resource_commitment` / `review_opinion` / `portfolio_decision` 四类聚合。
  - `conflicts.py`：表决前时间重叠检测——独占资源互斥、运力扫描线超容量（含三方以上叠加）、居民健身时段提示。
  - `service.py`：审议应用服务，承载全部业务规则。
  - `repository.py`：聚合装载与多聚合原子提交。
  - `projections.py`：申请方 / 管理人员 / 审计人员三类只读视图。
- `tests/`：契约兼容与全部业务规则测试（含 `_helpers.py` 测试夹具）。
- `examples/end_to_end.py`：三项赛事同季度申办的端到端演示。

## 已登记领域事件

```
APPLICATION_FILED / APPLICATION_AMENDED / APPLICATION_WITHDRAWN
SUPPLEMENT_REQUESTED / ADMISSIBILITY_EVIDENCE_SUBMITTED
BENEFIT_FORECAST_ATTACHED
RESOURCE_DECLARED / RESOURCE_REVISED
RESOURCE_CONFLICTED / CONFLICT_CHECK_RUN / RESOURCE_CONFLICT_RESOLVED
PUBLIC_SERVICE_COMMITTED / PUBLIC_SERVICE_PROTECTED
RECUSAL_DECLARED / OPINION_SIGNED / DISSENT_RECORDED
REVIEW_RESOLVED
CONDITION_SET / CONDITION_SATISFIED
DECISION_ISSUED
CHANGE_DECLARED / CHANGE_REOPENED / CHANGE_CLOSED
```

## 核心规则如何落地

- **分类准入证据**：竞技、职业、群众各有独立证据目录（`catalog.EVIDENCE_ITEMS`）；国际赛事按项目性质挂靠竞技/职业证据，另需国际组织授权与涉外协调意见。证据不齐只能补正或被否决。
- **预测收益仅供参考**：`Forecast` 构造即强制计算口径与覆盖点估计的置信范围；事件与三类视图中均标注 `advisory_only`，不设会签门槛，也永远不构成自动放行依据。
- **冲突在表决前显现**：`run_conflict_check()` 对在档申办全量比对；场馆/人员等独占资源时间重叠即阻断，公安/道路运力按扫描线求任意时刻并发合计是否超容量；每次检测留 `CONFLICT_CHECK_RUN` 痕，最新资源材料之后未检测、或阻断项未处置，均不得表决。占用修订后旧冲突自动登记消除。
- **回避、补正、少数意见均保留**：回避成员不能会签或表决（可由同部门无利害关系者代会签）；补正请求带编号、理由与答复追踪；少数意见永久随档，不阻断多数决定。
- **决定与版本**：批准 / 条件性批准 / 不予批准 / 暂缓，校验法定人数、票数一致与过半；条件性批准必须附条件，条件可履行留痕；同一申办再次出决定时文件版本递增。
- **批准后实质变更只重开受影响环节**：申报 scale / venue / funding_source 后，按映射只重开相关环节（如资金来源不重开场馆与安全，场地变化不重开财政），受影响环节须重新会签并逐一给出结论，之后重新表决生成新版决定；未受影响的原结论、条件继续有效。
- **公共服务不得静默消失**：已对外承诺的居民日常健身等服务，场地变更时必须重新提交；漏报、撤销对外承诺属性、或场地变了却不给替代安排一律拒绝，每次保护登记 `PUBLIC_SERVICE_PROTECTED`。

## 三类视图

- 申请方 `build_applicant_view`：补正理由与答复状态、准入证据齐备情况、决定版本与所附条件、预测材料（参考性标注）；看不到其他申办与审议内部讨论。
- 管理人员 `build_management_view(year)`：年度已批准组合在场馆/人员/运力上的峰值时段、峰值占用与利用率（如实呈现超载）、财政承诺合计与分项、公共服务保障情况。
- 审计人员 `build_audit_view`：跨四流的原始事件信封（逐条可过信封校验）+ 中文结构化时间线 + 决定版本、回避、少数意见、条件、重开轮次、公共服务保护与现状快照。

## 本地检查

```bash
python3 -m unittest discover -s tests   # 35 项测试
python3 -m examples.end_to_end          # 端到端演示
```

存储为内存实现（`src/store.EventStore`），接口按追加日志设计，可替换为持久化实现；时间与 ID 经 `clock` / `id_generator` 注入，便于测试复现。
