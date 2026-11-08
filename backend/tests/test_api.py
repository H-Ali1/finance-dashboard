"""Every screen's data loads, and the main write paths work, on demo data."""

from __future__ import annotations

import pytest

READ_ENDPOINTS = [
    "/api/health",
    "/api/connections",
    "/api/accounts",
    "/api/dashboard",
    "/api/insights",
    "/api/subscriptions",
    "/api/cashflow",
    "/api/networth",
    "/api/networth/history",
    "/api/transactions",
    "/api/anomalies",
    "/api/spending/categories",
    "/api/spending/trend",
    "/api/spending/merchants",
    "/api/spending/accounts",
    "/api/spending/budgets",
    "/api/spending/by-category-trend",
    "/api/categories",
    "/api/rules",
    "/api/budgets",
    "/api/assets",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_read_endpoint_ok(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[:300]


def test_health_reports_schema_and_jobs(client):
    from app import migrations

    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["schema_version"] == migrations.LATEST
    assert body["jobs"]["running"] is True
    assert body["transactions"] > 500


def test_demo_accounts_are_the_real_banks(client):
    institutions = {a["institution"] for a in client.get("/api/accounts").json()["accounts"]}
    assert institutions == {"Starling", "HSBC", "American Express", "Trading 212"}


def test_sync_with_nothing_configured_is_clean(client):
    body = client.post("/api/sync").json()
    assert body["results"] == [] and body["errors"] == []


def test_statement_import_generic_layout(client):
    created = client.post("/api/accounts/manual", data={"name": "Test import account", "kind": "current"}).json()
    csv = (
        "Transaction Date,Transaction Description,Debit Amount,Credit Amount\n"
        "03/11/2026,TESCO STORES 9999,12.34,\n"
        "04/11/2026,TUTORING J SMITH,,40.00\n"
    )
    files = {"file": ("statement.csv", csv, "text/csv")}
    first = client.post("/api/import/csv", data={"account_id": str(created["id"])}, files=files).json()
    assert (first["detected_format"], first["added"]) == ("generic", 2)

    again = client.post("/api/import/csv", data={"account_id": str(created["id"])}, files=files).json()
    assert again["added"] == 0 and again["duplicates_skipped"] == 2


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/connections/institutions"),
        ("get", "/api/connections/gocardless/callback"),
        ("post", "/api/connections/gocardless/link"),
    ],
)
def test_removed_gocardless_routes_are_gone(client, method, path):
    assert getattr(client, method)(path).status_code in (404, 405)
