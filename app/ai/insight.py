"""AI 决策层之二：交付风险雷达与瓶颈诊断。"""

from __future__ import annotations

from collections import defaultdict

from ..engine.calendar import ShopCalendar
from ..engine.model import Problem


def risk_radar(problem: Problem, assignments: dict[str, dict], calendar: ShopCalendar, metrics: dict) -> dict:
    tasks = {t.key: t for t in problem.tasks}
    by_order: dict[int, list] = defaultdict(list)
    for task in problem.tasks:
        by_order[task.order_id].append(task)

    risks = []
    for order_id, items in by_order.items():
        assign = [assignments.get(t.key) for t in items]
        if any(a is None for a in assign):
            risks.append(
                {
                    "order_id": order_id,
                    "order_code": items[0].order_code,
                    "level": "高",
                    "reason": "存在无法满足设备能力或人员技能的工序，当前未排入计划",
                }
            )
            continue
        last = max(assign, key=lambda a: a["end"])
        due = items[0].due
        slack = due - last["end"]
        if slack < 0:
            level, reason = "高", f"预计晚于交期 {abs(slack) / 60:.1f} 小时完成"
        elif slack < 4 * 60:
            level, reason = "中", f"仅剩 {slack / 60:.1f} 小时缓冲，插单或设备停机即可能拖期"
        else:
            level, reason = "低", f"交期缓冲 {slack / 60:.1f} 小时"

        # 找该订单占用最满的设备，作为解释依据
        load_by_machine = defaultdict(int)
        for a in assign:
            load_by_machine[a["machine_id"]] += a["end"] - a["start"]
        top_machine = max(load_by_machine.items(), key=lambda kv: kv[1])
        machine = problem.machines[top_machine[0]]
        risks.append(
            {
                "order_id": order_id,
                "order_code": items[0].order_code,
                "product": items[0].product_code,
                "qty": items[0].quantity,
                "due": calendar.to_datetime(due).strftime("%m-%d %H:%M"),
                "planned_end": calendar.to_datetime(last["end"]).strftime("%m-%d %H:%M"),
                "slack_hours": round(slack / 60, 1),
                "level": level,
                "reason": reason,
                "dominant_resource": machine.code,
                "dominant_minutes": top_machine[1],
            }
        )

    risks.sort(key=lambda r: ({"高": 0, "中": 1, "低": 2}[r["level"]], r.get("slack_hours", 0)))

    # 瓶颈设备排队估计（Kingman 近似）
    bottleneck = []
    for code, util in metrics["kpis"]["utilization"].items():
        avg_task = 90
        queue_hours = 0.0
        if util >= 0.999:
            queue_hours = 8.0
        elif util > 0.6:
            queue_hours = round((util / (1 - util)) * (avg_task / 60) / 2, 2)
        bottleneck.append(
            {
                "code": code,
                "utilization": util,
                "queue_hours": queue_hours,
                "level": "高" if util >= 0.9 else ("中" if util >= 0.75 else "低"),
            }
        )
    bottleneck.sort(key=lambda b: b["utilization"], reverse=True)

    # 技能缺口：某类工序只有 1 名合格人员
    gap = []
    for task in problem.tasks:
        if len(task.eligible_operators) <= 1:
            gap.append(
                {
                    "process_type": task.process_type,
                    "order_code": task.order_code,
                    "note": "仅 1 名合格人员，缺勤即工序停摆",
                }
            )
    unique_gap = {(g["process_type"], g["note"]): g for g in gap}

    suggestions = []
    if risks and risks[0]["level"] == "高":
        suggestions.append(f"优先处理高风险订单 {risks[0]['order_code']}，可拆批或调整瓶颈设备班次。")
    if bottleneck and bottleneck[0]["utilization"] >= 0.85:
        suggestions.append(f"{bottleneck[0]['code']} 负荷偏高，建议预留 10%~15% 产能作为插单缓冲。")
    if unique_gap:
        suggestions.append("存在单点技能依赖，建议安排跨岗培训或师徒带岗，形成第二合格人。")
    if not suggestions:
        suggestions.append("当前计划处于健康区间，保持每周滚动重排与日报工即可。")

    return {
        "risks": risks,
        "bottleneck": bottleneck[:5],
        "skill_gaps": list(unique_gap.values())[:5],
        "suggestions": suggestions,
    }
