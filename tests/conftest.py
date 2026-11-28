import os
import pathlib

os.environ["DATABASE_URL"] = "sqlite:///./data/test_aps.db"
os.environ["AUTO_SCHEDULE_ON_START"] = "0"
os.environ["AUTH_REQUIRED"] = "1"
os.environ["AUTH_SECRET"] = "test-secret-only"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "Test@123456"

db_file = pathlib.Path("data/test_aps.db")
if db_file.exists():
    db_file.unlink()

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        resp = c.post("/api/auth/login", json={"username": "admin", "password": "Test@123456"})
        assert resp.status_code == 200, resp.text
        c.headers.update({"Authorization": f"Bearer {resp.json()['token']}"})
        yield c


@pytest.fixture()
def operator_client(client):
    """只读/操作角色：用于验证 RBAC。"""
    client.post("/api/users", json={"username": "op1", "password": "Op@123456", "role": "operator", "full_name": "一号操作工"})
    with TestClient(app) as c:
        resp = c.post("/api/auth/login", json={"username": "op1", "password": "Op@123456"})
        assert resp.status_code == 200, resp.text
        c.headers.update({"Authorization": f"Bearer {resp.json()['token']}"})
        yield c
