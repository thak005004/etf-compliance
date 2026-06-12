"""ETF Compliance Checker — Streamlit app."""

from __future__ import annotations
import io
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

import audit
import engine
import checks as chk


def _get_api_key() -> str | None:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def _draft_memo(report: engine.Report, filename: str, api_key: str) -> str:
    import anthropic
    lines = []
    for v in report.violations:
        entry = f"- [{v.severity.upper()}] {v.rule}"
        if v.ticker:
            entry += f" — {v.ticker}"
        entry += f": {v.detail}"
        lines.append(entry)
    prompt = (
        f"You are a compliance officer drafting an internal memo for a portfolio management team.\n\n"
        f"Fund file: {filename}\n\n"
        f"The following compliance violations were detected:\n\n"
        + "\n".join(lines)
        + "\n\nDraft a concise professional compliance memo covering:\n"
        "1. An executive summary of what failed\n"
        "2. The severity and risk implications of each violation\n"
        "3. Recommended remediation actions\n\n"
        "Write in plain English suitable for a portfolio manager. Keep it under 400 words."
    )
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text

SAMPLES_DIR = Path("samples")
RULES_PATH = Path("rules.yaml")

st.set_page_config(page_title="ETF Compliance Checker", layout="wide")
st.title("ETF Compliance Checker")

# ── Sidebar: editable thresholds ─────────────────────────────────────────────
with st.sidebar:
    st.header("Rule Thresholds")
    with open(RULES_PATH) as f:
        base_config = yaml.safe_load(f)
    r = base_config["rules"]

    max_single = st.number_input(
        "Max single holding (%)", value=float(r["max_single_holding"]["threshold"]),
        min_value=1.0, max_value=100.0, step=0.5,
    )
    top5_thresh = st.number_input(
        "Top-5 concentration (%)", value=float(r["top5_concentration"]["threshold"]),
        min_value=1.0, max_value=100.0, step=0.5,
    )
    sum_tolerance = st.number_input(
        "Weights-sum tolerance (pp)", value=float(r["weights_sum"]["tolerance"]),
        min_value=0.01, max_value=5.0, step=0.05,
    )
    max_sec = st.number_input(
        "Max sector (%)", value=float(r["max_sector"]["threshold"]),
        min_value=1.0, max_value=100.0, step=0.5,
    )
    min_count = st.number_input(
        "Min holdings count", value=int(r["min_holdings_count"]["threshold"]),
        min_value=1, max_value=500, step=1,
    )
    recon_tol = st.number_input(
        "Reconciliation tolerance (pp)",
        value=float(base_config["reconciliation"]["weight_diff_tolerance_pp"]),
        min_value=0.01, max_value=5.0, step=0.01,
    )

    def _override_config(cfg: dict) -> dict:
        cfg = {**cfg}
        cfg["rules"] = {**cfg["rules"]}
        cfg["rules"]["max_single_holding"] = {**cfg["rules"]["max_single_holding"], "threshold": max_single}
        cfg["rules"]["top5_concentration"] = {**cfg["rules"]["top5_concentration"], "threshold": top5_thresh}
        cfg["rules"]["weights_sum"] = {**cfg["rules"]["weights_sum"], "tolerance": sum_tolerance}
        cfg["rules"]["max_sector"] = {**cfg["rules"]["max_sector"], "threshold": max_sec}
        cfg["rules"]["min_holdings_count"] = {**cfg["rules"]["min_holdings_count"], "threshold": int(min_count)}
        cfg["reconciliation"] = {**cfg["reconciliation"], "weight_diff_tolerance_pp": recon_tol}
        return cfg


