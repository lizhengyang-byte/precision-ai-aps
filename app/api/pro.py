"""生产化接口：登录鉴权、账号管理、计划审批发布、派工、OEE、产能与审计。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import ops, security
from ..database import get_db
from ..models_ext import User

router = APIRouter(prefix="/api")

_FAILS: dict[str, tuple[int, float]] = {}
_LOCK_SECONDS = 60
_MAX_FAILS = 5


class LoginIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class CreateUserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""
    role: str = "planner"


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class TransitionIn(BaseModel):
    note: str = ""


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    key = payload.username.lower()
    fails, locked_at = _FAILS.get(key, (0, 0.0))
    if locked_at and time.time() - locked_at < _LOCK_SECONDS:
        raise HTTPException(429, f"连续登录失败，请 {_LOCK_SECONDS} 秒后再试")
    user = db.query(User).filter(User.username == payload.username, User.active.is_(True)).first()
    if not user or not security.verify_password(payload.password, user.password_hash):
        fails += 1
        _FAILS[key] = (fails, time.time() if fails >= _MAX_FAILS else 0.0)
        ops.audit(db, None, "auth.failed", payload.username, {"attempts": fails}, _client(request))
        raise HTTPException(401, "账号或口令错误")
    _FAILS.pop(key, None)
    security.touch_login(db, user)
    ops.audit(db, user, "auth.login", user.username, {"role": user.role}, _client(request))
    return {
        "token": security.create_token(user.username, user.role),
        "token_type": "bearer",
        "expires_in": security.TOKEN_TTL,
        "user": security.describe(user),
    }


@router.get("/auth/me")
def me(user: User = Depends(security.get_current_user)):
    return security.describe(user)


@router.post("/auth/change-password")
def change_password(payload: ChangePasswordIn, request: Request,
                    user: User = Depends(security.get_current_user), db: Session = Depends(get_db)):
    if not security.verify_password(payload.old_password, user.password_hash):
        raise HTTPException(400, "原口令不正确")
    user.password_hash = security.hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    ops.audit(db, user, "auth.password_changed", user.username, {}, _client(request))
    return {"ok": True}


@router.get("/users")
def list_users(user: User = Depends(security.require_roles("admin")), db: Session = Depends(get_db)):
    return [security.describe(u) for u in db.query(User).order_by(User.id).all()]


@router.post("/users")
def create_user(payload: CreateUserIn, request: Request,
                user: User = Depends(security.require_roles("admin")), db: Session = Depends(get_db)):
    if payload.role not in security.ROLES:
        raise HTTPException(400, f"角色需为 {'/'.join(security.ROLES)}")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(409, "账号已存在")
    db.add(User(username=payload.username, full_name=payload.full_name, role=payload.role,
                password_hash=security.hash_password(payload.password), must_change_password=True))
    db.commit()
    ops.audit(db, user, "user.created", payload.username, {"role": payload.role}, _client(request))
    return {"ok": True, "username": payload.username}


@router.get("/plan/state")
def plan_state(db: Session = Depends(get_db), user: User = Depends(security.get_current_user)):
    return ops.plan_state(db)


@router.post("/plan/{version_id}/submit")
def plan_submit(version_id: int, payload: TransitionIn, request: Request,
                user: User = Depends(security.require_roles("planner", "admin")), db: Session = Depends(get_db)):
    return ops.submit(db, version_id, user, payload.note, _client(request))


@router.post("/plan/{version_id}/approve")
def plan_approve(version_id: int, payload: TransitionIn, request: Request,
                 user: User = Depends(security.require_roles("planner", "admin")), db: Session = Depends(get_db)):
    return ops.approve(db, version_id, user, payload.note, _client(request))


@router.post("/plan/{version_id}/reject")
def plan_reject(version_id: int, payload: TransitionIn, request: Request,
                user: User = Depends(security.require_roles("planner", "admin")), db: Session = Depends(get_db)):
    return ops.reject(db, version_id, user, payload.note, _client(request))


@router.post("/plan/{version_id}/publish")
def plan_publish(version_id: int, payload: TransitionIn, request: Request,
                 user: User = Depends(security.require_roles("planner", "admin")), db: Session = Depends(get_db)):
    return ops.publish(db, version_id, user, payload.note, _client(request))


@router.post("/plan/{version_id}/rollback")
def plan_rollback(version_id: int, payload: TransitionIn, request: Request,
                  user: User = Depends(security.require_roles("planner", "admin")), db: Session = Depends(get_db)):
    return ops.rollback(db, version_id, user, payload.note, _client(request))


@router.get("/dispatch")
def dispatch(day: str | None = None, db: Session = Depends(get_db),
             user: User = Depends(security.get_current_user)):
    return ops.dispatch(db, day)


@router.get("/oee")
def oee(db: Session = Depends(get_db), user: User = Depends(security.get_current_user)):
    return ops.oee(db)


@router.get("/capacity")
def capacity(days: int = 7, db: Session = Depends(get_db),
             user: User = Depends(security.get_current_user)):
    return ops.capacity_forecast(db, days)


@router.get("/audits")
def audits(limit: int = 50, db: Session = Depends(get_db),
           user: User = Depends(security.get_current_user)):
    return ops.recent_audits(db, limit)
