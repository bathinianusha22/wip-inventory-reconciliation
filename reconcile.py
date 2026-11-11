#!/usr/bin/env python3
"""Virtual Inventory Reconciliation & Disposition System.

Reconciles MES virtual inventory against physical cycle counts and drives
disposition of unaccounted-for material -- the day-to-day production-control
work of an NCM Process Technician (root cause, yield/process data analysis,
record-keeping, small cross-functional projects).

All data is SYNTHETIC, generated with a fixed seed so every run is
reproducible. No real Tesla data is used anywhere in this project.

Usage:
    python reconcile.py --build       generate synthetic data + build SQLite DB
    python reconcile.py --reconcile   match MES vs physical, write discrepancies
                                      + dispositions + ABC classification
    python reconcile.py --report      write outputs/ summary CSV, report and charts

Requires: pandas, numpy, matplotlib (see requirements.txt)
"""

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SEED = 42
AS_OF = date(2026, 11, 11)  # fixed reference date: runs are reproducible
N_RECORDS = 600
DISCREPANCY_RATE = 0.12

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
DB_PATH = BASE_DIR / "inventory.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

# 4 production areas + 3 warehouses
LOCATIONS = [
    "Coating Line A",
    "Cell Assembly",
    "Formation & Test",
    "Pack & Ship",
    "WH-01 Raw Materials",
    "WH-02 WIP Stores",
    "WH-03 Finished Goods",
]

# (category, description, unit-cost range, on-hand qty range,
#  annual usage qty range)
PART_CATALOG = [
    ("Cathode Active Material", "NCM 811 cathode powder, 25 kg drum", 18.0, 42.0, 40, 900, 400, 9000),
    ("Cathode Active Material", "NCM 622 cathode powder, 25 kg drum", 15.0, 36.0, 40, 900, 300, 8000),
    ("Anode Active Material", "Synthetic graphite anode powder, 20 kg bag", 8.0, 15.0, 60, 1100, 500, 11000),
    ("Anode Active Material", "Natural graphite anode powder, 20 kg bag", 6.0, 12.0, 60, 1000, 400, 9000),
    ("Separator", "Ceramic-coated separator film, 500 m roll", 0.40, 0.95, 80, 2400, 900, 20000),
    ("Separator", "PE separator film, 500 m roll", 0.30, 0.70, 80, 2400, 900, 18000),
    ("Electrolyte", "LiPF6 electrolyte, 200 L drum", 12.0, 25.0, 20, 400, 200, 4500),
    ("Electrolyte", "Electrolyte additive, 25 L carboy", 30.0, 65.0, 10, 200, 100, 2200),
    ("Current Collector", "Aluminium foil 15 um, 300 mm roll", 3.0, 8.0, 50, 1500, 600, 14000),
    ("Current Collector", "Copper foil 8 um, 300 mm roll", 4.0, 9.0, 50, 1500, 600, 14000),
    ("Cell Hardware", "2170 cell can, nickel-plated steel", 0.15, 0.60, 500, 20000, 5000, 250000),
    ("Cell Hardware", "Cell cap assembly with vent", 0.35, 1.20, 400, 16000, 4000, 200000),
    ("Cell Hardware", "Nickel tab, 0.15 x 6 mm, reel", 0.05, 0.25, 1000, 40000, 10000, 500000),
    ("Cell Hardware", "Insulating gasket ring", 0.02, 0.12, 1000, 50000, 10000, 600000),
    ("Binder & Solvent", "PVDF binder, 20 kg bag", 22.0, 48.0, 20, 400, 150, 3500),
    ("Binder & Solvent", "NMP solvent, 200 L drum", 9.0, 18.0, 20, 500, 200, 5000),
    ("Packaging", "ESD-safe cell tray, 100-cell", 1.20, 4.50, 100, 5000, 1000, 60000),
    ("Packaging", "Moisture-barrier bag, 500 x 700 mm", 0.80, 2.50, 200, 8000, 2000, 90000),
    ("Packaging", "Corrugated shipper, double-wall", 1.50, 6.00, 100, 4000, 800, 40000),
    ("Consumables", "Cleanroom wipe, lint-free, pack of 100", 2.00, 7.00, 50, 2000, 500, 22000),
    ("Consumables", "Nitrile glove, box of 100", 4.00, 9.00, 40, 1500, 400, 18000),
    ("Consumables", "HEPA filter element, 610 x 610 mm", 35.0, 90.0, 5, 120, 40, 1200),
    ("Consumables", "Isopropyl alcohol 99%, 5 L", 6.00, 14.00, 20, 600, 150, 6000),
    ("Test & Measurement", "Reference cell, formation QC", 8.00, 22.00, 10, 300, 80, 2500),
]

