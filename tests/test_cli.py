"""Tests for cli.py exit-code behavior."""

from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SAMPLES = ROOT / "samples"
CLI = ROOT / "cli.py"


def _run(csv_name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), str(SAMPLES / csv_name)],
        capture_output=True,
        text=True,
    )


def test_clean_fund_exits_0():
    assert _run("clean_fund.csv").returncode == 0


def test_violating_fund_exits_1():
    assert _run("violating_fund.csv").returncode == 1


def test_messy_fund_exits_1():
    assert _run("messy_fund.csv").returncode == 1


def test_clean_fund_stdout_says_pass():
    result = _run("clean_fund.csv")
    assert "PASS" in result.stdout


def test_violating_fund_stdout_says_fail():
    result = _run("violating_fund.csv")
    assert "FAIL" in result.stdout


def test_violating_fund_stdout_lists_violations():
    result = _run("violating_fund.csv")
    assert "COMPLIANCE VIOLATIONS" in result.stdout


def test_messy_fund_stdout_lists_dq_errors():
    result = _run("messy_fund.csv")
    assert "DATA QUALITY ERRORS" in result.stdout


def test_missing_file_exits_1():
    result = subprocess.run(
        [sys.executable, str(CLI), str(SAMPLES / "nonexistent_fund.csv")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
