"""Tests for engine.py — data-quality checks and end-to-end run."""

import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import engine


def _make_df(rows):
    return pd.DataFrame(rows, columns=["ticker", "name", "asset_class", "sector", "weight_pct"])


RULES_PATH = Path(__file__).parent.parent / "rules.yaml"


# ── Data-quality: duplicate tickers ──────────────────────────────────────────

def test_dq_duplicate_ticker():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Tech", 5.0),
        ("AAPL", "Apple Dup", "Equity", "Tech", 5.0),
    ])
    r = engine.run(df, rules_path=RULES_PATH)
    assert any(e.rule == "duplicate_ticker" for e in r.errors)
    assert r.violations == []


# ── Data-quality: missing weight ──────────────────────────────────────────────

def test_dq_missing_weight():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Tech", None),
        ("MSFT", "Microsoft", "Equity", "Tech", 5.0),
    ])
    r = engine.run(df, rules_path=RULES_PATH)
    assert any(e.rule == "missing_weight" and e.ticker == "AAPL" for e in r.errors)
    assert r.violations == []


# ── Data-quality: negative weight ────────────────────────────────────────────

def test_dq_negative_weight():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Tech", -1.0),
        ("MSFT", "Microsoft", "Equity", "Tech", 5.0),
    ])
    r = engine.run(df, rules_path=RULES_PATH)
    assert any(e.rule == "negative_weight" and e.ticker == "AAPL" for e in r.errors)
    assert r.violations == []


# ── Data-quality: non-numeric weight ─────────────────────────────────────────

def test_dq_non_numeric_weight():
    df = pd.DataFrame([
        {"ticker": "AAPL", "name": "Apple", "asset_class": "Equity", "sector": "Tech", "weight_pct": "N/A"},
        {"ticker": "MSFT", "name": "Microsoft", "asset_class": "Equity", "sector": "Tech", "weight_pct": 5.0},
    ])
    r = engine.run(df, rules_path=RULES_PATH)
    assert any(e.rule == "non_numeric_weight" and e.ticker == "AAPL" for e in r.errors)


# ── End-to-end: clean fund passes ────────────────────────────────────────────

def test_clean_fund_passes():
    df = pd.read_csv(Path(__file__).parent.parent / "samples" / "clean_fund.csv")
    r = engine.run(df, rules_path=RULES_PATH, base_dir=Path(__file__).parent.parent)
    assert r.errors == []
    assert r.violations == []
    assert r.passed is True


# ── End-to-end: violating fund catches all expected violations ────────────────

def test_violating_fund_violations():
    df = pd.read_csv(Path(__file__).parent.parent / "samples" / "violating_fund.csv")
    r = engine.run(df, rules_path=RULES_PATH, base_dir=Path(__file__).parent.parent)
    assert r.errors == []
    violated_rules = {v.rule for v in r.violations}
    assert "max_single_holding" in violated_rules
    assert "top5_concentration" in violated_rules
    assert "restricted_list" in violated_rules
    assert "max_sector" in violated_rules


# ── End-to-end: messy fund returns only DQ errors ────────────────────────────

def test_messy_fund_data_quality_errors():
    df = pd.read_csv(Path(__file__).parent.parent / "samples" / "messy_fund.csv")
    r = engine.run(df, rules_path=RULES_PATH, base_dir=Path(__file__).parent.parent)
    assert len(r.errors) > 0
    assert r.violations == [], "compliance checks should be skipped when DQ errors exist"
    error_rules = {e.rule for e in r.errors}
    assert "duplicate_ticker" in error_rules
    assert "negative_weight" in error_rules
    assert "missing_weight" in error_rules


# ── Report helpers ────────────────────────────────────────────────────────────

def test_report_passed_property():
    df = pd.read_csv(Path(__file__).parent.parent / "samples" / "clean_fund.csv")
    r = engine.run(df, rules_path=RULES_PATH, base_dir=Path(__file__).parent.parent)
    assert r.passed is True
    assert r.errors == []
    assert r.violations == []
