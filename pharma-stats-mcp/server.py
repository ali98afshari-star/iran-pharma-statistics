"""Iran pharma statistics MCP server.

Answers questions about Iran's pharmaceutical market from the official
IFDA yearbook files (data/processed + data/aggregates in this repo).
Stdio transport, FastMCP — run with:  python server.py

Data & caveats (enforced by every tool below; see data/SOURCES.md):
  * 1405 covers months 1-6 ONLY (H1). The "annual" 1405 file == the H1
    total. NEVER compare a 1405 total with the full 1404 year.
  * 1404 has 4,472 month-blank rows (1,223,214,254 units, 3,754.5 bn Toman
    consumer value, ~1.0% of the year and 2.1% of its units) that are in
    the yearly total but in NO month. Monthly sums for 1404 are therefore
    below the yearly total.
  * ATC was blank for 46.2% of 1404 rows / 51.7% of 1405 rows in the RAW
    files; in the aggregates it was completed first via a generic_code ->
    ATC map (scripts/fix_atc.py, zero conflicts) to 18.9% / 19.9% of rows,
    then via an exact ingredient-name lookup across the other files
    (scripts/fix_atc_round2.py, 270 generics) to 12.0% / 13.0% of rows,
    then via route/form disambiguation (36 generics) and ChEMBL/KEGG
    lookups (337 generics, scripts/fix_atc_round3.py) to 2.6% / 2.7%
    of rows (1.2% / 0.9% of consumer value). 100 generics still have
    no ATC anywhere. ATC breakdowns never sum to the market total.
  * Province files (1404, months 1/6/12 only) do NOT reconcile with the
    national monthly totals (m01 -20.2% units, m06 +5.6%, m12 -18.5%).
    Different cuts — never sum or compare them with national totals.
  * "units" = فروش عددی = number of units (tablets/ampoules/...), NOT
    packs. Packs are in the separate `packages` column.
  * All money is in Rial; Toman = Rial / 10. Prices are "price at time
    of supply" and change during the year; an average price is ALWAYS
    mix-weighted (total value / total units), never a simple mean.

Performance: all tools read the small aggregate parquet files
(data/aggregates/*.parquet) and filter early. The 2.3M-row processed
files are never loaded — no tool needs row-level detail beyond the
aggregates (brand search runs on brand_year.parquet).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Iran-Pharma-Stats")

REPO_ROOT = Path(__file__).resolve().parent.parent
AGG_DIR = REPO_ROOT / "data" / "aggregates"
STATS_PATH = REPO_ROOT / "data" / "stats.json"
REPORT_PATH = REPO_ROOT / "data" / "atc_fix_report.json"
REPORT2_PATH = REPO_ROOT / "data" / "atc_fix_report_round2.json"
REPORT3_PATH = REPO_ROOT / "data" / "atc_fix_report_round3.json"

CURRENCY_NOTE = "All money values are in Rial. Toman = Rial / 10."
H1_WARNING = (
    "1405 covers months 1-6 ONLY (first half of the year). The 'annual' "
    "1405 file is exactly the H1 total. Do NOT compare any 1405 total "
    "with the full 1404 year."
)
MONTH_BLANK_NOTE = (
    "1404 has 4,472 month-blank rows (1,223,214,254 units; 3,754.5 bn "
    "Toman consumer value, ~1.0% of the year and 2.1% of its units) that "
    "are in the yearly total but in no month, so monthly sums for 1404 "
    "are below the yearly total."
)
ATC_WARNING = (
    "ATC code is blank for 2.6% of 1404 aggregate rows / 2.7% of 1405 "
    "rows (1.2% / 0.9% of consumer value) after completion: generic_code "
    "-> ATC map (scripts/fix_atc.py, zero conflicts), exact "
    "ingredient-name lookup (scripts/fix_atc_round2.py, 270 generics), "
    "then route/form disambiguation + ChEMBL/KEGG lookups "
    "(scripts/fix_atc_round3.py, 373 generics); the raw files were 46.2% "
    "/ 51.7% blank. 100 generics still have no ATC anywhere. ATC-based "
    "totals UNDERSTATE the market; use generic_code for market totals."
)
PROVINCE_WARNING = (
    "Province files do NOT reconcile with national monthly totals "
    "(month 1: -20.2% units, month 6: +5.6%, month 12: -18.5% vs "
    "national). They are a different cut of the data — never sum "
    "province figures into national totals and never compare them "
    "directly with national monthly figures."
)
PRICE_NOTE = (
    "Prices are 'price at time of supply' and change during the year. "
    "Average price = total value / total units (mix-weighted by sales "
    "volume), never a simple mean of prices."
)
UNITS_NOTE = "units = فروش عددی = number of units (tablets/ampoules/...), NOT packs."

VALID_YEARS = (1404, 1405)
PROVINCE_MONTHS = (1, 6, 12)

# Verified market totals (from data/stats.json, cross-checked at build time).
MARKET_TOTALS = {
    1404: {"units": 57816859168, "value_consumer_rial": 3688056906766170,
           "scope": "full year, months 1-12"},
    1405: {"units": 28301605209, "value_consumer_rial": 4017874327274916,
           "scope": "H1 only, months 1-6"},
}


# ---------------------------------------------------------------------------
# Data loading (lazy, aggregates only, filtered early)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _load(name: str) -> pd.DataFrame:
    return pd.read_parquet(AGG_DIR / f"{name}.parquet")


def _generic_monthly() -> pd.DataFrame:
    return _load("generic_monthly")


def _brand_year() -> pd.DataFrame:
    return _load("brand_year")


def _market_monthly() -> pd.DataFrame:
    return _load("market_monthly")


def _atc_year() -> pd.DataFrame:
    return _load("atc_year")


def _province_month() -> pd.DataFrame:
    return _load("province_month")


def _money(rial) -> dict:
    rial = int(rial)
    return {
        "rial": rial,
        "toman": rial / 10,
        "billion_toman": round(rial / 10 / 1e9, 2),
    }


def _avg_price(value_rial, units) -> float | None:
    if not units:
        return None
    return round(float(value_rial) / float(units), 2)


def _yearly_generic_totals(year: int) -> pd.DataFrame:
    """Yearly units/value per generic_code. generic_monthly splits a
    generic/month across ATC values, so rows must be SUMMED, never counted
    once per row. The month-blank (NaN month) rows are included here, so
    these totals match the verified yearly totals."""
    gm = _generic_monthly()
    sub = gm[gm["year"] == year]
    return (
        sub.groupby(["generic_code", "generic_name"], dropna=False)
        .agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"),
             packages=("packages", "sum"))
        .reset_index()
    )


def _check_year(year: int) -> str | None:
    if year not in VALID_YEARS:
        return json.dumps(
            {"error": f"year must be 1404 or 1405, got {year!r}.",
             "note": H1_WARNING},
            ensure_ascii=False,
        )
    return None


def _result(payload: dict) -> str:
    payload.setdefault("currency", CURRENCY_NOTE)
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def search_drug(query: str, year: int = 1404) -> str:
    """Search drugs by generic name, generic code, ATC code or brand name
    (substring, case-insensitive). Returns up to 20 matches with
    generic_code, generic_name, ATC and yearly units + consumer value.
    Money is in Rial (Toman = Rial / 10)."""
    err = _check_year(year)
    if err:
        return err
    q = str(query).strip()
    if not q:
        return _result({"error": "query must not be empty.", "matches": []})
    ql = q.lower()
    totals = _yearly_generic_totals(year)
    mask = (
        totals["generic_name"].fillna("").str.lower().str.contains(ql, regex=False)
        | totals["generic_code"].astype(str).str.lower().str.contains(ql, regex=False)
    )
    # ATC match: take ATC from generic_monthly for the same year.
    gm = _generic_monthly()
    atc_codes = (
        gm[gm["year"] == year]
        .dropna(subset=["atc_code"])
        .groupby("generic_code")["atc_code"].first()
    )
    atc_hit = {
        code for code, atc in atc_codes.items()
        if ql in str(atc).lower()
    }
    mask = mask | totals["generic_code"].isin(atc_hit)
    # Brand match: brand_year.parquet (one row per IRC, grouped by brand).
    by = _brand_year()
    by_year = by[by["year"] == year]
    if year == 1405:
        by_year = by_year[by_year["period"] == "annual_h1"]
    brand_hit = by_year[
        by_year["brand_latin"].fillna("").str.lower().str.contains(ql, regex=False)
    ]["generic_code"].unique()
    mask = mask | totals["generic_code"].isin(set(brand_hit))
    hits = totals[mask].copy()
    hits["value_consumer"] = hits["value_consumer"].astype(int)
    hits = hits.sort_values("value_consumer", ascending=False).head(20)
    matches = []
    for _, r in hits.iterrows():
        atc = atc_codes.get(r["generic_code"])
        matches.append({
            "generic_code": str(r["generic_code"]),
            "generic_name": r["generic_name"],
            "atc_code": None if pd.isna(atc) else str(atc),
            "units": int(r["units"]),
            "value_consumer": _money(r["value_consumer"]),
            "avg_price_consumer_per_unit_rial": _avg_price(
                r["value_consumer"], r["units"]),
        })
    out = {
        "query": q, "year": year, "matches": matches,
        "count": len(matches),
        "units_note": UNITS_NOTE,
        "price_note": PRICE_NOTE,
    }
    if year == 1405:
        out["warning"] = H1_WARNING
    return _result(out)


@mcp.tool()
def drug_monthly_trend(generic_code: str, year: int = 1404) -> str:
    """Monthly units + consumer value + mix-weighted average price
    (value/units) for one generic_code in one year. A missing month means
    no sales were recorded that month, not necessarily zero sales. 1405
    covers months 1-6 only. Money is in Rial (Toman = Rial / 10)."""
    err = _check_year(year)
    if err:
        return err
    code = str(generic_code).strip()
    gm = _generic_monthly()
    sub = gm[(gm["generic_code"] == code) & (gm["year"] == year)].copy()
    if sub.empty:
        return _result({
            "error": f"No data for generic_code {code!r} in {year}.",
            "hint": "Use search_drug to find the correct generic_code.",
            "generic_code": code, "year": year,
        })
    # Sum across the ATC split rows within each month.
    monthly = (
        sub.groupby(["month", "month_name_fa"], dropna=False)
        .agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"))
        .reset_index()
    )
    blank = monthly[monthly["month"].isna()]
    monthly = monthly[monthly["month"].notna()].sort_values("month")
    months_present = [int(m) for m in monthly["month"]]
    expected = list(range(1, 13)) if year == 1404 else list(range(1, 7))
    missing = [m for m in expected if m not in months_present]
    rows = []
    for _, r in monthly.iterrows():
        rows.append({
            "month": int(r["month"]),
            "month_name_fa": r["month_name_fa"],
            "units": int(r["units"]),
            "value_consumer": _money(r["value_consumer"]),
            "avg_price_consumer_per_unit_rial": _avg_price(
                r["value_consumer"], r["units"]),
        })
    total_units = int(monthly["units"].sum())
    total_value = int(monthly["value_consumer"].sum())
    out = {
        "generic_code": code,
        "generic_name": sub["generic_name"].iloc[0],
        "year": year,
        "months": rows,
        "months_present": months_present,
        "months_missing_no_rows": missing,
        "missing_month_note": (
            "A month with no rows means no sales were recorded for that "
            "generic in that month in the yearbook; it is not necessarily "
            "zero sales."
        ),
        "total_units_months_only": total_units,
        "total_value_consumer_months_only": _money(total_value),
        "avg_price_consumer_per_unit_rial": _avg_price(total_value, total_units),
        "units_note": UNITS_NOTE,
        "price_note": PRICE_NOTE,
    }
    if len(blank):
        out["month_blank_rows"] = {
            "units": int(blank["units"].iloc[0]),
            "value_consumer": _money(blank["value_consumer"].iloc[0]),
            "note": "Rows with no month: in the yearly total but in no "
                    "month; excluded from the monthly series above.",
        }
    if year == 1405:
        out["warning"] = H1_WARNING
    else:
        out["note"] = MONTH_BLANK_NOTE
    return _result(out)


@mcp.tool()
def drug_summary(generic_code: str) -> str:
    """Totals for one generic_code in 1404 (full year) and 1405-H1,
    top 5 brands by consumer value, and the (mix-weighted) price range
    across brands. Money is in Rial (Toman = Rial / 10)."""
    code = str(generic_code).strip()
    gm = _generic_monthly()
    sub = gm[gm["generic_code"] == code]
    if sub.empty:
        return _result({
            "error": f"No data for generic_code {code!r}.",
            "hint": "Use search_drug to find the correct generic_code.",
            "generic_code": code,
        })
    by = _brand_year()
    years = {}
    for year in VALID_YEARS:
        g = sub[sub["year"] == year]
        units = int(g["units"].sum())
        value = int(g["value_consumer"].sum())
        b = by[by["generic_code"] == code]
        b = b[b["year"] == year]
        # brand_year holds BOTH the 1405 monthly and annual_h1 cuts; use
        # one only, or 1405 would be double-counted.
        b = b[b["period"] == ("monthly" if year == 1404 else "annual_h1")]
        grouped = (
            b.groupby("brand_latin", dropna=False)
            .agg(units=("units", "sum"), value_consumer=("value_consumer", "sum"))
            .reset_index()
            .sort_values("value_consumer", ascending=False)
        )
        top = []
        for _, r in grouped.head(5).iterrows():
            top.append({
                "brand_latin": r["brand_latin"],
                "units": int(r["units"]),
                "value_consumer": _money(r["value_consumer"]),
                "avg_price_consumer_per_unit_rial": _avg_price(
                    r["value_consumer"], r["units"]),
            })
        brand_prices = [
            _avg_price(r["value_consumer"], r["units"])
            for _, r in grouped.iterrows() if r["units"]
        ]
        years[str(year)] = {
            "units": units,
            "value_consumer": _money(value),
            "avg_price_consumer_per_unit_rial": _avg_price(value, units),
            "top_5_brands_by_value": top,
            "brand_avg_price_range_rial": (
                {"min": min(brand_prices), "max": max(brand_prices)}
                if brand_prices else None
            ),
            "scope": "full year" if year == 1404 else "H1 only (months 1-6)",
        }
    return _result({
        "generic_code": code,
        "generic_name": sub["generic_name"].iloc[0],
        "years": years,
        "warning": H1_WARNING + " Do not compare the 1405-H1 total with the full 1404 total.",
        "units_note": UNITS_NOTE,
        "price_note": PRICE_NOTE,
    })


@mcp.tool()
def top_drugs(year: int = 1404, by: str = "value", n: int = 20) -> str:
    """Top generic drugs in a year, ranked by consumer value or by units.
    Money is in Rial (Toman = Rial / 10)."""
    err = _check_year(year)
    if err:
        return err
    if by not in ("value", "units"):
        return _result({"error": "by must be 'value' or 'units'.", "by": by})
    n = max(1, min(int(n), 50))
    totals = _yearly_generic_totals(year)
    col = "value_consumer" if by == "value" else "units"
    totals = totals.sort_values(col, ascending=False).head(n)
    market = MARKET_TOTALS[year]
    rows = []
    for _, r in totals.iterrows():
        rows.append({
            "generic_code": str(r["generic_code"]),
            "generic_name": r["generic_name"],
            "units": int(r["units"]),
            "value_consumer": _money(r["value_consumer"]),
            "share_of_market_value_pct": round(
                100 * float(r["value_consumer"]) / market["value_consumer_rial"], 2),
            "share_of_market_units_pct": round(
                100 * float(r["units"]) / market["units"], 2),
            "avg_price_consumer_per_unit_rial": _avg_price(
                r["value_consumer"], r["units"]),
        })
    out = {
        "year": year, "ranked_by": by, "count": len(rows), "drugs": rows,
        "market_total_units": market["units"],
        "market_total_value_consumer": _money(market["value_consumer_rial"]),
        "units_note": UNITS_NOTE,
        "price_note": PRICE_NOTE,
    }
    if year == 1405:
        out["warning"] = H1_WARNING
    return _result(out)


@mcp.tool()
def market_totals(year: int | None = None, month: int | None = None) -> str:
    """Total market units + consumer value. With no arguments returns both
    the 1404 full-year total and the 1405-H1 total (with the H1 warning).
    With a year, returns that year's total; with year+month, that month.
    Money is in Rial (Toman = Rial / 10)."""
    mm = _market_monthly()
    if year is None and month is None:
        out = {}
        for y in VALID_YEARS:
            sub = mm[mm["year"] == y]
            out[str(y)] = {
                "units": int(sub["units"].sum()),
                "value_consumer": _money(sub["value_consumer"].sum()),
                "value_pharmacy": _money(sub["value_pharmacy"].sum()),
                "value_distributor": _money(sub["value_distributor"].sum()),
                "scope": MARKET_TOTALS[y]["scope"],
            }
        out["warning"] = H1_WARNING
        out["note"] = MONTH_BLANK_NOTE
        out["units_note"] = UNITS_NOTE
        return _result(out)
    if year is not None:
        err = _check_year(year)
        if err:
            return err
    if month is not None:
        if not 1 <= int(month) <= 12:
            return _result({"error": f"month must be 1-12, got {month!r}."})
        if year == 1405 and int(month) > 6:
            return _result({
                "error": f"1405 has no month {month}: only months 1-6 exist (H1).",
                "warning": H1_WARNING,
            })
        row = mm[(mm["year"] == year) & (mm["month"] == int(month))]
        if row.empty:
            return _result({
                "error": f"No market data for {year} month {month}.",
                "year": year, "month": month,
            })
        r = row.iloc[0]
        out = {
            "year": year, "month": int(month),
            "month_name_fa": r["month_name_fa"],
            "units": int(r["units"]),
            "value_consumer": _money(r["value_consumer"]),
            "value_pharmacy": _money(r["value_pharmacy"]),
            "value_distributor": _money(r["value_distributor"]),
            "distinct_generic_codes": int(r["distinct_generic_codes"]),
            "distinct_brands": int(r["distinct_brands"]),
            "units_note": UNITS_NOTE,
        }
        if year == 1405:
            out["warning"] = H1_WARNING
        return _result(out)
    sub = mm[mm["year"] == year]
    out = {
        "year": year,
        "units": int(sub["units"].sum()),
        "value_consumer": _money(sub["value_consumer"].sum()),
        "value_pharmacy": _money(sub["value_pharmacy"].sum()),
        "value_distributor": _money(sub["value_distributor"].sum()),
        "scope": MARKET_TOTALS[year]["scope"],
        "units_note": UNITS_NOTE,
    }
    if year == 1405:
        out["warning"] = H1_WARNING
    else:
        out["note"] = MONTH_BLANK_NOTE
    return _result(out)


@mcp.tool()
def atc_breakdown(year: int = 1404) -> str:
    """Consumer value / units share per ATC first-letter group, INCLUDING
    the blank-ATC share, with the blank-ATC warning. ATC totals never sum
    to the market total — the blank group is the gap. Money is in Rial
    (Toman = Rial / 10)."""
    err = _check_year(year)
    if err:
        return err
    at = _atc_year()
    sub = at[at["year"] == year].copy()
    total_value = int(sub["value_consumer"].sum())
    total_units = int(sub["units"].sum())
    groups = []
    for _, r in sub.sort_values("value_consumer", ascending=False).iterrows():
        groups.append({
            "atc_group": r["atc_group"],
            "units": int(r["units"]),
            "value_consumer": _money(r["value_consumer"]),
            "share_of_value_pct": round(100 * float(r["value_consumer"]) / total_value, 2),
            "share_of_units_pct": round(100 * float(r["units"]) / total_units, 2),
            "distinct_generic_codes": int(r["distinct_generic_codes"]),
        })
    blank = next((g for g in groups if g["atc_group"] == "(blank)"), None)
    return _result({
        "year": year,
        "groups": groups,
        "total_units": total_units,
        "total_value_consumer": _money(total_value),
        "blank_atc": blank,
        "warning": ATC_WARNING,
        "units_note": UNITS_NOTE,
    })


@mcp.tool()
def province_breakdown(year: int = 1404, month: int = 6) -> str:
    """Units + consumer value per province for one month. ONLY 1404
    months 1, 6 and 12 exist. Other months/years are refused. The result
    ALWAYS carries the mismatch warning: province totals do not reconcile
    with national monthly totals. Money is in Rial (Toman = Rial / 10)."""
    if year != 1404 or int(month) not in PROVINCE_MONTHS:
        return _result({
            "error": (
                f"Province data exists only for 1404, months 1, 6 and 12. "
                f"Requested year={year}, month={month}."
            ),
            "available": [{"year": 1404, "month": m} for m in PROVINCE_MONTHS],
        })
    pv = _province_month()
    sub = pv[(pv["year"] == 1404) & (pv["month"] == int(month))].copy()
    sub = sub.sort_values("value_consumer", ascending=False)
    total_units = int(sub["units"].sum())
    total_value = int(sub["value_consumer"].sum())
    national = _market_monthly()
    nat = national[(national["year"] == 1404) & (national["month"] == int(month))].iloc[0]
    nat_units, nat_value = int(nat["units"]), int(nat["value_consumer"])
    provinces = []
    for _, r in sub.iterrows():
        provinces.append({
            "province": r["province"],
            "units": int(r["units"]),
            "value_consumer": _money(r["value_consumer"]),
            "share_of_province_total_value_pct": round(
                100 * float(r["value_consumer"]) / total_value, 2),
            "distinct_generic_codes": int(r["distinct_generic_codes"]),
        })
    return _result({
        "year": 1404, "month": int(month),
        "month_name_fa": sub["month_name_fa"].iloc[0],
        "provinces": provinces,
        "province_count": len(provinces),
        "province_total_units": total_units,
        "province_total_value_consumer": _money(total_value),
        "national_monthly_units": nat_units,
        "national_monthly_value_consumer": _money(nat_value),
        "difference_vs_national_units_pct": round(
            100 * (total_units - nat_units) / nat_units, 1),
        "difference_vs_national_value_pct": round(
            100 * (total_value - nat_value) / nat_value, 1),
        "warning": PROVINCE_WARNING,
        "units_note": UNITS_NOTE,
    })


@mcp.tool()
def data_coverage() -> str:
    """Coverage and caveats of the dataset: years, months, month-blank
    rows, blank ATC, province mismatch, units vs packs, Rial vs Toman,
    supply-time prices. Read this before answering any statistics
    question."""
    coverage = {
        "source": "Official IFDA yearbooks (Iran Food and Drug Administration), "
                  "from the user's Google Drive folder 'amarnameh'. "
                  "See data/SOURCES.md for file names and SHA-256 hashes.",
        "years": {
            "1404": "Monthly, months 1-12 (national) + province detail for months 1, 6, 12 only. "
                    "569,933 rows.",
            "1405": "Monthly, months 1-6 ONLY (H1). The 'annual' 1405 file is exactly "
                    "the H1 total (difference vs monthly sum: 0). 262,349 monthly rows.",
        },
        "market_totals": {
            "1404_full_year": {
                "units": MARKET_TOTALS[1404]["units"],
                "value_consumer": _money(MARKET_TOTALS[1404]["value_consumer_rial"]),
            },
            "1405_H1_only": {
                "units": MARKET_TOTALS[1405]["units"],
                "value_consumer": _money(MARKET_TOTALS[1405]["value_consumer_rial"]),
            },
        },
        "caveats": [
            H1_WARNING,
            MONTH_BLANK_NOTE,
            ATC_WARNING,
            PROVINCE_WARNING,
            UNITS_NOTE,
            PRICE_NOTE,
            CURRENCY_NOTE,
        ],
        "catalog": "docs/data.json lists a catalog of 2,339 generic entries "
                   "(2,330 distinct generic_code values) across 1404 and "
                   "1405-H1; 100 of them have no ATC code.",
        "files_used": [
            "data/aggregates/generic_monthly.parquet",
            "data/aggregates/brand_year.parquet",
            "data/aggregates/market_monthly.parquet",
            "data/aggregates/atc_year.parquet",
            "data/aggregates/province_month.parquet",
        ],
        "note": "All tools read the aggregate parquet files only; the "
                "2.3M-row processed files are never loaded.",
    }
    try:
        stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        coverage["blank_atc_rows"] = {
            "1404": f"{stats['yearbook_1404_monthly']['blank_atc_share']:.1%} of rows",
            "1405": f"{stats['yearbook_1405_monthly']['blank_atc_share']:.1%} of rows",
        }
        coverage["month_blank_rows_1404"] = stats["yearbook_1404_monthly"]["month_blank_rows"]
    except Exception:
        pass
    try:
        # Post-fix aggregate shares (raw-file shares above are unchanged).
        # Round-3 report wins when present; rows_filled is cumulative.
        rep = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        rows_filled = rep["rows_filled"]
        method = "generic_code -> ATC map, scripts/fix_atc.py"
        if REPORT2_PATH.exists():
            rep = json.loads(REPORT2_PATH.read_text(encoding="utf-8"))
            rows_filled += rep["rows_filled"]
            method += (" + exact ingredient-name lookup across the other "
                       "files, scripts/fix_atc_round2.py")
        if REPORT3_PATH.exists():
            rep = json.loads(REPORT3_PATH.read_text(encoding="utf-8"))
            rows_filled += rep["rows_filled"]
            method += (" + route/form disambiguation and ChEMBL/KEGG "
                       "lookups, scripts/fix_atc_round3.py")
        coverage["blank_atc_after_fix"] = {
            "1404": f"{rep['after']['1404']['blank_rows_pct']:.1%} of aggregate rows "
                    f"({rep['after']['1404']['blank_value_pct']:.1%} of consumer value)",
            "1405": f"{rep['after']['1405']['blank_rows_pct']:.1%} of aggregate rows "
                    f"({rep['after']['1405']['blank_value_pct']:.1%} of consumer value)",
            "rows_filled": rows_filled,
            "conflicts": rep["conflict_count"] if "conflict_count" in rep else 0,
            "generics_still_blank": rep["generics_still_blank_count"],
            "method": method,
        }
    except Exception:
        pass
    return _result(coverage)


if __name__ == "__main__":
    mcp.run(transport="stdio")
