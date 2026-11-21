#!/usr/bin/env python3
"""Fix blank ATC codes, round 4: WHO ATC/DDD Index lookups (atcddd.fhi.no).

Rounds 1-3 are applied (commit 51bcb56). Remaining after round 3:
100 generics with no ATC (data/atc_fix_report_round3.json). Round 4 resolves
them from the WHO ATC/DDD Index only — the authoritative source. «پیام داروساز»
was NOT used for ATC (probe 2026-11-22: the site shows generic codes, forms,
brands and insurance %, but NO ATC anywhere on any record).

Acceptance rule (per code, evidence in hidden_files/atc_round4_lookups.json
and hidden_files/atc_round4_followups.json):
  * exactly one WHO entry for the exact ingredient, OR
  * one WHO entry for the ingredient's route/form as given in generic_name, OR
  * a WHO combination entry naming the exact combination, OR
  * a WHO combination bucket naming the (main) ingredient (e.g. A06AB58
    "sodium picosulfate, combinations"), OR
  * a WHO guideline text that decides the code (levonorgestrel emergency
    contraception -> G03AD01; buprenorphine high-strength depot -> N07BC01).
Bare "combinations" buckets (B01AC30, R05CB10) and category names were NOT
accepted. 71 of 100 resolved; 29 left blank (see NOT_RESOLVED).

What it does (read-only by default; --apply writes):
  1. Fills blank atc_code in generic_monthly.parquet for the resolved codes.
  2. Rebuilds data/aggregates/atc_year.parquet from the fixed table.
  3. Updates docs/data.json: catalog ATC fields, the atc_year array,
     and the `atc_fix` summary (adds a round4 block).
  4. Writes data/atc_fix_report_round4.json (before = round-3 after,
     after, rows filled, per-source counts, still-blank list with reasons)
     and data/atc_round4_search.json (evidence).

Safety: same as round 3 — data/originals/* never touched; stats.json and the
`stats` block in data.json NOT rewritten; every mapped code must be in the
round-3 still-blank list AND currently blank; exactly one ATC per code;
--apply writes new backups (*.bak-before-atc-round4); totals asserted equal
before/after; short ATCs applied as-is, never completed by guessing.

Usage (from the repo root, with the goal venv or any pandas+pyarrow env):
  python scripts/fix_atc_round4.py            # dry run: report only
  python scripts/fix_atc_round4.py --apply    # write fixed files
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
REPORT3_PATH = REPO_ROOT / "data" / "atc_fix_report_round3.json"
REPORT4_PATH = REPO_ROOT / "data" / "atc_fix_report_round4.json"
SEARCH4_PATH = REPO_ROOT / "data" / "atc_round4_search.json"
LOOKUPS_PATH = GOAL_DIR / "hidden_files" / "atc_round4_lookups.json"
FOLLOWUPS_PATH = GOAL_DIR / "hidden_files" / "atc_round4_followups.json"

WHO = "https://atcddd.fhi.no/atc_ddd_index/"

# code -> (ATC, evidence). Evidence cites the WHO index entry/guideline.
MAPPING: dict[str, tuple[str, str]] = {
    "1083": ("P01BD01", "WHO name search 'pyrimethamine': exact entry P01BD01 pyrimethamine; other hits are combinations (P01BD51, P01BF04, P01BF09); single-ingredient oral tablet"),
    "1088": ("C01BA01", "WHO name search 'quinidine': exact entry C01BA01 quinidine; other hits are combinations (C01BA51, C01BA71) or hydroquinidine (different substance)"),
    "11506": ("V03AB24", "WHO name search 'digitalis antitoxin': exactly 1 entry V03AB24 digitalis antitoxin (digoxin immune Fab = digitalis antitoxin)"),
    "12102": ("V03AF07", "WHO name search 'rasburicase': exactly 1 entry V03AF07 rasburicase"),
    "13879": ("L01CD01", "WHO code page L01CD01: 'paclitaxel includes solvent based paclitaxel and paclitaxel albumin' — albumin-bound form included"),
    "14202": ("A11BA", "WHO A11BA 'Multivitamins, plain' — the only WHO code for plain multivitamins (no 5th level exists); generic_latin_list is MULTIVITAMIN; applied as-is, 4-char code"),
    "1635": ("C01CA06", "WHO name search 'phenylephrine': 10 entries named phenylephrine; only C01CA06 is parenteral/systemic (others nasal R01AA04/R01AB01/R01BA03/R01BA53, ophthalmic S01FB01/S01GA05/S01GA55/S01FB51, rectal C05AX06); injection parenteral"),
    "16870": ("G03AD01", "WHO G03AD guideline: 'Levonorgestrel products (packages) indicated only for emergency contraception are classified in G03AD01'; DDD 1.5 mg oral; product is the 1.5 mg emergency tablet"),
    "16889": ("R07AA02", "WHO name search 'natural phospholipids': exactly 1 entry R07AA02; DDD 0.16 g, Adm.R Instill.solution; beractant is the natural-phospholipid lung surfactant, given endotracheally"),
    "2372": ("R07AA02", "WHO name search 'natural phospholipids': exactly 1 entry R07AA02; DDD 0.16 g, Adm.R Instill.solution; beractant is the natural-phospholipid lung surfactant, given endotracheally"),
    "2414": ("C05BX01", "WHO name search 'calcium dobesilate': exact entry C05BX01 calcium dobesilate; C05BX51 is combinations"),
    "2441": ("G03AD01", "WHO G03AD guideline as for 16870; 750 ug tablet = half of the 1.5 mg emergency course (2 x 750 ug); G03AC03 is the daily progestogen-only contraceptive"),
    "50061": ("N05AF01", "WHO code page N05AF01 flupentixol: DDD 6 mg O, 4 mg P depot — depot (decanoate) form included"),
    "50202": ("L03AB11", "WHO name search 'peginterferon alfa-2a': exact entry L03AB11 peginterferon alfa-2a; L03AB61 is combinations"),
    "50702": ("C09DX04", "WHO name search 'sacubitril': exactly 1 entry C09DX04 valsartan and sacubitril"),
    "50837": ("B06AC01", "WHO name search 'c1-inhibitor': exactly 1 entry B06AC01 c1-inhibitor, plasma derived"),
    "50838": ("S01FA04", "WHO name search 'cyclopentolate': exact entry S01FA04 cyclopentolate; S01FA54 is combinations"),
    "51005": ("C09DX04", "WHO name search 'sacubitril': exactly 1 entry C09DX04 valsartan and sacubitril"),
    "51252": ("A07BA01", "WHO name search 'charcoal': A07BA01 medicinal charcoal (plain); A07BA51 is combinations; oral suspension"),
    "514": ("N03AD01", "WHO name search 'ethosuximide': exact entry N03AD01 ethosuximide; N03AD51 is combinations"),
    "51459": ("L01EX09", "WHO name search 'nintedanib': exactly 1 entry L01EX09 nintedanib"),
    "51492": ("A06AB08", "WHO name search 'sodium picosulfate': exact entry A06AB08 sodium picosulfate; A06AB58 is combinations"),
    "51899": ("B03BA03", "WHO B03BA03 hydroxocobalamin DDD 20 mcg P (maintenance treatment of pernicious anaemia, depot-averaged); V03AB33 antidote has no DDD (5 g course); the 5 mg/mL 2 mL ampoule (10 mg) is the B12 maintenance product, not the 5 g cyanide antidote"),
    "51925": ("D06AX01", "WHO name search 'fusidic acid': D06AX01 is the topical entry; J01XC01 systemic, S01AA13 ophthalmic, D09AA02 dressings; cream topical"),
    "52154": ("R03AK13", "WHO name search 'salbutamol': entry R03AK13 salbutamol and beclometasone (also in 'beclometasone' search); exact combination"),
    "52234": ("C09DX04", "WHO name search 'sacubitril': exactly 1 entry C09DX04 valsartan and sacubitril"),
    "52317": ("S01CA12", "WHO code page S01CA12 'loteprednol and antiinfectives'; tobramycin is the antiinfective; ophthalmic drops"),
    "52328": ("R01AD58", "WHO code page R01AD58 'fluticasone, combinations'; R01AD guideline: combinations of corticosteroids with antihistamines etc. are classified in this group at separate 5th levels by using the 50-series; azelastine is an antihistamine; nasal spray"),
    "52620": ("N03AD01", "WHO name search 'ethosuximide': exact entry N03AD01 ethosuximide; N03AD51 is combinations"),
    "52765": ("B05AA06", "WHO name search 'gelatin': B05AA06 gelatin agents (plasma expanders); B02BC01 is absorbable gelatin sponge (haemostatic); product is a 4% modified gelatin IV infusion 500 mL"),
    "52815": ("D01AC20", "WHO code page D01AC20 'imidazoles/triazoles in combination with corticosteroids'; D01AC guideline: 'Combinations with corticosteroids are classified in D01AC20'; clotrimazole is an imidazole; topical cream"),
    "52829": ("C07FB07", "WHO name search 'bisoprolol': entry C07FB07 bisoprolol and amlodipine (also in 'amlodipine' search); exact combination"),
    "52830": ("C07FB07", "WHO name search 'bisoprolol': entry C07FB07 bisoprolol and amlodipine (also in 'amlodipine' search); exact combination"),
    "52831": ("C07FB07", "WHO name search 'bisoprolol': entry C07FB07 bisoprolol and amlodipine (also in 'amlodipine' search); exact combination"),
    "52832": ("C07FB07", "WHO name search 'bisoprolol': entry C07FB07 bisoprolol and amlodipine (also in 'amlodipine' search); exact combination"),
    "52855": ("G04CA53", "WHO name search 'tamsulosin and solifenacin': exactly 1 entry G04CA53 tamsulosin and solifenacin"),
    "52862": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "52863": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "52864": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "52865": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "52866": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "52899": ("A06AB58", "WHO code page A06AB58 'sodium picosulfate, combinations' — the standard bowel-prep combination (sodium picosulfate + magnesium oxide + citric acid)"),
    "52902": ("N02AJ19", "WHO name search 'oxycodone and ibuprofen': exactly 1 entry N02AJ19 oxycodone and ibuprofen"),
    "52925": ("A06AD65", "WHO code page A06AD65 'macrogol, combinations'; A06AD guideline: 'Macrogol in combination with electrolytes is classified in A06AD65' — exactly this product (macrogol + electrolytes)"),
    "53012": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "53013": ("A10BD15", "WHO name search 'dapagliflozin': entry A10BD15 metformin and dapagliflozin (also in 'metformin' search); exact combination"),
    "53038": ("M01AE51", "WHO code page M01AE51 'ibuprofen, combinations' — the M01AE combinations bucket; no specific code exists for ibuprofen + methocarbamol"),
    "53067": ("B05BA02", "WHO name search 'fat emulsion': exactly 1 entry B05BA02 fat emulsions"),
    "53120": ("A02BC51", "WHO code page A02BC51 'omeprazole, combinations' — the omeprazole combinations bucket; no specific code exists for omeprazole + sodium bicarbonate"),
    "53121": ("A02BC51", "WHO code page A02BC51 'omeprazole, combinations' — the omeprazole combinations bucket; no specific code exists for omeprazole + sodium bicarbonate"),
    "53297": ("C10BA06", "WHO name search 'rosuvastatin and ezetimibe': exactly 1 entry C10BA06 rosuvastatin and ezetimibe"),
    "53298": ("C10BA06", "WHO name search 'rosuvastatin and ezetimibe': exactly 1 entry C10BA06 rosuvastatin and ezetimibe"),
    "53299": ("C10BA06", "WHO name search 'rosuvastatin and ezetimibe': exactly 1 entry C10BA06 rosuvastatin and ezetimibe"),
    "53300": ("C10BA06", "WHO name search 'rosuvastatin and ezetimibe': exactly 1 entry C10BA06 rosuvastatin and ezetimibe"),
    "53335": ("N07BC01", "WHO N02AE guideline: 'High strength formulations (above 0.4 mg) of buprenorphine used in opioid dependence are classified in N07BC'; the 100 mg/0.5 mL extended-release depot is high-strength and used only in opioid dependence; N07BC01 DDD includes parenteral depot"),
    "53367": ("A04AA05", "WHO name search 'palonosetron': exact entry A04AA05 palonosetron; A04AA55 is combinations"),
    "53423": ("D07CA01", "WHO code page D07CA01 'hydrocortisone and antibiotics'; D07C group: combined corticosteroid/antibiotic preparations for dermatological use; fusidic acid is an antibiotic; topical cream"),
    "53448": ("N06DA52", "WHO name search 'donepezil and memantine': exactly 1 entry N06DA52 donepezil and memantine"),
    "53450": ("N06DA52", "WHO name search 'donepezil and memantine': exactly 1 entry N06DA52 donepezil and memantine"),
    "53451": ("N06DA52", "WHO name search 'donepezil and memantine': exactly 1 entry N06DA52 donepezil and memantine"),
    "53452": ("N06DA52", "WHO name search 'donepezil and memantine': exactly 1 entry N06DA52 donepezil and memantine"),
    "53598": ("M01AE02", "WHO name search 'naproxen': M01AE02 is the oral entry; G02CC02 vaginal, M02AA12 topical, others are combinations; tablet extended release oral"),
    "53680": ("H02AB06", "WHO name search 'prednisolone': 30 entries, all other routes; H02AB06 is the only oral systemic entry; oral solution"),
    "53701": ("H02AB06", "WHO name search 'prednisolone': 30 entries, all other routes; H02AB06 is the only oral systemic entry; oral solution"),
    "53806": ("A06AB08", "WHO name search 'sodium picosulfate': exact entry A06AB08 sodium picosulfate; A06AB58 is combinations"),
    "547": ("S01JA01", "WHO name search 'fluorescein': exact entry S01JA01 fluorescein; S01JA51 is combinations"),
    "5966": ("J07BB02", "WHO J07BB group: J07BB02 'influenza, inactivated, split virus or surface antigen' — the trivalent inactivated vaccine; J07BB01 whole virus, J07BB03 live attenuated, J07BB04 virus like particles"),
    "5979": ("V03AB17", "WHO V03AB17 methylthioninium chloride (Antidotes); V04CG05 is 'Tests for gastric secretion' (diagnostic); the 10 mg/mL parenteral injection is the antidote presentation"),
    "6540": ("A11HA04", "WHO name search 'riboflavin': A11HA04 riboflavin (vit B2) is the oral entry; S01XA26 is ophthalmic; tablet oral"),
    "909": ("D08AF01", "WHO name search 'nitrofural': D08AF01 is the topical entry; B05CA03 irrigating solution, D09AA03 medicated dressings; cream topical"),
    "9576": ("B02BD01", "WHO code page B02BD01 'coagulation factor IX, II, VII and X in combination' = prothrombin complex concentrate"),
}

NOT_RESOLVED: dict[str, str] = {
    "88": "anthocyanoside A: WHO index has no entry ('anthocyanoside' search: no match)",
    "11756": "acetylcysteine/ascorbic acid combination: WHO has no code for it; R05CB10 is a bare 'combinations' bucket with no ingredient named",
    "13710": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "19051": "icodextrin: WHO index has no entry ('icodextrin' search: no match; B05DB group page lists no 5th-level codes; B05DB10 code page has no entry)",
    "21373": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "2256": "diphtheria/tetanus/pertussis vaccine: WHO J07CA has no plain-DTP code (J07CA01 D-polio-T, J07CA02 D-P-polio-T, J07CA05 D-hepB-P-T, J07CA07 D-hepB-T — all include polio or hepatitis B)",
    "22891": "amphotericin B nanoliposomal gel topical: WHO 'amphotericin b' search returns no topical code (A01AB04 oral, A07AA07 intestinal, G01AA03 vaginal, J02AA01 systemic)",
    "2397": "sodium acid phosphate effervescent tablet oral: A06AD17 (laxative, DDD 50 g O) vs V03AG05 (hypercalcaemia, DDD 8 g O) — both oral; indication not determinable from form/dose",
    "50003": "daclatasvir/sofosbuvir: WHO has no code for this combination (J05AP07 daclatasvir, J05AP08 sofosbuvir, and other sofosbuvir combinations exist, but not daclatasvir+sofosbuvir)",
    "50342": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "50348": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "51421": "trace metal combination: WHO has no code for trace elements alone (A11AA04 is multivitamins and trace elements, not this product)",
    "51673": "silver sulfadiazine/EGF cream: WHO has no code for this combination (D06BA01 is plain silver sulfadiazine)",
    "51991": "lidocaine/chlorhexidine gel: WHO has no code for this combination",
    "52014": "fluocinolone/hydroquinone/tretinoin cream: WHO has no code for this combination",
    "52360": "copper sulfate/zinc sulfate lotion: WHO has no code for this combination",
    "52378": "sodium chloride 7% respiratory solution: WHO 'sodium chloride' search returns A12CA01 oral, B05CB01 irrigating, B05XA03 parenteral, S01XA03 ophthalmic — no respiratory/nebulizer code",
    "52416": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "52417": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "52419": "ADULT COLD PREPARATIONS is a category name, not an ingredient; composition unknown",
    "52662": "acetaminophen/ibuprofen tablet: WHO has no code for this combination",
    "52877": "dextromethorphan/guaifenesin/phenylephrine syrup: WHO has no code for this combination",
    "53020": "influenza vaccine, recombinant, tetravalent: WHO J07BB lists only J07BB01 whole virus, J07BB02 split/surface antigen, J07BB03 live attenuated, J07BB04 virus like particles — no recombinant entry",
    "53152": "lidocaine injection parenteral: C01BB01 (IV antiarrhythmic) and N01BB02 (local anaesthetic) both fit parenteral; form does not decide",
    "53292": "acetylsalicylic acid/clopidogrel tablet: WHO has no entry for this combination; B01AC30 is a bare 'combinations' bucket with no ingredient named",
    "53293": "acetylsalicylic acid/clopidogrel tablet: WHO has no entry for this combination; B01AC30 is a bare 'combinations' bucket with no ingredient named",
    "53386": "artificial saliva: WHO index has no entry ('artificial saliva' search: no match)",
    "53455": "acetaminophen/chlorpheniramine/dextromethorphan/phenylephrine/ascorbic acid tablet: WHO has no code for this 5-component cold combination",
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

    if REPORT4_PATH.exists():
        print("REFUSED: data/atc_fix_report_round4.json already exists — "
              "round 4 was already applied.")
        return
    report3 = json.loads(REPORT3_PATH.read_text(encoding="utf-8"))
    still_blank_r3 = set(str(c) for c in report3["generics_still_blank"])
    assert len(MAPPING) + len(NOT_RESOLVED) == len(still_blank_r3) == 100, (
        f"mapping {len(MAPPING)} + unresolved {len(NOT_RESOLVED)} != 100")
    assert set(MAPPING) | set(NOT_RESOLVED) == still_blank_r3, (
        "mapping+unresolved does not cover exactly the round-3 still-blank list")

    mapping = {c: norm_atc(a) for c, (a, _e) in MAPPING.items()}
    assert all(mapping.values()), "empty ATC in mapping"
    assert len(set(mapping.values())) <= len(mapping), "impossible"

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
        "method": ("round 4: WHO ATC/DDD Index (atcddd.fhi.no) lookups for the "
                   "100 generics still blank after round 3. Accepted only when "
                   "the WHO index returned exactly one ATC for the exact "
                   "ingredient, one ATC for its route/form, an exact combination "
                   "entry, a WHO combination bucket naming the (main) ingredient, "
                   "or a WHO guideline text deciding the code. 71 resolved, 29 "
                   "left blank. «پیام داروساز» NOT used for ATC (probe 2026-11-22: "
                   "no ATC anywhere on any record)."),
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
    followups = json.loads(FOLLOWUPS_PATH.read_text(encoding="utf-8"))
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
        "followups": followups,
    }

    if args.apply:
        for p in (AGG_DIR / "generic_monthly.parquet",
                  AGG_DIR / "atc_year.parquet", DATA_JSON):
            bak = p.with_suffix(p.suffix + ".bak-before-atc-round4")
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
        catalog.setdefault("atc_fix", {})["round4"] = {
            "method": report["method"],
            "before": before,
            "after": after,
            "rows_filled": int(fillable.sum()),
            "generics_filled": len(mapping),
            "generics_still_blank_count": len(still_blank_codes),
        }
        # Promote round-4 results to the top level: the dashboard reads
        # atc_fix.after / rows_filled / generics_still_blank_count, and
        # those must always be the LATEST fix state.
        af = catalog["atc_fix"]
        af["after"] = after
        af["rows_filled"] = int(af.get("rows_filled", 0)) + int(fillable.sum())
        af["generics_still_blank_count"] = len(still_blank_codes)
        af["method"] = (af.get("method", "") + " Round 4 "
                        "(scripts/fix_atc_round4.py): 71 more generics "
                        "filled from the WHO ATC/DDD Index.")
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, allow_nan=False)
        REPORT4_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        SEARCH4_PATH.write_text(json.dumps(search_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("APPLIED: round-4 fixed aggregates + data.json written, backups kept.")
    else:
        print("DRY RUN — nothing written. Re-run with --apply to write.")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
