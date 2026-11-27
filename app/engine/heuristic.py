"""快速启发式排产（列表调度）：作为 CP-SAT 的兜底，也用于“排产预演”。

规则：按交期/优先级依次取订单，沿工艺路线逐道工序，选择最早可完工的
“设备 + 人员”组合。支持按产品族成组投产，以减少换型次数。
"""

from __future__ import annotations

import time
from collections import defaultdict

from .model import Problem


def _free_slot(blocks: list[tuple[int, int]], earliest: int, duration: int) -> int:
    """在一串不可用区间中找到 >= earliest 且长度足够的空闲起点。"""
    t = max(0, earliest)
    for begin, end in sorted(blocks):
        if end <= t:
            continue
        if t + duration <= begin:
            return t
        if begin <= t < end:
            t = end
    return t


def _align_window(windows: list[tuple[int, int]], start: int, duration: int) -> int:
    """把开工时间推到能完整容纳该工序的班次内（不跨班、不被夜班打断）。"""
    if not windows:
        return start
    for win_start, win_end in windows:
        if win_end - win_start < duration:
            continue
        if start < win_start:
            return win_start
        if start <= win_end - duration:
            return start
    return start


def solve_heuristic(problem: Problem, group_families: bool = False) -> dict:
    started = time.perf_counter()
    machine_blocks: dict[int, list[tuple[int, int]]] = defaultdict(list)
    operator_blocks: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for m in problem.machines.values():
        machine_blocks[m.id].extend(m.blocks)
    for o in problem.operators.values():
        operator_blocks[o.id].extend(o.blocks)

    assignments: dict[str, dict] = {}
    unscheduled: list[dict] = []

    # 冻结工序先占坑
    for fixed in problem.fixed:
        machine_blocks[fixed.machine_id].append((fixed.start, fixed.end))
        operator_blocks[fixed.operator_id].append((fixed.start, fixed.end))
        assignments[fixed.key] = {
            "machine_id": fixed.machine_id,
            "operator_id": fixed.operator_id,
            "start": fixed.start,
            "end": fixed.end,
            "frozen": True,
        }

    tasks_by_order: dict[int, list] = defaultdict(list)
    for t in problem.tasks:
        tasks_by_order[t.order_id].append(t)
    for items in tasks_by_order.values():
        items.sort(key=lambda x: x.seq)

    order_ids = list(tasks_by_order)
    if group_families:
        order_ids.sort(key=lambda oid: (tasks_by_order[oid][0].product_family, tasks_by_order[oid][0].due))
    else:
        order_ids.sort(key=lambda oid: (tasks_by_order[oid][0].due, -tasks_by_order[oid][0].priority))

    fixed_keys = {f.key for f in problem.fixed}
    fixed_end_by_key = {f.key: f.end for f in problem.fixed}

    for oid in order_ids:
        chain_end = max(
            (fixed_end_by_key.get(t.key, 0) for t in tasks_by_order[oid] if t.key in fixed_keys),
            default=0,
        )
        for task in tasks_by_order[oid]:
            if task.key in fixed_keys:
                chain_end = max(chain_end, fixed_end_by_key[task.key])
                continue
            if not task.pairs:
                unscheduled.append({"task": task.key, "reason": "没有同时满足设备能力与人员技能的可用组合"})
                continue
            earliest = max(task.min_start, chain_end)
            best = None
            for mid, wid in task.pairs:
                cand = earliest
                for _ in range(6):
                    m_start = _free_slot(machine_blocks[mid], cand, task.duration)
                    o_start = _free_slot(operator_blocks[wid], m_start, task.duration)
                    nxt = _align_window(problem.windows, max(m_start, o_start), task.duration)
                    if nxt == cand:
                        break
                    cand = nxt
                finish = cand + task.duration
                load = len(machine_blocks[mid])
                score = (finish, load, mid, wid)
                if best is None or score < best[0]:
                    best = (score, mid, wid, cand, finish)
            _, mid, wid, start, finish = best
            machine_blocks[mid].append((start, finish))
            operator_blocks[wid].append((start, finish))
            assignments[task.key] = {
                "machine_id": mid,
                "operator_id": wid,
                "start": start,
                "end": finish,
                "frozen": False,
            }
            chain_end = finish

    elapsed = time.perf_counter() - started
    return {
        "assignments": assignments,
        "unscheduled": unscheduled,
        "status": "HEURISTIC",
        "solver": "list-scheduling",
        "solve_seconds": round(elapsed, 3),
        "objective": {},
    }
