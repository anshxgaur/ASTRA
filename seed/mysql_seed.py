"""Source 1/5 — MySQL: Colleges/Institutes.

Simulates the AICTE-adjacent institutes master list: one big relational table,
mostly well-maintained, with a handful of copy-paste errors.
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pymysql

from seed import db_utils
from seed.conflicts_log import add as log_conflict
from seed.name_noise_utils import noisy_variant

db_utils.load_env()

NOW = date(2026, 8, 16)
CONFLICTS_PATH = PROJECT_ROOT / "conflicts_seeded.json"
DUPLICATE_COUNT = 18  # within-source duplicates to plant (was 9)

CREATE_SQL = """
CREATE TABLE institutes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  AICTE_Code VARCHAR(20) NOT NULL,
  Institute_Name VARCHAR(255) NOT NULL,
  State VARCHAR(60),
  District VARCHAR(60),
  City VARCHAR(60),
  Institute_Type VARCHAR(40),
  Ownership VARCHAR(20),
  Approval_Status VARCHAR(20),
  Current_Status VARCHAR(20),
  Year_Established INT,
  Is_Autonomous TINYINT(1),
  NBA_Accredited TINYINT(1),
  Accreditation_Valid_Until DATE,
  Last_Updated DATE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _connect():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _insert(cur, row: dict) -> None:
    cur.execute(
        "INSERT INTO institutes (AICTE_Code, Institute_Name, State, District, City, "
        "Institute_Type, Ownership, Approval_Status, Current_Status, Year_Established, "
        "Is_Autonomous, NBA_Accredited, Accreditation_Valid_Until, Last_Updated) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (row["aicte_code"], row["name"], row["state"], row["district"], row["city"],
         row["institution_type"], row["ownership"], row["approval_status"], row["current_status"],
         row["established_year"], int(bool(row["is_autonomous"])), int(bool(row["nba_accredited"])),
         row["accreditation_valid_until"] or None, row["last_updated"]),
    )


def _plant_duplicates(cur, institutes, rng) -> list[dict]:
    """Plant 18 within-source duplicates and log each one."""
    planted = []
    for k in range(DUPLICATE_COUNT):
        src = institutes[rng.randrange(len(institutes))]
        dup_id = f"mysql_dup_{k + 1:02d}"
        base = {
            "aicte_code": src["aicte_code"], "state": src["state"], "district": src["district"],
            "city": src["city"], "institution_type": src["institution_type"],
            "ownership": src["ownership"], "approval_status": src["approval_status"],
            "current_status": src["current_status"], "established_year": src["established_year"],
            "is_autonomous": src["is_autonomous"], "nba_accredited": src["nba_accredited"],
            "accreditation_valid_until": src["accreditation_valid_until"],
            "last_updated": NOW - timedelta(days=rng.randint(0, 90)),
        }
        if k % 3 == 0:
            # same AICTE_Code reused, name spelled differently
            row = dict(base, name=noisy_variant(src["name"], rng, min_transforms=2, max_transforms=3))
            desc = "Same AICTE_Code reused for a second row with a differently-spelled name (copy-paste error)"
            detail = {"aicte_code": src["aicte_code"], "name_a": src["name"], "name_b": row["name"]}
        elif k % 3 == 1:
            # copy-paste row: identical except Last_Updated differs
            row = dict(base, name=src["name"], last_updated=NOW - timedelta(days=rng.randint(1, 14)))
            desc = "Copy-paste duplicate row; only Last_Updated differs"
            detail = {"aicte_code": src["aicte_code"], "name": src["name"]}
        else:
            # exact duplicate row
            row = dict(base, name=src["name"])
            desc = "Exact duplicate row (identical field values)"
            detail = {"aicte_code": src["aicte_code"], "name": src["name"]}
        _insert(cur, row)
        log_conflict(CONFLICTS_PATH, {
            "type": "within_source_duplicate", "source": "mysql", "id": dup_id,
            "description": desc, "institutes": [src["name"]], "detail": detail,
        })
        planted.append(dup_id)
    return planted


def main() -> None:
    rng = random.Random(20260816 + 1)
    registry = json.loads((PROJECT_ROOT / "institute_registry.json").read_text(encoding="utf-8"))
    institutes = registry["institutes"]

    # conflict carriers keep their canonical name so cross-source
    # contradictions are actually detectable after normalization
    plan = registry["conflict_plan"]
    plan_carriers = {n for lst in plan.values() for n in lst}

    if not db_utils.wait_mysql():
        print("[mysql] ERROR: could not reach MySQL. Is it up? (docker compose up -d)")
        sys.exit(1)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS institutes")
            cur.execute(CREATE_SQL)
            inserted = 0
            noisy_count = 0
            for inst in institutes:
                name = inst["name"]
                if inst["name"] not in plan_carriers and rng.random() < 0.15:
                    name = noisy_variant(name, rng)
                    noisy_count += 1
                row = {
                    "aicte_code": inst["aicte_code"], "name": name, "state": inst["state"],
                    "district": inst["district"], "city": inst["city"],
                    "institution_type": inst["institution_type"], "ownership": inst["ownership"],
                    "approval_status": inst["approval_status"], "current_status": inst["current_status"],
                    "established_year": inst["established_year"], "is_autonomous": inst["is_autonomous"],
                    "nba_accredited": inst["nba_accredited"],
                    "accreditation_valid_until": inst["accreditation_valid_until"],
                    "last_updated": NOW - timedelta(days=rng.randint(0, 90)),
                }
                _insert(cur, row)
                inserted += 1

            planted = _plant_duplicates(cur, institutes, rng)

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM institutes")
            total = cur.fetchone()["c"]

        print("=" * 60)
        print(f"[mysql] source: aicte_institutes.institutes ({os.environ['MYSQL_HOST']}:{os.environ['MYSQL_PORT']})")
        print(f"[mysql] institutes seeded        : {inserted}  (name-noisy rows: {noisy_count})")
        print(f"[mysql] within-source duplicates: {len(planted)}")
        print(f"[mysql] total rows in table     : {total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
