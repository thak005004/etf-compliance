"""Pure compliance check functions. Each returns a list of violation dicts."""

from __future__ import annotations
import pandas as pd
from typing import Any


def check_max_single_holding(df: pd.DataFrame, threshold: float) -> list[dict]:
    violations = []
    for _, row in df.iterrows():
        w = row["weight_pct"]
        if w > threshold:
            excess = round(w - threshold, 4)
            violations.append({
                "ticker": row["ticker"],
                "weight_pct": w,
                "detail": f"{row['ticker']} at {w:.1f}%, exceeds {threshold}% by {excess:.1f}pp",
            })
    return violations


def check_top5_concentration(df: pd.DataFrame, threshold: float) -> list[dict]:
    top5 = df.nlargest(5, "weight_pct")
    total = round(top5["weight_pct"].sum(), 4)
    if total > threshold:
        excess = round(total - threshold, 4)
        tickers = ", ".join(top5["ticker"].tolist())
        return [{
            "ticker": tickers,
            "weight_pct": total,
            "detail": f"Top-5 ({tickers}) combined {total:.1f}%, exceeds {threshold}% by {excess:.1f}pp",
        }]
    return []


def check_weights_sum(df: pd.DataFrame, target: float, tolerance: float) -> list[dict]:
    total = round(df["weight_pct"].sum(), 4)
    diff = abs(total - target)
    if diff > tolerance:
        direction = "over" if total > target else "under"
        return [{
            "ticker": "PORTFOLIO",
            "weight_pct": total,
            "detail": f"Weights sum to {total:.2f}%, {direction} target {target}% by {diff:.2f}pp (tolerance ±{tolerance}pp)",
        }]
    return []


def check_restricted_list(df: pd.DataFrame, restricted_tickers: set[str]) -> list[dict]:
    violations = []
    for _, row in df.iterrows():
        if row["ticker"] in restricted_tickers:
            violations.append({
                "ticker": row["ticker"],
                "weight_pct": row["weight_pct"],
                "detail": f"{row['ticker']} appears on the restricted ticker list at {row['weight_pct']:.2f}%",
            })
    return violations


def check_max_sector(df: pd.DataFrame, threshold: float) -> list[dict]:
    sector_weights = df.groupby("sector")["weight_pct"].sum()
    violations = []
    for sector, total in sector_weights.items():
        total = round(total, 4)
        if total > threshold:
            excess = round(total - threshold, 4)
            tickers = ", ".join(df[df["sector"] == sector]["ticker"].tolist())
            violations.append({
                "ticker": sector,
                "weight_pct": total,
                "detail": f"Sector '{sector}' ({tickers}) at {total:.1f}%, exceeds {threshold}% by {excess:.1f}pp",
            })
    return violations


def check_min_holdings_count(df: pd.DataFrame, threshold: int) -> list[dict]:
    count = len(df)
    if count < threshold:
        shortfall = threshold - count
        return [{
            "ticker": "PORTFOLIO",
            "weight_pct": None,
            "detail": f"Portfolio has {count} holdings, below minimum {threshold} by {shortfall}",
        }]
    return []


def reconcile_weights(
    df_primary: pd.DataFrame,
    df_vendor: pd.DataFrame,
    tolerance: float,
) -> pd.DataFrame:
    """Join on ticker, return rows where |weight_diff| > tolerance."""
    merged = df_primary[["ticker", "weight_pct"]].merge(
        df_vendor[["ticker", "weight_pct"]],
        on="ticker",
        how="outer",
        suffixes=("_primary", "_vendor"),
    )
    merged["weight_diff_pp"] = (
        merged["weight_pct_primary"] - merged["weight_pct_vendor"]
    ).round(4)
    diffs = merged[merged["weight_diff_pp"].abs() > tolerance].copy()
    diffs["detail"] = diffs.apply(
        lambda r: (
            f"{r['ticker']}: primary {r['weight_pct_primary']:.2f}% vs vendor "
            f"{r['weight_pct_vendor']:.2f}% (diff {r['weight_diff_pp']:+.2f}pp)"
        ),
        axis=1,
    )
    return diffs.reset_index(drop=True)
