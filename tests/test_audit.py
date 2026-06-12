"""Tests for audit.py — log_run, log_acknowledgment, fetch_acknowledgments, fetch_runs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import audit


@pytest.fixture
def db(tmp_path):
    return tmp_path / "test.db"


# ── sha256_of_bytes ───────────────────────────────────────────────────────────

def test_sha256_deterministic():
    h = audit.sha256_of_bytes(b"hello")
    assert len(h) == 64
    assert h == audit.sha256_of_bytes(b"hello")
    assert h != audit.sha256_of_bytes(b"world")


# ── log_run / fetch_runs ──────────────────────────────────────────────────────

def test_log_run_returns_positive_id(db):
    run_id = audit.log_run("f.csv", b"data", "1.0.0", 0, 0, db_path=db)
    assert isinstance(run_id, int) and run_id > 0


def test_log_run_stores_rules_sha256(db):
    h = audit.sha256_of_bytes(b"my rules")
    audit.log_run("f.csv", b"data", "1.0.0", 0, 0, rules_sha256=h, db_path=db)
    runs = audit.fetch_runs(db_path=db)
    assert runs[0]["rules_sha256"] == h


def test_log_run_rules_sha256_defaults_none(db):
    audit.log_run("f.csv", b"data", "1.0.0", 0, 0, db_path=db)
    runs = audit.fetch_runs(db_path=db)
    assert runs[0]["rules_sha256"] is None


def test_log_run_pass_flag(db):
    audit.log_run("a.csv", b"x", "1.0", 0, 0, db_path=db)   # pass
    audit.log_run("b.csv", b"y", "1.0", 0, 2, db_path=db)   # violations
    audit.log_run("c.csv", b"z", "1.0", 1, 0, db_path=db)   # errors
    by_file = {r["filename"]: r["pass"] for r in audit.fetch_runs(db_path=db)}
    assert by_file["a.csv"] == 1
    assert by_file["b.csv"] == 0
    assert by_file["c.csv"] == 0


def test_fetch_runs_newest_first(db):
    audit.log_run("first.csv", b"a", "1.0", 0, 0, db_path=db)
    audit.log_run("second.csv", b"b", "1.0", 0, 0, db_path=db)
    runs = audit.fetch_runs(db_path=db)
    assert runs[0]["filename"] == "second.csv"
    assert runs[1]["filename"] == "first.csv"


def test_fetch_runs_limit(db):
    for i in range(5):
        audit.log_run(f"{i}.csv", b"x", "1.0", 0, 0, db_path=db)
    assert len(audit.fetch_runs(limit=3, db_path=db)) == 3


# ── log_acknowledgment / fetch_acknowledgments ────────────────────────────────

def test_log_and_fetch_acknowledgment(db):
    run_id = audit.log_run("test.csv", b"d", "1.0", 0, 2, db_path=db)
    audit.log_acknowledgment(
        run_id=run_id,
        exception_index=0,
        rule="max_single_holding",
        ticker="AAPL",
        acknowledged_by="Alice",
        reason="Approved by investment committee",
        db_path=db,
    )
    acks = audit.fetch_acknowledgments(run_id, db_path=db)
    assert len(acks) == 1
    a = acks[0]
    assert a["exception_index"] == 0
    assert a["rule"] == "max_single_holding"
    assert a["ticker"] == "AAPL"
    assert a["acknowledged_by"] == "Alice"
    assert a["reason"] == "Approved by investment committee"
    assert "acknowledged_at" in a and a["acknowledged_at"]


def test_acknowledgment_overwrite(db):
    run_id = audit.log_run("test.csv", b"d", "1.0", 0, 1, db_path=db)
    audit.log_acknowledgment(run_id, 0, "rule", "T", "Alice", "first", db_path=db)
    audit.log_acknowledgment(run_id, 0, "rule", "T", "Bob", "updated", db_path=db)
    acks = audit.fetch_acknowledgments(run_id, db_path=db)
    assert len(acks) == 1
    assert acks[0]["acknowledged_by"] == "Bob"
    assert acks[0]["reason"] == "updated"


def test_multiple_acknowledgments_for_one_run(db):
    run_id = audit.log_run("test.csv", b"d", "1.0", 0, 3, db_path=db)
    audit.log_acknowledgment(run_id, 0, "rule_a", "A", "Alice", "reason A", db_path=db)
    audit.log_acknowledgment(run_id, 2, "rule_b", "B", "Bob", "reason B", db_path=db)
    acks = audit.fetch_acknowledgments(run_id, db_path=db)
    assert len(acks) == 2
    assert acks[0]["exception_index"] == 0
    assert acks[1]["exception_index"] == 2


def test_fetch_acknowledgments_empty(db):
    run_id = audit.log_run("test.csv", b"d", "1.0", 0, 0, db_path=db)
    assert audit.fetch_acknowledgments(run_id, db_path=db) == []


def test_acknowledgments_isolated_per_run(db):
    run1 = audit.log_run("a.csv", b"x", "1.0", 0, 1, db_path=db)
    run2 = audit.log_run("b.csv", b"y", "1.0", 0, 1, db_path=db)
    audit.log_acknowledgment(run1, 0, "rule", "T", "Alice", "for run1", db_path=db)
    assert audit.fetch_acknowledgments(run2, db_path=db) == []
    assert len(audit.fetch_acknowledgments(run1, db_path=db)) == 1
