"""HTTP 接口测试。运行： pytest"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def payload(**overrides):
    base = {
        "readings": [
            {"time": "2026-09-25T08:00:00+08:00", "temperature": -14.0},
            {"time": "2026-09-25T08:05:00+08:00", "temperature": -14.0},
            {"time": "2026-09-25T08:30:00+08:00", "temperature": -14.0},
            {"time": "2026-09-25T08:35:00+08:00", "temperature": -14.0},
        ],
        "temperature_limit": -18.0,
        "max_sample_interval_seconds": 600,
        "min_alarm_duration_seconds": 60,
        "maintenance_windows": [],
    }
    base.update(overrides)
    return base


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_review_ok():
    r = client.post("/api/v1/cold-storage/temperature/review", json=payload())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["timezone"] == "+08:00"
    # 两段越限（被 25 分钟断档分开），一个未知区间
    assert len(data["unknown_intervals"]) == 1
    assert data["unknown_intervals"][0]["duration_seconds"] == 1500.0
    assert len(data["excursion_intervals"]) == 2
    assert len(data["alarms"]) == 2
    assert data["alarms"][0]["start"].endswith("+08:00")


def test_review_with_maintenance():
    body = payload(
        maintenance_windows=[
            {"start": "2026-09-25T08:02:00+08:00", "end": "2026-09-25T08:03:30+08:00"}
        ],
        min_alarm_duration_seconds=120,
    )
    r = client.post("/api/v1/cold-storage/temperature/review", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    # 第一段 300s 被扣掉 90s => 120s + 90s（仅 120s 成报警）；
    # 断档后的第二段 300s 不受豁免影响，单独成报警
    assert [a["duration_seconds"] for a in data["alarms"]] == [120.0, 300.0]


def test_non_monotonic_returns_422():
    body = payload()
    body["readings"][1]["time"] = "2026-09-25T07:59:00+08:00"
    r = client.post("/api/v1/cold-storage/temperature/review", json=body)
    assert r.status_code == 422
    assert "严格递增" in r.text


def test_naive_time_returns_422():
    body = payload()
    body["readings"][0]["time"] = "2026-09-25T08:00:00"
    r = client.post("/api/v1/cold-storage/temperature/review", json=body)
    assert r.status_code == 422
    assert "时区" in r.text


def test_invalid_window_returns_422():
    body = payload(maintenance_windows=[
        {"start": "2026-09-25T09:00:00+08:00", "end": "2026-09-25T08:00:00+08:00"}
    ])
    r = client.post("/api/v1/cold-storage/temperature/review", json=body)
    assert r.status_code == 422


def test_duplicate_timestamps_returns_422():
    body = payload()
    body["readings"][1]["time"] = body["readings"][0]["time"]
    r = client.post("/api/v1/cold-storage/temperature/review", json=body)
    assert r.status_code == 422


def test_bad_interval_params_422():
    r = client.post(
        "/api/v1/cold-storage/temperature/review",
        json=payload(max_sample_interval_seconds=0),
    )
    assert r.status_code == 422
    r = client.post(
        "/api/v1/cold-storage/temperature/review",
        json=payload(min_alarm_duration_seconds=-1),
    )
    assert r.status_code == 422
