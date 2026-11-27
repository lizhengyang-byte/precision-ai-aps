import os
import pathlib

os.environ["DATABASE_URL"] = "sqlite:///./data/test_aps.db"
os.environ["AUTO_SCHEDULE_ON_START"] = "0"

db_file = pathlib.Path("data/test_aps.db")
if db_file.exists():
    db_file.unlink()

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