# ── Mode tabs ─────────────────────────────────────────────────────────────────
tab_check, tab_recon, tab_history = st.tabs(["Compliance Check", "Reconciliation", "Past Runs"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Compliance Check
# ══════════════════════════════════════════════════════════════════════════════
with tab_check:
    st.subheader("Upload Holdings")

    col_up, col_samp = st.columns([2, 3])
    with col_up:
        uploaded = st.file_uploader("Upload a CSV", type="csv", key="main_upload")

    with col_samp:
        st.markdown("**Or load a sample:**")
        sc1, sc2, sc3 = st.columns(3)
        load_clean = sc1.button("Clean fund")
        load_violating = sc2.button("Violating fund")
        load_messy = sc3.button("Messy fund")

    # ── Persist the active file across Streamlit rerenders ────────────────
    # st.file_uploader stays truthy on every rerender while a file is held,
    # so we gate on (name, size) to avoid re-triggering the engine on unrelated
    # button clicks (e.g. the Acknowledge form submit).
    if uploaded:
        upload_id = f"{uploaded.name}_{uploaded.size}"
        if upload_id != st.session_state.get("_upload_id"):
            st.session_state["_upload_id"] = upload_id
            st.session_state["_file_bytes"] = uploaded.read()
            st.session_state["_filename"] = uploaded.name
            st.session_state.pop("_result_key", None)
    elif load_clean:
        st.session_state.pop("_upload_id", None)
        st.session_state["_file_bytes"] = (SAMPLES_DIR / "clean_fund.csv").read_bytes()
        st.session_state["_filename"] = "clean_fund.csv"
        st.session_state.pop("_result_key", None)
    elif load_violating:
        st.session_state.pop("_upload_id", None)
        st.session_state["_file_bytes"] = (SAMPLES_DIR / "violating_fund.csv").read_bytes()
        st.session_state["_filename"] = "violating_fund.csv"
        st.session_state.pop("_result_key", None)
    elif load_messy:
        st.session_state.pop("_upload_id", None)
        st.session_state["_file_bytes"] = (SAMPLES_DIR / "messy_fund.csv").read_bytes()
        st.session_state["_filename"] = "messy_fund.csv"
        st.session_state.pop("_result_key", None)

    file_bytes: bytes | None = st.session_state.get("_file_bytes")
    filename: str = st.session_state.get("_filename", "")

    if file_bytes:
        # ── Run engine, caching by (file hash × rules config hash) ───────
        cfg = _override_config(base_config)
        rules_yaml_bytes = yaml.dump(cfg, sort_keys=True).encode()
        rules_sha256 = audit.sha256_of_bytes(rules_yaml_bytes)
        file_sha256 = audit.sha256_of_bytes(file_bytes)
        result_key = f"{file_sha256}_{rules_sha256}"

        if st.session_state.get("_result_key") != result_key:
            df_raw = pd.read_csv(io.BytesIO(file_bytes))

            with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False, dir=".") as tmp:
                tmp.write(rules_yaml_bytes)
                tmp_rules = tmp.name
            try:
                report = engine.run(df_raw, filename=filename, rules_path=tmp_rules, base_dir=".")
            finally:
                os.unlink(tmp_rules)

            run_id = audit.log_run(
                filename=filename,
                file_bytes=file_bytes,
                rules_version=report.rules_version,
                rules_sha256=rules_sha256,
                error_count=len(report.errors),
                fail_count=len(report.violations),
            )

            st.session_state["_result_key"] = result_key
            st.session_state["_df_raw"] = df_raw
            st.session_state["_report"] = report
            st.session_state["_run_id"] = run_id

        df_raw: pd.DataFrame = st.session_state["_df_raw"]
        report = st.session_state["_report"]
        run_id: int = st.session_state["_run_id"]

        # Reload acknowledgments on every render so the form submit is reflected
        acks = audit.fetch_acknowledgments(run_id)
        ack_map: dict[int, dict] = {a["exception_index"]: a for a in acks}

        # ── Data-quality errors ───────────────────────────────────────────
        if report.errors:
            st.markdown("---")
            st.markdown("### Data Quality Errors")
            st.error(f"{len(report.errors)} error(s) found — compliance checks skipped until resolved.")
            err_df = pd.DataFrame([
                {"Rule": e.rule, "Ticker": e.ticker, "Detail": e.detail}
                for e in report.errors
            ])
            st.dataframe(err_df, use_container_width=True, hide_index=True)

        else:
            # ── Summary banner ────────────────────────────────────────────
            st.markdown("---")
            open_count = sum(1 for i in range(len(report.violations)) if i not in ack_map)
            if report.passed:
                st.success("All compliance checks passed.")
            elif open_count == 0:
                st.success(f"All {len(report.violations)} violation(s) acknowledged.")
            else:
                st.warning(
                    f"{len(report.violations)} violation(s) — "
                    f"{open_count} open, {len(ack_map)} acknowledged."
                )

            # ── Exceptions ────────────────────────────────────────────────
            if report.violations:
                st.markdown("### Exceptions")
                for i, v in enumerate(report.violations):
                    ack = ack_map.get(i)
                    tag = "✓" if ack else "○"
                    label = f"{tag}  #{i+1} · {v.severity.upper()} · {v.rule} — {v.ticker}"
                    with st.expander(label, expanded=(ack is None)):
                        st.markdown(f"**Detail:** {v.detail}")
                        if ack:
                            st.success(
                                f"**Acknowledged by:** {ack['acknowledged_by']}  \n"
                                f"**When:** {ack['acknowledged_at']}  \n"
                                f"**Reason:** {ack['reason']}"
                            )
                        else:
                            with st.form(key=f"ack_{run_id}_{i}"):
                                ack_name = st.text_input("Your name")
                                ack_reason = st.text_area("Reason for acknowledgment")
                                if st.form_submit_button("Acknowledge"):
                                    if ack_name.strip() and ack_reason.strip():
                                        audit.log_acknowledgment(
                                            run_id, i, v.rule, v.ticker,
                                            ack_name.strip(), ack_reason.strip(),
                                        )
                                        st.rerun()
                                    else:
                                        st.warning("Both name and reason are required.")

            # ── AI-drafted compliance memo ────────────────────────────────
            if report.violations:
                st.markdown("---")
                api_key = _get_api_key()
                memo_key = f"_memo_{run_id}"
                if api_key:
                    if st.button("Draft summary memo", key=f"memo_btn_{run_id}"):
                        with st.spinner("Drafting compliance memo…"):
                            try:
                                st.session_state[memo_key] = _draft_memo(report, filename, api_key)
                            except Exception as exc:
                                st.session_state[memo_key] = f"_error_{exc}"
                    if memo_key in st.session_state:
                        memo = st.session_state[memo_key]
                        if memo.startswith("_error_"):
                            st.error(f"Memo generation failed: {memo[7:]}")
                        else:
                            st.markdown("**Compliance Memo**")
                            st.markdown(memo)
                else:
                    st.info(
                        "Set ANTHROPIC_API_KEY in Streamlit secrets or environment "
                        "to enable AI-drafted compliance memos."
                    )

            # ── Exposure charts ───────────────────────────────────────────
            st.markdown("---")
            st.markdown("### Exposure Breakdown")
            chart1, chart2 = st.columns(2)

            with chart1:
                st.markdown("**By Sector**")
                sec_df = (
                    df_raw.assign(weight_pct=pd.to_numeric(df_raw["weight_pct"], errors="coerce"))
                    .groupby("sector", as_index=False)["weight_pct"]
                    .sum()
                    .sort_values("weight_pct", ascending=False)
                )
                st.bar_chart(sec_df.set_index("sector")["weight_pct"])

            with chart2:
                st.markdown("**By Asset Class**")
                ac_df = (
                    df_raw.assign(weight_pct=pd.to_numeric(df_raw["weight_pct"], errors="coerce"))
                    .groupby("asset_class", as_index=False)["weight_pct"]
                    .sum()
                    .sort_values("weight_pct", ascending=False)
                )
                st.bar_chart(ac_df.set_index("asset_class")["weight_pct"])

            # ── Download report ───────────────────────────────────────────
            st.markdown("---")
            all_issues = report.errors + report.violations
            if all_issues:
                dl_rows = []
                for issue in report.errors:
                    dl_rows.append({
                        "category": issue.category,
                        "rule": issue.rule,
                        "severity": issue.severity,
                        "ticker": issue.ticker,
                        "weight_pct": issue.weight_pct,
                        "detail": issue.detail,
                        "acknowledged_by": "",
                        "acknowledged_at": "",
                        "ack_reason": "",
                    })
                for j, issue in enumerate(report.violations):
                    ack = ack_map.get(j)
                    dl_rows.append({
                        "category": issue.category,
                        "rule": issue.rule,
                        "severity": issue.severity,
                        "ticker": issue.ticker,
                        "weight_pct": issue.weight_pct,
                        "detail": issue.detail,
                        "acknowledged_by": ack["acknowledged_by"] if ack else "",
                        "acknowledged_at": ack["acknowledged_at"] if ack else "",
                        "ack_reason": ack["reason"] if ack else "",
                    })
                st.download_button(
                    "Download Report CSV",
                    data=pd.DataFrame(dl_rows).to_csv(index=False).encode(),
                    file_name=f"compliance_report_{filename}",
                    mime="text/csv",
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Reconciliation
# ══════════════════════════════════════════════════════════════════════════════
with tab_recon:
    st.subheader("Reconcile Two Holdings Files")

    rc1, rc2 = st.columns(2)
    with rc1:
        st.markdown("**Primary file**")
        recon_primary = st.file_uploader("Primary CSV", type="csv", key="recon_primary")
        if st.button("Load clean_fund as primary"):
            st.session_state["recon_primary_bytes"] = (SAMPLES_DIR / "clean_fund.csv").read_bytes()
            st.session_state["recon_primary_name"] = "clean_fund.csv"

    with rc2:
        st.markdown("**Vendor / reference file**")
        recon_vendor = st.file_uploader("Vendor CSV", type="csv", key="recon_vendor")
        if st.button("Load holdings_vendor as vendor"):
            st.session_state["recon_vendor_bytes"] = (SAMPLES_DIR / "holdings_vendor.csv").read_bytes()
            st.session_state["recon_vendor_name"] = "holdings_vendor.csv"

    primary_bytes = (
        recon_primary.read() if recon_primary
        else st.session_state.get("recon_primary_bytes")
    )
    vendor_bytes = (
        recon_vendor.read() if recon_vendor
        else st.session_state.get("recon_vendor_bytes")
    )

    if primary_bytes and vendor_bytes:
        df_p = pd.read_csv(io.BytesIO(primary_bytes))
        df_v = pd.read_csv(io.BytesIO(vendor_bytes))

        df_p["weight_pct"] = pd.to_numeric(df_p["weight_pct"], errors="coerce")
        df_v["weight_pct"] = pd.to_numeric(df_v["weight_pct"], errors="coerce")

        diffs = chk.reconcile_weights(df_p, df_v, recon_tol)

        if diffs.empty:
            st.success(f"No weight differences exceed {recon_tol}pp tolerance.")
        else:
            st.warning(f"{len(diffs)} ticker(s) with weight differences > {recon_tol}pp")
            st.dataframe(
                diffs[["ticker", "weight_pct_primary", "weight_pct_vendor", "weight_diff_pp", "detail"]],
                use_container_width=True, hide_index=True,
            )
            st.download_button(
                "Download Reconciliation CSV",
                data=diffs.to_csv(index=False).encode(),
                file_name="reconciliation_diffs.csv",
                mime="text/csv",
            )
    else:
        st.info("Upload or load both files to run reconciliation.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Past Runs
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.subheader("Audit Log — Past Runs")
    runs = audit.fetch_runs(limit=100)
    if not runs:
        st.info("No runs recorded yet.")
    else:
        # ── Trend: violation counts over time ─────────────────────────────
        trend_df = (
            pd.DataFrame(runs)[["run_at", "fail_count"]]
            .copy()
            .assign(run_at=lambda d: pd.to_datetime(d["run_at"]))
            .sort_values("run_at")
            .set_index("run_at")
        )
        st.markdown("**Violation Count Over Time**")
        st.line_chart(trend_df["fail_count"])
        st.caption("Each point is one compliance run. Runs with data-quality errors show 0 violations.")

        st.markdown("---")

        # ── History table ─────────────────────────────────────────────────
        hist_df = pd.DataFrame(runs)
        hist_df["pass"] = hist_df["pass"].map({1: "PASS", 0: "FAIL"})
        for col in ("file_sha256", "rules_sha256"):
            hist_df[col] = hist_df[col].apply(
                lambda h: (h[:12] + "…") if isinstance(h, str) else "—"
            )
        hist_df = hist_df.drop(columns=["id"], errors="ignore")
        hist_df = hist_df.rename(columns={
            "run_at": "Run At (UTC)",
            "filename": "File",
            "file_sha256": "File SHA256",
            "rules_sha256": "Rules SHA256",
            "rules_version": "Rules Ver",
            "error_count": "Errors",
            "fail_count": "Violations",
            "pass": "Result",
        })
        st.dataframe(hist_df, use_container_width=True, hide_index=True)
