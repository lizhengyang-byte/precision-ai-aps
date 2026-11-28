"""业务服务层：把数据库主数据翻译成排产问题，执行求解并落库版本。"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from . import models
from .ai.strategy import recommend_strategy
from .engine.calendar import ShopCalendar
from .engine.cpsat_solver import solve_cpsat
from .engine.heuristic import solve_heuristic
from .engine.metrics import compute_metrics
from .engine.model import FixedAssignment, Operator, Problem, Task, WorkCenter

PRIORITY_WEIGHT = {1: 5, 2: 15, 3: 40}
PRIORITY_LABEL = {1: "普通", 2: "较高", 3: "紧急"}
FREEZE_HORIZON_MINUTES = int(os.getenv("FREEZE_HORIZON_MINUTES", "120"))


def _event_blocks(db: Session, calendar: ShopCalendar, machines, workers):
    mblocks = defaultdict(list)
    wblocks = defaultdict(list)
    now = datetime.now()
    for event in db.query(models.EventLog).filter(models.EventLog.active.is_(True)).all():
        end = event.end or (now.replace(hour=20, minute=0) + timedelta(days=3))
        begin_wm, end_wm = calendar.block(event.start, end)
        payload = event.payload or {}
        if event.kind == "breakdown" and payload.get("machine_code"):
            mblocks[payload["machine_code"]].append((begin_wm, end_wm))
        if event.kind == "leave" and payload.get("worker_name"):
            wblocks[payload["worker_name"]].append((begin_wm, end_wm))
    return mblocks, wblocks


def build_problem(db: Session, now: datetime | None = None, freeze: bool = True,
                  version: models.ScheduleVersion | None = None,
                  freeze_horizon_minutes: int | None = None) -> tuple[Problem, ShopCalendar]:
    now = now or datetime.now()
    horizon_minutes = FREEZE_HORIZON_MINUTES if freeze_horizon_minutes is None else freeze_horizon_minutes
    calendar = ShopCalendar(start=now, days=21)
    horizon = calendar.total_minutes
    now_wm = 0

    event_mblocks, event_wblocks = _event_blocks(db, calendar, None, None)

    machines: dict[int, WorkCenter] = {}
    by_code: dict[str, int] = {}
    for m in db.query(models.Machine).all():
        blocks = list(event_mblocks.get(m.code, []))
        if m.status == "down":
            blocks.append((0, horizon))
        machines[m.id] = WorkCenter(
            id=m.id, code=m.code, name=m.name, machine_type=m.machine_type,
            precision_grade=m.precision_grade, efficiency=m.efficiency, blocks=blocks,
        )
        by_code[m.code] = m.id

    operators: dict[int, Operator] = {}
    operator_by_name: dict[str, int] = {}
    for w in db.query(models.Worker).all():
        blocks = list(event_wblocks.get(w.name, []))
        if not w.active:
            blocks.append((0, horizon))
        operators[w.id] = Operator(
            id=w.id, code=w.code, name=w.name, level=w.level,
            skills=dict(w.skills or {}), blocks=blocks,
        )
        operator_by_name[w.name] = w.id

    # 冻结：上一版本中已开工 / 已完工，以及即将开工（冻结窗口内）的工序
    fixed: list[FixedAssignment] = []
    fixed_ends: dict[str, int] = {}
    status_keep: dict[str, dict] = {}
    if freeze and version is not None:
        freeze_until = now + timedelta(minutes=horizon_minutes)
        for a in version.assignments:
            if a.status in ("doing", "done") or a.start <= freeze_until:
                start_wm = calendar.to_wm(a.start)
                dur = max(1, int((a.end - a.start).total_seconds() // 60))
                fixed.append(
                    FixedAssignment(key=a.task_key, machine_id=a.machine_id,
                                    operator_id=a.operator_id, start=start_wm, duration=dur)
                )
                fixed_ends[a.task_key] = start_wm + dur
                status_keep[a.task_key] = {"status": a.status, "good_qty": a.good_qty, "scrap_qty": a.scrap_qty}

    tasks: list[Task] = []
    warnings: list[str] = []
    orders = (
        db.query(models.Order)
        .options(joinedload(models.Order.product).joinedload(models.Product.routings))
        .filter(models.Order.status.in_(["open", "scheduled"]))
        .order_by(models.Order.due_date)
        .all()
    )
    for order in orders:
        product = order.product
        due = calendar.to_wm(order.due_date)
        release = max(now_wm, calendar.to_wm(order.release_at))
        for routing in sorted(product.routings, key=lambda r: r.seq):
            key = f"{order.code}-{routing.seq}"
            duration = int(round(routing.std_minutes * order.qty + routing.setup_minutes))
            duration = max(10, duration)
            eligible_m = [
                mid for mid, m in machines.items()
                if m.machine_type == routing.machine_type and m.precision_grade >= routing.min_precision
            ]
            eligible_w = [
                oid for oid, o in operators.items()
                if o.skills.get(routing.process_type, 0) >= routing.required_level
            ]
            if not eligible_m:
                warnings.append(f"{key} {routing.name}：无满足精度({routing.min_precision})的{routing.machine_type}设备")
            if not eligible_w:
                warnings.append(f"{key} {routing.name}：无技能等级≥{routing.required_level}的{routing.process_type}人员")
            min_start = release
            if routing.seq > 1:
                prev = f"{order.code}-{routing.seq - 1}"
                if prev in fixed_ends:
                    min_start = max(min_start, fixed_ends[prev])
            base_weight = PRIORITY_WEIGHT.get(order.priority, 10)
            # 已过交期的订单按超期时长自动加权，避免“老单越拖越没人管”
            aging = max(0, min(30, (now_wm - due) // 120)) if due < now_wm else 0
            tasks.append(
                Task(
                    key=key, order_id=order.id, order_code=order.code,
                    product_code=product.code, product_family=product.family,
                    seq=routing.seq, name=routing.name, process_type=routing.process_type,
                    duration=duration, quantity=order.qty, required_level=routing.required_level,
                    min_start=min_start, due=due,
                    priority=order.priority, weight=base_weight + aging,
                    eligible_machines=eligible_m, eligible_operators=eligible_w,
                    setup_minutes=routing.setup_minutes,
                )
            )

    problem = Problem(tasks=tasks, machines=machines, operators=operators,
                      horizon=horizon, fixed=fixed, windows=[(s, e) for s, e, _, _ in calendar.segments],
                      warnings=warnings, now=now_wm)
    problem.status_keep = status_keep  # type: ignore[attr-defined]
    return problem, calendar


def run_schedule(db: Session, strategy: str = "auto", time_limit: float = 20.0,
                 note: str = "", freeze: bool = True, parent_id: int | None = None) -> dict:
    latest = current_version(db)
    advice = None
    if strategy == "auto":
        preview_problem, _ = build_problem(db, freeze=False)
        advice = recommend_strategy(preview_problem)
        strategy = advice["strategy"]
        weights = advice["weights"]
    else:
        weights = None

    problem, calendar = build_problem(db, freeze=freeze, version=latest if freeze else None,
                                      freeze_horizon_minutes=FREEZE_HORIZON_MINUTES if freeze else 0)
    if strategy == "changeover":
        result = solve_heuristic(problem, group_families=True)
    else:
        result = solve_cpsat(problem, weights=weights, time_limit=time_limit)
        if not result or not result["assignments"]:
            fallback = solve_heuristic(problem)
            fallback["status"] = f'{result["status"] if result else "NO_SOLVER"}→HEURISTIC'
            result = fallback

    version = models.ScheduleVersion(
        strategy=strategy,
        solver=result["solver"],
        status=result["status"],
        time_limit=time_limit,
        solve_seconds=result.get("solve_seconds", 0),
        horizon_start=calendar.start,
        note=note,
        parent_id=parent_id if parent_id is not None else (latest.id if latest else None),
        objective=result.get("objective", {}),
    )
    db.add(version)
    db.flush()

    task_map = {t.key: t for t in problem.tasks}
    machine_map = {m.id: m for m in problem.machines.values()}
    operator_map = {o.id: o for o in problem.operators.values()}
    keep = getattr(problem, "status_keep", {})

    for key, a in result["assignments"].items():
        task = task_map.get(key)
        if task is None:
            continue
        start_dt = calendar.to_datetime(a["start"])
        end_dt = calendar.to_datetime(a["end"])
        extra = keep.get(key, {})
        db.add(
            models.Assignment(
                version_id=version.id,
                order_id=task.order_id,
                task_key=key,
                seq=task.seq,
                op_name=task.name,
                process_type=task.process_type,
                order_code=task.order_code,
                product_code=task.product_code,
                machine_code=machine_map[a["machine_id"]].code,
                operator_code=operator_map[a["operator_id"]].name,
                machine_id=a["machine_id"],
                operator_id=a["operator_id"],
                start=start_dt,
                end=end_dt,
                quantity=task.quantity,
                priority=task.priority,
                status=extra.get("status", "planned"),
                frozen=bool(a.get("frozen")),
                good_qty=extra.get("good_qty", 0),
                scrap_qty=extra.get("scrap_qty", 0),
            )
        )
    db.flush()

    metrics = compute_metrics(problem, result["assignments"])
    if result["assignments"]:
        first_wm = min(a["start"] for a in result["assignments"].values())
        last_wm = max(a["end"] for a in result["assignments"].values())
        plan_start = calendar.to_datetime(first_wm)
        plan_end = calendar.to_datetime(last_wm)
        calendar_hours = (plan_end - plan_start).total_seconds() / 3600
        metrics["kpis"]["plan_start"] = plan_start.isoformat(timespec="minutes")
        metrics["kpis"]["plan_end"] = plan_end.isoformat(timespec="minutes")
        metrics["kpis"]["makespan_calendar_hours"] = round(calendar_hours, 1)
    version.metrics = {
        "kpis": metrics["kpis"],
        "orders": metrics["orders"],
        "tardy_orders": metrics["tardy_orders"],
        "unscheduled": result.get("unscheduled", []),
        "warnings": problem.warnings[:20],
        "strategy_advice": advice,
        "freeze_horizon_minutes": FREEZE_HORIZON_MINUTES if freeze else 0,
    }
    version.status = "ok"
    db.commit()
    db.refresh(version)
    return serialize_version(version)


def current_version(db: Session) -> models.ScheduleVersion | None:
    from sqlalchemy.orm import selectinload

    return (
        db.query(models.ScheduleVersion)
        .options(selectinload(models.ScheduleVersion.assignments))
        .order_by(models.ScheduleVersion.id.desc())
        .first()
    )


def get_version(db: Session, version_id: int) -> models.ScheduleVersion | None:
    from sqlalchemy.orm import selectinload

    return (
        db.query(models.ScheduleVersion)
        .options(selectinload(models.ScheduleVersion.assignments))
        .filter(models.ScheduleVersion.id == version_id)
        .first()
    )


def serialize_assignment(a: models.Assignment) -> dict:
    return {
        "id": a.id,
        "task_key": a.task_key,
        "order_code": a.order_code,
        "product_code": a.product_code,
        "seq": a.seq,
        "op_name": a.op_name,
        "process_type": a.process_type,
        "machine_code": a.machine_code,
        "operator_code": a.operator_code,
        "start": a.start.isoformat(timespec="minutes"),
        "end": a.end.isoformat(timespec="minutes"),
        "quantity": a.quantity,
        "priority": a.priority,
        "status": a.status,
        "frozen": a.frozen,
        "good_qty": a.good_qty,
        "scrap_qty": a.scrap_qty,
    }


def serialize_version(version: models.ScheduleVersion | None, with_assignments: bool = True) -> dict:
    if version is None:
        return {}
    payload = {
        "id": version.id,
        "created_at": version.created_at.isoformat(timespec="seconds"),
        "strategy": version.strategy,
        "solver": version.solver,
        "status": version.status,
        "solve_seconds": version.solve_seconds,
        "horizon_start": version.horizon_start.isoformat(timespec="minutes"),
        "metrics": version.metrics,
        "objective": version.objective,
        "note": version.note,
        "parent_id": version.parent_id,
    }
    if with_assignments:
        payload["assignments"] = [serialize_assignment(a) for a in sorted(version.assignments, key=lambda x: (x.start, x.machine_code))]
    return payload


def version_diff(db: Session, base_id: int, target_id: int) -> dict:
    base = get_version(db, base_id)
    target = get_version(db, target_id)
    if not base or not target:
        return {}
    base_map = {a.task_key: a for a in base.assignments}
    target_map = {a.task_key: a for a in target.assignments}
    added, removed, changed = [], [], []
    for key, a in target_map.items():
        if key not in base_map:
            added.append(serialize_assignment(a))
            continue
        b = base_map[key]
        if (a.machine_code != b.machine_code or a.operator_code != b.operator_code
                or abs((a.start - b.start).total_seconds()) >= 60):
            changed.append(
                {
                    "task_key": key,
                    "order_code": a.order_code,
                    "op_name": a.op_name,
                    "before": {"machine": b.machine_code, "operator": b.operator_code,
                               "start": b.start.isoformat(timespec="minutes")},
                    "after": {"machine": a.machine_code, "operator": a.operator_code,
                              "start": a.start.isoformat(timespec="minutes")},
                }
            )
    for key, b in base_map.items():
        if key not in target_map:
            removed.append(serialize_assignment(b))
    return {
        "base": base_id, "target": target_id,
        "added": added, "removed": removed, "changed": changed,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed),
                    "kept": len(target_map) - len(added) - len(changed)},
    }


def latest_advice(db: Session) -> dict:
    problem, _ = build_problem(db, freeze=False)
    return recommend_strategy(problem)


def progress_summary(db: Session, version: models.ScheduleVersion) -> dict:
    total = len(version.assignments)
    done = sum(1 for a in version.assignments if a.status == "done")
    doing = sum(1 for a in version.assignments if a.status == "doing")
    good = sum(a.good_qty for a in version.assignments)
    scrap = sum(a.scrap_qty for a in version.assignments)
    return {
        "tasks_total": total,
        "tasks_done": done,
        "tasks_doing": doing,
        "progress_rate": round(done / total, 3) if total else 0,
        "good_qty": good,
        "scrap_qty": scrap,
        "scrap_rate": round(scrap / (good + scrap), 3) if good + scrap else 0,
    }
