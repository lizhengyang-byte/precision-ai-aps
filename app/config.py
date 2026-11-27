"""运行配置（全部可用环境变量覆盖，便于容器化部署）。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'aps.db'}")
SEED_ON_START = os.getenv("SEED_ON_START", "1") == "1"
DEFAULT_SOLVE_SECONDS = float(os.getenv("SOLVE_TIME_LIMIT", "20"))
APP_NAME = "精密加工 AI 智能排产与进度管控系统"
APP_VERSION = "1.0.0"
