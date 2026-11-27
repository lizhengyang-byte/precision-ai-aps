"""AI 决策层之一：排产策略推荐。

思路：先用启发式做一次“排产预演”，提取订单紧迫度、设备瓶颈、产品族
混杂度等特征，再用可解释的评分规则选出策略与目标权重。规则结论都可以
回溯到具体数字，避免“黑箱调参”。
"""

from __future__ import annotations

from collections import Counter

from ..engine.heuristic import solve_heuristic
from ..engine.metrics import compute_metrics
from ..engine.model import Problem

STRATEGIES = {
    "balanced": {
        "label": "均衡排产",
        "desc": "在交期、在制周期与设备负荷之间取平衡，适合订单结构稳定的常态生产。",
        "weights": {"tardiness": 100, "flow": 1, "makespan": 2},
    },
    "due_date": {
        "label": "交期优先",
        "desc": "以加权拖期最小为目标，优先保障紧急订单与关键客户交付。",
        "weights": {"tardiness": 250, "flow": 1, "makespan": 1},
    },
    "efficiency": {
        "label": "产能效率优先",
        "desc": "压缩完工跨度、提高瓶颈设备连续利用率，适合产能紧张、订单交期宽松时。",
        "weights": {"tardiness": 100, "flow": 2, "makespan": 30},
    },
    "changeover": {
        "label": "换型最少",
        "desc": "按产品族成组投产，减少装夹与程序切换，适合多品种小批量场景。",
        "weights": {"tardiness": 100, "flow": 2, "makespan": 4},
    },
}


def recommend_strategy(problem: Problem) -> dict:
    preview = solve_heuristic(problem, group_families=False)
    metrics = compute_metrics(problem, preview["assignments"])
    kpis = metrics["kpis"]

    late_ratio = kpis["orders_late"] / max(kpis["orders_total"], 1)
    bottleneck_util = kpis["bottleneck"]["utilization"]
    families = Counter(t.product_family for t in problem.tasks)
    avg_qty = sum(t.quantity for t in problem.tasks) / max(len(problem.tasks), 1)

    urgent = 0
    for order in metrics["orders"]:
        slack = order["due"] - order["end"]
        if order["priority"] >= 3 and slack < 8 * 60:
            urgent += 1

    factors = [
        {
            "name": "加权拖期风险",
            "value": f"{kpis['orders_late']}/{kpis['orders_total']} 单存在拖期",
            "score": round(late_ratio, 3),
            "reading": "预演中交期无法满足的订单占比",
        },
        {
            "name": "瓶颈设备负荷",
            "value": f"{kpis['bottleneck']['code']} 利用率 {bottleneck_util:.0%}",
            "score": bottleneck_util,
            "reading": "最忙设备的占用率，超过 90% 即进入排队恶化区",
        },
        {
            "name": "品种混杂度",
            "value": f"{len(families)} 个产品族 / 平均批量 {avg_qty:.0f} 件",
            "score": round(len(families) / 5, 3),
            "reading": "产品族越多、批量越小，换型损失越敏感",
        },
        {
            "name": "紧急订单",
            "value": f"{urgent} 单（优先级≥3 且余量<8h）",
            "score": urgent,
            "reading": "需要优先插队保护的关键订单数量",
        },
    ]

    if late_ratio >= 0.25:
        chosen = "due_date"
        reason = (
            f"预演有 {kpis['orders_late']} 张订单超期（占比 {late_ratio:.0%}），"
            f"瓶颈设备 {kpis['bottleneck']['code']} 利用率已达 {bottleneck_util:.0%}，"
            "此时继续追求设备连续运转会放大拖期，应切换为交期优先、按优先级加权保护关键订单。"
        )
        confidence = min(0.95, 0.6 + late_ratio)
    elif bottleneck_util >= 0.9:
        chosen = "efficiency"
        reason = (
            f"{kpis['bottleneck']['code']} 利用率 {bottleneck_util:.0%}，已成为节拍瓶颈；"
            "交期风险可控，优先压缩完工跨度、减少瓶颈设备的空转与换型等待。"
        )
        confidence = 0.8
    elif len(families) >= 4 and avg_qty <= 80:
        chosen = "changeover"
        reason = (
            f"当前 {len(families)} 个产品族、平均批量仅 {avg_qty:.0f} 件，"
            "换型与装夹时间占比偏高，按族成组投产可显著减少切换次数。"
        )
        confidence = 0.75
    else:
        chosen = "balanced"
        reason = (
            f"拖期占比 {late_ratio:.0%}、瓶颈利用率 {bottleneck_util:.0%}，"
            "处于健康区间，采用均衡策略兼顾交期与在制周期。"
        )
        confidence = 0.7

    meta = STRATEGIES[chosen]
    return {
        "strategy": chosen,
        "label": meta["label"],
        "description": meta["desc"],
        "weights": meta["weights"],
        "confidence": round(confidence, 2),
        "rationale": reason,
        "factors": factors,
        "preview": {
            "orders_late": kpis["orders_late"],
            "on_time_rate": kpis["on_time_rate"],
            "makespan_hours": kpis["makespan_hours"],
            "bottleneck": kpis["bottleneck"],
        },
        "alternatives": [
            {"strategy": key, "label": value["label"], "when": value["desc"]}
            for key, value in STRATEGIES.items()
            if key != chosen
        ],
    }
