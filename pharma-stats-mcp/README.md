# pharma-stats-mcp — Iran Pharma Statistics MCP Server

A small, standalone MCP server (Python, **stdio** transport) that answers
questions about Iran's pharmaceutical market from the official IFDA
yearbook files in this repo (`data/aggregates/*.parquet`, built from
`data/processed/*.parquet`). It exposes 8 tools for AI agents: drug
search, monthly trends, drug summaries, top drugs, market totals, ATC
breakdown, province breakdown, and data coverage.

Data source: official yearbooks of the Iran Food and Drug Administration
(IFDA), from the Drive folder «amarnameh». See `../data/SOURCES.md` for
file names, SHA-256 hashes, and the full caveats.

## Tools

| Tool | What it does |
|---|---|
| `search_drug` | Search by generic name / generic code / ATC / brand name (substring, case-insensitive); yearly units + consumer value, up to 20 matches |
| `drug_monthly_trend` | Monthly units + consumer value + mix-weighted avg price for one `generic_code` in one year; lists missing months |
| `drug_summary` | Totals for one `generic_code` in 1404 and 1405-H1, top 5 brands by value, brand price range |
| `top_drugs` | Top generics in a year, ranked by consumer value or units |
| `market_totals` | Total market units + value (both years, one year, or one month) |
| `atc_breakdown` | Value/units share per ATC first-letter group, including the blank-ATC share |
| `province_breakdown` | Units + value per province (1404, months 1/6/12 only), with the mismatch vs national totals |
| `data_coverage` | Coverage and caveats of the dataset — read this first |

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python >= 3.10.

> **Note on the MCP SDK version:** pin `mcp>=1.23.0,<2.0.0`. The server
> imports `FastMCP` from `mcp.server.fastmcp`; in MCP SDK 2.x that import
> was removed (`FastMCP` was renamed to `MCPServer`), so an unpinned
> install pulls mcp 2.x and breaks the server at import time.

## Run

stdio transport (default — for MCP clients such as Claude Desktop, or any
MCP-compatible agent):

```bash
python server.py
```

Example MCP client config (Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pharma-stats": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/pharma-stats-mcp/server.py"]
    }
  }
}
```

The server resolves the data directory from its own location
(`../data/aggregates`), so it works from any working directory.

## Test

```bash
python test_smoke.py
```

Runs the server over a real MCP stdio session (initialize → list_tools →
call_tool) and checks the results against the verified market totals.

## Example questions

- "What was the total pharmaceutical market in 1404, in Toman?"
  → `market_totals(year=1404)` → 57,816,859,168 units;
  3,688,056,906,766,170 Rial = 368,805.69 bn Toman (consumer value).
- "How did metformin 1000 mg sell month by month in 1404?"
  → `search_drug(query="METFORMIN")` to find generic_code `10915`,
  then `drug_monthly_trend(generic_code="10915", year=1404)`.
- "Top 10 drugs by value in the first half of 1405?"
  → `top_drugs(year=1405, by="value", n=10)` — remember: H1 only.
- "Which province sold the most in Shahrivar 1404?"
  → `province_breakdown(year=1404, month=6)`.

## Mandatory caveats (enforced by the server)

- **1405 = H1 only (months 1–6).** The "annual" 1405 file is exactly
  the H1 total. Never compare a 1405 total with the full 1404 year.
- **1404 month-blank rows:** 4,472 rows (1,223,214,254 units; 3,754.5 bn
  Toman, ~1.0% of annual value and 2.1% of units) are in the yearly total
  but in no month — monthly sums for 1404 are below the yearly total.
- **Blank ATC:** raw files had 46.2% of 1404 rows / 51.7% of 1405 rows blank;
  aggregates were completed first via a generic_code -> ATC map
  (scripts/fix_atc.py, zero conflicts) to 18.9% / 19.9% of rows, then via
  an exact ingredient-name lookup across the other files
  (scripts/fix_atc_round2.py, 270 generics) to 12.0% / 13.0% of rows
  (14.9% / 19.0% of consumer value). 473 generics still have no ATC
  anywhere. ATC totals understate the market; use generic codes for
  market totals.
- **Province files do not reconcile** with national monthly totals
  (month 1: −20.2% units, month 6: +5.6%, month 12: −18.5%). Different
  cuts — never sum province figures into national totals.
- **units = فروش عددی** = number of units (tablets/ampoules/...), NOT
  packs. Packs are a separate column.
- **Money is in Rial; Toman = Rial / 10.** Prices are "price at time of
  supply" and change during the year; average price = total value /
  total units (mix-weighted), never a simple mean of prices.

## بخش فارسی

سرور MCP آمار دارویی ایران: با ۸ ابزار، به سؤال‌های مربوط به بازار
داروی ایران (بر اساس سالنامه‌های رسمی سازمان غذا و دارو، سال‌های ۱۴۰۴ و
نیمهٔ اول ۱۴۰۵) پاسخ می‌دهد. همهٔ مبالغ به **ریال** است (تومان = ریال ÷
۱۰) و «تعداد» یعنی تعداد واحد (قرص/آمپول/…) نه تعداد بسته. میانگین قیمت
همیشه موزون با مقدار فروش است (ارزش ÷ تعداد)، نه میانگین سادهٔ قیمت‌ها.
**۱۴۰۵ فقط نیمهٔ اول سال است** و با کل سال ۱۴۰۴ قابل مقایسه نیست.
نصب و اجرا مثل بخش انگلیسی بالا است (`pip install -r requirements.txt`
و سپس `python server.py`).
