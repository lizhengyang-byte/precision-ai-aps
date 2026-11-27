"""排产结果指标计算：交期达成、拖期、设备/人员负荷、瓶颈识别。"""

from __future__ import annotations

from collections import defaultdict

from .model import Problem


def compute_metrics(problem: Problem, assignments: dict[str, dict]) -> dict:
    tasks = {t.key: t for t in problem.tasks}
    order_last: dict[int, dict] = {}
    machine_busy: dict[int, int] = defaultdict(int)
    operator_busy: dict[int, int] = defaultdict(int)

    for key, a in assignments.items():
        task = tasks.get(key)
        if task is None:
            # 冻结工序也需要计入负荷，这里通过 fixed 列表回填
            for f in problem.fixed:
                if f.key == key:
                    machine_busy[f.machine_id] += f.duration
                    operator_busy[f.operator_id] += f.duration
            continue
        duration = a["end"] - a["start"]
        machine_busy[a["machine_id"]] += duration
        operator_busy[a["operator_id"]] += duration
        cur = order_last.get(task.order_id)
        if cur is None or a["end"] > cur["end"]:
            order_last[task.order_id] = {
                "order_id": task.order_id,
                "order_code": task.order_code,
                "due": task.due,
                "end": a["end"],
                "priority": task.priority,
                "weight": task.weight,
            }

    orders = []
    tardy = []
    for info in order_last.values():
        late = max(0, info["end"] - info["due"]) if info["due"] else 0
        info["tardiness"] = late
        info["on_time"] = late == 0
        orders.append(info)
        if late:
            tardy.append(info)

    makespan = max((a["end"] for a in assignments.values()), default=0)
    scheduled = len([k for k in assignments if k in tasks])
    total = len(problem.tasks)

    utilization = {}
    for mid, center in problem.machines.items():
        available = max(makespan, 1)
        utilization[center.code] = round(machine_busy.get(mid, 0) / available, 3)
    operator_util = {
        op.code: round(operator_busy.get(oid, 0) / max(makespan, 1), 3)
        for oid, op in problem.operators.items()
    }

    bottleneck = max(utilization.items(), key=lambda kv: kv[1]) if utilization else ("-", 0)
    on_time = len(orders) - len(tardy)
    kpis = {
        "orders_total": len(orders),
        "orders_on_time": on_time,
        "orders_late": len(tardy),
        "on_time_rate": round(on_time / len(orders), 3) if orders else 1.0,
        "avg_tardiness_min": round(sum(o["tardiness"] for o in orders) / len(orders), 1) if orders else 0,
        "max_tardiness_min": max((o["tardiness"] for o in orders), default=0),
        "makespan_min": makespan,
        "makespan_hours": round(makespan / 60, 2),
        "scheduled_tasks": scheduled,
        "total_tasks": total,
        "utilization": utilization,
        "operator_utilization": operator_util,
        "bottleneck": {"code": bottleneck[0], "utilization": bottleneck[1]},
    }
    return {"kpis": kpis, "orders": orders, "tardy_orders": tardy}
