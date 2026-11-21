#!/usr/bin/env python3
"""Fix blank ATC codes in the Iran pharma statistics aggregates.

Phase 1 of the dashboard-v2 rebuild (2026-11-21). Authorized by the user
(«مجاز به هرکاری هستی»).

What it does (read-only by default; --apply writes):
  1. Reads data/aggregates/generic_monthly.parquet
     (columns: generic_code, generic_name, atc_code, year, month,
     month_name_fa, units, value_consumer, packages, ...). A generic/month
     can be split across several rows with different ATC values; tools SUM
     across those rows, so filling a blank atc_code never changes totals.
  2. Normalizes ATC codes (strip + UPPER — the source has 92 rows with
     lowercase codes, e.g. 'a02...' vs 'A02...'; ATC is case-insensitive,
     so they are the same code). Lowercase groups 'a','d','l' in atc_year
     merge into 'A','D','L'.
  3. Builds a generic_code -> ATC map from rows that HAVE an ATC code
     (both years pooled). If a generic has exactly one distinct non-blank
     ATC, that is the fill value. If several, the majority by units wins
     and the conflict is recorded in the report.
  4. Fills blank atc_code in generic_monthly where the map says so.
  5. Rebuilds data/aggregates/atc_year.parquet from the fixed table
     (group = first letter of the ATC code; blank group stays "(blank)").
  6. Updates docs/data.json (compact format, as the original):
     catalog ATC fields, the atc_year array, a new `atc_fix` summary
     object, plus `generic_series` (per-generic monthly
     [year, month, units, value_consumer], month 0 = month-blank rows)
     and `generic_brands` (top 5 brands by value per generic+year;
     1404 uses period=monthly, 1405 uses period=annual_h1 — the same
     rule as pharma-stats-mcp/server.py drug_summary, to avoid
     double-counting 1405) for the dashboard drug-detail panel.
  7. Writes data/atc_fix_report.json + prints coverage before/after:
     blank share of rows, units and consumer value, per year; conflicts;
     generics still blank (these need an external ATC reference).

Safety:
  * data/originals/* (the IFDA yearbook Excels) are NEVER touched.
  * stats.json and the `stats` block inside data.json describe the RAW
    source files — they are NOT rewritten (raw-file blank shares stay
    46.21% / 51.73%). Post-fix numbers live in `atc_fix` / the report.
  * --apply writes fixed aggregates + data.json in place but first
    writes a backup copy next to each file (*.bak-before-atc-fix).
  * Totals are invariant: the fill only moves rows between ATC groups
    (incl. out of "(blank)"); units/value sums are asserted equal
    before/after and the run aborts if they differ.

Usage (from the repo root, with the goal venv or any pandas+pyarrow env):
  python scripts/fix_atc.py            # dry run: report only, no writes
  python scripts/fix_atc.py --apply    # write fixed files + backups
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
AGG_DIR = REPO_ROOT / "data" / "aggregates"
DATA_JSON = REPO_ROOT / "docs" / "data.json"
REPORT_PATH = REPO_ROOT / "data" / "atc_fix_report.json"


def norm_atc(v) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip().upper()
    return s or None


def is_blank(series: pd.Series) -> pd.Series:
    return series.map(lambda v: norm_atc(v) is None)


def blank_share(df: pd.DataFrame) -> dict:
    blank = is_blank(df["atc_code"])
    out = {}
    for year, sub in df.groupby("year"):
        b = blank[sub.index]
        out[str(int(year))] = {
            "rows": int(len(sub)),
            "blank_rows": int(b.sum()),
            "blank_rows_pct": round(float(b.mean()), 4),
            "blank_units_pct": round(float(sub.loc[b, "units"].sum() / sub["units"].sum()), 4),
            "blank_value_pct": round(float(sub.loc[b, "value_consumer"].sum() / sub["value_consumer"].sum()), 4),
        }
    return out


def build_map(df: pd.DataFrame) -> tuple[dict, list, int]:
    """generic_code -> ATC (normalized). Majority by units on conflict."""
    known = df[df["atc_code"].map(lambda v: norm_atc(v) is not None)].copy()
    known["atc_norm"] = known["atc_code"].map(norm_atc)
    agg = (
        known.groupby(["generic_code", "atc_norm"], dropna=False)
        .agg(units=("units", "sum"))
        .reset_index()
    )
    mapping, conflicts = {}, []
    for code, sub in agg.groupby("generic_code"):
        top = sub.sort_values("units", ascending=False)
        mapping[str(code)] = str(top.iloc[0]["atc_norm"])
        if len(top) > 1:
            conflicts.append({
                "generic_code": str(code),
                "candidates": [
                    {"atc": str(r["atc_norm"]), "units": int(r["units"])}
                    for _, r in top.iterrows()
                ],
                "chosen": str(top.iloc[0]["atc_norm"]),
            })
    return mapping, conflicts, len(known)


def rebuild_atc_year(df: pd.DataFrame) -> pd.DataFrame:
    at = df.copy()
    at["atc_group"] = at["atc_code"].map(
        lambda v: norm_atc(v)[0] if norm_atc(v) else "(blank)")
    return (
        at.groupby(["year", "atc_group"])
        .agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"),
             distinct_generic_codes=("generic_code", "nunique"))
        .reset_index()
        .sort_values(["year", "value_consumer"], ascending=[True, False])
    )


def build_series(df: pd.DataFrame) -> dict:
    """{generic_code: [[year, month(0=blank), units, value_consumer], ...]}
    Rows are AGGREGATED by (year, month): a generic/month can have two
    rows in generic_monthly (an ATC row + a blank-ATC row that was filled
    with the same ATC), so listing rows raw would show duplicate months."""
    agg = (
        df.groupby(["generic_code", "year", "month"], dropna=False)
        .agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"))
        .reset_index()
    )
    out = {}
    for code, sub in agg.groupby("generic_code"):
        rows = []
        for r in sub.itertuples():
            m = 0 if pd.isna(r.month) else int(r.month)
            rows.append([int(r.year), m, int(r.units), int(r.value_consumer)])
        rows.sort()
        out[str(code)] = rows
    return out


def build_brands() -> dict:
    """{generic_code: {"1404": [[brand, units, value], ...top5], "1405": [...]}}"""
    by = pd.read_parquet(AGG_DIR / "brand_year.parquet")
    out: dict = {}
    for year, period in ((1404, "monthly"), (1405, "annual_h1")):
        sub = by[(by["year"] == year) & (by["period"] == period)].copy()
        sub["brand"] = sub["brand_latin"].fillna(sub["brand_fa"]).fillna("(بدون نام)")
        g = (
            sub.groupby(["generic_code", "brand"], dropna=False)
            .agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"))
            .reset_index()
        )
        for code, s in g.groupby("generic_code"):
            top = s.sort_values("value_consumer", ascending=False).head(5)
            entry = out.setdefault(str(code), {})
            entry[str(year)] = [
                [str(r.brand), int(r.units), int(r.value_consumer)]
                for r in top.itertuples()
            ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write fixed files (default: dry run)")
    ap.add_argument("--force", action="store_true",
                    help="re-apply even if data/atc_fix_report.json exists "
                         "(preserves the ORIGINAL 'before' numbers in the report)")
    args = ap.parse_args()

    old_report = None
    if REPORT_PATH.exists():
        old_report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    if args.apply and old_report is not None and not args.force:
        print("REFUSED: data/atc_fix_report.json already exists — the fix was "
              "already applied. Re-run with --apply --force to rebuild "
              "(original 'before' numbers are preserved).")
        return

    gm = pd.read_parquet(AGG_DIR / "generic_monthly.parquet")
    before = blank_share(gm)
    totals_before = {
        "units": int(gm["units"].sum()),
        "value_consumer": int(gm["value_consumer"].sum()),
    }
    lowercase_rows = int(gm["atc_code"].astype(str).str.match(r"^[a-z]").sum())

    mapping, conflicts, known_rows = build_map(gm)
    fixed = gm.copy()
    # Normalize every ATC code first (lowercase -> upper), then fill blanks.
    fixed["atc_code"] = fixed["atc_code"].map(norm_atc)
    blank = fixed["atc_code"].isna()
    fillable = blank & fixed["generic_code"].astype(str).isin(mapping)
    fixed.loc[fillable, "atc_code"] = fixed.loc[fillable, "generic_code"].astype(str).map(mapping)
    after = blank_share(fixed)
    totals_after = {
        "units": int(fixed["units"].sum()),
        "value_consumer": int(fixed["value_consumer"].sum()),
    }
    assert totals_before == totals_after, "totals changed — aborting"

    still = fixed[fixed["atc_code"].isna()]
    still_blank_codes = sorted(still["generic_code"].astype(str).unique())
    still_blank_value = int(still["value_consumer"].sum())
    still_blank_units = int(still["units"].sum())

    atc_year = rebuild_atc_year(fixed)
    series = build_series(fixed)
    brands = build_brands()

    catalog = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else catalog.get("catalog", catalog.get("generics", []))
    catalog_filled = sum(
        1 for e in entries
        if (not e.get("atc")) and str(e.get("generic_code", "")) in mapping
    )
    catalog_still_blank = sum(1 for e in entries if not e.get("atc")) - catalog_filled

    report = {
        "before": before,
        "after": after,
        "rows_filled": int(fillable.sum()),
        "lowercase_atc_rows_normalized": lowercase_rows,
        "generics_mapped": len(mapping),
        "generics_with_known_atc_rows": known_rows,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "generics_still_blank": still_blank_codes,
        "generics_still_blank_count": len(still_blank_codes),
        "still_blank_units": still_blank_units,
        "still_blank_value_consumer_rial": still_blank_value,
        "catalog_entries_fillable": catalog_filled,
        "catalog_entries_still_blank_after": catalog_still_blank,
        "series_generics": len(series),
        "brands_generics": len(brands),
        "totals_unchanged": totals_before == totals_after,
    }

    # On a --force re-run the input is already fixed, so "before" would
    # wrongly equal "after" — preserve the original run's numbers.
    if old_report is not None and args.apply and args.force:
        report["before"] = old_report["before"]
        report["rows_filled"] = old_report["rows_filled"]
        report["lowercase_atc_rows_normalized"] = old_report["lowercase_atc_rows_normalized"]
        report["generics_mapped"] = old_report["generics_mapped"]

    if args.apply:
        for p in (AGG_DIR / "generic_monthly.parquet",
                  AGG_DIR / "atc_year.parquet", DATA_JSON):
            bak = p.with_suffix(p.suffix + ".bak-before-atc-fix")
            if not bak.exists():
                shutil.copy(p, bak)
        fixed.to_parquet(AGG_DIR / "generic_monthly.parquet", index=False)
        atc_year.to_parquet(AGG_DIR / "atc_year.parquet", index=False)
        for e in entries:
            code = str(e.get("generic_code", ""))
            if (not e.get("atc")) and code in mapping:
                e["atc"] = mapping[code]
        catalog["atc_year"] = [
            {"year": int(r.year), "atc_group": str(r.atc_group), "units": int(r.units),
             "value_consumer": int(r.value_consumer),
             "distinct_generic_codes": int(r.distinct_generic_codes)}
            for r in atc_year.itertuples()
        ]
        catalog["atc_fix"] = {
            "method": ("generic_code -> ATC map built from rows that have an ATC code "
                       "(both years pooled); majority by units on conflict; ATC codes "
                       "upper-cased; raw source files untouched, their blank shares "
                       "stay 46.21% (1404) / 51.73% (1405)"),
            "before": old_report["before"] if old_report else before,
            "after": after,
            "rows_filled": old_report["rows_filled"] if old_report else int(fillable.sum()),
            "conflict_count": len(conflicts),
            "generics_still_blank_count": len(still_blank_codes),
        }
        catalog["generic_series"] = series
        catalog["generic_brands"] = brands
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, allow_nan=False)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("APPLIED: fixed aggregates + data.json written, backups kept.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to write.")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
