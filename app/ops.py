"""生产运营能力：审计、计划审批发布、回滚、派工单、OEE 与产能负荷。

这些能力不参与求解，但决定系统能不能被车间真正用起来：
- 计划必须经过“提交—审批—发布”才能下发，避免任何一次重排直接改现场；
- 发布即冻结历史：旧版本标记 archived，新版本成为执行基线；
- 回滚不篡改历史版本，而是克隆出一个新版本并留痕；
- 所有写操作落审计表，可追溯到人、时间、对象和参数。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models, services
from .models_ext import AuditLog, PlanApproval, User

SHIFT_MINUTES = 12 * 60


# ---------------------------------------------------------------- 审计

def audit(db: Session, user: User | None, action: str, target: str = "", detail: dict | None = None, ip: str = "") -> None:
    db.add(
        AuditLog(
            actor=getattr(user, "username", "system") or "system",
            role=getattr(user, "role", "") or "",
            action=action,
            target=str(target),
            detail=detail or {},
            ip=ip,
        )
    )
    db.commit()


def recent_audits(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 200)).all()
    return [
        {
            "id": r.id,
            "actor": r.actor,
            "role": r.role,
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "ip": r.ip,
            "created_at": r.created_at.isoformat(timespec="seconds"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------- 计划生命周期
_ALLOWED = {
    "ok": ["submitted"],
    "submitted": ["approved", "rejected"],
    "approved": ["published"],
    "rejected": ["submitted"],
    "published": ["archived"],
}


def _version_or_404(db: Session, version_id: int) -> models.ScheduleVersion:
    version = services.get_version(db, version_id)
    if not version:
        raise HTTPException(404, "排产版本不存在")
    return version


def _transition(db: Session, version: models.ScheduleVersion, to: str, user: User, note: str, ip: str) -> dict:
    current = version.status or "ok"
    if to not in _ALLOWED.get(current, []):
        raise HTTPException(409, f"当前状态 {current} 不允许变更为 {to}")
    version.status = to
    db.add(PlanApproval(version_id=version.id, status=to, actor=user.username, note=note))
    db.commit()
    audit(db, user, f"plan.{to}", f"version#{version.id}", {"from": current, "note": note}, ip)
    return services.serialize_version(version, with_assignments=False)


def submit(db: Session, version_id: int, user: User, note: str = "", ip: str = "") -> dict:
    return _transition(db, _version_or_404(db, version_id), "submitted", user, note, ip)


def approve(db: Session, version_id: int, user: User, note: str = "", ip: str = "") -> dict:
    return _transition(db, _version_or_404(db, version_id), "approved", user, note, ip)


def reject(db: Session, version_id: int, user: User, note: str = "", ip: str = "") -> dict:
    return _transition(db, _version_or_404(db, version_id), "rejected", user, note, ip)


def publish(db: Session, version_id: int, user: User, note: str = "", ip: str = "") -> dict:
    version = _version_or_404(db, version_id)
    if version.status not in ("approved", "submitted"):
        raise HTTPException(409, f"版本状态 {version.status} 不可发布，需先审批")
    if version.status == "submitted" and user.role != "admin":
        raise HTTPException(403, "提交状态的计划需计划主管审批后才能发布")
    for other in db.query(models.ScheduleVersion).filter(models.ScheduleVersion.status == "published").all():
        other.status = "archived"
    version.status = "published"
    db.add(PlanApproval(version_id=version.id, status="published", actor=user.username, note=note))
    db.commit()
    audit(db, user, "plan.published", f"version#{version.id}", {"note": note}, ip)
    return services.serialize_version(version, with_assignments=False)


def rollback(db: Session, version_id: int, user: User, note: str = "", ip: str = "") -> dict:
    """把历史版本克隆为新的执行版本，历史记录保持不变。"""
    target = _version_or_404(db, version_id)
    current = services.current_version(db)
    clone = models.ScheduleVersion(
        strategy=target.strategy,
        solver=target.solver,
        status="published",
        time_limit=target.time_limit,
        solve_seconds=0,
        horizon_start=target.horizon_start,
        note=f"回滚至版本 #{target.id}：{note}".strip("："),
        parent_id=current.id if current else None,
        objective=target.objective,
    )
    db.add(clone)
    db.flush()
    for a in target.assignments:
        db.add(
            models.Assignment(
                version_id=clone.id,
                order_id=a.order_id,
                task_key=a.task_key,
                seq=a.seq,
                op_name=a.op_name,
                process_type=a.process_type,
                order_code=a.order_code,
                product_code=a.product_code,
                machine_code=a.machine_code,
                operator_code=a.operator_code,
                machine_id=a.machine_id,
                operator_id=a.operator_id,
                start=a.start,
                end=a.end,
                quantity=a.quantity,
                priority=a.priority,
                status=a.status,
                frozen=True,
                good_qty=a.good_qty,
                scrap_qty=a.scrap_qty,
            )
        )
    clone.metrics = target.metrics
    for other in db.query(models.ScheduleVersion).filter(models.ScheduleVersion.status == "published").all():
        if other.id != clone.id:
            other.status = "archived"
    db.commit()
    audit(db, user, "plan.rolled_back", f"version#{clone.id}", {"from": target.id, "note": note}, ip)
    return services.serialize_version(clone, with_assignments=False)


def plan_state(db: Session) -> dict:
    version = services.current_version(db)
    if not version:
        return {"version": {}, "history": []}
    approvals = (
        db.query(PlanApproval)
        .filter(PlanApproval.version_id == version.id)
        .order_by(PlanApproval.id.desc())
        .all()
    )
    return {
        "version": services.serialize_version(version, with_assignments=False),
        "history": [
            {"status": a.status, "actor": a.actor, "note": a.note, "created_at": a.created_at.isoformat(timespec="seconds")}
            for a in approvals
        ],
    }


# ---------------------------------------------------------------- 派工单

def dispatch(db: Session, day: str | None = None) -> dict:
    version = services.current_version(db)
    if not version:
        return {"date": day, "machines": [], "summary": {"tasks": 0, "overdue": 0}}
    target = datetime.fromisoformat(day).date() if day else datetime.now().date()
    families: dict[str, str] = {}
    for a in version.assignments:
        families[a.order_code] = a.product_code

    rows = [a for a in version.assignments if a.start.date() <= target <= a.end.date()]
    by_machine: dict[str, list] = defaultdict(list)
    for a in sorted(rows, key=lambda x: (x.machine_code, x.start)):
        by_machine[a.machine_code].append(a)

    cards = []
    for machine, items in sorted(by_machine.items()):
        last_family = None
        for a in items:
            setup = last_family is not None and last_family != families.get(a.order_code)
            last_family = families.get(a.order_code)
            cards.append(
                {
                    "id": a.id,
                    "machine": machine,
                    "order_code": a.order_code,
                    "product_code": a.product_code,
                    "op_name": a.op_name,
                    "seq": a.seq,
                    "operator": a.operator_code,
                    "quantity": a.quantity,
                    "good_qty": a.good_qty,
                    "scrap_qty": a.scrap_qty,
                    "start": a.start.isoformat(timespec="minutes"),
                    "end": a.end.isoformat(timespec="minutes"),
                    "status": a.status,
                    "frozen": a.frozen,
                    "changeover": setup,
                    "priority": a.priority,
                }
            )
    overdue = sum(1 for a in cards if a["status"] != "done" and datetime.fromisoformat(a["end"]) < datetime.now())
    return {
        "date": target.isoformat(),
        "version_id": version.id,
        "machines": sorted(by_machine),
        "cards": cards,
        "summary": {"tasks": len(cards), "overdue": overdue, "changeovers": sum(1 for c in cards if c["changeover"])},
    }


# ---------------------------------------------------------------- OEE 与产能

def _available_minutes(start: datetime, end: datetime) -> float:
    """计划区间内的有效班次分钟：周日休息，其余每天按 12 小时班次计。"""
    if end <= start:
        return 0.0
    total = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        nxt = day + timedelta(days=1)
        if day.weekday() != 6:  # 周日休
            overlap_start = max(start, day)
            overlap_end = min(end, nxt)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds() / 60
        day = nxt
    return total


def oee(db: Session) -> dict:
    version = services.current_version(db)
    if not version:
        return {"overall": {}, "machines": [], "basis": "无排产版本"}
    kpis = version.metrics.get("kpis", {})
    plan_start = datetime.fromisoformat(kpis["plan_start"]) if kpis.get("plan_start") else None
    plan_end = datetime.fromisoformat(kpis["plan_end"]) if kpis.get("plan_end") else None
    if not plan_start or not plan_end:
        return {"overall": {}, "machines": [], "basis": "缺少计划起止时间"}

    window = _available_minutes(plan_start, plan_end) or 1.0
    downtime: dict[str, float] = defaultdict(float)
    for e in db.query(models.EventLog).filter(models.EventLog.kind == "breakdown").all():
        code = (e.payload or {}).get("machine_code")
        end = e.end or datetime.now()
        if code and end > plan_start and e.start < plan_end:
            overlap = (min(end, plan_end) - max(e.start, plan_start)).total_seconds() / 60
            downtime[code] += max(0.0, overlap)

    rows = []
    for machine in sorted({a.machine_code for a in version.assignments}):
        items = [a for a in version.assignments if a.machine_code == machine]
        scheduled = sum((a.end - a.start).total_seconds() / 60 for a in items)
        good = sum(a.good_qty for a in items)
        scrap = sum(a.scrap_qty for a in items)
        quality = good / (good + scrap) if (good + scrap) else 1.0
        availability = max(0.0, 1 - downtime.get(machine, 0) / window)
        performance = min(1.0, scheduled / window)
        rows.append(
            {
                "machine": machine,
                "availability": round(availability, 3),
                "performance": round(performance, 3),
                "quality": round(quality, 3),
                "oee": round(availability * performance * quality, 3),
                "scheduled_hours": round(scheduled / 60, 1),
                "downtime_hours": round(downtime.get(machine, 0) / 60, 1),
                "good_qty": good,
                "scrap_qty": scrap,
            }
        )
    all_good = sum(a.good_qty for a in version.assignments)
    all_scrap = sum(a.scrap_qty for a in version.assignments)
    quality_all = all_good / (all_good + all_scrap) if (all_good + all_scrap) else 1.0
    availability_all = max(0.0, 1 - sum(downtime.values()) / (window * max(len(rows), 1)))
    performance_all = min(1.0, sum((a.end - a.start).total_seconds() / 60 for a in version.assignments) / (window * max(len(rows), 1)))
    return {
        "overall": {
            "availability": round(availability_all, 3),
            "performance": round(performance_all, 3),
            "quality": round(quality_all, 3),
            "oee": round(availability_all * performance_all * quality_all, 3),
        },
        "machines": rows,
        "basis": "可用率=1-故障停机占有效班次比；性能率=计划占用/有效班次；良品率=良品/(良品+报废)。有效班次按每日 12 小时、周日休息计，待接入实际节拍后可替换为标准工时口径。",
    }


def capacity_forecast(db: Session, days: int = 7) -> dict:
    version = services.current_version(db)
    if not version:
        return {"days": [], "summary": {}}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    machines = sorted({a.machine_code for a in version.assignments})
    out = []
    for i in range(max(1, min(days, 14))):
        day = today + timedelta(days=i)
        per = {}
        for m in machines:
            busy = 0.0
            for a in version.assignments:
                if a.machine_code != m:
                    continue
                start = max(a.start, day)
                end = min(a.end, day + timedelta(days=1))
                if end > start:
                    busy += (end - start).total_seconds() / 60
            per[m] = round(min(1.0, busy / SHIFT_MINUTES), 3)
        out.append({"date": day.date().isoformat(), "utilization": per})
    return {
        "days": out,
        "summary": {
            "machines": machines,
            "peak": max((max(d["utilization"].values()) for d in out), default=0),
            "basis": f"日可用产能按 {SHIFT_MINUTES // 60} 小时班次估算",
        },
    }
