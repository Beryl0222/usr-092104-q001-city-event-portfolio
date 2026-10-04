# 城市赛事承办审议

本仓库保存城市赛事承办审议的领域词汇、事件约定与承办审议后端，供赛事发展专班把申办、会签、表决、变更放进同一决策档案，并让申请方、管理部门与审计人员各取所需视图。

## 目录

- `contracts/domain.schema.json`：领域事件信封与稳定枚举（后端全部事件与之兼容）。
- `data/sample.json`：事件信封联调样例；`data/sample_application.json`：一份完整申办材料样例。
- `src/`：领域模型、审议服务、组合视图、审计回放与 HTTP 接口。
- `tests/`：领域资料一致性与后端行为测试。

## 决策档案

每项赛事的决策档案（`GET /applications/{id}`）包含：申办版本、运营主体资质、项目级别、预计人群、场馆与人员占用、交通保障、安全方案、财政承诺、商业权益、赛后利用计划、公共服务承诺，以及会签、回避、冲突、决定与变更的完整留痕。竞技、职业、群众赛事按各自准入证据受理（`src/admissions.py`），缺件进入"待补正"，补正理由对申请方可见。

关键规则：

- **预测收益只是材料**：必须带测算口径与置信范围才能入档；决定必须记载有权部门与理由，系统取值（如 `system`）一律拒绝。
- **冲突在表决前显现**：同一资源（场馆、安保、医疗、交通、居民健身时段）的时间重叠在受理/改版时即检测，未协调完毕不能表决。
- **回避与少数意见留痕**：回避登记后被回避人的会签无效；反对意见随决定一并归档。
- **变更只重开受影响环节**：规模变化超 20%、场地变化、资金来源变化视为实质变化，分别重开对应环节，其余环节会签继续有效；资源占用本身变化时必重开资源环节。
- **公共服务不得静默消失**：已承诺的公共服务在新版本中消失时，必须逐项明示撤销理由与权限，否则拒绝变更。
- **条件性批准**：条件随决定版本登记，达成情况逐条留痕。

## 运行

```bash
python3 -m src.api --port 8080 --log data/events.jsonl
```

主要接口（详见 `src/api.py` 模块文档）：

- `POST /applications` 受理申办；`POST /applications/{id}/versions` 补件/改版
- `POST /applications/{id}/opinions` 会签；`POST /applications/{id}/recusals` 回避登记
- `GET /applications/{id}/conflicts` 未协调冲突；`POST /conflicts/{id}/resolve` 协调登记
- `POST /applications/{id}/decision` 表决决定；`POST /applications/{id}/conditions/{cid}/fulfill` 条件达成
- `POST /applications/{id}/changes` 变更申报；`POST /applications/{id}/changes/{cid}/close` 变更收口
- `POST /public-services/{id}/revoke` 撤销公共服务承诺（须理由与权限）
- `GET /applications/{id}/applicant` 申请方视图（补正理由、决定版本）
- `GET /portfolio/{year}` 年度组合峰值压力（场馆、财政、城市保障）
- `GET /audit/{id}` 审计回放（材料、会签、条件性批准、变更全过程）

## 事件兼容性

核心对象为 event_application、resource_commitment、review_opinion、portfolio_decision、public_service_commitment。事件类型在既有 APPLICATION_FILED、RESOURCE_CONFLICTED、OPINION_SIGNED、DECISION_ISSUED、CHANGE_REOPENED 基础上扩充（补件、回避、资源生效/释放、条件达成、变更申报、公共服务承诺/撤销等），既有取值与信封字段保持不变；同一赛事的全部事件以 `correlation_id` 串联，事件日志（JSONL）为审计留痕的系统记录，内存投影可在重启后重建（后续工作）。

## 本地检查

```bash
python3 -m unittest discover -s tests
```
