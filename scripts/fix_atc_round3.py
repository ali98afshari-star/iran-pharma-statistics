#!/usr/bin/env python3
"""Fix blank ATC codes, round 3: route disambiguation + public primary sources.

Rounds 1+2 are applied (commits 083e67c, ddbb65a). Remaining after round 2:
473 generics with no ATC (data/atc_fix_report_round2.json). Round 3 resolves
them from TWO primary sources only — «پیام داروساز» was explicitly NOT used
(user: usable only after midnight tonight, not before):

  A. Route/form disambiguation of the 39 ambiguous (data/atc_round2_search.json
     "ambiguous"): each had several candidate ATCs depending on route/form.
     Resolved below ONLY where the form+route in the data maps to exactly
     one candidate by standard ATC rules (single-ingredient check via
     generic_latin_list; combination codes excluded for single-ingredient
     generics). 36 of 39 resolved; 3 left blank (see NOT_RESOLVED_AMBIGUOUS).
  B. Public primary-source lookups for the 434 not-found
     (hidden_files/atc_round3_lookup.py): ChEMBL API (pref_name__iexact ->
     atc_classifications) and KEGG API (find/drug + get -> ATC code + Brite
     level-5 names / level-4 group names). Accepted ONLY when exactly one
     ATC remained after exact-name, entry-composition, level-5 specificity,
     combination-group and route filters. Evidence per code in
     hidden_files/atc_round3_lookups.json (and copied to
     data/atc_round3_search.json by this script).

What it does (read-only by default; --apply writes):
  1. Fills blank atc_code in generic_monthly.parquet for the resolved codes.
  2. Rebuilds data/aggregates/atc_year.parquet from the fixed table.
  3. Updates docs/data.json: catalog ATC fields, the atc_year array,
     and the `atc_fix` summary (adds a round3 block).
  4. Writes data/atc_fix_report_round3.json (before = round-2 after,
     after, rows filled, per-source counts, still-blank list with reasons)
     and data/atc_round3_search.json (evidence).

Safety:
  * data/originals/* are NEVER touched. stats.json and the `stats` block
    in data.json describe the RAW source files and are NOT rewritten.
  * Guards: every mapped code must be in the round-2 still-blank list
    AND currently blank in generic_monthly; exactly one ATC per code;
    disambiguation and lookup maps must not overlap.
  * --apply writes new backups (*.bak-before-atc-round3) before
    overwriting; round-1/2 backups are kept. Totals are asserted equal
    before/after; the run aborts if they differ.
  * Short ATCs are applied as-is, never completed by guessing.

Usage (from the repo root, with the goal venv or any pandas+pyarrow env):
  python scripts/fix_atc_round3.py            # dry run: report only
  python scripts/fix_atc_round3.py --apply    # write fixed files
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
REPORT2_PATH = REPO_ROOT / "data" / "atc_fix_report_round2.json"
REPORT3_PATH = REPO_ROOT / "data" / "atc_fix_report_round3.json"
SEARCH3_PATH = REPO_ROOT / "data" / "atc_round3_search.json"
LOOKUPS_PATH = GOAL_DIR / "hidden_files" / "atc_round3_lookups.json"

# --- A. Route/form disambiguation of the 39 ambiguous (code -> (ATC, reason)) ---
# All single-ingredient (verified via generic_latin_list). Form/route taken
# from generic_name in the data; the candidate that does not fit the route
# is dropped. Combination codes (N02BE71 = paracetamol + psycholeptics,
# A10AD-style groups) do not apply to single-ingredient generics.
DISAMBIGUATED: dict[str, tuple[str, str]] = {
    # Acetaminophen, oral/rectal, single ingredient -> N02BE01 (plain);
    # N02BE71 is paracetamol in combination with psycholeptics.
    "12928": ("N02BE01", "acetaminophen tablet effervescent oral, single ingredient; N02BE71 is combo with psycholeptics"),
    "17679": ("N02BE01", "acetaminophen tablet orally disintegrating oral, single ingredient"),
    "18133": ("N02BE01", "acetaminophen tablet orally disintegrating oral, single ingredient"),
    "52196": ("N02BE01", "acetaminophen suppository rectal, single ingredient"),
    "52792": ("N02BE01", "acetaminophen suspension oral, single ingredient"),
    "52816": ("N02BE01", "acetaminophen capsule liquid filled oral, single ingredient"),
    "53238": ("N02BE01", "acetaminophen tablet extended release oral, single ingredient"),
    # Acetylcysteine, oral powder/granule -> R05CB01 (mucolytic);
    # V03AB23 is the parenteral antidote form.
    "13957": ("R05CB01", "acetylcysteine powder oral; V03AB23 is parenteral antidote"),
    "52797": ("R05CB01", "acetylcysteine granule oral; V03AB23 is parenteral antidote"),
    "52798": ("R05CB01", "acetylcysteine granule oral; V03AB23 is parenteral antidote"),
    # Ibuprofen, oral/rectal systemic -> M01AE01; C01EB16 is IV neonatal
    # PDA closure, M02AA13 is topical.
    "19899": ("M01AE01", "ibuprofen tablet extended release oral"),
    "52480": ("M01AE01", "ibuprofen suspension oral"),
    "52755": ("M01AE01", "ibuprofen tablet oral"),
    "52756": ("M01AE01", "ibuprofen tablet extended release oral"),
    "52985": ("M01AE01", "ibuprofen suppository rectal"),
    "52986": ("M01AE01", "ibuprofen suppository rectal"),
    # Diclofenac: oral -> M01AB05; topical gel -> M02AA15; S01BC03 ophthalmic.
    "19930": ("M01AB05", "diclofenac tablet extended release oral"),
    "53050": ("M01AB05", "diclofenac tablet extended release oral"),
    "53380": ("M02AA15", "diclofenac gel topical"),
    # Gentamicin, parenteral injection -> J01GB03; S01AA11 ophthalmic, S03AA06 otological.
    "51335": ("J01GB03", "gentamicin injection parenteral"),
    # Fluticasone, nasal spray -> R01AD08; R03BA05 is inhaled.
    "52149": ("R01AD08", "fluticasone spray nasal"),
    # Ketotifen, syrup oral -> R06AX17; S01 is ophthalmic.
    "52299": ("R06AX17", "ketotifen syrup oral"),
    # Sodium chloride: IV -> B05XA03; nasal -> R01AX (as stored, not completed).
    "52793": ("B05XA03", "sodium chloride injection solution intravenous"),
    "53229": ("R01AX", "sodium chloride spray nasal"),
    "53246": ("R01AX", "sodium chloride solution drops nasal"),
    # Ciprofloxacin: oral -> J01MA02; ophthalmic drops -> S01AE03.
    "52435": ("J01MA02", "ciprofloxacin tablet oral"),
    "53406": ("S01AE03", "ciprofloxacin solution drops ophthalmic"),
    # Tadalafil, oral -> G04BE08 (tadalafil-specific; G04BE is the parent group).
    "52889": ("G04BE08", "tadalafil tablet oral"),
    # Isotretinoin, oral capsule -> D10BA01 (systemic); D10AD04 is topical.
    "53031": ("D10BA01", "isotretinoin capsule liquid filled oral"),
    # Budesonide, capsule respiratory (inhalation) -> R03BA02;
    # A07EA06 is intestinal oral, R01AD05 is nasal.
    "53061": ("R03BA02", "budesonide capsule respiratory"),
    # Aciclovir, oral -> J05AB01; D06BB03 topical, S01AD03 ophthalmic.
    "53098": ("J05AB01", "aciclovir suspension oral"),
    "53200": ("J05AB01", "aciclovir powder for suspension oral"),
    # Valproate sodium, systemic -> N03AG01 (valproic acid; N03AG is the parent group).
    "53457": ("N03AG01", "valproate sodium injection solution parenteral"),
    "8601": ("N03AG01", "valproate sodium syrup oral"),
    # Terbinafine, cream topical -> D01AE15; D01BA02 is oral.
    "53379": ("D01AE15", "terbinafine cream topical"),
    # Clindamycin, cream vaginal -> G01AA10; D10AF01 topical acne, J01FF01 systemic.
    "53593": ("G01AA10", "clindamycin cream vaginal"),
}

NOT_RESOLVED_AMBIGUOUS: dict[str, str] = {
    "1635": "phenylephrine injection parenteral: candidates R01AA04 (nasal) and S01GA55 (ophthalmic) both do not fit parenteral; correct code per WHO ATC is C01CA06 but it was not among the candidates found in local files, so left blank rather than forced",
    "52378": "sodium chloride solution respiratory 7%: no candidate (B05CB01/B05XA03/R01AX/S01XA03) fits the respiratory/nebulizer route",
    "53152": "lidocaine injection parenteral: D04AB01 is topical; C01BB01 is IV antiarrhythmic but the injection could equally be local anaesthetic (N01BB02, not a candidate); form does not decide",
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

    if REPORT3_PATH.exists():
        print("REFUSED: data/atc_fix_report_round3.json already exists — "
              "round 3 was already applied.")
        return
    report2 = json.loads(REPORT2_PATH.read_text(encoding="utf-8"))
    still_blank_r2 = set(str(c) for c in report2["generics_still_blank"])
    lookups = json.loads(LOOKUPS_PATH.read_text(encoding="utf-8"))

    # Build the map with guards.
    mapping: dict[str, str] = {}
    sources: dict[str, str] = {}
    for code, (atc, _reason) in DISAMBIGUATED.items():
        assert code in still_blank_r2, f"disambiguated {code} not in round-2 still-blank list"
        mapping[code] = norm_atc(atc)
        sources[code] = "route_disambiguation"
    for code, entry in lookups.items():
        atc = norm_atc(entry.get("accepted"))
        if not atc:
            continue
        assert code in still_blank_r2, f"lookup {code} not in round-2 still-blank list"
        assert code not in mapping, f"{code} in both disambiguation and lookups"
        mapping[code] = atc
        src = entry.get("source", "ChEMBL/KEGG")
        if src.startswith("ChEMBL pref_name"):
            src = "ChEMBL (single ATC)"
        sources[code] = src
    for code in NOT_RESOLVED_AMBIGUOUS:
        assert code in still_blank_r2, f"unresolved {code} not in round-2 still-blank list"
        assert code not in mapping, f"unresolved {code} unexpectedly mapped"

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
    assert len(still_blank_codes) == len(still_blank_r2) - len(mapping), (
        f"still-blank mismatch: {len(still_blank_codes)} vs "
        f"{len(still_blank_r2) - len(mapping)}")

    atc_year = rebuild_atc_year(fixed)

    catalog = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else catalog.get("catalog", catalog.get("generics", []))
    catalog_filled = sum(
        1 for e in entries
        if (not e.get("atc")) and str(e.get("generic_code", "")) in mapping
    )
    assert catalog_filled == len(mapping), (
        f"catalog fillable {catalog_filled} != mapped {len(mapping)}")

    by_source = collections.Counter(sources.values())
    still_reasons: dict[str, str] = {}
    for code, entry in lookups.items():
        if code not in mapping:
            still_reasons[code] = entry.get("reason") or "no unique ATC from ChEMBL/KEGG"
    for code, reason in NOT_RESOLVED_AMBIGUOUS.items():
        still_reasons[code] = reason

    report = {
        "method": ("round 3: (A) route/form disambiguation of 39 ambiguous "
                   "(36 resolved, 3 left blank); (B) ChEMBL API "
                   "(pref_name__iexact) + KEGG API (find/drug + get) lookups "
                   "for 434 not-found, accepted only when exactly one ATC "
                   "remained after exact-name, entry-composition, level-5 "
                   "specificity, combination-group and route filters. "
                   "«پیام داروساز» NOT used (user: only after midnight)."),
        "before": before,
        "after": after,
        "rows_filled": int(fillable.sum()),
        "generics_filled": len(mapping),
        "generics_filled_by_source": dict(by_source),
        "generics_filled_disambiguated": len(DISAMBIGUATED),
        "generics_still_blank": still_blank_codes,
        "generics_still_blank_count": len(still_blank_codes),
        "still_blank_reasons": still_reasons,
        "still_blank_units": int(still["units"].sum()),
        "still_blank_value_consumer_rial": int(still["value_consumer"].sum()),
        "catalog_entries_filled": catalog_filled,
        "totals_unchanged": totals_before == totals_after,
    }

    search_evidence = {
        "disambiguated": [
            {"generic_code": c, "atc": a, "reason": r}
            for c, (a, r) in sorted(DISAMBIGUATED.items())
        ],
        "not_resolved_ambiguous": [
            {"generic_code": c, "reason": r}
            for c, r in sorted(NOT_RESOLVED_AMBIGUOUS.items())
        ],
        "lookups": lookups,
    }

    if args.apply:
        for p in (AGG_DIR / "generic_monthly.parquet",
                  AGG_DIR / "atc_year.parquet", DATA_JSON):
            bak = p.with_suffix(p.suffix + ".bak-before-atc-round3")
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
        catalog.setdefault("atc_fix", {})["round3"] = {
            "method": report["method"],
            "before": before,
            "after": after,
            "rows_filled": int(fillable.sum()),
            "generics_filled": len(mapping),
            "generics_still_blank_count": len(still_blank_codes),
        }
        # Promote round-3 results to the top level: the dashboard reads
        # atc_fix.after / rows_filled / generics_still_blank_count, and
        # those must always be the LATEST fix state (round 2 did the same).
        af = catalog["atc_fix"]
        af["after"] = after
        af["rows_filled"] = int(af.get("rows_filled", 0)) + int(fillable.sum())
        af["generics_still_blank_count"] = len(still_blank_codes)
        af["method"] = (af.get("method", "") + " Round 3 "
                        "(scripts/fix_atc_round3.py): 373 more generics "
                        "filled by route/form disambiguation (36) and "
                        "ChEMBL/KEGG lookups (337).")
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, allow_nan=False)
        REPORT3_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        SEARCH3_PATH.write_text(json.dumps(search_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("APPLIED: round-3 fixed aggregates + data.json written, backups kept.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to write.")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
