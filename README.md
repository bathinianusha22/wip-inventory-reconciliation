# Virtual Inventory Reconciliation & Disposition System

A Python CLI tool that reconciles MES virtual inventory against physical cycle
counts and drives the disposition of unaccounted-for material to closure.
Built as a portfolio project for a Tesla **NCM Process Technician**
application (Buffalo, NY).

## Overview

On a production line, the MES (Manufacturing Execution System) says what
should be on hand. The floor says what is actually there. When the two do
not agree, the gap is unaccounted-for virtual inventory: material the system
believes exists but cannot be found, or material that exists with no system
record. Left unresolved, it corrupts planning, hides shrinkage, and stalls
production.

This tool closes that loop:

1. **Build** — generates a synthetic inventory dataset (600 records across
   4 production areas and 3 warehouses) and loads it into SQLite.
2. **Reconcile** — matches every MES record against its physical count,
   flags variances, classifies them (SHORT / OVER / UNACCOUNTED), ages them,
   assigns a disposition with an owner and due date, and classifies every
   part by ABC annual usage value.
3. **Report** — produces an actionable summary CSV, a markdown report with
   findings and recommended actions, and charts for variance by location,
   aging, and disposition status.

## Why this matters for the NCM Process Technician role

The Tesla posting for this role (Buffalo, NY) calls out the exact work this
project models:

| Job requirement | How this project demonstrates it |
|---|---|
| Disposition of unaccounted-for virtual inventory | Core workflow: every discrepancy gets a disposition (scrap / rework / return-to-stock / quarantine / write-off), an owner, a due date, and a status, with past-due items flagged for escalation |
| Day-to-day production line coverage and rapid response on out-of-control processes | Aging buckets (0-7 / 8-30 / 31-90 / 90+ days) and a past-due report surface the oldest, highest-risk items first |
| Root cause and countermeasures | Variance broken down by location and part category points at systemic causes (e.g. one warehouse driving 24% of variance value) instead of one-off corrections |
| Maintaining manufacturing instructions and analyzing yield / process data | Structured, auditable records in SQLite plus a repeatable reconciliation report with findings and recommended actions |
| Data tools (Tableau, SQL) | 10 ready-to-run SQL queries in `queries.sql` against a normalized SQLite schema; the same result sets feed Tableau-style charts (matplotlib) |
| Detail and record-keeping | Full audit trail: MES qty, physical qty, signed variance, dollar value, aging, disposition, owner, and due date for every record |
| Managing small cross-functional projects | Disposition owners across the team, severity-based due dates, and status tracking from OPEN to CLOSED |

## Features

- **Seeded synthetic data** — 600 inventory records, 24 part types
  (cathode/anode material, separator, electrolyte, foil, cell hardware,
  packaging, consumables), 7 locations, reproducible with seed 42.
- **Realistic discrepancy model** — 12% of records are short, over, or
  unaccounted-for, with aging from 0 to 120 days.
- **Disposition workflow** — rule-based assignment (overages return to
  stock; unaccounted high-value material is quarantined; small shorts are
  written off), severity-based due dates, and past-due detection.
- **ABC classification** — parts ranked by annual usage value
  (A = top 80% of value, B = next 15%, C = rest) with count accuracy
  reported per class.
- **SQL analysis** — `queries.sql` holds 10 queries: total variance value,
  top discrepancies, aging over 30 days, disposition summary, variance by
  location, ABC distribution, past-due open items, count accuracy by class,
  disposition mix, and variance by category.
- **Reporting** — summary CSV, markdown report, and three charts committed
  under `outputs/`, so results are visible without running anything.

## Tech stack

- Python 3.12, pandas, NumPy, matplotlib
- SQLite (standard library `sqlite3`)
- No web framework, no external services — runs fully offline

## Project structure

```text
wip-inventory-reconciliation/
├── reconcile.py            # CLI: --build, --reconcile, --report
├── schema.sql              # Database schema (5 tables + indexes)
├── queries.sql             # 10 analysis queries for inventory.db
├── requirements.txt
├── .gitignore
├── inventory.db            # SQLite database (built by --build/--reconcile)
├── data/
│   ├── mes_inventory.csv       # MES virtual inventory (600 records)
│   └── physical_counts.csv     # Physical cycle-count results (600 records)
└── outputs/
    ├── reconciliation_summary.csv   # One row per discrepancy + disposition
    ├── reconciliation_report.md     # Findings, tables, recommended actions
    ├── variance_by_location.png
    ├── aging_buckets.png
    └── disposition_status.png
```

## How to run

```bash
pip install -r requirements.txt

# 1. Generate synthetic data and build the database
python reconcile.py --build

# 2. Match MES vs physical counts; write discrepancies, dispositions, ABC classes
python reconcile.py --reconcile

# 3. Write the summary CSV, markdown report, and charts to outputs/
python reconcile.py --report

# Inspect the database with the bundled queries
sqlite3 inventory.db < queries.sql
```

All three steps are idempotent and can be re-run; `--build` regenerates the
same dataset every time (fixed seed), so results are reproducible.

## Sample results

Run of 2026-11-11 (as-of date), 600 records across 7 locations:

- **Count accuracy: 88.0%** — 528 of 600 records matched exactly.
- **Discrepancies: 72 (12.0% of records).**
- **Total absolute variance value: $75,900.48** on $3,190,686.27 of MES
  inventory value (2.38%). Net variance: -$27,766.32 (net short).

### Discrepancies by type

| Variance type | Count | Absolute value (USD) | Share of value |
|---|---:|---:|---:|
| SHORT | 49 | $27,107.69 | 35.7% |
| OVER | 13 | $4,370.87 | 5.8% |
| UNACCOUNTED | 10 | $44,421.92 | 58.5% |

Unaccounted material is only 10 records but 58.5% of the dollar exposure —
the single largest item is NCM-ANO-1001 (synthetic graphite anode powder),
785 units / $10,997.85 unaccounted for at WH-02 WIP Stores.

### Variance by location

| Location | Absolute variance value (USD) | Share |
|---|---:|---:|
| WH-02 WIP Stores | $18,265.21 | 24.1% |
| Coating Line A | $13,594.43 | 17.9% |
| Cell Assembly | $12,016.87 | 15.8% |
| WH-01 Raw Materials | $12,003.26 | 15.8% |
| WH-03 Finished Goods | $7,737.78 | 10.2% |
| Pack & Ship | $6,915.63 | 9.1% |
| Formation & Test | $5,367.30 | 7.1% |

### Aging

52 of 72 discrepancies (72.2%) are over 30 days old: 30 in the 31-90 day
bucket ($32,756.99) and 22 at 90+ days ($18,707.77). These are the
escalation candidates.

### Dispositions

| Status | Count |
|---|---:|
| OPEN | 23 |
| IN-PROGRESS | 20 |
| CLOSED | 29 |

11 open / in-progress dispositions are past due ($15,628.96 of value).
Disposition mix: WRITE-OFF 42, QUARANTINE 13, RETURN-TO-STOCK 9, REWORK 8.

### Count accuracy by ABC class

| ABC class | Parts | Share of annual usage value | Count accuracy |
|---|---:|---:|---:|
| A | 279 | 79.9% | 88.5% |
| B | 154 | 15.0% | 87.0% |
| C | 167 | 5.0% | 88.0% |

## Synthetic data disclaimer

All data in this project is synthetic. Part numbers, quantities, costs,
locations, owners, and dates were generated programmatically with a fixed
random seed (42) for demonstration purposes. No real Tesla data, and no data
from any real employer, is used anywhere in this repository. Any resemblance
to real inventory records is coincidental.
