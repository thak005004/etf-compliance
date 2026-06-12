"""Headless CLI for ETF compliance checking.

Usage:
    python cli.py <holdings.csv>

Exit codes:
    0 — all checks passed
    1 — any compliance violations or data-quality errors
"""

from __future__ import annotations
import sys
import argparse
import io
import pandas as pd
from pathlib import Path

# Resolve project root so this works regardless of working directory
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import engine


def _print_section(title: str, items: list) -> None:
    print(f"\n{title} ({len(items)})")
    print("-" * 60)
    for item in items:
        ticker_part = f" — {item.ticker}" if item.ticker else ""
        print(f"  [{item.severity.upper()}] {item.rule}{ticker_part}")
        print(f"    {item.detail}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETF Compliance Checker — headless CLI mode"
    )
    parser.add_argument("holdings", help="Path to holdings CSV file")
    parser.add_argument(
        "--rules", default=str(ROOT / "rules.yaml"),
        help="Path to rules YAML (default: rules.yaml in project root)",
    )
    args = parser.parse_args()

    holdings_path = Path(args.holdings)
    if not holdings_path.exists():
        print(f"ERROR: File not found: {holdings_path}", file=sys.stderr)
        sys.exit(1)

    try:
        df_raw = pd.read_csv(holdings_path)
    except Exception as exc:
        print(f"ERROR: Could not read CSV: {exc}", file=sys.stderr)
        sys.exit(1)

    report = engine.run(
        df_raw,
        filename=holdings_path.name,
        rules_path=args.rules,
        base_dir=str(ROOT),
    )

    print(f"File:          {holdings_path}")
    print(f"Rules version: {report.rules_version}")

    if report.errors:
        _print_section("DATA QUALITY ERRORS", report.errors)

    if report.violations:
        _print_section("COMPLIANCE VIOLATIONS", report.violations)

    print()
    if not report.errors and not report.violations:
        print("RESULT: PASS — all compliance checks passed.")
        sys.exit(0)
    else:
        parts = []
        if report.errors:
            parts.append(f"{len(report.errors)} data quality error(s)")
        if report.violations:
            parts.append(f"{len(report.violations)} compliance violation(s)")
        print(f"RESULT: FAIL — {', '.join(parts)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
