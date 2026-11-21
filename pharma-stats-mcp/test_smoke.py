"""Smoke test for pharma-stats-mcp/server.py.

Runs the server over a REAL MCP stdio session (initialize -> list_tools
-> call_tool) and checks:
  1. list_tools returns the 8 expected tools
  2. data_coverage mentions the 1405-H1 caveat
  3. market_totals() matches the verified totals:
     1404 = 57,816,859,168 units, 3,688,056,906,766,170 Rial consumer value
     1405-H1 = 28,301,605,209 units, 4,017,874,327,274,916 Rial
  4. market_totals(year=1404, month=1) = 4,809,752,426 units
  5. search_drug("METFORMIN") returns matches incl. generic_code 10915
     (METFORMIN HYDROCHLORIDE TABLET ORAL 1000 mg)
  6. drug_monthly_trend("10915", 1404): 12 months, total
     128,073,190 units / 5,926,045,692,000 Rial
  7. drug_summary("10915"): 1404 total matches, top brands include GLUCOPHAGE
  8. top_drugs(1404, by="value", n=5): 5 rows, first is PEMBROLIZUMAB
     (generic_code 25268) with value 66,393,387,810,000 Rial — verified
     against the raw processed parquet, not docs/data.json (whose
     top-generics list is slightly stale: it lists clopidogrel first
     with 37,876,272,925,000 Rial, while the aggregates/raw give
     clopidogrel 38,110,726,375,000 Rial)
  9. atc_breakdown(1404): blank-ATC value share is ~16.5% (after the ATC fix)
 10. province_breakdown(1404, 6): 31 provinces, total 4,890,314,208 units
 11. province_breakdown(1404, 2) is refused with a clear message
 12. market_totals(year=1405, month=9) is refused (1405 has months 1-6 only)

Usage:  python test_smoke.py
Exit code 0 = all checks PASS, 1 = any FAIL.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"

EXPECTED_TOOLS = {
    "search_drug", "drug_monthly_trend", "drug_summary", "top_drugs",
    "market_totals", "atc_breakdown", "province_breakdown", "data_coverage",
}

# Verified facts (data/stats.json, cross-checked at build time).
T1404_UNITS = 57816859168
T1404_VALUE = 3688056906766170
T1405_UNITS = 28301605209
T1405_VALUE = 4017874327274916
M1404_01_UNITS = 4809752426
METFORMIN_1404_UNITS = 128073190
METFORMIN_1404_VALUE = 5926045692000
TOP1_1404_CODE = "25268"
TOP1_1404_VALUE = 66393387810000
PROV_M06_UNITS = 4890314208


def _check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return ok


def _content(result) -> dict:
    """Extract the JSON payload from a CallToolResult (text content)."""
    texts = [c.text for c in result.content if hasattr(c, "text")]
    return json.loads("".join(texts))


async def main_async() -> list[bool]:
    results: list[bool] = []
    params = StdioServerParameters(
        command=sys.executable, args=[str(SERVER)], env=None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            results.append(_check(
                "list_tools returns 8 expected tools",
                names == EXPECTED_TOOLS, f"tools={sorted(names)}"))

            cov = _content(await session.call_tool("data_coverage", {}))
            results.append(_check(
                "data_coverage mentions 1405 H1 caveat",
                "H1" in json.dumps(cov, ensure_ascii=False)
                and "1405" in json.dumps(cov, ensure_ascii=False),
                f"keys={sorted(cov.keys())}"))

            mt = _content(await session.call_tool("market_totals", {}))
            ok1404 = (mt["1404"]["units"] == T1404_UNITS
                      and mt["1404"]["value_consumer"]["rial"] == T1404_VALUE)
            ok1405 = (mt["1405"]["units"] == T1405_UNITS
                      and mt["1405"]["value_consumer"]["rial"] == T1405_VALUE)
            results.append(_check(
                "market_totals() 1404 total matches verified facts",
                ok1404,
                f"units={mt['1404']['units']} value={mt['1404']['value_consumer']['rial']}"))
            results.append(_check(
                "market_totals() 1405-H1 total matches verified facts",
                ok1405,
                f"units={mt['1405']['units']} value={mt['1405']['value_consumer']['rial']}"))

            m1 = _content(await session.call_tool(
                "market_totals", {"year": 1404, "month": 1}))
            results.append(_check(
                "market_totals(1404, month=1)",
                m1.get("units") == M1404_01_UNITS,
                f"units={m1.get('units')}"))

            sr = _content(await session.call_tool(
                "search_drug", {"query": "METFORMIN", "year": 1404}))
            codes = {m["generic_code"] for m in sr.get("matches", [])}
            results.append(_check(
                "search_drug METFORMIN incl. generic_code 10915",
                "10915" in codes and len(sr.get("matches", [])) > 0,
                f"matches={len(sr.get('matches', []))} codes_sample={sorted(codes)[:5]}"))

            tr = _content(await session.call_tool(
                "drug_monthly_trend", {"generic_code": "10915", "year": 1404}))
            results.append(_check(
                "drug_monthly_trend 10915/1404: 12 months, totals match",
                len(tr.get("months", [])) == 12
                and tr.get("total_units_months_only") == METFORMIN_1404_UNITS
                and tr.get("total_value_consumer_months_only", {}).get("rial") == METFORMIN_1404_VALUE,
                f"months={len(tr.get('months', []))} units={tr.get('total_units_months_only')} "
                f"value={tr.get('total_value_consumer_months_only', {}).get('rial')}"))

            ds = _content(await session.call_tool(
                "drug_summary", {"generic_code": "10915"}))
            y1404 = ds.get("years", {}).get("1404", {})
            brands = [b["brand_latin"] for b in y1404.get("top_5_brands_by_value", [])]
            results.append(_check(
                "drug_summary 10915: 1404 total + GLUCOPHAGE top brand",
                y1404.get("units") == METFORMIN_1404_UNITS
                and any("GLUCOPHAGE" in b for b in brands),
                f"units={y1404.get('units')} top_brands={brands[:3]}"))

            td = _content(await session.call_tool(
                "top_drugs", {"year": 1404, "by": "value", "n": 5}))
            first = td.get("drugs", [{}])[0]
            results.append(_check(
                "top_drugs 1404 by value: #1 pembrolizumab",
                len(td.get("drugs", [])) == 5
                and first.get("generic_code") == TOP1_1404_CODE
                and first.get("value_consumer", {}).get("rial") == TOP1_1404_VALUE,
                f"first={first.get('generic_code')} {first.get('generic_name')} "
                f"value={first.get('value_consumer', {}).get('rial')}"))

            at = _content(await session.call_tool(
                "atc_breakdown", {"year": 1404}))
            blank = at.get("blank_atc", {})
            results.append(_check(
                "atc_breakdown 1404: blank-ATC value share ~16.5% (post-fix)",
                blank and abs(blank.get("share_of_value_pct", 0) - 16.48) < 0.5,
                f"blank share value={blank.get('share_of_value_pct')}% "
                f"units={blank.get('share_of_units_pct')}%"))

            pv = _content(await session.call_tool(
                "province_breakdown", {"year": 1404, "month": 6}))
            results.append(_check(
                "province_breakdown 1404 m06: 31 provinces, totals match",
                pv.get("province_count") == 31
                and pv.get("province_total_units") == PROV_M06_UNITS,
                f"provinces={pv.get('province_count')} units={pv.get('province_total_units')} "
                f"diff_vs_national={pv.get('difference_vs_national_units_pct')}%"))

            bad = _content(await session.call_tool(
                "province_breakdown", {"year": 1404, "month": 2}))
            results.append(_check(
                "province_breakdown month 2 refused",
                "error" in bad and "1, 6 and 12" in bad.get("error", ""),
                f"error={bad.get('error', '')[:80]}"))

            bad2 = _content(await session.call_tool(
                "market_totals", {"year": 1405, "month": 9}))
            results.append(_check(
                "market_totals 1405 month 9 refused (H1 only)",
                "error" in bad2,
                f"error={bad2.get('error', '')[:80]}"))
    return results


def main() -> int:
    results = asyncio.run(main_async())
    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
