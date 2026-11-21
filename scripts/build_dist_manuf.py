"""Build distributor + manufacturer year aggregates and extend docs/data.json.

Sources (national monthly files ONLY — province files are a different cut and
the 1405 annual file is the same H1 total as the monthly file; adding them
would double-count):
  data/processed/yearbook_1404_monthly.parquet  (year 1404, 12 months + 4,472 month-blank rows)
  data/processed/yearbook_1405_monthly.parquet  (year 1405, months 1-6 = H1)

Outputs:
  data/aggregates/distributor_year.parquet
  data/aggregates/manufacturer_year.parquet
  docs/data.json gains keys: distributor_year, manufacturer_year, dist_manuf_meta
    (compact arrays: top generics/brands per item are [name, units, value_consumer])

Blank handling: blank distributor/manufacturer rows go into a «نامشخص» bucket
so per-year totals still reconcile exactly with market_year_totals.
manufacturer_fa is mapped from all processed files (the 1404 monthly file
itself has no manufacturer_fa column).

Totals are asserted unchanged vs market_year_totals before writing anything.
Usage: python scripts/build_dist_manuf.py [--apply]   (default: dry run)
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
PROC = REPO / "data" / "processed"
AGG = REPO / "data" / "aggregates"
DATA_JSON = REPO / "docs" / "data.json"

UNKNOWN = "نامشخص"
YEARS = {1404: "yearbook_1404_monthly.parquet", 1405: "yearbook_1405_monthly.parquet"}
EXPECTED_TOTALS = {
    1404: {"units": 57816859168, "value_consumer": 3688056906766170},
    1405: {"units": 28301605209, "value_consumer": 4017874327274916},
}


def load_monthly():
    frames = []
    for year, fname in YEARS.items():
        df = pd.read_parquet(PROC / fname)
        df = df.copy()
        df["year"] = year
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def manufacturer_fa_map():
    """manufacturer (Latin) -> manufacturer_fa, from every processed file."""
    mapping = {}
    # read the files that carry both columns
    for f in PROC.glob("*.parquet"):
        cols = pd.read_parquet(f).columns
        if "manufacturer" in cols and "manufacturer_fa" in cols:
            sub = pd.read_parquet(f, columns=["manufacturer", "manufacturer_fa"]).dropna()
            for m, fa in zip(sub["manufacturer"], sub["manufacturer_fa"]):
                m = str(m).strip()
                fa = str(fa).strip()
                if m and fa and m not in mapping:
                    mapping[m] = fa
    return mapping


def top_lists(group, name_col, n=5):
    """Top-n by value_consumer within one (year, key) group."""
    g = group.groupby(name_col).agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"))
    g = g[g["value_consumer"] > 0].sort_values("value_consumer", ascending=False).head(n)
    return [[str(idx), int(r.units), int(r.value_consumer)] for idx, r in g.iterrows()]


def build(df, key_col, fa_map=None):
    rows = []
    df = df.copy()
    df[key_col] = df[key_col].fillna("").astype(str).str.strip().replace("", UNKNOWN)
    for (year, key), group in df.groupby(["year", key_col]):
        rec = {
            "year": int(year),
            key_col: key,
            "units": int(group["units"].sum()),
            "value_consumer": int(group["value_consumer"].sum()),
            "value_distributor": int(group["value_distributor"].sum()),
            "value_pharmacy": int(group["value_pharmacy"].sum()),
            "packages": int(group["packages"].sum()),
            "distinct_generic_codes": int(group["generic_code"].nunique()),
            "distinct_brands": int(group["brand_latin"].nunique()),
            "top_generics": top_lists(group, "generic_name"),
            "top_brands": top_lists(group, "brand_latin"),
        }
        if fa_map is not None:
            rec["manufacturer_fa"] = fa_map.get(key)
        rows.append(rec)
    rows.sort(key=lambda r: (r["year"], -r["value_consumer"]))
    return rows


def main():
    apply = "--apply" in sys.argv
    df = load_monthly()
    fa_map = manufacturer_fa_map()
    print(f"manufacturer_fa mapped for {len(fa_map)} manufacturers")

    dist = build(df, "distributor")
    manuf = build(df, "manufacturer", fa_map=fa_map)

    # --- totals assertion: per-year sums must equal market_year_totals ---
    for year, exp in EXPECTED_TOTALS.items():
        for rows, label in ((dist, "distributor"), (manuf, "manufacturer")):
            units = sum(r["units"] for r in rows if r["year"] == year)
            value = sum(r["value_consumer"] for r in rows if r["year"] == year)
            assert units == exp["units"], f"{label} {year} units {units} != {exp['units']}"
            assert value == exp["value_consumer"], f"{label} {year} value {value} != {exp['value_consumer']}"
        print(f"year {year}: totals OK (units={exp['units']:,}, value={exp['value_consumer']:,})")

    n_dist = len({r["distributor"] for r in dist})
    n_manuf = len({r["manufacturer"] for r in manuf})
    no_fa = sum(1 for r in manuf if not r.get("manufacturer_fa"))
    print(f"distributors: {n_dist} unique ({len(dist)} year-rows)")
    print(f"manufacturers: {n_manuf} unique ({len(manuf)} year-rows), without fa name: {no_fa} year-rows")
    print(f"«{UNKNOWN}» bucket: dist 1404 value=" +
          f"{sum(r['value_consumer'] for r in dist if r['distributor'] == UNKNOWN and r['year'] == 1404):,}, " +
          f"manuf 1404 value={sum(r['value_consumer'] for r in manuf if r['manufacturer'] == UNKNOWN and r['year'] == 1404):,}")

    if not apply:
        print("DRY RUN — nothing written. Re-run with --apply to write.")
        return

    # backups (keep one generation, like the ATC scripts)
    for p in (AGG / "distributor_year.parquet", AGG / "manufacturer_year.parquet"):
        bak = Path(str(p) + ".bak-before-dist-manuf")
        if p.exists() and not bak.exists():
            shutil.copy(p, bak)
    bak_json = Path(str(DATA_JSON) + ".bak-before-dist-manuf")
    if not bak_json.exists():
        shutil.copy(DATA_JSON, bak_json)

    pd.DataFrame([{k: v for k, v in r.items() if k not in ("top_generics", "top_brands")}
                   for r in dist]).to_parquet(AGG / "distributor_year.parquet", index=False)
    pd.DataFrame([{k: v for k, v in r.items() if k not in ("top_generics", "top_brands")}
                   for r in manuf]).to_parquet(AGG / "manufacturer_year.parquet", index=False)

    with open(DATA_JSON, encoding="utf-8") as f:
        catalog = json.load(f)
    catalog["distributor_year"] = dist
    catalog["manufacturer_year"] = manuf
    catalog["dist_manuf_meta"] = {
        "source": "yearbook_1404_monthly + yearbook_1405_monthly (national monthly files; province files are a different cut and were not added; 1405 annual = H1 total, not added)",
        "blank_distributor_rows": {"1404": 0, "1405": 0},
        "blank_manufacturer_rows": {"1404": 9, "1405": 0},
        "blank_manufacturer_grouped_as": UNKNOWN,
        "distributors_unique": n_dist,
        "manufacturers_unique": n_manuf,
        "manufacturers_without_fa_name_year_rows": no_fa,
        "top_lists": "top 5 generics and top 5 brands per (year, name), by value_consumer, as [name, units, value_consumer]",
    }
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, allow_nan=False)
    print("APPLIED: aggregates + data.json written, backups kept.")


if __name__ == "__main__":
    main()
