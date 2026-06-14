# ETF Compliance Checker

A portfolio compliance tool for ETF operations teams, compliance analysts, and fund administrators who need a fast, auditable way to verify that a fund's holdings satisfy concentration limits, restricted-ticker policies, and weights-sum constraints before NAV publication or regulatory filing. Upload a holdings CSV, see which rules pass or fail, acknowledge exceptions with a name and reason that persists to an audit log, reconcile weights against a vendor file, and track the compliance history of every run, including which exact version of the rules was in effect.

Live app: https://etf-compliance.streamlit.app/

## Quickstart

```bash
pip install -r requirements.txt
```

**Web UI**
```bash
streamlit run app.py
```
Open `http://localhost:8501`. Use the sample buttons to load `clean_fund.csv`, `violating_fund.csv`, or `messy_fund.csv`, or upload your own holdings CSV. Thresholds are editable in the sidebar and take effect immediately.

**Headless CLI** (for CI pipelines or scheduled jobs)
```bash
python cli.py samples/violating_fund.csv
# exit 0 → all checks passed
# exit 1 → any violations or data-quality errors

python cli.py holdings.csv --rules custom_rules.yaml
```

**AI-drafted compliance memo**

Set `ANTHROPIC_API_KEY` in your environment (or in `.streamlit/secrets.toml`) and click "Draft summary memo" on any run with violations. The button calls `claude-sonnet-4-6` and returns a plain-English memo with severity assessment and recommended remediation. The button is silently hidden if no key is configured, so the app never errors in demo mode.

## Running the tests

```bash
python3.12 -m pytest tests/ -v
```

Requires Python 3.12+. If `python3` on your machine is an Anaconda build you may hit an Intel MKL error; use `python3.12` explicitly (e.g. from Homebrew or python.org) to avoid it.

## Holdings CSV format

| Column | Type | Notes |
|---|---|---|
| `ticker` | string | Must be unique per file |
| `weight_pct` | numeric | Portfolio weight as a percentage (e.g. `5.2` means 5.2%) |
| `sector` | string | Used for sector-concentration check |
| `asset_class` | string | Used for the exposure breakdown chart |

See `samples/` for working examples.

## Rules and how to edit `rules.yaml`

```yaml
rules_version: "1.0.0"

rules:
  max_single_holding:
    threshold: 25.0       # any single ticker above this % triggers HIGH violation
    severity: high

  top5_concentration:
    threshold: 50.0       # sum of top-5 weights above this % triggers HIGH violation
    severity: high

  weights_sum:
    target: 100.0
    tolerance: 0.5        # weights must sum to 100 ± tolerance pp
    severity: high

  restricted_list:
    file: samples/restricted.csv   # path to CSV with a 'ticker' column
    severity: high

  max_sector:
    threshold: 40.0       # any single sector above this % triggers MEDIUM violation
    severity: medium

  min_holdings_count:
    threshold: 10         # fewer holdings than this triggers LOW violation
    severity: low

reconciliation:
  weight_diff_tolerance_pp: 0.1   # pp diff threshold for the recon tab
```

Thresholds can also be overridden per-session in the sidebar without touching the file. Every run hashes the exact YAML bytes and stores the hash in the audit log, so you always know which rule version produced a given result.

## Design decisions

**Data-quality errors are separated from compliance violations.**  
Before running any compliance rule the engine validates the raw CSV: duplicate tickers, missing weights, non-numeric weights, and negative weights all surface as `error`-category issues and halt compliance checking. This mirrors how a real operations workflow works: a file with bad data should never generate a compliance pass or fail, it should route to a data-fix queue.

**Check functions are pure and independently tested.**  
`checks.py` contains one function per rule, each accepting a clean DataFrame and threshold parameters and returning a list of result dicts. `engine.py` is the only orchestrator that knows about files, YAML, and the audit log. This split means the 10 check-level unit tests have no filesystem dependencies and run in milliseconds, while the 8 engine tests verify the full pipeline including DQ gating.

**SQLite audit log with file + rules hashes for provenance.**  
Every run writes a row to `audit.db` containing a SHA-256 of the uploaded file bytes and a SHA-256 of the exact YAML used (after sidebar overrides are applied). The Past Runs tab shows both hashes (truncated to 12 chars for readability). This answers "which data, under which rules" for any historical run, without storing the files themselves.

**Persisted exception acknowledgments as evidence capture.**  
Acknowledging a violation requires a name and a free-text reason; both are written to an `acknowledgments` table keyed to `(run_id, exception_index)`. Acknowledgments survive app restarts and are shown inline with the exception they cover. The download report CSV includes ack metadata. This is the minimum evidence trail an operations team would need to demonstrate to an auditor that an exception was reviewed and deliberately accepted.

**SQLite for the prototype; Postgres + ECS + S3 in production.**  
SQLite requires zero infrastructure and works well for single-process use. A production deployment would replace the `audit.db` connection with Postgres on AWS RDS (swap `sqlite3` for `psycopg2`, same schema), run the Streamlit app as a container on ECS Fargate behind an ALB, and accept holdings files via an S3 bucket event trigger rather than a browser upload. The engine is already a pure function that accepts a DataFrame, so the intake path is the only thing that changes.

## Assumptions

The thresholds in `rules.yaml` are illustrative starting points. They are not legal advice and do not constitute a compliance program. Real ETFs operate under concentration limits defined in their prospectus, SAI, and the Investment Company Act of 1940 (Section 5 diversification tests for registered investment companies, 80% name-test requirements for index funds, etc.). Any production deployment requires rule sets reviewed and approved by fund counsel and mapped explicitly to the fund's investment objective and regulatory classification.

The weights-sum rule assumes the input file represents 100% of the portfolio. Funds with short positions, derivatives overlays, or cash allocations treated separately will need a more nuanced check.

## What I'd build next

**Real regulatory rule packs.** Parameterized rule sets for the most common ETF structures: 1940 Act diversified fund (5/25 test), non-diversified fund, commodity pool, and exemptive-relief structures. Each pack would be version-controlled YAML reviewed by counsel, selectable at fund registration.

**N-PORT and EDGAR/iXBRL workflows.** SEC Form N-PORT is filed monthly for funds over $1B and quarterly otherwise. The compliance engine already produces structured issue data; the next step is a renderer that maps that data to the N-PORT XML schema and validates against the EDGAR XBRL taxonomy before submission. iXBRL tagging for Form N-CEN annual reports would follow the same pattern.

**Multi-format vendor ingestion via SFTP.** Custodians deliver holdings in Bloomberg BVAL, FactSet, or proprietary flat-file formats. A vendor-adaptor layer (one module per custodian) would normalize to the internal DataFrame schema and drop files to S3, triggering the engine automatically. SFTP polling via AWS Transfer Family, delivery confirmation via SNS, and a dead-letter queue for files that fail normalization.

**Scheduled checks with alerting.** A daily Lambda (or ECS Fargate scheduled task) that re-runs compliance against the prior day's holdings from S3, writes results to the audit DB, and pages the operations team via PagerDuty or SNS if any HIGH-severity violation is open and unacknowledged for more than 24 hours. Trend data from the existing audit log feeds directly into this without schema changes.
