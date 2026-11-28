"""应用入口：鉴权、可观测性、静态看板与生产环境装配。"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import models, security, services
from .api import pro
from .api.routes import router
from .config import APP_NAME, APP_VERSION
from .database import SessionLocal, engine, init_db
from .engine.cpsat_solver import cpsat_available
from .seed import seed_if_empty

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("aps")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

_METRICS = {"requests": 0, "errors": 0, "latency_ms": 0.0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        security.bootstrap_admin(db)
        created = seed_if_empty(db)
        auto = os.getenv("AUTO_SCHEDULE_ON_START", "1") == "1"
        if auto and db.query(models.ScheduleVersion).count() == 0:
            limit = float(os.getenv("STARTUP_SOLVE_SECONDS", "8"))
            services.run_schedule(db, strategy="balanced", time_limit=limit,
                                  note="系统初始化自动排产" + ("（演示数据）" if created else ""))
    finally:
        db.close()
    log.info("startup complete version=%s auth_required=%s solver=%s",
             APP_VERSION, security.AUTH_REQUIRED, cpsat_available())
    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001
        _METRICS["errors"] += 1
        log.exception("unhandled error rid=%s path=%s", request_id, request.url.path)
        return JSONResponse({"detail": "服务器内部错误", "request_id": request_id}, status_code=500)
    cost = (time.perf_counter() - started) * 1000
    _METRICS["requests"] += 1
    _METRICS["latency_ms"] += cost
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.path.startswith("/api"):
        log.info("%s %s %s %.1fms rid=%s", request.method, request.url.path, response.status_code, cost, request_id)
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": APP_VERSION, "solver": "cpsat" if cpsat_available() else "heuristic"}


@app.get("/readyz")
def readyz():
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "not-ready", "reason": str(exc)}, status_code=503)
    return {"status": "ready", "database": engine.url.get_backend_name()}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    avg = _METRICS["latency_ms"] / _METRICS["requests"] if _METRICS["requests"] else 0
    return (
        "# HELP aps_requests_total 累计请求数\n"
        "# TYPE aps_requests_total counter\n"
        f"aps_requests_total {_METRICS['requests']}\n"
        "# TYPE aps_errors_total counter\n"
        f"aps_errors_total {_METRICS['errors']}\n"
        "# TYPE aps_request_latency_ms_avg gauge\n"
        f"aps_request_latency_ms_avg {avg:.2f}\n"
        f'aps_solver_cpsat {1 if cpsat_available() else 0}\n'
    )


app.include_router(router, dependencies=[Depends(security.auth_guard)])
app.include_router(pro.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static-assets")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
