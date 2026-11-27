"""演示数据：一个典型的精密加工车间（阀体/缸筒/航空件/齿轮轴/传感器壳体）。

数据刻意覆盖真实痛点：高精度设备只有 1 台、关键工序只有 1~2 名合格人员、
多品种小批量、交期交叉。可在此基础上替换为企业真实主数据。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import models


def _dt(days: int, hour: int = 18) -> datetime:
    base = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days)


PRODUCTS = [
    dict(code="P-1001", name="精密阀体", material="6061-T6 铝合金", tolerance_um=15, family="A"),
    dict(code="P-1002", name="液压缸筒", material="45# 无缝钢管", tolerance_um=25, family="B"),
    dict(code="P-1003", name="航空连接件", material="TC4 钛合金", tolerance_um=5, family="C"),
    dict(code="P-1004", name="减速器齿轮轴", material="20CrMnTi", tolerance_um=12, family="B"),
    dict(code="P-1005", name="传感器外壳", material="316L 不锈钢", tolerance_um=20, family="A"),
]

# (产品, 序号, 工序名, 工序类型, 单件工时, 装夹准备, 最低技能, 设备类型, 最低精度)
ROUTINGS = [
    ("P-1001", 1, "CNC 粗铣外形", "mill", 0.8, 30, 2, "mill", 2),
    ("P-1001", 2, "数控车密封面", "lathe", 0.9, 25, 2, "lathe", 2),
    ("P-1001", 3, "精密磨配合面", "grind", 0.5, 40, 3, "grind", 3),
    ("P-1001", 4, "三坐标全尺寸检测", "inspect", 0.15, 10, 3, "inspect", 3),
    ("P-1002", 1, "车外圆及端面", "lathe", 1.2, 30, 2, "lathe", 2),
    ("P-1002", 2, "镗内孔", "boring", 1.0, 35, 2, "lathe", 2),
    ("P-1002", 3, "磨内孔", "grind", 0.8, 45, 3, "grind", 3),
    ("P-1002", 4, "内径检测", "inspect", 0.2, 10, 3, "inspect", 2),
    ("P-1003", 1, "五轴铣叶形槽", "mill", 1.6, 60, 3, "mill", 3),
    ("P-1003", 2, "电火花去毛刺", "edm", 0.4, 20, 3, "edm", 2),
    ("P-1003", 3, "真空热处理", "heat", 0.3, 90, 2, "heat", 1),
    ("P-1003", 4, "精密磨基准面", "grind", 0.6, 40, 3, "grind", 3),
    ("P-1003", 5, "三坐标检测", "inspect", 0.25, 15, 3, "inspect", 3),
    ("P-1004", 1, "车削齿轮坯", "lathe", 0.9, 25, 2, "lathe", 2),
    ("P-1004", 2, "滚齿", "mill", 0.7, 50, 3, "mill", 2),
    ("P-1004", 3, "渗碳热处理", "heat", 0.2, 80, 2, "heat", 1),
    ("P-1004", 4, "磨齿", "grind", 0.8, 45, 3, "grind", 3),
    ("P-1004", 5, "齿形检测", "inspect", 0.2, 10, 3, "inspect", 3),
    ("P-1005", 1, "车削壳体", "lathe", 0.5, 20, 2, "lathe", 2),
    ("P-1005", 2, "铣密封槽", "mill", 0.4, 25, 2, "mill", 2),
    ("P-1005", 3, "电火花开孔", "edm", 0.3, 15, 2, "edm", 2),
    ("P-1005", 4, "外观与尺寸检测", "inspect", 0.12, 8, 2, "inspect", 2),
]

MACHINES = [
    dict(code="CNC-M01", name="三轴加工中心 M01", machine_type="mill", precision_grade=2),
    dict(code="CNC-M02", name="三轴加工中心 M02", machine_type="mill", precision_grade=2),
    dict(code="CNC-M03", name="五轴加工中心 M03", machine_type="mill", precision_grade=3),
    dict(code="CNC-M04", name="数控车床 M04", machine_type="lathe", precision_grade=2),
    dict(code="CNC-M05", name="数控车床 M05", machine_type="lathe", precision_grade=3),
    dict(code="GRD-M01", name="精密磨床 M01", machine_type="grind", precision_grade=3),
    dict(code="EDM-M01", name="电火花 EDM01", machine_type="edm", precision_grade=2),
    dict(code="CMM-M01", name="三坐标测量机", machine_type="inspect", precision_grade=3),
    dict(code="HT-M01", name="真空热处理炉", machine_type="heat", precision_grade=1),
]

WORKERS = [
    dict(code="W001", name="王强", level=4, skills={"mill": 4, "lathe": 3}, shift="白班"),
    dict(code="W002", name="李敏", level=3, skills={"mill": 3, "edm": 2}, shift="白班"),
    dict(code="W003", name="张伟", level=4, skills={"grind": 4, "mill": 2}, shift="白班"),
    dict(code="W004", name="赵磊", level=4, skills={"lathe": 4, "boring": 4}, shift="白班"),
    dict(code="W005", name="陈晨", level=4, skills={"mill": 4, "lathe": 2}, shift="白班"),
    dict(code="W006", name="刘洋", level=3, skills={"edm": 3, "mill": 2}, shift="夜班"),
    dict(code="W007", name="孙悦", level=4, skills={"inspect": 4, "grind": 2}, shift="白班"),
    dict(code="W008", name="周涛", level=3, skills={"heat": 3, "lathe": 2}, shift="夜班"),
]

ORDERS = [
    # 订单号, 产品, 数量, 交期(天), 优先级, 客户, 齐套(天)
    ("PO20261127001", "P-1003", 120, 2, 3, "成都航发", 0),
    ("PO20261127002", "P-1001", 240, 2, 3, "绵阳海拓", 0),
    ("PO20261127003", "P-1002", 160, 3, 2, "德阳重装", 0),
    ("PO20261127004", "P-1004", 200, 3, 2, "重庆精传", 1),
    ("PO20261127005", "P-1003", 80, 3, 3, "成都航发", 1),
    ("PO20261127006", "P-1005", 300, 4, 1, "深圳传感", 0),
    ("PO20261127007", "P-1001", 180, 5, 2, "绵阳海拓", 2),
    ("PO20261127008", "P-1002", 120, 5, 1, "德阳重装", 2),
    ("PO20261127009", "P-1003", 60, 5, 3, "西安航空", 3),
    ("PO20261127010", "P-1004", 300, 6, 1, "重庆精传", 2),
    ("PO20261127011", "P-1001", 140, 7, 1, "成都力源", 3),
    ("PO20261127012", "P-1005", 250, 8, 2, "深圳传感", 4),
]


def seed_if_empty(db: Session) -> bool:
    if db.query(models.Product).count() > 0:
        return False
    seed(db)
    return True


def seed(db: Session) -> None:
    products = {}
    for item in PRODUCTS:
        product = models.Product(**item)
        db.add(product)
        products[item["code"]] = product
    db.flush()

    for code, seq, name, ptype, std, setup, level, mtype, precision in ROUTINGS:
        db.add(
            models.Routing(
                product_id=products[code].id,
                seq=seq,
                name=name,
                process_type=ptype,
                std_minutes=std,
                setup_minutes=setup,
                required_level=level,
                machine_type=mtype,
                min_precision=precision,
            )
        )

    for item in MACHINES:
        db.add(models.Machine(**item))
    for item in WORKERS:
        db.add(models.Worker(**item))

    for code, product_code, qty, days, priority, customer, release in ORDERS:
        db.add(
            models.Order(
                code=code,
                product_id=products[product_code].id,
                qty=qty,
                due_date=_dt(days),
                priority=priority,
                release_at=_dt(release, hour=8),
                customer=customer,
                status="open",
            )
        )
    db.commit()


def reset(db: Session) -> None:
    for table in (
        models.ProgressReport,
        models.Assignment,
        models.ScheduleVersion,
        models.EventLog,
        models.Order,
        models.Worker,
        models.Machine,
        models.Routing,
        models.Product,
    ):
        db.query(table).delete()
    db.commit()
    seed(db)
