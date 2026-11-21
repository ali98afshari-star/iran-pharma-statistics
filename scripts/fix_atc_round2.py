#!/usr/bin/env python3
"""Fix blank ATC codes, round 2: cross-file lookup by ingredient name.

Round 1 (scripts/fix_atc.py) built a generic_code -> ATC map from
data/aggregates/generic_monthly.parquet and left 743 generics with no ATC
anywhere in that aggregate. Round 2 searches the OTHER local files:

  * By generic_code in every data/processed/*.parquet + brand_year:
    0 of the 743 have a non-blank ATC row anywhere (blank at the source).
  * By exact normalized generic_name (full product name): 0 found.
  * By exact normalized generic_latin_list (ingredient list name,
    «نام لاتین فهرست»): 270 found with exactly one distinct ATC across
    all ~2.8M rows. 39 ingredients have >1 ATC (route-dependent, e.g.
    paracetamol, ibuprofen) and are NOT applied. 434 not found by any name.

The evidence (found/ambiguous/not-found with sources) lives in
data/atc_round2_search.json and is the input of this script.

What it does (read-only by default; --apply writes):
  1. Fills blank atc_code in generic_monthly.parquet for the 270 codes.
  2. Rebuilds data/aggregates/atc_year.parquet from the fixed table.
  3. Updates docs/data.json: catalog ATC fields, the atc_year array,
     and the `atc_fix` summary (adds a round2 block).
  4. Writes data/atc_fix_report_round2.json (before = round-1 after,
     after, rows filled, found-by-source counts, ambiguous list,
     still-blank list = 473 codes).

Safety:
  * data/originals/* are NEVER touched. stats.json and the `stats` block
    in data.json describe the RAW source files and are NOT rewritten.
  * Guards: every mapped code must be in the round-1 still-blank list
    AND currently blank in generic_monthly; exactly one ATC per code.
  * --apply writes new backups (*.bak-before-atc-round2) before
    overwriting; round-1 backups are kept. Totals are asserted equal
    before/after; the run aborts if they differ.
  * 14 of the 270 ATCs are short (4-5 chars, e.g. R05X) because that is
    what the yearbook stores; they are applied as-is, never completed.

Usage (from the repo root, with the goal venv or any pandas+pyarrow env):
  python scripts/fix_atc_round2.py            # dry run: report only
  python scripts/fix_atc_round2.py --apply    # write fixed files
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
AGG_DIR = REPO_ROOT / "data" / "aggregates"
DATA_JSON = REPO_ROOT / "docs" / "data.json"
REPORT1_PATH = REPO_ROOT / "data" / "atc_fix_report.json"
SEARCH_PATH = REPO_ROOT / "data" / "atc_round2_search.json"
REPORT2_PATH = REPO_ROOT / "data" / "atc_fix_report_round2.json"


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write fixed files (default: dry run)")
    args = ap.parse_args()

    if REPORT2_PATH.exists():
        print("REFUSED: data/atc_fix_report_round2.json already exists — "
              "round 2 was already applied.")
        return
    report1 = json.loads(REPORT1_PATH.read_text(encoding="utf-8"))
    still_blank_r1 = set(str(c) for c in report1["generics_still_blank"])
    search = json.loads(SEARCH_PATH.read_text(encoding="utf-8"))
    found = search["found_by_generic_name"] + search["found_by_latin_list"]

    # Build the map with guards.
    mapping: dict[str, str] = {}
    for entry in found:
        code, atc = str(entry["generic_code"]), norm_atc(entry["atc"])
        assert atc, f"empty ATC for {code}"
        assert code in still_blank_r1, f"{code} not in round-1 still-blank list"
        assert code not in mapping, f"duplicate map entry for {code}"
        mapping[code] = atc

    gm = pd.read_parquet(AGG_DIR / "generic_monthly.parquet")
    before = blank_share(gm)
    totals_before = {
        "units": int(gm["units"].sum()),
        "value_consumer": int(gm["value_consumer"].sum()),
    }

    fixed = gm.copy()
    fixed["atc_code"] = fixed["atc_code"].map(norm_atc)
    blank = fixed["atc_code"].isna()
    codes_blank_now = set(fixed.loc[blank, "generic_code"].astype(str).unique())
    assert set(mapping) <= codes_blank_now, (
        "some mapped codes are not blank anymore: "
        f"{sorted(set(mapping) - codes_blank_now)[:10]}")
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
    assert len(still_blank_codes) == len(still_blank_r1) - len(mapping), (
        f"still-blank mismatch: {len(still_blank_codes)} vs "
        f"{len(still_blank_r1) - len(mapping)}")

    atc_year = rebuild_atc_year(fixed)

    catalog = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else catalog.get("catalog", catalog.get("generics", []))
    catalog_filled = sum(
        1 for e in entries
        if (not e.get("atc")) and str(e.get("generic_code", "")) in mapping
    )
    assert catalog_filled == len(mapping), (
        f"catalog fillable {catalog_filled} != mapped {len(mapping)}")

    found_by_source = collections.Counter(
        s for e in found for s in e.get("sources", []))
    short_atcs = [e for e in found if len(norm_atc(e["atc"])) < 7]

    report = {
        "method": ("round 2: exact normalized generic_latin_list (ingredient "
                   "list name) matched against all local files "
                   "(data/processed/*.parquet + brand_year + generic_monthly); "
                   "exactly one distinct ATC required; code and full-name "
                   "matches found 0; 39 ingredients ambiguous (>1 ATC) not applied"),
        "before": before,
        "after": after,
        "rows_filled": int(fillable.sum()),
        "generics_filled": len(mapping),
        "generics_filled_by_tier": {
            "generic_name": len(search["found_by_generic_name"]),
            "generic_latin_list": len(search["found_by_latin_list"]),
        },
        "found_by_source_file": dict(found_by_source),
        "short_atc_codes_applied_as_is": len(short_atcs),
        "short_atc_examples": [
            {"generic_code": e["generic_code"], "atc": norm_atc(e["atc"])}
            for e in short_atcs
        ],
        "ambiguous_not_applied": search["ambiguous"],
        "ambiguous_count": len(search["ambiguous"]),
        "generics_still_blank": still_blank_codes,
        "generics_still_blank_count": len(still_blank_codes),
        "still_blank_units": int(still["units"].sum()),
        "still_blank_value_consumer_rial": int(still["value_consumer"].sum()),
        "catalog_entries_filled": catalog_filled,
        "totals_unchanged": totals_before == totals_after,
    }

    if args.apply:
        for p in (AGG_DIR / "generic_monthly.parquet",
                  AGG_DIR / "atc_year.parquet", DATA_JSON):
            bak = p.with_suffix(p.suffix + ".bak-before-atc-round2")
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
        catalog.setdefault("atc_fix", {})["round2"] = {
            "method": report["method"],
            "before": before,
            "after": after,
            "rows_filled": int(fillable.sum()),
            "generics_filled": len(mapping),
            "generics_still_blank_count": len(still_blank_codes),
        }
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, allow_nan=False)
        REPORT2_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("APPLIED: round-2 fixed aggregates + data.json written, backups kept.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to write.")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
