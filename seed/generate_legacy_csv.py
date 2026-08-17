"""Source 5/5 — Flat files (CSV): Approvals / Legacy Data.

Simulates an old system nobody actively maintains: no database at all, just
exported spreadsheets. This is the messiest source on purpose:
  - different date format per file (DD-MM-YYYY / YYYY/MM/DD / DD Mon YYYY)
  - inconsistent casing on status fields
  - stray whitespace, blank values, one file with an extra empty column
  - dates 2-3 years older than the other sources (staleness)

Written with pandas directly to data/legacy/. NO database connection.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from seed import db_utils
from seed.conflicts_log import add as log_conflict
from seed.name_noise_utils import legacy_messy_variant, noisy_variant

db_utils.load_env()

OUT_DIR = PROJECT_ROOT / "data" / "legacy"
CONFLICTS_PATH = PROJECT_ROOT / "conflicts_seeded.json"

# Institutes that exist only in these CSVs (legacy-only records) — 12 of them
LEGACY_ONLY = [
    "Ratnagiri Institute of Technology, Ratnagiri",
    "Nanded Engineering College",
    "Shri Ganesh Polytechnic, Bhopal",
    "Coastal Engineering Institute, Visakhapatnam",
    "Kharagpur Old Polytechnic",
    "Bikaner Technical College, Bikaner",
    "Haldia Institute of Pharmacy, Haldia",
    "Yamuna Engineering College, Etawah",
    "Pragati Polytechnic, Jhansi",
    "Saurashtra Institute of Management, Porbandar",
    "Girna College of Applied Arts, Malegaon",
    "Sundarban Institute of Technology, Canning",
]

STALE_RANGE = (date(2023, 1, 1), date(2024, 6, 30))

NBA_STATUSES = ["Accredited", "accredited", "ACCREDITED", "Not Accredited", "not accredited", "Not Accredited"]
CLOSE_REASONS = [
    "No admission for 3 consecutive years",
    "affiliation revoked",
    "Infrastructure non-compliance",
    "voluntarily closed",
    "Merged with another institute",
    "Poor placement record",
]
FLAG_REASONS = [
    "Missing mandatory approvals",
    "AICTE code mismatch",
    "Pending NBA accreditation",
    "Fee regulation violation",
    "Staff qualification not verified",
    "Land documents pending",
]


def _stale_date(rng: random.Random) -> date:
    span = (STALE_RANGE[1] - STALE_RANGE[0]).days
    return STALE_RANGE[0] + timedelta(days=rng.randint(0, span))


def _pick_name(rng, registry_names, messy_p, pad_p):
    """Pick a registry name, occasionally mess it up (legacy = messiest source)."""
    name = rng.choice(registry_names)
    if rng.random() < messy_p:
        name = legacy_messy_variant(name, rng)
    elif rng.random() < pad_p:
        name = " " + name + " "
    return name


def _gen_nba(rng, registry_names, registry_by_name, plan) -> pd.DataFrame:
    rows = []
    nba_conflict = plan["under_review_but_nba_accredited"]
    # ~72 registry institutes + legacy-only names
    names = rng.sample(registry_names, 72) + LEGACY_ONLY[:4]
    for name in names:
        if name in registry_by_name:
            # non-conflict rows must not contradict MySQL: only Approved institutes
            # can be accredited; everyone else is Not Accredited (case varies)
            if registry_by_name[name]["approval_status"] == "Approved":
                status = rng.choice(["Accredited", "accredited", "ACCREDITED", "Not Accredited", "not accredited"])
            else:
                status = rng.choice(["Not Accredited", "not accredited", "Not accredited"])
        else:
            status = rng.choice(NBA_STATUSES)  # legacy-only institute, free choice
        valid_until = ""
        if rng.random() < 0.85:
            d = _stale_date(rng) + timedelta(days=900)
            valid_until = d.strftime("%d-%m-%Y")
        rows.append({
            "Institute_Name": _pick_name(rng, [name], 0.5, 0.15),
            "NBA_Status": status,
            "Valid_Until": valid_until,
            "last_updated": _stale_date(rng).strftime("%d-%m-%Y"),
        })
    # plant the nba conflicts explicitly (messy spellings)
    for k, name in enumerate(nba_conflict):
        rows.append({
            "Institute_Name": legacy_messy_variant(name, rng),
            "NBA_Status": "Accredited",
            "Valid_Until": (_stale_date(rng) + timedelta(days=900)).strftime("%d-%m-%Y"),
            "last_updated": _stale_date(rng).strftime("%d-%m-%Y"),
        })
        log_conflict(CONFLICTS_PATH, {
            "type": "cross_source_conflict", "source": "legacy_csv:nba_autonomous_status.csv", "id": f"cs_nba_{k + 1:02d}",
            "description": "Institute is Under Review in MySQL but NBA CSV lists it as Accredited",
            "institutes": [name],
            "sources": ["mysql", "legacy_csv:nba_autonomous_status.csv"],
            "fields_involved": {"mysql.approval_status": "Under Review", "legacy_csv.nba_status": "Accredited"},
        })
    df = pd.DataFrame(rows)
    df[""] = ""  # stray empty column — legacy spreadsheet export artifact
    return df


def _gen_closed(rng, registry_names, registry_by_name, plan) -> pd.DataFrame:
    rows = []
    # non-conflict rows must NOT contradict MySQL: only reference institutes that
    # are NOT Approved (a closed institute can't be approved unless it's a planted conflict)
    candidates = [n for n in registry_names
                  if registry_by_name[n]["approval_status"] != "Approved"
                  and n not in plan["approved_but_closed"]]
    names = rng.sample(candidates, min(88, len(candidates))) + LEGACY_ONLY[2:5]
    for name in names:
        rows.append({
            "Institute_Name": _pick_name(rng, [name], 0.5, 0.15),
            "Closure_Year": rng.randint(2018, 2024),
            "Reason": rng.choice(CLOSE_REASONS) if rng.random() > 0.08 else "",
            "last_updated": _stale_date(rng).strftime("%Y/%m/%d"),
        })
    # the planted conflicts: Approved in MySQL but closed here
    for k, name in enumerate(plan["approved_but_closed"]):
        closure_year = rng.randint(2019, 2024)
        rows.append({
            "Institute_Name": legacy_messy_variant(name, rng),
            "Closure_Year": closure_year,
            "Reason": "closed by regulatory order" if rng.random() < 0.5 else rng.choice(CLOSE_REASONS),
            "last_updated": _stale_date(rng).strftime("%Y/%m/%d"),
        })
        log_conflict(CONFLICTS_PATH, {
            "type": "cross_source_conflict", "source": "legacy_csv:closed_institutes.csv", "id": f"cs_closed_{k + 1:02d}",
            "description": "Institute is Approved in MySQL but appears in closed_institutes.csv",
            "institutes": [name],
            "sources": ["mysql", "legacy_csv:closed_institutes.csv"],
            "fields_involved": {"mysql.approval_status": "Approved", "legacy_csv.closed_institutes": f"closure_year={closure_year}"},
        })
    return pd.DataFrame(rows)


def _gen_unapproved(rng, registry_names, registry_by_name, plan) -> pd.DataFrame:
    rows = []
    # only reference institutes that are NOT Approved (unless planted conflict)
    candidates = [n for n in registry_names
                  if registry_by_name[n]["approval_status"] != "Approved"
                  and n not in plan["approved_but_unapproved_listed"]]
    names = rng.sample(candidates, min(95, len(candidates))) + LEGACY_ONLY[5:9]
    for name in names:
        rows.append({
            "Institute_Name": _pick_name(rng, [name], 0.5, 0.15),
            "State": name.rsplit(", ", 1)[-1] if ", " in name else "",
            "Flag_Reason": rng.choice(FLAG_REASONS) if rng.random() > 0.06 else "",
            "last_updated": _stale_date(rng).strftime("%d %b %Y"),
        })
    for k, name in enumerate(plan["approved_but_unapproved_listed"]):
        rows.append({
            "Institute_Name": legacy_messy_variant(name, rng),
            "State": name.rsplit(", ", 1)[-1],
            "Flag_Reason": "Listed in unapproved register despite active approval" if rng.random() < 0.5 else rng.choice(FLAG_REASONS),
            "last_updated": _stale_date(rng).strftime("%d %b %Y"),
        })
        log_conflict(CONFLICTS_PATH, {
            "type": "cross_source_conflict", "source": "legacy_csv:unapproved_list.csv", "id": f"cs_unapproved_{k + 1:02d}",
            "description": "Institute is Approved in MySQL but appears in unapproved_list.csv",
            "institutes": [name],
            "sources": ["mysql", "legacy_csv:unapproved_list.csv"],
            "fields_involved": {"mysql.approval_status": "Approved", "legacy_csv.unapproved_list": "flagged"},
        })
    return pd.DataFrame(rows)


def main() -> None:
    rng = random.Random(20260816 + 5)
    registry = json.loads((PROJECT_ROOT / "institute_registry.json").read_text(encoding="utf-8"))
    registry_names = [i["name"] for i in registry["institutes"]]
    registry_by_name = {i["name"]: i for i in registry["institutes"]}
    plan = registry["conflict_plan"]

    # legacy-only institutes (exist nowhere else) are logged as orphaned records
    for k, name in enumerate(LEGACY_ONLY):
        log_conflict(CONFLICTS_PATH, {
            "type": "orphaned_record", "source": "legacy_csv", "id": f"csv_legacy_only_{k + 1:02d}",
            "description": "Institute appears only in legacy CSV files, nowhere else",
            "institutes": [name],
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    nba = _gen_nba(rng, registry_names, registry_by_name, plan)
    closed = _gen_closed(rng, registry_names, registry_by_name, plan)
    unapproved = _gen_unapproved(rng, registry_names, registry_by_name, plan)

    nba.to_csv(OUT_DIR / "nba_autonomous_status.csv", index=False)
    closed.to_csv(OUT_DIR / "closed_institutes.csv", index=False)
    unapproved.to_csv(OUT_DIR / "unapproved_list.csv", index=False)

    print("=" * 60)
    print(f"[legacy_csv] wrote 3 files to data/legacy/ (no DB involved)")
    print(f"[legacy_csv] nba_autonomous_status.csv : {len(nba)} rows   (date format: DD-MM-YYYY)")
    print(f"[legacy_csv] closed_institutes.csv      : {len(closed)} rows   (date format: YYYY/MM/DD)")
    print(f"[legacy_csv] unapproved_list.csv        : {len(unapproved)} rows   (date format: DD Mon YYYY)")
    print(f"[legacy_csv] cross-source conflicts planted (logged): "
          f"{len(plan['approved_but_closed'])} closed + "
          f"{len(plan['approved_but_unapproved_listed'])} unapproved + "
          f"{len(plan['under_review_but_nba_accredited'])} nba")


if __name__ == "__main__":
    main()
