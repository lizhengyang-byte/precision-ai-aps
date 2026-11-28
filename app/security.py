"""账号、口令与令牌：PBKDF2 口令散列 + HMAC-SHA256 签名令牌（无第三方依赖）。

设计取舍：
- 口令用 PBKDF2-HMAC-SHA256 迭代 26 万次，散列串自带参数，升级成本可控；
- 令牌为标准 JWT（HS256），便于前端与第三方系统复用；
- 鉴权默认开启（AUTH_REQUIRED=1），健康检查与登录接口除外；
- 所有角色判断集中在 require_roles，避免路由里散落 if。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models_ext import User

log = logging.getLogger("aps.security")

ITERATIONS = 260_000
SECRET = os.getenv("AUTH_SECRET", "")
TOKEN_TTL = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", str(12 * 3600)))
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "1") == "1"
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe@2026")

if AUTH_REQUIRED and not SECRET:
    SECRET = secrets.token_hex(32)
    log.warning("AUTH_SECRET 未配置，已生成临时密钥；重启后令牌会失效，生产环境务必固定该变量。")

ROLES = ("admin", "planner", "operator", "viewer")
_ROLE_RANK = {"viewer": 0, "operator": 1, "planner": 2, "admin": 3}


# ---------------------------------------------------------------- 口令

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- 令牌

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_token(sub: str, role: str, ttl: int | None = None) -> str:
    now = int(time.time())
    payload = {"sub": sub, "role": role, "iat": now, "exp": now + (ttl or TOKEN_TTL)}
    header = {"alg": "HS256", "typ": "JWT"}
    body = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def decode_token(token: str) -> dict:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        expected = hmac.new(SECRET.encode(), f"{head_b64}.{body_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(body_b64))
        if payload.get("exp", 0) < int(time.time()):
            raise ValueError("token expired")
        return payload
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, f"令牌无效：{exc}") from exc


# ---------------------------------------------------------------- 鉴权依赖
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not AUTH_REQUIRED:
        user = db.query(User).filter(User.role == "admin").first()
        if user:
            return user
        return User(username="anonymous", role="admin", password_hash="", active=True)
    if creds is None:
        raise HTTPException(401, "未登录或令牌缺失")
    payload = decode_token(creds.credentials)
    user = db.query(User).filter(User.username == payload.get("sub"), User.active.is_(True)).first()
    if not user:
        raise HTTPException(401, "账号不存在或已停用")
    request.state.user = user
    return user


def require_roles(*roles: str):
    """返回一个依赖：当前账号角色需落在给定集合内。"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"需要角色 {'/'.join(roles)}，当前为 {user.role}")
        return user

    return _dep


def auth_guard(user: User = Depends(get_current_user)) -> None:
    """整组路由的统一准入依赖：AUTH_REQUIRED=0 时自动放行。"""
    return None


def bootstrap_admin(db: Session) -> None:
    if db.query(User).count():
        return
    admin = User(
        username=ADMIN_USER,
        full_name="系统管理员",
        role="admin",
        password_hash=hash_password(ADMIN_PASSWORD),
        must_change_password=True,
    )
    db.add(admin)
    db.commit()
    log.warning("已创建初始管理员 %s，请尽快修改口令（当前取自 ADMIN_PASSWORD 或默认值）。", ADMIN_USER)


def describe(user: User) -> dict:
    return {
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "active": user.active,
        "must_change_password": user.must_change_password,
        "last_login": user.last_login.isoformat(timespec="seconds") if user.last_login else None,
    }


def touch_login(db: Session, user: User) -> None:
    user.last_login = datetime.now()
    db.commit()
