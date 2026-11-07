"""Serving the UI behind Home Assistant's ingress proxy, and the access guard."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_index_gets_base_for_ingress_prefix(client):
    html = client.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc_123"}).text
    assert '<base href="/api/hassio_ingress/abc_123/" />' in html


def test_index_without_proxy_has_no_base(client):
    assert "<base" not in client.get("/").text


def test_unsafe_ingress_prefix_is_ignored(client):
    html = client.get("/", headers={"X-Ingress-Path": '/x"><script>alert(1)</script>'}).text
    assert "<base" not in html and "<script>alert" not in html


def test_assets_are_cached_forever(client):
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_index_is_never_cached(client):
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_deep_links_get_the_app_and_unknown_api_paths_404(client):
    assert "<html>" in client.get("/net-worth").text
    response = client.get("/api/not-a-thing")
    assert response.status_code == 404 and response.json()["detail"] == "Not found"


def test_path_traversal_returns_the_app_not_files(client):
    response = client.get("/../../etc/passwd")
    assert response.status_code in (200, 404)
    assert "root:" not in response.text


def test_large_responses_are_compressed(client):
    response = client.get("/api/dashboard", headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


def test_guard_blocks_unknown_clients(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_client_ips", "172.30.32.2")
    # No `with`: these clients share the app the session fixture already started,
    # instead of starting (and stopping) its lifecycle a second time.
    outsider = TestClient(app, client=("203.0.113.9", 5000))
    for path in ("/", "/api/dashboard", "/api/transactions", "/assets/index-abc123.js"):
        assert outsider.get(path).status_code == 403, path
    assert outsider.post("/api/sync").status_code == 403
    # A forged forwarding header doesn't change who's actually connected.
    forged = outsider.get("/api/dashboard", headers={"X-Forwarded-For": "172.30.32.2"})
    assert forged.status_code == 403


def test_health_check_always_answers_so_watchdogs_never_loop(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_client_ips", "172.30.32.2")
    watchdog = TestClient(app, client=("172.30.33.5", 5000))
    assert watchdog.get("/api/health").status_code == 200


def test_guard_allows_ingress_proxy_and_loopback(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_client_ips", "172.30.32.2")
    proxy = TestClient(app, client=("172.30.32.2", 5000))
    assert proxy.get("/api/dashboard").status_code == 200
    probe = TestClient(app, client=("127.0.0.1", 5000))
    assert probe.get("/api/dashboard").status_code == 200
