"""AI 决策层之三：自然语言调度指令解析（规则优先，可选大模型兜底）。

车间里计划员不会写 JSON，最自然的交互是“三号加工中心故障，停 4 小时，
把 A 订单加急”。本模块把这类口语指令解析成结构化动作，再交给重排引擎。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

INTENTS = {
    "reschedule": ["重新排产", "重排", "重新调度", "优化排产", "滚动排产", "排一下"],
    "urgent": ["加急", "插单", "优先", "提前", "催单"],
    "breakdown": ["故障", "停机", "坏了", "维修", "报警", "停机保"],
    "leave": ["请假", "缺勤", "不在岗", "调休", "病假"],
    "quantity": ["数量改为", "改数量", "批量改为", "减到", "加到"],
    "query": ["风险", "延期", "进度", "瓶颈", "负荷", "哪些订单", "怎么样"],
}

DATE_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?|(\d{1,2})[-/月](\d{1,2})日?|(\d{1,2})[号日]")
QTY_RE = re.compile(r"(?:数量|批量)[^0-9]{0,4}(\d{2,5})")
MACHINE_RE = re.compile(r"\b([A-Z]{2,4}-[A-Z]?\d{1,3})\b")
WORKER_RE = re.compile(r"([\u4e00-\u9fa5]{2,3})(?:请假|缺勤|不在岗|调休|病假)")
DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(个?小时|h|H|分钟|天)")


def parse_date(text: str, now: datetime) -> datetime | None:
    if "明天" in text:
        return (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "后天" in text:
        return (now + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
    if "下周" in text:
        return (now + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)
    m = DATE_RE.search(text)
    if not m:
        return None
    if m.group(1):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif m.group(4):
        year, month, day = now.year, int(m.group(4)), int(m.group(5))
    else:
        month, day = now.month, int(m.group(6))
        year = now.year
    try:
        target = datetime(year, month, day, 18, 0)
    except ValueError:
        return None
    if not m.group(1) and target < now - timedelta(days=180):
        target = target.replace(year=year + 1)
    return target


def parse_duration_minutes(text: str) -> int | None:
    m = DURATION_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if "天" in unit:
        return int(value * 24 * 60)
    if "分钟" in unit:
        return int(value)
    return int(value * 60)


def classify(text: str) -> tuple[str, float]:
    scores = {}
    for intent, words in INTENTS.items():
        hits = sum(1 for w in words if w in text)
        if hits:
            scores[intent] = hits
    if not scores:
        return "unknown", 0.0
    intent = max(scores, key=scores.get)
    confidence = min(0.95, 0.45 + 0.15 * scores[intent])
    return intent, confidence


def parse_instruction(text: str, now: datetime | None = None, orders=None, machines=None, workers=None) -> dict:
    now = now or datetime.now()
    text = text.strip()
    intent, confidence = classify(text)

    entities: dict = {"machine_codes": [], "worker_names": [], "order_codes": []}
    order_hits = re.findall(r"(PO\d{6,}|\b[A-Z]{2,4}-?\d{4,}\b)", text, flags=re.I)
    entities["order_codes"] = [h.upper() for h in order_hits]
    if orders is not None and not entities["order_codes"]:
        # 支持“阀体订单”“第三个订单”这类口语
        for order in orders:
            if order.code.lower() in text.lower():
                entities["order_codes"].append(order.code)
    if "第一" in text or "第1" in text:
        entities["order_index"] = 0
    elif "第二" in text or "第2" in text:
        entities["order_index"] = 1
    elif "第三" in text or "第3" in text:
        entities["order_index"] = 2

    if machines is not None:
        matched = []
        for m in machines:
            if m.code.lower() in text.lower() or m.name in text:
                matched.append(m.code)
        if not matched:
            for keyword, mtype in (("加工中心", "mill"), ("车床", "lathe"), ("磨床", "grind"),
                                  ("电火花", "edm"), ("三坐标", "inspect"), ("热处理", "heat")):
                if keyword in text:
                    matched.extend([m.code for m in machines if m.machine_type == mtype])
        entities["machine_codes"] = matched
    if not entities["machine_codes"]:
        entities["machine_codes"] = MACHINE_RE.findall(text.upper())

    if workers is not None:
        entities["worker_names"] = [w.name for w in workers if w.name in text]
    if not entities["worker_names"]:
        entities["worker_names"] = WORKER_RE.findall(text)

    entities["duration_minutes"] = parse_duration_minutes(text)
    deadline = parse_date(text, now)
    if deadline:
        entities["deadline"] = deadline.isoformat()

    qty = QTY_RE.search(text)
    if qty:
        entities["quantity"] = int(qty.group(1))
    if "加急" in text and "priority" not in entities:
        entities["priority"] = 3

    explanation = _explain(intent, entities)
    return {"intent": intent, "confidence": round(confidence, 2), "entities": entities, "explanation": explanation}


def _explain(intent: str, entities: dict) -> str:
    if intent == "reschedule":
        return "识别为滚动重排指令：冻结已开工工序，重新求解剩余工序。"
    if intent == "urgent":
        parts = []
        if entities.get("order_codes"):
            parts.append("订单 " + "、".join(entities["order_codes"]))
        if entities.get("deadline"):
            parts.append("交期提前至 " + entities["deadline"][:10])
        if entities.get("priority"):
            parts.append("优先级提升为加急")
        return "识别为加急/插单：" + ("，".join(parts) if parts else "需补充订单号")
    if intent == "breakdown":
        return f"识别为设备异常：{entities.get('machine_codes') or '未指明设备'}，停机 {entities.get('duration_minutes') or 0} 分钟。"
    if intent == "leave":
        return f"识别为人员缺勤：{entities.get('worker_names') or '未指明人员'}，缺勤 {entities.get('duration_minutes') or 0} 分钟。"
    if intent == "quantity":
        return f"识别为批量调整：数量改为 {entities.get('quantity')}。"
    if intent == "query":
        return "识别为进度/风险查询。"
    return "未识别到明确的调度动作，可换一种说法或直接点“智能排产”。"
