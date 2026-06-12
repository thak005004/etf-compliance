"""Tests for pure check functions in checks.py."""

import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import checks as chk


def _make_df(rows):
    return pd.DataFrame(rows, columns=["ticker", "name", "asset_class", "sector", "weight_pct"])


# ── max_single_holding ────────────────────────────────────────────────────────

def test_max_single_holding_no_violation():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Technology", 24.9),
        ("MSFT", "Microsoft", "Equity", "Technology", 10.0),
    ])
    assert chk.check_max_single_holding(df, 25.0) == []


def test_max_single_holding_violation():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Technology", 31.2),
        ("MSFT", "Microsoft", "Equity", "Technology", 10.0),
    ])
    result = chk.check_max_single_holding(df, 25.0)
    assert len(result) == 1
    assert result[0]["ticker"] == "AAPL"
    assert "31.2" in result[0]["detail"]
    assert "6.2pp" in result[0]["detail"]


# ── top5_concentration ────────────────────────────────────────────────────────

def test_top5_concentration_no_violation():
    rows = [(f"T{i}", f"Co{i}", "Equity", "Tech", 8.0) for i in range(10)]
    df = _make_df(rows)
    assert chk.check_top5_concentration(df, 50.0) == []


def test_top5_concentration_violation():
    rows = [
        ("A", "Co A", "Equity", "Tech", 15.0),
        ("B", "Co B", "Equity", "Tech", 14.0),
        ("C", "Co C", "Equity", "Tech", 12.0),
        ("D", "Co D", "Equity", "Tech", 10.0),
        ("E", "Co E", "Equity", "Tech", 9.0),
        ("F", "Co F", "Equity", "Tech", 5.0),
    ]
    df = _make_df(rows)
    result = chk.check_top5_concentration(df, 50.0)
    assert len(result) == 1
    assert result[0]["weight_pct"] == 60.0
    assert "60.0%" in result[0]["detail"]
    assert "10.0pp" in result[0]["detail"]


# ── weights_sum ───────────────────────────────────────────────────────────────

def test_weights_sum_passes_within_tolerance():
    df = _make_df([
        ("A", "Co A", "Equity", "Tech", 50.3),
        ("B", "Co B", "Equity", "Tech", 50.0),
    ])
    assert chk.check_weights_sum(df, 100.0, 0.5) == []


def test_weights_sum_fails_outside_tolerance():
    df = _make_df([
        ("A", "Co A", "Equity", "Tech", 50.0),
        ("B", "Co B", "Equity", "Tech", 46.0),
    ])
    result = chk.check_weights_sum(df, 100.0, 0.5)
    assert len(result) == 1
    assert "96.00%" in result[0]["detail"]
    assert "under" in result[0]["detail"]


# ── restricted_list ───────────────────────────────────────────────────────────

def test_restricted_list_hit():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Tech", 5.0),
        ("BADCO", "Bad Co", "Equity", "Finance", 2.0),
    ])
    result = chk.check_restricted_list(df, {"BADCO", "FRDC"})
    assert len(result) == 1
    assert result[0]["ticker"] == "BADCO"


def test_restricted_list_clean():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Tech", 5.0),
        ("MSFT", "Microsoft", "Equity", "Tech", 4.0),
    ])
    assert chk.check_restricted_list(df, {"BADCO", "FRDC"}) == []


# ── max_sector ────────────────────────────────────────────────────────────────

def test_max_sector_violation():
    df = _make_df([
        ("AAPL", "Apple", "Equity", "Technology", 20.0),
        ("MSFT", "Microsoft", "Equity", "Technology", 15.0),
        ("NVDA", "NVIDIA", "Equity", "Technology", 12.0),
        ("JPM", "JPMorgan", "Equity", "Financials", 10.0),
    ])
    result = chk.check_max_sector(df, 40.0)
    assert len(result) == 1
    assert result[0]["ticker"] == "Technology"
    assert result[0]["weight_pct"] == 47.0


# ── reconcile_weights ─────────────────────────────────────────────────────────

def test_reconcile_detects_diff():
    primary = pd.DataFrame({
        "ticker": ["AAPL", "MSFT", "AMZN"],
        "weight_pct": [5.0, 4.0, 3.0],
    })
    vendor = pd.DataFrame({
        "ticker": ["AAPL", "MSFT", "AMZN"],
        "weight_pct": [5.0, 4.0, 3.5],
    })
    result = chk.reconcile_weights(primary, vendor, tolerance=0.1)
    assert len(result) == 1
    assert result.iloc[0]["ticker"] == "AMZN"
    assert result.iloc[0]["weight_diff_pp"] == pytest.approx(-0.5)
