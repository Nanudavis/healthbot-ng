"""Console authentication: session cookie gate on console APIs."""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "CONSOLE_AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "console-token")


def test_console_api_requires_login(auth_on):
    client = TestClient(app)
    assert client.get("/api/stats/summary").status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/knowledge").status_code == 401


def test_login_rejects_wrong_token(auth_on):
    client = TestClient(app)
    r = client.post("/api/auth/login", data={"token": "wrong"})
    assert r.status_code == 403


def test_login_sets_cookie_and_unlocks_console(auth_on):
    client = TestClient(app)
    r = client.post("/api/auth/login", data={"token": "console-token"})
    assert r.status_code == 200
    assert "healthbot_session" in r.headers.get("set-cookie", "")
    assert client.get("/api/stats/summary").status_code == 200


def test_logout_revokes_session(auth_on):
    client = TestClient(app)
    client.post("/api/auth/login", data={"token": "console-token"})
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/stats/summary").status_code == 401


def test_status_reports_auth_state(auth_on):
    client = TestClient(app)
    assert client.get("/api/auth/status").json()["authenticated"] is False
    client.post("/api/auth/login", data={"token": "console-token"})
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_webhook_survey_and_sus_submission_stay_public(auth_on):
    client = TestClient(app)
    assert (
        client.post(
            "/webhook/ussd",
            data={"sessionId": "x", "phoneNumber": "+2348000000000", "text": ""},
        ).status_code
        == 200
    )
    assert client.get("/survey").status_code == 200
    r = client.post(
        "/api/sus",
        data={"participant_code": "P01", "answers": "1,2,3,4,5,1,2,3,4,5"},
    )
    assert r.status_code == 200


def test_disabled_auth_leaves_console_open(monkeypatch):
    monkeypatch.setattr(config, "CONSOLE_AUTH_REQUIRED", False)
    client = TestClient(app)
    assert client.get("/api/stats/summary").status_code == 200