COUNTERS = ["J. Alvarez", "M. Chen", "R. Patel", "S. Okafor", "T. Nguyen", "K. Brooks"]
OWNERS = ["J. Alvarez", "M. Chen", "R. Patel", "S. Okafor", "T. Nguyen", "K. Brooks"]

DISPOSITIONS = ["SCRAP", "REWORK", "RETURN-TO-STOCK", "QUARANTINE", "WRITE-OFF"]

AGING_BUCKETS = ["0-7", "8-30", "31-90", "90+"]


def aging_bucket(days: int) -> str:
    """Map an age in days to an aging bucket."""
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 90:
        return "31-90"
    return "90+"


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# --build: generate synthetic data and build the database
# ---------------------------------------------------------------------------
def build() -> None:
    rng = np.random.default_rng(SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    mes_rows, physical_rows = [], []
    for i in range(1, N_RECORDS + 1):
        cat, desc, c_lo, c_hi, q_lo, q_hi, u_lo, u_hi = PART_CATALOG[
            rng.integers(0, len(PART_CATALOG))
        ]
        prefix = {
            "Cathode Active Material": "NCM-CAT",
            "Anode Active Material": "NCM-ANO",
            "Separator": "NCM-SEP",
            "Electrolyte": "NCM-ELY",
            "Current Collector": "NCM-FOL",
            "Cell Hardware": "NCM-HDW",
            "Binder & Solvent": "NCM-BND",
            "Packaging": "NCM-PKG",
            "Consumables": "NCM-CON",
            "Test & Measurement": "NCM-TST",
        }[cat]
        part_number = f"{prefix}-{1000 + i}"
        location = LOCATIONS[rng.integers(0, len(LOCATIONS))]
        mes_qty = int(rng.integers(q_lo, q_hi + 1))
        unit_cost = round(float(rng.uniform(c_lo, c_hi)), 2)
        annual_usage = int(rng.integers(u_lo, u_hi + 1))
        last_movement_days = int(rng.integers(0, 121))
        last_movement = AS_OF - timedelta(days=last_movement_days)

        # ~12% of records carry a discrepancy between MES and physical count.
        roll = rng.random()
        if roll < DISCREPANCY_RATE:
            kind = rng.choice(
                ["SHORT", "OVER", "UNACCOUNTED"], p=[0.55, 0.25, 0.20]
            )
            if kind == "SHORT":
                delta = max(1, int(round(mes_qty * rng.uniform(0.02, 0.18))))
                physical_qty = mes_qty - delta
            elif kind == "OVER":
                delta = max(1, int(round(mes_qty * rng.uniform(0.02, 0.15))))
                physical_qty = mes_qty + delta
            else:  # UNACCOUNTED: material missing, or found stock with no MES record
                if rng.random() < 0.7:
                    physical_qty = 0
                else:
                    mes_qty, physical_qty = 0, int(rng.integers(q_lo, q_hi + 1))
        else:
            physical_qty = mes_qty

        mes_rows.append(
            {
                "record_id": i,
                "part_number": part_number,
                "description": desc,
                "category": cat,
                "location": location,
                "mes_qty": mes_qty,
                "unit_cost": unit_cost,
                "annual_usage_qty": annual_usage,
                "last_movement_date": last_movement.isoformat(),
            }
        )
        physical_rows.append(
            {
                "record_id": i,
                "part_number": part_number,
                "location": location,
                "physical_qty": physical_qty,
                "counted_by": COUNTERS[rng.integers(0, len(COUNTERS))],
                "count_date": AS_OF.isoformat(),
            }
        )

    mes_df = pd.DataFrame(mes_rows)
    physical_df = pd.DataFrame(physical_rows)
    mes_df.to_csv(DATA_DIR / "mes_inventory.csv", index=False)
    physical_df.to_csv(DATA_DIR / "physical_counts.csv", index=False)

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect_db()
    conn.executescript(SCHEMA_PATH.read_text())
    mes_df.to_sql("mes_inventory", conn, if_exists="append", index=False)
    physical_df.to_sql("physical_counts", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()

    n_short = int((physical_df["physical_qty"] < mes_df["mes_qty"]).sum())
    print(f"[build] Generated {len(mes_df)} MES records and "
          f"{len(physical_df)} physical counts (seed={SEED}).")
    print(f"[build] Records where physical < MES: {n_short} "
          f"(discrepancy rate is ~{DISCREPANCY_RATE:.0%}; see --reconcile).")
    print(f"[build] Data written to {DATA_DIR}/ and database {DB_PATH}.")


# ---------------------------------------------------------------------------
# --reconcile: match MES vs physical, write discrepancies + dispositions + ABC
# ---------------------------------------------------------------------------
def reconcile() -> None:
    if not DB_PATH.exists():
        sys.exit("[reconcile] inventory.db not found. Run --build first.")
    rng = np.random.default_rng(SEED + 1)
    conn = connect_db()

    mes = pd.read_sql("SELECT * FROM mes_inventory", conn)
    phys = pd.read_sql("SELECT * FROM physical_counts", conn)
    merged = mes.merge(
        phys[["record_id", "physical_qty"]], on="record_id", how="left"
    )
    merged["physical_qty"] = merged["physical_qty"].fillna(0).astype(int)
    merged["variance_qty"] = merged["physical_qty"] - merged["mes_qty"]
    merged["variance_value"] = (
        merged["variance_qty"] * merged["unit_cost"]
    ).round(2)
    merged["last_movement_date"] = pd.to_datetime(merged["last_movement_date"])
    merged["aging_days"] = (pd.Timestamp(AS_OF) - merged["last_movement_date"]).dt.days

    disc = merged[merged["variance_qty"] != 0].copy()

    def variance_type(row) -> str:
        if row["physical_qty"] == 0 and row["mes_qty"] > 0:
            return "UNACCOUNTED"
        if row["mes_qty"] == 0 and row["physical_qty"] > 0:
            return "UNACCOUNTED"
        return "SHORT" if row["variance_qty"] < 0 else "OVER"

    disc["variance_type"] = disc.apply(variance_type, axis=1)
    disc["aging_bucket"] = disc["aging_days"].apply(aging_bucket)
    disc = disc.sort_values("record_id").reset_index(drop=True)
    disc["discrepancy_id"] = disc.index + 1
    disc["detected_date"] = AS_OF.isoformat()

    disc_out = disc[
        [
            "discrepancy_id", "record_id", "part_number", "location",
            "mes_qty", "physical_qty", "variance_qty", "variance_type",
            "variance_value", "aging_days", "aging_bucket", "detected_date",
        ]
    ]
    conn.execute("DELETE FROM discrepancies")
    conn.execute("DELETE FROM dispositions")
    disc_out.to_sql("discrepancies", conn, if_exists="append", index=False)

    # --- Disposition workflow -------------------------------------------
    # Rule-based assignment: what should happen to the unaccounted-for
    # material, who owns it, and when it is due.
    disp_rows = []
    for _, row in disc.iterrows():
        value = abs(row["variance_value"])
        vtype = row["variance_type"]
        if vtype == "OVER":
            disposition = rng.choice(
                ["RETURN-TO-STOCK", "QUARANTINE", "REWORK"], p=[0.65, 0.20, 0.15]
            )
        elif vtype == "UNACCOUNTED":
            disposition = rng.choice(
                ["QUARANTINE", "WRITE-OFF", "SCRAP"], p=[0.45, 0.35, 0.20]
            )
        else:  # SHORT
            disposition = (
                "WRITE-OFF" if value < 500 else rng.choice(
                    ["QUARANTINE", "WRITE-OFF", "REWORK"], p=[0.45, 0.35, 0.20]
                )
            )
        severity_days = 7 if value >= 5000 else (14 if value >= 1000 else 30)
        due = AS_OF + timedelta(days=severity_days)
        status = rng.choice(["OPEN", "IN-PROGRESS", "CLOSED"], p=[0.35, 0.30, 0.35])
        # ~25% of still-open items are already past due: they need escalation.
        if status in ("OPEN", "IN-PROGRESS") and rng.random() < 0.25:
            due = AS_OF - timedelta(days=int(rng.integers(1, 21)))
        disp_rows.append(
            {
                "discrepancy_id": int(row["discrepancy_id"]),
                "disposition": disposition,
                "owner": OWNERS[rng.integers(0, len(OWNERS))],
                "due_date": due.isoformat(),
                "status": status,
                "notes": (
                    f"{vtype} of {abs(int(row['variance_qty']))} units; "
                    f"abs value ${value:,.2f}; aging {row['aging_bucket']} days."
                ),
            }
        )
    disp_df = pd.DataFrame(disp_rows)
    disp_df.insert(0, "disposition_id", range(1, len(disp_df) + 1))
    disp_df.to_sql("dispositions", conn, if_exists="append", index=False)

    # --- ABC classification ---------------------------------------------
    abc = mes[["part_number", "unit_cost", "annual_usage_qty"]].copy()
    abc["annual_usage_value"] = (
        abc["unit_cost"] * abc["annual_usage_qty"]
    ).round(2)
    abc = abc.sort_values("annual_usage_value", ascending=False).reset_index(drop=True)
    total_value = abc["annual_usage_value"].sum()
    abc["cum_pct"] = abc["annual_usage_value"].cumsum() / total_value
    abc["abc_class"] = np.where(
        abc["cum_pct"] <= 0.80, "A",
        np.where(abc["cum_pct"] <= 0.95, "B", "C"),
    )
    conn.execute("DELETE FROM abc_classification")
    abc[["part_number", "annual_usage_value", "abc_class"]].to_sql(
        "abc_classification", conn, if_exists="append", index=False
    )
    conn.commit()

    n_disc = len(disc)
    total_abs = float(disc["variance_value"].abs().sum())
    print(f"[reconcile] {n_disc} discrepancies found "
          f"({n_disc / len(mes):.1%} of {len(mes)} records).")
    print(f"[reconcile] Total absolute variance value: ${total_abs:,.2f}.")
    print(f"[reconcile] {len(disp_df)} dispositions created; "
          f"ABC classes assigned to {len(abc)} parts.")
    conn.close()


# ---------------------------------------------------------------------------
# --report: summary CSV, markdown report, charts
# ---------------------------------------------------------------------------
def _chart_bar(values, title, xlabel, ylabel, path, color="#1f77b4",
               value_labels=True):
    fig, ax = plt.subplots(figsize=(9, 5))
    labels = list(values.index)
    vals = list(values.values)
    bars = ax.bar(labels, vals, color=color)
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=30, labelsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    if value_labels:
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{v:,.0f}" if v >= 1 else f"{v:.1f}",
                    ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def report() -> None:
    if not DB_PATH.exists():
        sys.exit("[report] inventory.db not found. Run --build and --reconcile first.")
    conn = connect_db()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mes = pd.read_sql("SELECT * FROM mes_inventory", conn)
    disc = pd.read_sql("SELECT * FROM discrepancies", conn)
    disp = pd.read_sql("SELECT * FROM dispositions", conn)
    abc = pd.read_sql("SELECT * FROM abc_classification", conn)

    if disc.empty:
        sys.exit("[report] No discrepancies in the database. Run --reconcile first.")

    # --- Summary CSV: every discrepancy with its disposition -------------
    summary = disc.merge(
        disp[["discrepancy_id", "disposition", "owner", "due_date", "status"]],
        on="discrepancy_id", how="left",
    )
    summary["abs_variance_value"] = summary["variance_value"].abs().round(2)
    summary = summary.sort_values("abs_variance_value", ascending=False)
    summary_path = OUTPUT_DIR / "reconciliation_summary.csv"
    summary.to_csv(summary_path, index=False)

    # --- Aggregate numbers ------------------------------------------------
    n_records = len(mes)
    n_disc = len(disc)
    total_abs = float(disc["variance_value"].abs().sum())
    net_value = float(disc["variance_value"].sum())
    total_mes_value = float((mes["mes_qty"] * mes["unit_cost"]).sum())
    count_accuracy = 100.0 * (n_records - n_disc) / n_records
    by_type = disc.groupby("variance_type").agg(
        count=("discrepancy_id", "count"),
        abs_value=("variance_value", lambda s: s.abs().sum()),
    )
    by_location = disc.groupby("location")["variance_value"].apply(
        lambda s: s.abs().sum()
    ).sort_values(ascending=False)
    by_bucket = disc.groupby("aging_bucket").size().reindex(AGING_BUCKETS).fillna(0).astype(int)
    by_bucket_value = disc.groupby("aging_bucket")["variance_value"].apply(
        lambda s: s.abs().sum()
    ).reindex(AGING_BUCKETS).fillna(0)
    by_status = disp.groupby("status").size()
    by_disp_type = disp.groupby("disposition").size().sort_values(ascending=False)
    past_due = disp[
        (disp["status"].isin(["OPEN", "IN-PROGRESS"]))
        & (pd.to_datetime(disp["due_date"]) < pd.Timestamp(AS_OF))
    ]
    past_due_value = float(
        disc.set_index("discrepancy_id").loc[
            past_due["discrepancy_id"], "variance_value"
        ].abs().sum()
    ) if not past_due.empty else 0.0

    # Count accuracy by ABC class
    merged = mes.merge(abc, on="part_number", how="left")
    has_disc = set(disc["record_id"])
    merged["has_variance"] = merged["record_id"].isin(has_disc)
    abc_acc = merged.groupby("abc_class").agg(
        records=("record_id", "count"),
        with_variance=("has_variance", "sum"),
    )
    abc_acc["accuracy_pct"] = (
        100.0 * (abc_acc["records"] - abc_acc["with_variance"]) / abc_acc["records"]
    ).round(1)
    abc_acc = abc_acc.reindex(["A", "B", "C"])

    top10 = summary.head(10)[
        ["part_number", "location", "variance_type", "variance_qty",
         "abs_variance_value", "aging_bucket", "disposition", "owner", "status"]
    ]

    # --- Charts ------------------------------------------------------------
    _chart_bar(
        by_location, "Absolute Variance Value by Location",
        "Location", "Absolute variance value (USD)",
        OUTPUT_DIR / "variance_by_location.png", color="#c0392b",
    )
    _chart_bar(
        by_bucket, "Discrepancies by Aging Bucket",
        "Days since last movement", "Number of discrepancies",
        OUTPUT_DIR / "aging_buckets.png", color="#2e86c1",
    )
    _chart_bar(
        by_status, "Dispositions by Status",
        "Status", "Number of dispositions",
        OUTPUT_DIR / "disposition_status.png", color="#229954",
    )

    # --- Markdown report ----------------------------------------------------
    def _fmt_money(v):
        return f"${v:,.2f}"

    lines = []
    lines.append("# Virtual Inventory Reconciliation Report\n")
    lines.append(f"**As of:** {AS_OF.isoformat()}  |  **Records reconciled:** "
                 f"{n_records}  |  **Locations:** {mes['location'].nunique()}  "
                 f"(4 production areas, 3 warehouses)\n")
    lines.append("> All data in this report is synthetic, generated with a fixed "
                 "seed for reproducibility. No real Tesla data is used.\n")
    lines.append("## Executive Summary\n")
    lines.append(f"- **Count accuracy:** {count_accuracy:.1f}% "
                 f"({n_records - n_disc} of {n_records} records matched exactly).")
    lines.append(f"- **Discrepancies found:** {n_disc} "
                 f"({n_disc / n_records:.1%} of records).")
    lines.append(f"- **Total absolute variance value:** {_fmt_money(total_abs)} "
                 f"on {_fmt_money(total_mes_value)} of MES inventory value "
                 f"({100.0 * total_abs / total_mes_value:.2f}%).")
    lines.append(f"- **Net variance value:** {_fmt_money(net_value)} "
                 f"(negative = net short).")
    lines.append(f"- **Open / in-progress dispositions past due:** {len(past_due)} "
                 f"items, {_fmt_money(past_due_value)} of value.\n")
    lines.append("## Discrepancies by Type\n")
    lines.append("| Variance type | Count | Absolute value (USD) | Share of value |")
    lines.append("|---|---:|---:|---:|")
    for t, row in by_type.iterrows():
        lines.append(f"| {t} | {int(row['count'])} | {_fmt_money(row['abs_value'])} "
                     f"| {100.0 * row['abs_value'] / total_abs:.1f}% |")
    lines.append("")
    lines.append("## Variance by Location\n")
    lines.append("| Location | Absolute variance value (USD) | Share |")
    lines.append("|---|---:|---:|")
    for loc, val in by_location.items():
        lines.append(f"| {loc} | {_fmt_money(val)} "
                     f"| {100.0 * val / total_abs:.1f}% |")
    lines.append("\n![Variance by location](variance_by_location.png)\n")
    lines.append("## Aging of Discrepancies\n")
    lines.append("| Aging bucket | Count | Absolute value (USD) |")
    lines.append("|---|---:|---:|")
    for b in AGING_BUCKETS:
        lines.append(f"| {b} days | {int(by_bucket[b])} | "
                     f"{_fmt_money(by_bucket_value[b])} |")
    over30 = int(by_bucket[["31-90", "90+"]].sum())
    lines.append(f"\n{over30} discrepancies ({100.0 * over30 / n_disc:.1f}%) "
                 f"are over 30 days old and need escalation.")
    lines.append("\n![Aging buckets](aging_buckets.png)\n")
    lines.append("## Disposition Status\n")
    lines.append("| Status | Count |")
    lines.append("|---|---:|")
    for s, c in by_status.items():
        lines.append(f"| {s} | {int(c)} |")
    lines.append("\n![Disposition status](disposition_status.png)\n")
    lines.append("## Disposition Mix\n")
    lines.append("| Disposition | Count |")
    lines.append("|---|---:|")
    for d, c in by_disp_type.items():
        lines.append(f"| {d} | {int(c)} |")
    lines.append("")
    lines.append("## Count Accuracy by ABC Class\n")
    lines.append("| ABC class | Records | Records with variance | Count accuracy |")
    lines.append("|---|---:|---:|---:|")
    for cls, row in abc_acc.iterrows():
        lines.append(f"| {cls} | {int(row['records'])} | "
                     f"{int(row['with_variance'])} | {row['accuracy_pct']:.1f}% |")
    lines.append("\nClass A parts carry the most annual usage value and are "
                 "counted first in the cycle-count rotation; class C parts are "
                 "counted least often.\n")
    lines.append("## Top 10 Discrepancies by Value\n")
    lines.append("| Part | Location | Type | Variance qty | Abs value (USD) | "
                 "Aging | Disposition | Owner | Status |")
    lines.append("|---|---|---|---:|---:|---|---|---|---|")
    for _, r in top10.iterrows():
        lines.append(f"| {r['part_number']} | {r['location']} | {r['variance_type']} "
                     f"| {int(r['variance_qty'])} | {_fmt_money(r['abs_variance_value'])} "
                     f"| {r['aging_bucket']} | {r['disposition']} | {r['owner']} "
                     f"| {r['status']} |")
    lines.append("")
    lines.append("## Recommended Actions\n")
    lines.append(f"1. Escalate the {len(past_due)} past-due open dispositions "
                 "to the production-control lead this week.")
    lines.append(f"2. Physically recount the top 10 discrepancies above before "
                 "confirming any write-off.")
    lines.append("3. Investigate the highest-variance location for a systemic "
                 "MES transaction or scanning issue (root cause, not just "
                 "correction).")
    lines.append("4. Tighten cycle-count frequency for class A parts, which "
                 "carry the most value at risk.\n")
    lines.append("## Files\n")
    lines.append("- `reconciliation_summary.csv` — one row per discrepancy, "
                 "with disposition, owner, due date and status.")
    lines.append("- `variance_by_location.png`, `aging_buckets.png`, "
                 "`disposition_status.png` — charts for this report.")
    lines.append("- `../inventory.db` — SQLite database; see `../queries.sql` "
                 "for the standard analysis queries.\n")

    report_path = OUTPUT_DIR / "reconciliation_report.md"
    report_path.write_text("\n".join(lines))
    conn.close()

    print(f"[report] Summary CSV: {summary_path} ({len(summary)} rows).")
    print(f"[report] Report: {report_path}.")
    print("[report] Charts: variance_by_location.png, aging_buckets.png, "
          "disposition_status.png.")
    print(f"[report] Count accuracy {count_accuracy:.1f}%; "
          f"total abs variance {_fmt_money(total_abs)}; "
          f"{len(past_due)} past-due open items.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Virtual Inventory Reconciliation & Disposition System "
                    "(synthetic data)."
    )
    parser.add_argument("--build", action="store_true",
                        help="Generate synthetic data and build the SQLite DB.")
    parser.add_argument("--reconcile", action="store_true",
                        help="Match MES vs physical counts; write discrepancies, "
                             "dispositions and ABC classification.")
    parser.add_argument("--report", action="store_true",
                        help="Write summary CSV, markdown report and charts to outputs/.")
    args = parser.parse_args()
    if not (args.build or args.reconcile or args.report):
        parser.print_help()
        sys.exit(1)
    if args.build:
        build()
    if args.reconcile:
        reconcile()
    if args.report:
        report()


if __name__ == "__main__":
    main()
