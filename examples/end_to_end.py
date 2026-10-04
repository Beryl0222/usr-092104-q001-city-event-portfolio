"""端到端演示：同一季度多项赛事申办的承办审议全过程。

运行：python3 -m examples.end_to_end

情节：
1. 同一季度受理职业联赛、群众路跑、拟引进国际赛事三项申办；
2. 三类赛事提交各自准入证据；路跑被要求补正；各单位提交带口径与置信范围的预测收益；
3. 表决前冲突检测显现场馆档期与公安运力重叠；协调改期/分时段/优化安保后消解；
4. 一名委员因利害关系回避；一名委员保留少数意见；国际赛条件性批准；
5. 联赛批准后场地实质变化：只重开受影响环节，已承诺的市民晨练服务给出替代安排，
   重审后决定升至 v2；
6. 输出申请方、管理人员、审计人员三类视图。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from itertools import count

from src.catalog import Category, ChangeAspect, Decision, Phase
from src.objects import (
    CrowdEstimate,
    FinancialCommitment,
    Forecast,
    Occupancy,
    PublicServicePlan,
    TimeWindow,
)
from src.projections import PHASE_CN, build_applicant_view, build_audit_view, build_management_view
from src.repository import Repository
from src.service import ReviewService
from src.store import EventStore

TZ = timezone(timedelta(hours=8))
_seq = count(1)


def clock() -> datetime:
    # 单调推进的演示时钟
    return datetime(2026, 7, 1, 9, tzinfo=TZ) + timedelta(minutes=next(_seq))


def ids() -> str:
    return f"evt-{next(_seq):04d}"


def w(hour: int, day: int = 19, span: float = 10) -> TimeWindow:
    return TimeWindow(datetime(2026, 9, day, hour, tzinfo=TZ), datetime(2026, 9, day, hour, tzinfo=TZ) + timedelta(hours=span))


def heading(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def main() -> None:
    svc = ReviewService(Repository(EventStore()), clock=clock, id_generator=ids)

    heading("一、受理三项申办（竞技/职业/群众各用各的准入证据）")
    svc.file_application(
        "LEAGUE-2026",
        title="2026 城市职业篮球联赛",
        category=Category.PROFESSIONAL,
        season="2026",
        operator={"name": "城市篮球俱乐部有限公司", "qualification_no": "OP-LEAGUE-01"},
        project_level="职业联赛",
        expected_crowd=CrowdEstimate(peak_on_site=18000, cumulative=210000, note="14 个主场"),
    )
    svc.file_application(
        "RUN-2026",
        title="2026 城市马拉松（群众路跑）",
        category=Category.MASS,
        season="2026",
        operator={"name": "市路跑协会", "qualification_no": "OP-RUN-02"},
        project_level="全国A类群众赛事",
        expected_crowd=CrowdEstimate(peak_on_site=30000, cumulative=30000),
    )
    svc.file_application(
        "INTL-2026",
        title="2026 国际田联城市挑战赛（拟引进）",
        category=Category.INTERNATIONAL,
        season="2026",
        operator={"name": "国际赛事运营有限公司", "qualification_no": "OP-INTL-03"},
        project_level="国际A类",
        expected_crowd=CrowdEstimate(peak_on_site=25000, cumulative=25000),
        international_nature=Category.COMPETITIVE,
    )
    print("已受理：LEAGUE-2026（职业）、RUN-2026（群众）、INTL-2026（国际·竞技性质）")

    heading("二、补正、分类准入证据与预测收益材料（仅供参考）")
    rid = svc.request_supplement(
        "RUN-2026",
        missing=["路线踏勘与医疗救援布点"],
        reason="初版材料仅有起终点医疗点，全程 21 个路口救援布点缺失",
        requested_by="公安局",
    )
    pro_evidence = [
        ("联赛主体授权文件", "DOC-PRO-1"),
        ("职业俱乐部参赛确认", "DOC-PRO-2"),
        ("运动员工作合同合规证明", "DOC-PRO-3"),
        ("商业权益排他性说明", "DOC-PRO-4"),
    ]
    mass_evidence = [
        ("活动安全许可前置材料", "DOC-MASS-1"),
        ("志愿服务与人群组织方案", "DOC-MASS-2"),
        ("公众参与告知与熔断机制", "DOC-MASS-3"),
    ]
    intl_evidence = [
        ("体育行政部门赛事级别认定", "DOC-COMP-1"),
        ("竞赛规程与技术代表确认", "DOC-COMP-2"),
        ("反兴奋剂承诺", "DOC-COMP-3"),
        ("裁判员与技术官员配置", "DOC-COMP-4"),
        ("国际组织授权或备案", "DOC-INTL-1"),
        ("涉外事务协调意见", "DOC-INTL-2"),
    ]
    svc.submit_evidence("LEAGUE-2026", items=[{"item": i, "doc_ref": d} for i, d in pro_evidence])
    svc.submit_evidence("RUN-2026", items=[{"item": i, "doc_ref": d} for i, d in mass_evidence])
    svc.submit_evidence(
        "RUN-2026",
        request_id=rid,
        items=[{"item": "路线踏勘与医疗救援布点", "doc_ref": "DOC-MASS-4"}],
    )
    svc.submit_evidence("INTL-2026", items=[{"item": i, "doc_ref": d} for i, d in intl_evidence])

    for cid, metric, point, low, high in (
        ("LEAGUE-2026", "住宿间夜数", 26000, 21000, 31000),
        ("RUN-2026", "综合消费（万元）", 9000, 7200, 11500),
        ("INTL-2026", "境外转播受众（万人次）", 4000, 2600, 6800),
    ):
        svc.attach_forecast(
            cid,
            Forecast(
                metric=metric,
                point_estimate=point,
                confidence_low=low,
                confidence_high=high,
                methodology="以近三届同类赛事样本回归，置信水平 95%，含外溢情景假设；不作审批依据",
                source="赛事发展专班测算",
            ),
        )
    print(f"路跑补正通知 {rid} 已答复；预测材料均带口径与 95% 置信范围，标注 advisory_only")

    heading("三、资源占用申报（初始材料存在档期与运力冲突）")
    league_occ = [
        Occupancy("市体育中心", "venue", w(8), label="职业联赛主场"),
        Occupancy("公安安保一组", "police_capacity", w(8), load=300, capacity=500, label="赛日警力"),
    ]
    run_occ = [
        Occupancy("市体育中心", "venue", w(8), label="路跑起终点区域", public_use_displaced=True),
        Occupancy("公安安保一组", "police_capacity", w(8), load=200, capacity=500),
    ]
    intl_occ = [
        Occupancy("市体育中心", "venue", w(8), label="国际挑战赛场地"),
        Occupancy("公安安保一组", "police_capacity", w(8), load=300, capacity=500),
    ]
    for cid, occ, fiscal, sponsor, legacy in (
        ("LEAGUE-2026", league_occ, 800, "联赛冠名排他", "青少年篮球公益赛季"),
        ("RUN-2026", run_occ, 300, "路跑冠名", "赛后赛道公园化开放"),
        ("INTL-2026", intl_occ, 1500, "国际赛事全球赞助", "场馆升级与田径进校园"),
    ):
        svc.declare_resources(
            cid,
            occupancies=occ,
            finance=FinancialCommitment(fiscal, 400, "市级引导资金+市场开发"),
            commercial_rights={"title_sponsor": sponsor, "exclusive_scope": "冠名"},
            legacy_plan=legacy,
        )
    svc.commit_public_service(
        "LEAGUE-2026",
        PublicServicePlan(
            service_id="PS-EVENING",
            description="市体育中心工作日 18-20 时市民羽毛球开放时段",
            window=w(18, span=2),
            externally_committed=True,
        ),
    )

    found = svc.run_conflict_check()
    print(f"表决前检测：发现 {len(found)} 项冲突/提示")
    for c in found:
        print(f"  - [{c['severity']}] {c['kind']} {c['resource_id']} "
              f"{c['window']['start'][11:16]}~{c['window']['end'][11:16]} 涉及 {','.join(c['case_ids'])}：{c['detail']}")

    heading("四、协调：路跑改期滨江赛道；联赛与国际赛同日分时段并优化安保")
    svc.revise_resources(
        "RUN-2026",
        occupancies=[
            Occupancy("滨江赛道", "venue", w(7, day=20, span=6), label="马拉松赛道"),
            Occupancy("公安安保一组", "police_capacity", w(7, day=20, span=6), load=180, capacity=500),
        ],
        reason="与场馆赛事撞档，改至 9 月 20 日滨江赛道",
    )
    # 先记录有权部门的人工处置决定，再据决定修订为分时段占用
    res = svc.repo.load_resources("INTL-2026")
    idx = [c["index"] for c in res.conflicts if c["status"] == "open"]
    svc.resolve_conflict(
        "INTL-2026",
        conflict_indexes=idx,
        resolution="同日分时段使用",
        note="联赛 8-14 时、国际赛 15-23 时；国际赛优化安保部署，峰值警力降至 180",
    )
    svc.revise_resources(
        "INTL-2026",
        occupancies=[
            Occupancy("市体育中心", "venue", w(15, span=8), label="国际挑战赛晚间场"),
            Occupancy("公安安保一组", "police_capacity", w(15, span=8), load=180, capacity=500),
        ],
        reason="分时段使用并优化安保部署",
    )
    svc.revise_resources(
        "LEAGUE-2026",
        occupancies=[
            Occupancy("市体育中心", "venue", w(8, span=6), label="职业联赛下午场"),
            Occupancy("公安安保一组", "police_capacity", w(8, span=6), load=300, capacity=500),
        ],
        reason="与国际赛分时段",
    )
    remaining = svc.run_conflict_check()
    print(f"协调后再次检测：剩余阻断冲突 {len([c for c in remaining if c['severity'] == 'block'])} 项"
          f"（居民健身时段提示已在公共服务安排中回应）")

    heading("五、回避、会签、少数意见与表决")
    svc.declare_recusal("LEAGUE-2026", member="周五", reason="其近亲属持有联赛运营公司股份")
    svc.record_dissent(
        "INTL-2026",
        member="吴六",
        opinion="1500 万财政承诺占三季度赛事盘子比例偏高，建议压减至 1200 万并强化绩效约束",
    )
    baseline = [
        ("赵一", "体育局", "局长"), ("钱二", "场馆管理单位", "场长"), ("孙三", "人员保障部门", "主任"),
        ("李四", "公安交管局", "支队长"), ("周五", "公安局", "副局长"), ("吴六", "财政局", "处长"),
        ("郑七", "商务部门", "处长"),
    ]
    for cid in ("RUN-2026", "INTL-2026"):
        for m, d, p in baseline:
            svc.sign_opinion(cid, member=m, department=d, position=p)
    for m, d, p in baseline:
        if m != "周五":  # 回避者不会签
            svc.sign_opinion("LEAGUE-2026", member=m, department=d, position=p)
    svc.sign_opinion("LEAGUE-2026", member="陈八", department="公安局", position="安保处长")

    league_voters = ["赵一", "钱二", "孙三", "李四", "陈八", "吴六", "郑七"]
    svc.set_conditions(
        "INTL-2026",
        [{
            "id": "K-SECU",
            "description": "开赛前 45 日提交涉外安保与观众疏散终稿并经公安复核",
            "owner_phase": Phase.SECURITY,
            "required_by": "2026-08-05",
        }],
    )
    svc.issue_decision(2026, "LEAGUE-2026", decision=Decision.APPROVED,
                       vote={"赞成": 7}, voting_members=league_voters, quorum=5,
                       note="周五回避；批准")
    svc.issue_decision(2026, "RUN-2026", decision=Decision.APPROVED,
                       vote={"赞成": 6, "弃权": 1}, voting_members=[m for m, _, _ in baseline], quorum=5)
    svc.issue_decision(2026, "INTL-2026", decision=Decision.CONDITIONAL_APPROVAL,
                       vote={"赞成": 6, "反对": 1}, voting_members=[m for m, _, _ in baseline], quorum=5,
                       note="吴六反对意见随档保留；附安保条件")
    print("联赛：批准（周五回避，陈八代会签公安局）")
    print("路跑：批准；国际赛：条件性批准（吴六少数意见保留）")

    heading("六、批准后联赛场地变更：只重开受影响环节，公共服务不得静默消失")
    svc.declare_post_decision_change(
        "LEAGUE-2026",
        aspects=[ChangeAspect.VENUE],
        justification="市体育中心承接设备检修，联赛主场调整至城西体育中心",
        occupancies=[
            Occupancy("城西体育中心", "venue", w(8, span=6), label="联赛新主场"),
            Occupancy("公安安保一组", "police_capacity", w(8, span=6), load=300, capacity=500),
        ],
        public_services=[
            PublicServicePlan(
                service_id="PS-EVENING",
                description="市民羽毛球开放时段（随主场迁移）",
                window=w(18, span=2),
                replacement="城西体育中心副馆 18-20 时等量开放，并加开周末上午场",
                externally_committed=True,
            )
        ],
        legacy_plan="青少年篮球公益赛季改在城西体育中心落地，覆盖周边 12 所学校",
    )
    review = svc.repo.load_review("LEAGUE-2026")
    print("仅重开：" + "、".join(PHASE_CN.get(p, p) for p in review.rounds[0]["phases"]))
    # 受影响环节重新会签（财政、商业等未受影响环节不再重开）
    deputies = {"体育局": ("王副局长", "副局长"), "场馆管理单位": ("李场长", "场长"),
                "公安交管局": ("钱支队长", "支队长"), "公安局": ("孙局长", "副局长")}
    voters = []
    for dep, (m, pos) in deputies.items():
        svc.sign_opinion("LEAGUE-2026", member=m, department=dep, position=pos)
        voters.append(m)
    svc.run_conflict_check()
    svc.resolve_reopened_round(
        "LEAGUE-2026",
        conclusions={p: f"{p} 复核通过" for p in review.rounds[0]["phases"]},
    )
    svc.issue_decision(2026, "LEAGUE-2026", decision=Decision.APPROVED,
                       vote={"赞成": 4}, voting_members=voters, quorum=3,
                       note="变更重审后批准，决定升至 v2")
    print("受影响环节复核通过；财政承诺、商业权益等原结论继续有效；决定升至 v2")

    heading("七、申请方视图（路跑：补正理由与决定版本）")
    print(json.dumps(build_applicant_view(svc.repo, "RUN-2026").to_dict(),
                     ensure_ascii=False, indent=2, default=str))

    heading("八、管理人员视图：2026 年度赛事组合峰值压力")
    mgmt = build_management_view(svc.repo, 2026).to_dict()
    summary = {
        "纳入组合": [f"{c['case_id']}《{c['title']}》" for c in mgmt["included_cases"]],
        "财政承诺合计（万元）": mgmt["fiscal"]["total_commitment_wan"],
        "峰值压力": [
            {
                "资源": r["resource_id"],
                "峰值时段": f"{r['peak_window']['start'][5:16]} ~ {r['peak_window']['end'][5:16]}",
                "峰值占用": r["peak_load"],
                "容量": r["capacity"],
                "利用率": r["utilization"],
                "并发赛事": r["concurrent_cases"],
            }
            for r in mgmt["peak_pressure"]["by_resource"]
        ],
        "超载资源": mgmt["peak_pressure"]["overloaded"],
        "预测材料声明": mgmt["advisory_forecasts"]["disclaimer"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    heading("九、审计视图：联赛完整回放（材料→会签→条件→变更）")
    audit = build_audit_view(svc.repo, "LEAGUE-2026")
    print(f"原始事件信封数：{audit['replay']['event_count']}（每条均通过信封校验）")
    print("决定版本：", [(d["version"], d["decision_cn"]) for d in audit["replay"]["decision_versions"]])
    print("回避：", audit["current_state"]["recusals"])
    print("重开轮次：", [(r["round_no"], r["phases"], r["resolved_at"] is not None)
                          for r in audit["replay"]["reopened_rounds"]])
    print("公共服务保护：", audit["current_state"]["public_service_protections"])
    print("\n结构化时间线：")
    for item in audit["replay"]["timeline"]:
        print(f"  {item['at'][11:16]} {item['event_type']:<32} {item['action']}")


if __name__ == "__main__":
    main()
