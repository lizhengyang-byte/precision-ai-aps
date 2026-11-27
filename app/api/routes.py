"""REST API：主数据、排产、动态重排、报工、AI 助手。"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, services
from ..ai import llm
from ..ai.insight import risk_radar
from ..ai.nlu import parse_instruction
from ..ai.strategy import STRATEGIES, recommend_strategy
from ..config import DEFAULT_SOLVE_SECONDS
from ..database import get_db
from ..engine.calendar import ShopCalendar

router = APIRouter(prefix="/api")


def _order_code(db: Session, code: str) -> models.Order | None:
    return db.query(models.Order).filter(models.Order.code == code.upper()).first()


class OrderIn(BaseModel):
    product_code: str
    qty: int = Field(gt=0)
    due_date: str
    priority: int = Field(default=2, ge=1, le=3)
    customer: str = ""
    note: str = ""


class OrderPatch(BaseModel):
    qty: int | None = None
    due_date: str | None = None
    priority: int | None = None
    customer: str | None = None


class SolveIn(BaseModel):
    strategy: str = "auto"
    time_limit: float = DEFAULT_SOLVE_SECONDS
    note: str = ""
    freeze: bool = True


class EventIn(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    message: str = ""
    duration_minutes: int | None = None
    auto_reschedule: bool = True


class ReportIn(BaseModel):
    assignment_id: int
    good_qty: int = Field(ge=0)
    scrap_qty: int = Field(default=0, ge=0)
    status: str | None = None
    note: str = ""


class ChatIn(BaseModel):
    message: str
    execute: bool = True


@router.get("/meta")
def meta():
    return {
        "name": "精密加工 AI 智能排产与进度管控系统",
        "strategies": [
            {"key": k, "label": v["label"], "description": v["desc"]} for k, v in STRATEGIES.items()
        ],
        "capabilities": ["CP-SAT 有限产能排产", "AI 策略推荐", "交付风险雷达", "自然语言调度", "事件驱动重排", "报工与进度看板"],
    }


@router.get("/master")
def master(db: Session = Depends(get_db)):
    machines = [
        {"code": m.code, "name": m.name, "machine_type": m.machine_type,
         "precision_grade": m.precision_grade, "status": m.status, "efficiency": m.efficiency}
        for m in db.query(models.Machine).order_by(models.Machine.code).all()
    ]
    workers = [
        {"code": w.code, "name": w.name, "level": w.level, "skills": w.skills, "shift": w.shift}
        for w in db.query(models.Worker).order_by(models.Worker.code).all()
    ]
    products = [
        {"code": p.code, "name": p.name, "material": p.material, "tolerance_um": p.tolerance_um,
         "family": p.family,
         "routings": [
             {"seq": r.seq, "name": r.name, "process_type": r.process_type,
              "std_minutes": r.std_minutes, "setup_minutes": r.setup_minutes,
              "required_level": r.required_level, "machine_type": r.machine_type,
              "min_precision": r.min_precision}
             for r in sorted(p.routings, key=lambda x: x.seq)
         ]}
        for p in db.query(models.Product).all()
    ]
    orders = [
        {"id": o.id, "code": o.code, "product_code": o.product.code, "product_name": o.product.name,
         "qty": o.qty, "due_date": o.due_date.isoformat(timespec="minutes"),
         "priority": o.priority, "customer": o.customer, "status": o.status,
         "release_at": o.release_at.isoformat(timespec="minutes")}
        for o in db.query(models.Order).filter(models.Order.status != "cancelled").order_by(models.Order.due_date).all()
    ]
    return {"machines": machines, "workers": workers, "products": products, "orders": orders}


@router.post("/orders")
def create_order(payload: OrderIn, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.code == payload.product_code).first()
    if not product:
        raise HTTPException(404, "产品不存在")
    code = f"PO{datetime.now().strftime('%Y%m%d')}{db.query(models.Order).count() + 1:03d}"
    order = models.Order(
        code=code, product_id=product.id, qty=payload.qty,
        due_date=datetime.fromisoformat(payload.due_date),
        priority=payload.priority, customer=payload.customer, note=payload.note,
        release_at=datetime.now(), status="open",
    )
    db.add(order)
    db.commit()
    return {"code": order.code}


@router.patch("/orders/{code}")
def patch_order(code: str, payload: OrderPatch, db: Session = Depends(get_db)):
    order = _order_code(db, code)
    if not order:
        raise HTTPException(404, "订单不存在")
    if payload.qty is not None:
        order.qty = payload.qty
    if payload.due_date is not None:
        order.due_date = datetime.fromisoformat(payload.due_date)
    if payload.priority is not None:
        order.priority = payload.priority
    if payload.customer is not None:
        order.customer = payload.customer
    db.commit()
    return {"code": order.code, "qty": order.qty, "due_date": order.due_date.isoformat(timespec="minutes"),
            "priority": order.priority}


@router.post("/schedule/solve")
def solve(payload: SolveIn, db: Session = Depends(get_db)):
    if payload.strategy not in ("auto", *STRATEGIES):
        raise HTTPException(400, "未知策略")
    version = services.run_schedule(db, strategy=payload.strategy, time_limit=payload.time_limit,
                                    note=payload.note, freeze=payload.freeze)
    return version


@router.get("/schedule/current")
def current(db: Session = Depends(get_db)):
    version = services.current_version(db)
    if not version:
        return {"version": {}, "assignments": [], "progress": {}}
    return {
        "version": services.serialize_version(version, with_assignments=False),
        "assignments": [services.serialize_assignment(a) for a in sorted(version.assignments, key=lambda x: (x.machine_code, x.start))],
        "progress": services.progress_summary(db, version),
    }


@router.get("/schedule/versions")
def versions(db: Session = Depends(get_db)):
    rows = db.query(models.ScheduleVersion).order_by(models.ScheduleVersion.id.desc()).limit(30).all()
    return [services.serialize_version(v, with_assignments=False) for v in rows]


@router.get("/schedule/versions/{version_id}")
def version_detail(version_id: int, db: Session = Depends(get_db)):
    version = services.get_version(db, version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    return services.serialize_version(version)


@router.get("/schedule/diff")
def diff(base: int, target: int, db: Session = Depends(get_db)):
    return services.version_diff(db, base, target)


@router.get("/kpi")
def kpi(db: Session = Depends(get_db)):
    version = services.current_version(db)
    if not version:
        return {}
    return {
        "version": services.serialize_version(version, with_assignments=False),
        "kpis": version.metrics.get("kpis", {}),
        "progress": services.progress_summary(db, version),
        "orders": version.metrics.get("orders", []),
    }


@router.get("/ai/advice")
def advice(db: Session = Depends(get_db)):
    result = services.latest_advice(db)
    version = services.current_version(db)
    if version:
        result["current"] = {
            "strategy": version.strategy,
            "kpis": version.metrics.get("kpis", {}),
        }
    result["llm_enabled"] = llm.available()
    return result


def _current_assignments_wm(version, calendar):
    mapping = {}
    for a in version.assignments:
        start = calendar.to_wm(a.start)
        end = max(start + 1, calendar.to_wm(a.end))
        mapping[a.task_key] = {"machine_id": a.machine_id, "operator_id": a.operator_id,
                               "start": start, "end": end}
    return mapping


@router.get("/ai/radar")
def radar(db: Session = Depends(get_db)):
    version = services.current_version(db)
    if not version:
        return {"risks": [], "bottleneck": [], "skill_gaps": [], "suggestions": ["请先执行一次排产。"]}
    problem, calendar = services.build_problem(db, freeze=False)
    assignments = _current_assignments_wm(version, calendar)
    result = risk_radar(problem, assignments, calendar, {"kpis": version.metrics.get("kpis", {})})
    result["version_id"] = version.id
    return result


@router.post("/ai/chat")
def chat(payload: ChatIn, db: Session = Depends(get_db)):
    orders = db.query(models.Order).all()
    machines = db.query(models.Machine).all()
    workers = db.query(models.Worker).all()
    parsed = parse_instruction(payload.message, orders=orders, machines=machines, workers=workers)

    if parsed["intent"] == "unknown" and llm.available():
        text = llm.narrate({"指标": (services.current_version(db).metrics if services.current_version(db) else {})},
                           payload.message)
        if text:
            return {"reply": text, "action": "llm", "parsed": parsed, "executed": False}

    intent = parsed["intent"]
    entities = parsed["entities"]
    action_detail = ""
    executed = False
    version_payload = None
    diff_payload = None

    def reschedule(note: str):
        nonlocal executed, version_payload, diff_payload
        before = services.current_version(db)
        new_version = services.run_schedule(db, strategy="auto", note=note, freeze=True)
        executed = True
        version_payload = new_version
        if before:
            diff_payload = services.version_diff(db, before.id, new_version["id"])

    if intent == "query":
        version = services.current_version(db)
        if not version:
            return {"reply": "当前还没有排产版本，先点击“智能排产”。", "parsed": parsed, "executed": False}
        problem, calendar = services.build_problem(db, freeze=False)
        metrics = version.metrics
        radar = risk_radar(problem, _current_assignments_wm(version, calendar), calendar,
                           {"kpis": metrics.get("kpis", {})})
        high = [r for r in radar["risks"] if r["level"] == "高"]
        lines = [f"当前版本 #{version.id}：{metrics['kpis']['orders_late']} 张订单存在拖期风险。"]
        if high:
            lines.append("高风险：" + "；".join(f"{r['order_code']}（{r['reason']}）" for r in high[:3]))
        lines.append("瓶颈：" + "；".join(f"{b['code']} 利用率 {b['utilization']:.0%}" for b in radar["bottleneck"][:2]))
        return {"reply": "\n".join(lines), "parsed": parsed, "executed": False,
                "radar": radar}

    if not payload.execute:
        return {"reply": "已解析：" + parsed["explanation"] + "（未执行，如需执行请打开 execute）",
                "parsed": parsed, "executed": False}

    if intent == "reschedule":
        reschedule("自然语言指令：滚动重排")
        action_detail = "已执行滚动重排（冻结已开工工序）"

    elif intent == "urgent":
        codes = entities.get("order_codes") or []
        target = _order_code(db, codes[0]) if codes else None
        if target:
            target.priority = 3
            if entities.get("deadline"):
                target.due_date = datetime.fromisoformat(entities["deadline"])
            db.commit()
            action_detail = f"订单 {target.code} 已设为加急" + (f"，交期 {target.due_date:%m-%d %H:%M}" if entities.get("deadline") else "")
            reschedule("自然语言指令：加急插单 " + target.code)
        else:
            return {"reply": "没有识别到具体订单号，请补充（例如 PO20261127001）。", "parsed": parsed, "executed": False}

    elif intent == "breakdown":
        code = (entities.get("machine_codes") or [None])[0]
        minutes = entities.get("duration_minutes") or 240
        if not code:
            return {"reply": "请指明故障设备（如 CNC-M03 或“五轴”）。", "parsed": parsed, "executed": False}
        now = datetime.now()
        db.add(models.EventLog(kind="breakdown", payload={"machine_code": code},
                               message=f"{code} 故障停机 {minutes} 分钟",
                               start=now, end=now + timedelta(minutes=minutes), active=True))
        db.commit()
        action_detail = f"已登记 {code} 停机 {minutes} 分钟"
        reschedule(f"设备故障 {code} 停机 {minutes} 分钟")

    elif intent == "leave":
        name = (entities.get("worker_names") or [None])[0]
        minutes = entities.get("duration_minutes") or 480
        if not name:
            return {"reply": "请指明请假人员姓名。", "parsed": parsed, "executed": False}
        now = datetime.now()
        db.add(models.EventLog(kind="leave", payload={"worker_name": name},
                               message=f"{name} 请假 {minutes} 分钟",
                               start=now, end=now + timedelta(minutes=minutes), active=True))
        db.commit()
        action_detail = f"已登记 {name} 缺勤 {minutes} 分钟"
        reschedule(f"人员缺勤 {name}")

    elif intent == "quantity":
        codes = entities.get("order_codes") or []
        target = _order_code(db, codes[0]) if codes else None
        qty = entities.get("quantity")
        if not target or not qty:
            return {"reply": "需要同时给出订单号与新的数量。", "parsed": parsed, "executed": False}
        target.qty = qty
        db.commit()
        action_detail = f"订单 {target.code} 数量改为 {qty}"
        reschedule("批量变更 " + target.code)
    else:
        return {"reply": parsed["explanation"], "parsed": parsed, "executed": False}

    reply = action_detail
    if version_payload:
        k = version_payload["metrics"]["kpis"]
        reply += f"\n重排完成（版本 #{version_payload['id']}，{version_payload['solver']}）：按期率 {k['on_time_rate']:.0%}，拖期 {k['orders_late']} 单，完工跨度 {k['makespan_hours']} 小时。"
        if diff_payload:
            reply += f"\n影响：{diff_payload['summary']['changed']} 道工序时间/资源发生变化，{diff_payload['summary']['removed']} 道移出，{diff_payload['summary']['added']} 道新增。"
    return {"reply": reply, "parsed": parsed, "executed": executed,
            "version": version_payload, "diff": diff_payload}


@router.post("/events")
def create_event(payload: EventIn, db: Session = Depends(get_db)):
    now = datetime.now()
    end = now + timedelta(minutes=payload.duration_minutes) if payload.duration_minutes else None
    event = models.EventLog(kind=payload.kind, payload=payload.payload, message=payload.message,
                            start=now, end=end, active=True)
    db.add(event)
    db.commit()
    result = {"event_id": event.id, "kind": event.kind, "message": event.message}
    if payload.auto_reschedule:
        result["version"] = services.run_schedule(db, strategy="auto", note=f"事件驱动重排：{payload.message or payload.kind}")
    return result


@router.post("/progress/report")
def report(payload: ReportIn, db: Session = Depends(get_db)):
    assignment = db.query(models.Assignment).filter(models.Assignment.id == payload.assignment_id).first()
    if not assignment:
        raise HTTPException(404, "工序任务不存在")
    assignment.good_qty += payload.good_qty
    assignment.scrap_qty += payload.scrap_qty
    if payload.status in ("planned", "doing", "done"):
        assignment.status = payload.status
    elif assignment.good_qty >= assignment.quantity and assignment.quantity > 0:
        assignment.status = "done"
    elif assignment.good_qty > 0:
        assignment.status = "doing"
    db.add(models.ProgressReport(assignment_id=assignment.id, good_qty=payload.good_qty,
                                 scrap_qty=payload.scrap_qty, note=payload.note))
    db.commit()
    version = services.get_version(db, assignment.version_id)
    return {"assignment": services.serialize_assignment(assignment),
            "progress": services.progress_summary(db, version)}
