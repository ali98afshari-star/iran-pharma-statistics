#!/usr/bin/env python3
"""Fix blank ATC codes, round 5: WHO ATC/DDD Index mapping from brand-confirmed ingredients.

Rounds 1-4 are applied (commit cb32ee8). Remaining after round 4:
29 generics with no ATC (data/atc_fix_report_round4.json). Round 5 resolves
them from the WHO ATC/DDD Index only, using the brand-confirmed ingredient
identities from «پیام داروساز» (hidden_files/payamdaroosaz_brand_lookups_29.md,
28/29 found; 52360 ALIBOUR WATER not found anywhere on the site). The site
confirms ingredient names for the named combinations/vaccines but does NOT
break out ingredients for the 7 «ADULT COLD PREPARATIONS» codes or the
trace-metal code — and it shows NO ATC anywhere (verified 2026-11-22), so it
was NOT used for ATC.

Acceptance rule (per code, evidence in hidden_files/atc_round5_lookups.json):
  * exactly one WHO entry for the exact ingredient, OR
  * one WHO entry for the ingredient's route/form as given in generic_name, OR
  * a WHO combination entry naming the exact combination, OR
  * a WHO combination bucket naming the (main) ingredient (e.g. D06BA51
    "silver sulfadiazine, combinations"), OR
  * a WHO guideline text that decides the code (N02B: cold preparations with
    therapeutic analgesic levels -> 50-series; N01BB: lidocaine gels used as
    anaesthetics -> N01BB; R05F: cough suppressant + expectorant combos).
  One round-4 rejection is corrected with new WHO evidence: 52662
  (acetaminophen/ibuprofen) — the WHO N02BE page states "Combinations of
  paracetamol and e.g. ibuprofen are classified in N02BE51", which round 4
  had missed by looking only for an entry naming both ingredients.
  R05X was NOT used for the 7 cold preparations: the WHO R05 guideline
  routes therapeutic-analgesic cold preparations to N02/M01 and only
  minimal-analgesic ones to R05X, and the analgesic's identity is
  unconfirmed for those codes (guessing from strengths is forbidden), so no
  exact WHO entry can be matched. 6 of 29 resolved; 23 left blank
  (see NOT_RESOLVED).

What it does (read-only by default; --apply writes):
  1. Fills blank atc_code in generic_monthly.parquet for the resolved codes.
  2. Rebuilds data/aggregates/atc_year.parquet from the fixed table.
  3. Updates docs/data.json: catalog ATC fields, the atc_year array,
     and the `atc_fix` summary (adds a round5 block).
  4. Writes data/atc_fix_report_round5.json (before = round-4 after,
     after, rows filled, per-source counts, still-blank list with reasons)
     and data/atc_round5_search.json (evidence).

Safety: same as round 4 — data/originals/* never touched; stats.json and the
`stats` block in data.json NOT rewritten; every mapped code must be in the
round-4 still-blank list AND currently blank; exactly one ATC per code;
--apply writes new backups (*.bak-before-atc-round5); totals asserted equal
before/after; short ATCs applied as-is, never completed by guessing.

Usage (from the repo root, with the goal venv or any pandas+pyarrow env):
  python scripts/fix_atc_round5.py            # dry run: report only
  python scripts/fix_atc_round5.py --apply    # write fixed files
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
GOAL_DIR = REPO_ROOT.parent
AGG_DIR = REPO_ROOT / "data" / "aggregates"
DATA_JSON = REPO_ROOT / "docs" / "data.json"
REPORT4_PATH = REPO_ROOT / "data" / "atc_fix_report_round4.json"
REPORT5_PATH = REPO_ROOT / "data" / "atc_fix_report_round5.json"
SEARCH5_PATH = REPO_ROOT / "data" / "atc_round5_search.json"
LOOKUPS_PATH = GOAL_DIR / "hidden_files" / "atc_round5_lookups.json"

WHO = "https://atcddd.fhi.no/atc_ddd_index/"

# code -> (ATC, evidence). Evidence cites the WHO index entry/guideline.
MAPPING: dict[str, tuple[str, str]] = {
    "19051": ("B05DA", "WHO B05D page: B05DA 'Isotonic solutions' / B05DB 'Hypertonic solutions', no 5th-level codes under either. Extraneal 7.5% icodextrin is isosmotic (282-286 mOsm/L; FDA label: 'EXTRANEAL is an isosmotic peritoneal dialysis solution'; SmPC: 'approximately iso-osmolar to serum') -> B05DA, not B05DB"),
    "51673": ("D06BA51", "WHO D06BA page: D06BA51 'silver sulfadiazine, combinations' — names the main ingredient (same rule as round 4's M01AE51/A02BC51 accepts); EGF 0.001% is a minor component; cream topical"),
    "51991": ("N01BB52", "WHO N01BB guideline: 'Creams, plasters and sprays containing e.g. lidocaine... used as anesthetics/analgesics... are classified in N01BB' (vs D04AB for dermatological pruritus/burns/insect stings). Gel is a urethral local anaesthetic (CATHEJELL, catheterization). N01BB52 = 'lidocaine, combinations'; D04AB has no combinations bucket (plain 01-07 only)"),
    "52662": ("N02BE51", "WHO N02BE page: 'Combinations of paracetamol and e.g. ibuprofen are classified in N02BE51.' N02BE51 = 'paracetamol, combinations excl. psycholeptics'; acetaminophen 500 mg + ibuprofen 200 mg tablet oral, no psycholeptics. Corrects round 4's rejection"),
    "52877": ("R05FB02", "WHO R05FB02 = 'cough suppressants and expectorants' (dextromethorphan = cough suppressant, guaifenesin = expectorant WHO R05CA03). R05F guideline allows additional agents ('bronchodilating agents, antihistamines etc.') — phenylephrine is an additional decongestant. No analgesic; R05FB02 is the specific preceding group, which excludes R05X ('cannot be classified in the preceding groups')"),
    "53455": ("N02BE51", "WHO N02B guideline: 'Cold preparations with therapeutic levels of analgesics are classified in this group at separate 5th levels by using the 50-series.' Acetaminophen 500 mg/tablet is therapeutic (N02BE01 DDD 3 g/day). N02BE51 = 'paracetamol, combinations excl. psycholeptics'; no psycholeptics; ascorbic acid 38 mg < 50 mg is moot (combination on other grounds)"),
}

NOT_RESOLVED: dict[str, str] = {
    "88": "anthocyanoside A: WHO index has no entry ('anthocyanoside' search: no match)",
    "11756": "acetylcysteine/ascorbic acid combination: WHO has no code for it; R05CB10 is a bare 'combinations' bucket with no ingredient named",
    "13710": "ADULT COLD PREPARATIONS (4-11): ingredients unknown (site does not break them out); WHO R05 guideline forks therapeutic-analgesic cold preparations to N02/M01 and minimal-analgesic ones to R05X, and the analgesic's identity is unconfirmed, so no exact WHO entry can be matched; guessing from strengths is forbidden",
    "21373": "ADULT COLD PREPARATIONS (3-7): ingredients unknown (same as 13710)",
    "2256": "diphtheria/tetanus/pertussis vaccine: WHO J07CA has no plain-DTP code; J07CA03 is diphtheria-rubella-tetanus (verified from round-4 lookup data), all others include polio, hepatitis B, Hib or meningococcus",
    "22891": "amphotericin B nanoliposomal gel topical: WHO 'amphotericin b' search returns no topical code (A01AB04 oral, A07AA07 intestinal, G01AA03 vaginal, J02AA01 systemic)",
    "2397": "sodium acid phosphate effervescent tablet oral: A06AD17 (laxative, DDD 50 g O) vs V03AG05 (hypercalcaemia, DDD 8 g O) — both oral; indication not determinable from form/dose; no new evidence in round 5",
    "50003": "daclatasvir/sofosbuvir: WHO has no code for this combination (J05AP07 daclatasvir, J05AP08 sofosbuvir, and other sofosbuvir combinations exist, but not daclatasvir+sofosbuvir)",
    "50342": "ADULT COLD PREPARATIONS (1-2): ingredients unknown (same as 13710)",
    "50348": "ADULT COLD PREPARATIONS (4-2): ingredients unknown (same as 13710)",
    "51421": "trace metal combination (ADDAVEN): WHO has no code for trace elements alone (A11AA04 is multivitamins and trace elements, not this product)",
    "52014": "fluocinolone/hydroquinone/tretinoin cream: D07AC has only plain codes (01-19, 21), no combinations bucket; no WHO code for this combination",
    "52360": "ALIBOUR WATER (copper sulfate/zinc sulfate lotion): not found on پیام داروساز (nearest record is a different generic code, 01813); WHO has no code for this combination",
    "52378": "sodium chloride 7% respiratory solution: WHO 'sodium chloride' search returns A12CA01 oral, B05CB01 irrigating, B05XA03 parenteral, S01XA03 ophthalmic — no respiratory/nebulizer code",
    "52416": "ADULT COLD PREPARATIONS (4-12): ingredients unknown (same as 13710)",
    "52417": "ADULT COLD PREPARATIONS (1-4): ingredients unknown (same as 13710)",
    "52419": "ADULT COLD PREPARATIONS (5-4): ingredients unknown (same as 13710)",
    "53020": "influenza vaccine, recombinant, tetravalent: WHO J07BB lists only J07BB01 whole virus, J07BB02 split/surface antigen, J07BB03 live attenuated, J07BB04 virus like particles — no recombinant entry; brand record says only 'INFLUENZA VACCINE'",
    "53152": "lidocaine injection parenteral: C01BB01 (IV antiarrhythmic) and N01BB02 (local anaesthetic) both fit parenteral; form does not decide; no new evidence in round 5",
    "53292": "acetylsalicylic acid/clopidogrel tablet: WHO has no entry for this combination; B01AC30 is a bare 'combinations' bucket with no ingredient named",
    "53293": "acetylsalicylic acid/clopidogrel tablet: WHO has no entry for this combination; B01AC30 is a bare 'combinations' bucket with no ingredient named",
    "53386": "artificial saliva: WHO index has no entry ('artificial saliva' search: no match)",
    "7913": "succimer (dimercaptosuccinic acid) chelator: WHO index has no entry ('succimer' search returns only technetium radiopharmaceuticals V09IA03/V09CA02; 'dimercaptosuccinic' search: no match)",
}


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

    if REPORT5_PATH.exists():
        print("REFUSED: data/atc_fix_report_round5.json already exists — "
              "round 5 was already applied.")
        return
    report4 = json.loads(REPORT4_PATH.read_text(encoding="utf-8"))
    still_blank_r4 = set(str(c) for c in report4["generics_still_blank"])
    assert len(MAPPING) + len(NOT_RESOLVED) == len(still_blank_r4) == 29, (
        f"mapping {len(MAPPING)} + unresolved {len(NOT_RESOLVED)} != 29")
    assert set(MAPPING) | set(NOT_RESOLVED) == still_blank_r4, (
        "mapping+unresolved does not cover exactly the round-4 still-blank list")

    mapping = {c: norm_atc(a) for c, (a, _e) in MAPPING.items()}
    assert all(mapping.values()), "empty ATC in mapping"

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
    assert set(still_blank_codes) == set(NOT_RESOLVED), (
        f"still-blank mismatch: {sorted(set(still_blank_codes) ^ set(NOT_RESOLVED))[:10]}")

    atc_year = rebuild_atc_year(fixed)

    catalog = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else catalog.get("catalog", catalog.get("generics", []))
    catalog_filled = sum(
        1 for e in entries
        if (not e.get("atc")) and str(e.get("generic_code", "")) in mapping
    )
    assert catalog_filled == len(mapping), (
        f"catalog fillable {catalog_filled} != mapped {len(mapping)}")

    by_source = collections.Counter({"WHO ATC/DDD Index": len(mapping)})

    report = {
        "method": ("round 5: WHO ATC/DDD Index (atcddd.fhi.no) mapping from the "
                   "brand-confirmed ingredient identities in "
                   "hidden_files/payamdaroosaz_brand_lookups_29.md (28/29 found "
                   "on پیام داروساز; 52360 not found). Accepted only with exact "
                   "WHO evidence: an exact ingredient entry, a route/form entry, "
                   "an exact combination entry, a WHO combination bucket naming "
                   "the (main) ingredient, or a WHO guideline text deciding the "
                   "code. 6 resolved, 23 left blank. R05X was NOT used for the "
                   "7 cold preparations: the WHO R05 guideline routes "
                   "therapeutic-analgesic cold preparations to N02/M01 and only "
                   "minimal-analgesic ones to R05X, and the analgesic's identity "
                   "is unconfirmed for those codes. «پیام داروساز» NOT used for "
                   "ATC (it shows none anywhere)."),
        "before": before,
        "after": after,
        "rows_filled": int(fillable.sum()),
        "generics_filled": len(mapping),
        "generics_filled_by_source": dict(by_source),
        "generics_still_blank": still_blank_codes,
        "generics_still_blank_count": len(still_blank_codes),
        "still_blank_reasons": NOT_RESOLVED,
        "still_blank_units": int(still["units"].sum()),
        "still_blank_value_consumer_rial": int(still["value_consumer"].sum()),
        "catalog_entries_filled": catalog_filled,
        "totals_unchanged": totals_before == totals_after,
    }

    lookups = json.loads(LOOKUPS_PATH.read_text(encoding="utf-8"))
    search_evidence = {
        "mapping": [
            {"generic_code": c, "atc": a, "evidence": e}
            for c, (a, e) in sorted(MAPPING.items())
        ],
        "not_resolved": [
            {"generic_code": c, "reason": r}
            for c, r in sorted(NOT_RESOLVED.items())
        ],
        "who_base_url": WHO,
        "lookups": lookups,
    }

    if args.apply:
        for p in (AGG_DIR / "generic_monthly.parquet",
                  AGG_DIR / "atc_year.parquet", DATA_JSON):
            bak = p.with_suffix(p.suffix + ".bak-before-atc-round5")
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
        catalog.setdefault("atc_fix", {})["round5"] = {
            "method": report["method"],
            "before": before,
            "after": after,
            "rows_filled": int(fillable.sum()),
            "generics_filled": len(mapping),
            "generics_still_blank_count": len(still_blank_codes),
        }
        # Promote round-5 results to the top level: the dashboard reads
        # atc_fix.after / rows_filled / generics_still_blank_count, and
        # those must always be the LATEST fix state.
        af = catalog["atc_fix"]
        af["after"] = after
        af["rows_filled"] = int(af.get("rows_filled", 0)) + int(fillable.sum())
        af["generics_still_blank_count"] = len(still_blank_codes)
        af["method"] = (af.get("method", "") + " Round 5 "
                        "(scripts/fix_atc_round5.py): 6 more generics "
                        "filled from the WHO ATC/DDD Index using "
                        "brand-confirmed ingredients.")
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, allow_nan=False)
        REPORT5_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        SEARCH5_PATH.write_text(json.dumps(search_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("APPLIED: round-5 fixed aggregates + data.json written, backups kept.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to write.")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
