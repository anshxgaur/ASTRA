"""
12_TESTS — API smoke tests for the Phase-4 frontapi (api/app.py).

Run with: pytest pipeline/12_TESTS/TEST_API.py -v

DB-dependent tests auto-skip when PostgreSQL is not reachable (CI has no
database, so they skip there; locally they run against the seeded store).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.app import app  # noqa: E402

client = TestClient(app)


def _db_reachable() -> bool:
    try:
        resp = client.get("/health")
        return resp.status_code == 200 and resp.json().get("database", {}).get("ok") is True
    except Exception:  # noqa: BLE001 — any failure means "not reachable"
        return False


@pytest.fixture(scope="module")
def db_ready():
    if not _db_reachable():
        pytest.skip("PostgreSQL not reachable — DB-dependent API tests skipped")
    return True


# ---------------------------------------------------------------------------
# DB-independent tests (always run)
# ---------------------------------------------------------------------------

def test_home_serves_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<title>" in resp.text


def test_index_describes_api():
    resp = client.get("/api")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "AICTE Unified Search API (Phase 4)"
    assert "/search?q=..." in body["endpoints"]


def test_health_reports_status():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "database" in body
    assert "llm" in body


def test_conflicts_returns_ground_truth():
    resp = client.get("/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert body["summary"]["conflicts"] >= 0
    assert body["summary"]["duplicates"] >= 0
    assert body["summary"]["orphans"] >= 0


def test_conflicts_kind_filter():
    resp = client.get("/conflicts?kind=duplicate")
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body


def test_conflicts_bad_kind_rejected():
    resp = client.get("/conflicts?kind=banana")
    assert resp.status_code == 422


def test_search_requires_query():
    resp = client.get("/search")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DB-dependent tests (skip when PostgreSQL is unreachable)
# ---------------------------------------------------------------------------

def test_search_rule_based(db_ready):
    resp = client.get("/search", params={"q": "How many approved colleges in Uttar Pradesh"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule_matched"] is True
    assert body["structured"] is not None


def test_search_vector_hits(db_ready):
    resp = client.get("/search", params={"q": "engineering college", "top_k": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert "vector" in body
    assert body["count"] <= 3


def test_answer_returns_text(db_ready):
    resp = client.get("/answer", params={"q": "How many approved colleges in Uttar Pradesh"})
    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert body["answer"]


def test_entity_lookup(db_ready):
    resp = client.get("/search", params={"q": "engineering college", "top_k": 1})
    hits = resp.json().get("vector") or []
    if not hits:
        pytest.skip("no vector hits to look up")
    entity_id = hits[0]["entity_id"]
    detail = client.get(f"/entity/{entity_id}")
    assert detail.status_code == 200
    assert detail.json()["entity_id"] == entity_id


def test_unknown_entity_404(db_ready):
    resp = client.get("/entity/DOES_NOT_EXIST_12345")
    assert resp.status_code == 404