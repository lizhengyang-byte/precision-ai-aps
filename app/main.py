"""应用入口：FastAPI + 静态看板。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import models, services
from .api.routes import router
from .config import APP_NAME, APP_VERSION
from .database import SessionLocal, init_db
from .seed import seed_if_empty

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        created = seed_if_empty(db)
        auto = os.getenv("AUTO_SCHEDULE_ON_START", "1") == "1"
        if auto and db.query(models.ScheduleVersion).count() == 0:
            limit = float(os.getenv("STARTUP_SOLVE_SECONDS", "8"))
            services.run_schedule(db, strategy="balanced", time_limit=limit,
                                  note="系统初始化自动排产" + ("（演示数据）" if created else ""))
    finally:
        db.close()
    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
app.include_router(router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
