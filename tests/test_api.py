"""API 集成测试：主数据、排产、风险雷达、自然语言指令。"""


def test_meta_and_master(client):
    assert client.get("/api/meta").json()["name"].startswith("精密加工")
    master = client.get("/api/master").json()
    assert len(master["machines"]) >= 8
    assert len(master["workers"]) >= 8
    assert len(master["orders"]) >= 10
    assert master["products"][0]["routings"]


def test_solve_and_current(client):
    resp = client.post("/api/schedule/solve", json={"strategy": "balanced", "time_limit": 3})
    assert resp.status_code == 200
    version = resp.json()
    assert version["assignments"]
    assert version["metrics"]["kpis"]["scheduled_tasks"] > 0
    current = client.get("/api/schedule/current").json()
    assert current["assignments"]
    assert current["version"]["id"] == version["id"]


def test_advice_and_radar(client):
    advice = client.get("/api/ai/advice").json()
    assert advice["strategy"] in ("balanced", "due_date", "efficiency", "changeover")
    assert 0 < advice["confidence"] <= 1
    radar = client.get("/api/ai/radar").json()
    assert "risks" in radar and "bottleneck" in radar


def test_natural_language_query(client):
    reply = client.post("/api/ai/chat", json={"message": "现在哪些订单有延期风险？", "execute": True}).json()
    assert "版本" in reply["reply"] or "风险" in reply["reply"]
    assert reply["parsed"]["intent"] == "query"


def test_report_progress(client):
    current = client.get("/api/schedule/current").json()
    assignment = current["assignments"][0]
    resp = client.post("/api/progress/report", json={
        "assignment_id": assignment["id"], "good_qty": 20, "scrap_qty": 1,
    })
    assert resp.status_code == 200
    assert resp.json()["assignment"]["good_qty"] == 20
