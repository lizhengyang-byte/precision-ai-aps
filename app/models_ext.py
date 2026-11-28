"""生产化扩展模型：账号、审计、计划审批。

与核心排产模型分表存放，避免业务表膨胀；启动时由 init_db 统一建表。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String, Text

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    full_name = Column(String(64), default="")
    role = Column(String(16), default="viewer", nullable=False)  # admin / planner / operator / viewer
    password_hash = Column(String(255), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    must_change_password = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    last_login = Column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id = Column(Integer, primary_key=True)
    actor = Column(String(64), default="system")
    role = Column(String(16), default="")
    action = Column(String(48), nullable=False, index=True)
    target = Column(String(64), default="")
    detail = Column(JSON, default=dict)
    ip = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class PlanApproval(Base):
    __tablename__ = "plan_approvals"
    __table_args__ = (Index("ix_approval_version", "version_id"),)

    id = Column(Integer, primary_key=True)
    version_id = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False)  # submitted / approved / published / rejected / rolled_back
    actor = Column(String(64), default="")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
