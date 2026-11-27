"""CP-SAT 有限产能排产模型（OR-Tools）。

决策变量：每道工序的开工时间，以及“设备-人员”配对选择（可选区间）。
约束：
  1. 每道工序必须且只能选择一组满足设备能力/人员技能的组合；
  2. 同一设备、同一人员同一时刻只能执行一道工序（NoOverlap）；
  3. 同一订单的工序严格按工艺路线先后执行；
  4. 设备故障、人员请假等以不可用区间参与 NoOverlap；
  5. 已开工工序冻结，不参与重排。
目标：加权拖期 + 流程时间 + 完工跨度，权重由 AI 策略推荐给出。
"""

from __future__ import annotations

import time
from collections import defaultdict

try:  # OR-Tools 为可选依赖，缺失时自动降级到启发式
    from ortools.sat.python import cp_model
except Exception:  # pragma: no cover
    cp_model = None

from .model import Problem

DEFAULT_WEIGHTS = {"tardiness": 100, "flow": 1, "makespan": 2}


def cpsat_available() -> bool:
    return cp_model is not None


def solve_cpsat(problem: Problem, weights: dict | None = None, time_limit: float = 20.0,
                warm_start: bool = True) -> dict | None:
    if cp_model is None:
        return None
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    started = time.perf_counter()
    horizon = int(max(problem.horizon, 1))
    model = cp_model.CpModel()

    machine_intervals: dict[int, list] = defaultdict(list)
    operator_intervals: dict[int, list] = defaultdict(list)

    # 冻结工序作为固定区间占用资源
    for fixed in problem.fixed:
        iv = model.NewFixedSizeIntervalVar(int(fixed.start), int(fixed.duration), f"fx_{fixed.key}")
        machine_intervals[fixed.machine_id].append(iv)
        operator_intervals[fixed.operator_id].append(iv)

    starts, ends = {}, {}
    choice_vars = {}
    fixed_keys = {f.key for f in problem.fixed}
    for task in problem.tasks:
        if not task.pairs or task.key in fixed_keys:
            continue
        duration = max(1, int(task.duration))
        start = model.NewIntVar(int(task.min_start), max(int(task.min_start), horizon - duration), f"s_{task.key}")
        end = model.NewIntVar(duration, horizon, f"e_{task.key}")
        model.Add(end == start + duration)
        starts[task.key], ends[task.key] = start, end
        if problem.windows:
            ranges = []
            for win_start, win_end in problem.windows:
                if win_end - win_start < duration:
                    continue
                lo = max(win_start, task.min_start)
                hi = win_end - duration
                if hi >= lo:
                    ranges.append([int(lo), int(hi)])
            if ranges:
                model.AddLinearExpressionInDomain(start, cp_model.Domain.FromIntervals(ranges))
        choices = []
        for mid, wid in task.pairs:
            present = model.NewBoolVar(f"p_{task.key}_{mid}_{wid}")
            interval = model.NewOptionalIntervalVar(start, duration, end, present, f"i_{task.key}_{mid}_{wid}")
            machine_intervals[mid].append(interval)
            operator_intervals[wid].append(interval)
            choices.append(present)
            choice_vars[(task.key, mid, wid)] = present
        model.AddExactlyOne(choices)

    # 工序先后顺序
    by_order: dict[int, list] = defaultdict(list)
    for task in problem.tasks:
        if task.key in starts:
            by_order[task.order_id].append(task)
    for items in by_order.values():
        items.sort(key=lambda t: t.seq)
        for prev, cur in zip(items, items[1:]):
            model.Add(starts[cur.key] >= ends[prev.key])

    # 设备 / 人员不可用区间
    for mid, center in problem.machines.items():
        for idx, (begin, end) in enumerate(center.blocks):
            if end > begin:
                machine_intervals[mid].append(
                    model.NewFixedSizeIntervalVar(int(begin), int(end - begin), f"blk_m{mid}_{idx}")
                )
    for oid, operator in problem.operators.items():
        for idx, (begin, end) in enumerate(operator.blocks):
            if end > begin:
                operator_intervals[oid].append(
                    model.NewFixedSizeIntervalVar(int(begin), int(end - begin), f"blk_o{oid}_{idx}")
                )
    for mid, intervals in machine_intervals.items():
        if intervals:
            model.AddNoOverlap(intervals)
    for oid, intervals in operator_intervals.items():
        if intervals:
            model.AddNoOverlap(intervals)

    # 拖期
    tardiness_vars = []
    for oid, items in by_order.items():
        last = items[-1]
        due = last.due or horizon
        late = model.NewIntVar(0, horizon, f"late_{oid}")
        model.Add(late >= ends[last.key] - int(due))
        tardiness_vars.append((late, last.weight))

    if warm_start and problem.tasks:
        # 用启发式解作为初始提示，加速获得可行解
        try:
            from .heuristic import solve_heuristic

            hint = solve_heuristic(problem)
            for key, item in hint["assignments"].items():
                if key in starts and not item.get("frozen"):
                    model.AddHint(starts[key], int(item["start"]))
        except Exception:
            pass

    makespan = model.NewIntVar(0, horizon, "makespan")
    if ends:
        model.AddMaxEquality(makespan, list(ends.values()))
    else:
        model.Add(makespan == 0)

    objective_terms = [weights["tardiness"] * w * late for late, w in tardiness_vars]
    objective_terms.append(weights["makespan"] * makespan)
    objective_terms.extend(weights["flow"] * s for s in starts.values())
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 42
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "assignments": {},
            "unscheduled": [{"task": t.key, "reason": "约束求解无可行解"} for t in problem.tasks],
            "status": solver.StatusName(status),
            "solver": "cp-sat",
            "solve_seconds": round(time.perf_counter() - started, 3),
            "objective": {},
        }

    assignments: dict[str, dict] = {}
    for task in problem.tasks:
        if task.key not in starts:
            continue
        for mid, wid in task.pairs:
            present = solver.BooleanValue(choice_vars[(task.key, mid, wid)])
            if present:
                assignments[task.key] = {
                    "machine_id": mid,
                    "operator_id": wid,
                    "start": int(solver.Value(starts[task.key])),
                    "end": int(solver.Value(ends[task.key])),
                    "frozen": False,
                }
                break

    return {
        "assignments": assignments,
        "unscheduled": [],
        "status": solver.StatusName(status),
        "solver": "cp-sat",
        "solve_seconds": round(time.perf_counter() - started, 3),
        "objective": {"value": round(solver.ObjectiveValue(), 2), "bound": round(solver.BestObjectiveBound(), 2)},
    }
