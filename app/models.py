"""数据模型：主数据（产品/工艺/设备/人员）、订单、排产版本、报工与异常事件。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    material: Mapped[str] = mapped_column(String(32), default="")
    tolerance_um: Mapped[int] = mapped_column(Integer, default=20)
    family: Mapped[str] = mapped_column(String(16), default="A")
    note: Mapped[str] = mapped_column(Text, default="")

    routings: Mapped[list["Routing"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Routing(Base):
    """工艺路线：一道标准工序。"""

    __tablename__ = "routings"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    process_type: Mapped[str] = mapped_column(String(16))
    std_minutes: Mapped[float] = mapped_column(Float, default=1.0)  # 单件工时
    setup_minutes: Mapped[int] = mapped_column(Integer, default=0)   # 一次装夹/程序准备
    required_level: Mapped[int] = mapped_column(Integer, default=2)
    machine_type: Mapped[str] = mapped_column(String(16))
    min_precision: Mapped[int] = mapped_column(Integer, default=1)

    product: Mapped[Product] = relationship(back_populates="routings")


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    machine_type: Mapped[str] = mapped_column(String(16))
    precision_grade: Mapped[int] = mapped_column(Integer, default=1)  # 1 普通 / 2 精密 / 3 超精密
    efficiency: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / down / maintenance
    shift_pattern: Mapped[str] = mapped_column(String(16), default="两班")
    note: Mapped[str] = mapped_column(Text, default="")


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[int] = mapped_column(Integer, default=3)
    skills: Mapped[dict] = mapped_column(JSON, default=dict)  # 工序类型 -> 技能等级
    shift: Mapped[str] = mapped_column(String(16), default="白班")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 普通 / 2 较高 / 3 紧急
    release_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open / scheduled / done / cancelled
    customer: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    product: Mapped[Product] = relationship()


class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    strategy: Mapped[str] = mapped_column(String(16), default="balanced")
    solver: Mapped[str] = mapped_column(String(16), default="cp-sat")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    time_limit: Mapped[float] = mapped_column(Float, default=20.0)
    solve_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    horizon_start: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    objective: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_versions.id"), nullable=True)

    assignments: Mapped[list["Assignment"]] = relationship(back_populates="version", cascade="all, delete-orphan")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    task_key: Mapped[str] = mapped_column(String(48), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    op_name: Mapped[str] = mapped_column(String(64), default="")
    process_type: Mapped[str] = mapped_column(String(16), default="")
    order_code: Mapped[str] = mapped_column(String(32), default="")
    product_code: Mapped[str] = mapped_column(String(32), default="")
    machine_code: Mapped[str] = mapped_column(String(16), default="")
    operator_code: Mapped[str] = mapped_column(String(32), default="")
    machine_id: Mapped[int] = mapped_column(Integer, default=0)
    operator_id: Mapped[int] = mapped_column(Integer, default=0)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned / doing / done
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    good_qty: Mapped[int] = mapped_column(Integer, default=0)
    scrap_qty: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")

    version: Mapped[ScheduleVersion] = relationship(back_populates="assignments")


class ProgressReport(Base):
    __tablename__ = "progress_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    good_qty: Mapped[int] = mapped_column(Integer, default=0)
    scrap_qty: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EventLog(Base):
    """现场异常：设备故障、人员请假、插单、批量变更等。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
