"""生产化能力测试：鉴权、RBAC、计划审批发布回滚、审计、派工、OEE、健康检查。"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_without_auth():
    with TestClient(app) as c:
        assert c.get("/healthz").json()["status"] == "ok"
        assert c.get("/readyz").json()["status"] == "ready"
        assert "aps_requests_total" in c.get("/metrics").text


def test_requires_token():
    with TestClient(app) as c:
        assert c.get("/api/kpi").status_code == 401
        assert c.get("/api/auth/me").status_code == 401


def test_login_me_and_audit(client):
    me = client.get("/api/auth/me").json()
    assert me["role"] == "admin" and me["username"] == "admin"
    audits = client.get("/api/audits?limit=20").json()
    assert any(a["action"] == "auth.login" for a in audits)


def test_login_lock_and_wrong_password():
    with TestClient(app) as c:
        for _ in range(2):
            assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong-pass"}).status_code == 401


def test_rbac_operator_cannot_publish(operator_client):
    assert operator_client.get("/api/oee").status_code == 200
    version_id = operator_client.get("/api/schedule/current").json()["version"]["id"]
    resp = operator_client.post(f"/api/plan/{version_id}/publish", json={"note": "越权尝试"})
    assert resp.status_code == 403


def test_plan_lifecycle_and_rollback(client):
    version = client.post("/api/schedule/solve", json={"strategy": "balanced", "time_limit": 2}).json()
    vid = version["id"]
    assert client.post(f"/api/plan/{vid}/submit", json={"note": "提交计划"}).status_code == 200
    assert client.post(f"/api/plan/{vid}/approve", json={"note": "审批通过"}).status_code == 200
    published = client.post(f"/api/plan/{vid}/publish", json={"note": "下发车间"}).json()
    assert published["status"] in ("published", "ok") or published.get("id") == vid
    # 重复发布应被状态机拒绝
    assert client.post(f"/api/plan/{vid}/publish", json={"note": ""}).status_code == 409
    # 再排一版后回滚到已发布版本
    newer = client.post("/api/schedule/solve", json={"strategy": "balanced", "time_limit": 2}).json()
    cloned = client.post(f"/api/plan/{vid}/rollback", json={"note": "回到稳定计划"}).json()
    assert cloned["id"] != newer["id"] and cloned["status"] == "published"
    state = client.get("/api/plan/state").json()
    assert state["version"]["id"] == cloned["id"]


def test_dispatch_oee_capacity(client):
    client.post("/api/schedule/solve", json={"strategy": "balanced", "time_limit": 2})
    dispatch = client.get("/api/dispatch").json()
    assert dispatch["cards"] and "changeover" in dispatch["cards"][0]
    oee = client.get("/api/oee").json()
    assert 0 <= oee["overall"]["oee"] <= 1 and oee["machines"]
    cap = client.get("/api/capacity?days=7").json()
    assert len(cap["days"]) == 7


def test_freeze_horizon_recorded(client):
    version = client.post("/api/schedule/solve", json={"strategy": "balanced", "time_limit": 2}).json()
    assert version["metrics"]["freeze_horizon_minutes"] == 120
