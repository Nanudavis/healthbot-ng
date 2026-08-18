"""The root route is the system's table of contents, not a JSON 404."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_returns_toc_page():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "HealthBot NG" in body
    assert "table of contents" in body.lower()
    for link in ("/dashboard/", "/survey", "/docs", "/openapi.json", "/health",
                 "/webhook/whatsapp", "/webhook/ussd"):
        assert f'href="{link}"' in body, link


def test_root_is_public_no_auth_needed():
    r = client.get("/")
    assert r.status_code == 200
