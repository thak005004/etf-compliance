"""Compliance engine: loads rules, runs data-quality checks, then compliance rules."""

from __future__ import annotations
import os
import pandas as pd
import yaml
from dataclasses import dataclass, field
from pathlib import Path

import checks as chk


@dataclass
class Issue:
    category: str          # "error" | "violation"
    rule: str
    severity: str          # "high" | "medium" | "low" | "error"
    ticker: str
    weight_pct: float | None
    detail: str
    status: str = "open"   # "open" | "acknowledged"


@dataclass
class Report:
    filename: str
    rules_version: str
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.category == "error"]

    @property
    def violations(self) -> list[Issue]:
        return [i for i in self.issues if i.category == "violation"]

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0


def _load_rules(rules_path: str | Path = "rules.yaml") -> dict:
    with open(rules_path) as f:
        return yaml.safe_load(f)


def _load_restricted(file_path: str, base_dir: str | Path = ".") -> set[str]:
    p = Path(base_dir) / file_path
    df = pd.read_csv(p)
    return set(df["ticker"].str.strip().tolist())


def _data_quality_checks(df: pd.DataFrame) -> list[Issue]:
    issues = []

    # Duplicate tickers
    dupes = df[df.duplicated("ticker", keep=False)]["ticker"].unique()
    for t in dupes:
        issues.append(Issue(
            category="error", rule="duplicate_ticker", severity="error",
            ticker=t, weight_pct=None,
            detail=f"Ticker '{t}' appears more than once in the file",
        ))

    # Missing weights
    missing = df[df["weight_pct"].isna()]
    for _, row in missing.iterrows():
        issues.append(Issue(
            category="error", rule="missing_weight", severity="error",
            ticker=row["ticker"], weight_pct=None,
            detail=f"Ticker '{row['ticker']}' has a missing weight",
        ))

    # Non-numeric weights (caught during parse; flag any that slipped through)
    non_num = df[pd.to_numeric(df["weight_pct"], errors="coerce").isna() & df["weight_pct"].notna()]
    for _, row in non_num.iterrows():
        issues.append(Issue(
            category="error", rule="non_numeric_weight", severity="error",
            ticker=row["ticker"], weight_pct=None,
            detail=f"Ticker '{row['ticker']}' has non-numeric weight '{row['weight_pct']}'",
        ))

    # Negative weights
    numeric_vals = pd.to_numeric(df["weight_pct"], errors="coerce")
    numeric_mask = numeric_vals.notna()
    neg = df[numeric_mask & (numeric_vals < 0)]
    for _, row in neg.iterrows():
        issues.append(Issue(
            category="error", rule="negative_weight", severity="error",
            ticker=row["ticker"], weight_pct=float(row["weight_pct"]),
            detail=f"Ticker '{row['ticker']}' has negative weight {row['weight_pct']:.2f}%",
        ))

    return issues


def run(
    df_raw: pd.DataFrame,
    filename: str = "upload",
    rules_path: str | Path = "rules.yaml",
    base_dir: str | Path = ".",
) -> Report:
    config = _load_rules(rules_path)
    rules_version = config.get("rules_version", "unknown")
    report = Report(filename=filename, rules_version=rules_version)

    df_raw = df_raw.copy()

    # 1. Data quality on raw data so non-numeric weights are caught before coercion
    dq_issues = _data_quality_checks(df_raw)
    report.issues.extend(dq_issues)
    if dq_issues:
        return report

    # 2. Coerce to numeric now that we know all weights are valid numbers
    df_raw["weight_pct"] = pd.to_numeric(df_raw["weight_pct"], errors="coerce")

    # 3. Work with clean subset (no NaN weights, no negatives, no dupes)
    df = df_raw.drop_duplicates("ticker").dropna(subset=["weight_pct"])
    df = df[df["weight_pct"] >= 0].reset_index(drop=True)

    rules = config["rules"]

    # max_single_holding
    r = rules["max_single_holding"]
    for v in chk.check_max_single_holding(df, r["threshold"]):
        report.issues.append(Issue(
            category="violation", rule="max_single_holding",
            severity=r["severity"], **v,
        ))

    # top5_concentration
    r = rules["top5_concentration"]
    for v in chk.check_top5_concentration(df, r["threshold"]):
        report.issues.append(Issue(
            category="violation", rule="top5_concentration",
            severity=r["severity"], **v,
        ))

    # weights_sum
    r = rules["weights_sum"]
    for v in chk.check_weights_sum(df, r["target"], r["tolerance"]):
        report.issues.append(Issue(
            category="violation", rule="weights_sum",
            severity=r["severity"], **v,
        ))

    # restricted_list
    r = rules["restricted_list"]
    restricted = _load_restricted(r["file"], base_dir)
    for v in chk.check_restricted_list(df, restricted):
        report.issues.append(Issue(
            category="violation", rule="restricted_list",
            severity=r["severity"], **v,
        ))

    # max_sector
    r = rules["max_sector"]
    for v in chk.check_max_sector(df, r["threshold"]):
        report.issues.append(Issue(
            category="violation", rule="max_sector",
            severity=r["severity"], **v,
        ))

    # min_holdings_count
    r = rules["min_holdings_count"]
    for v in chk.check_min_holdings_count(df, r["threshold"]):
        report.issues.append(Issue(
            category="violation", rule="min_holdings_count",
            severity=r["severity"], **v,
        ))

    return report
