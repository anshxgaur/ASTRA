"""Orchestrator: build registry, generate legacy CSVs, seed all 4 databases.

Usage:
    docker compose up -d --wait     # start MySQL / PostgreSQL / MongoDB
    python seed_all.py              # seed everything + print summary

Idempotent: every script truncates/drops its table/collection first and the
registry + conflicts log are regenerated fresh each run.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seed import db_utils
from seed.conflicts_log import reset as reset_conflicts

CONFLICTS_PATH = PROJECT_ROOT / "conflicts_seeded.json"

STEPS = [
    ("registry  ", ["-m", "seed.generate_registry"]),
    ("legacy_csv", ["-m", "seed.generate_legacy_csv"]),
    ("internships", ["-m", "seed.generate_internships"]),
    ("mysql     ", ["-m", "seed.mysql_seed"]),
    ("pg_courses", ["-m", "seed.postgres_courses_seed"]),
    ("pg_faculty", ["-m", "seed.postgres_faculty_seed"]),
    ("mongo     ", ["-m", "seed.mongo_seed"]),
]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _run_all() -> bool:
    print("=" * 60)
    print(" AICTE Search Engine Prototype — seeding 5 fragmented sources")
    print("=" * 60)
    for label, args in STEPS:
        print(f"\n>>> [{label}] starting ...")
        proc = subprocess.run([sys.executable, *args], cwd=PROJECT_ROOT)
        if proc.returncode != 0:
            print(f"\n[seed_all] FAILED at step '{label}' (exit {proc.returncode}). Aborting.")
            return False
    return True


def _summary() -> None:
    import pandas as pd

    import pymysql
    import psycopg
    from pymongo import MongoClient

    registry = json.loads((PROJECT_ROOT / "institute_registry.json").read_text(encoding="utf-8"))
    institutes = registry["institutes"]
    canonical = {_norm(i["name"]): i["name"] for i in institutes}

    def overlap(source_names: list[str]) -> int:
        matched = set()
        for n in source_names:
            nn = _norm(n)
            for c in canonical:
                if nn == c or nn in c or c in nn:
                    matched.add(c)
                    break
        return len(matched)

    # --- counts per source ---
    # MySQL
    m = pymysql.connect(
        host=db_utils.os.environ["MYSQL_HOST"], port=int(db_utils.os.environ["MYSQL_PORT"]),
        user=db_utils.os.environ["MYSQL_USER"], password=db_utils.os.environ["MYSQL_PASSWORD"],
        database=db_utils.os.environ["MYSQL_DATABASE"],
    )
    try:
        with m.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM institutes")
            mysql_rows = cur.fetchone()[0]
            cur.execute("SELECT Institute_Name FROM institutes")
            mysql_names = [r[0] for r in cur.fetchall()]
    finally:
        m.close()

    # Postgres courses + faculty
    p = psycopg.connect(
        host=db_utils.os.environ["POSTGRES_HOST"], port=int(db_utils.os.environ["POSTGRES_PORT"]),
        user=db_utils.os.environ["POSTGRES_USER"], password=db_utils.os.environ["POSTGRES_PASSWORD"],
        dbname=db_utils.os.environ["COURSES_DB"], autocommit=True,
    )
    try:
        with p.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT college_name) FROM courses")
            courses_rows, courses_distinct = cur.fetchone()
            cur.execute("SELECT college_name FROM courses")
            courses_names = [r[0] for r in cur.fetchall()]
    finally:
        p.close()

    p2 = psycopg.connect(
        host=db_utils.os.environ["POSTGRES_HOST"], port=int(db_utils.os.environ["POSTGRES_PORT"]),
        user=db_utils.os.environ["POSTGRES_USER"], password=db_utils.os.environ["POSTGRES_PASSWORD"],
        dbname=db_utils.os.environ["FACULTY_DB"], autocommit=True,
    )
    try:
        with p2.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT institute_ref) FROM faculty")
            faculty_rows, faculty_distinct = cur.fetchone()
            cur.execute("SELECT institute_ref FROM faculty")
            faculty_names = [r[0] for r in cur.fetchall()]
    finally:
        p2.close()

    # MongoDB
    client = MongoClient(db_utils.os.environ["MONGO_HOST"], int(db_utils.os.environ["MONGO_PORT"]), serverSelectionTimeoutMS=5000)
    coll = client[db_utils.os.environ["MONGO_DB"]]["scholarships"]
    mongo_docs = coll.count_documents({})
    mongo_refs = [d.get("applicable_institutes", []) for d in coll.find({"applicable_institutes": {"$exists": True}})]
    mongo_names = [n for refs in mongo_refs for n in refs]
    client.close()

    # CSV files
    csv_counts = {}
    csv_names: list[str] = []
    for f in ["nba_autonomous_status.csv", "closed_institutes.csv", "unapproved_list.csv"]:
        df = pd.read_csv(PROJECT_ROOT / "data" / "legacy" / f, dtype=str)
        csv_counts[f] = len(df)
        col = "Institute_Name" if "Institute_Name" in df.columns else df.columns[0]
        csv_names += [str(x) for x in df[col].dropna().tolist()]

    # Internships CSV (clean 6th source)
    int_df = pd.read_csv(PROJECT_ROOT / "data" / "internships.csv", dtype=str)
    internships_rows = len(int_df)
    internships_distinct = int_df["institution_name"].nunique()
    internships_names = [str(x) for x in int_df["institution_name"].dropna().tolist()]

    # --- planted-issue counts from ground truth ---
    conflicts = json.loads(CONFLICTS_PATH.read_text(encoding="utf-8"))
    cc = len(conflicts["cross_source_conflicts"])
    wd = len(conflicts["within_source_duplicates"])
    orp = len(conflicts["orphaned_records"])

    print("\n" + "=" * 72)
    print(" FINAL SUMMARY — 5 fragmented sources, seeded & verified")
    print("=" * 72)
    print(f" {'source':<28}{'tech':<12}{'rows':>7}{'distinct names':>16}{'~registry match':>16}")
    print("-" * 72)
    print(f" {'1. institutes (MySQL)':<28}{'MySQL 8':<12}{mysql_rows:>7}{'':>16}{overlap(mysql_names):>16}")
    print(f" {'2. courses (Postgres)':<28}{'PG 16':<12}{courses_rows:>7}{courses_distinct:>16}{overlap(courses_names):>16}")
    print(f" {'3. faculty (Postgres)':<28}{'PG 16':<12}{faculty_rows:>7}{faculty_distinct:>16}{overlap(faculty_names):>16}")
    print(f" {'4. scholarships (Mongo)':<28}{'Mongo 7':<12}{mongo_docs:>7}{len(set(mongo_names)):>16}{overlap(mongo_names):>16}")
    print(f" {'6. internships (CSV)':<28}{'clean':<12}{internships_rows:>7}{internships_distinct:>16}{overlap(internships_names):>16}")
    for f, c in csv_counts.items():
        print(f" {'5. ' + f:<28}{'CSV':<12}{c:>7}{'':>16}{'':>16}")
    print("-" * 72)
    print(f" Planted issues (ground truth in conflicts_seeded.json):")
    print(f"   cross_source_conflicts   : {cc}")
    print(f"   within_source_duplicates : {wd}   (mysql 18, pg_courses 24, mongo 12)")
    print(f"   orphaned_records         : {orp}   (faculty 14, pg_courses 6, legacy-only 12)")
    print("=" * 72)


def main() -> int:
    db_utils.load_env()
    reset_conflicts(CONFLICTS_PATH)  # fresh ground-truth file every run
    ok = _run_all()
    if not ok:
        return 1
    _summary()
    print("\nAll 6 sources seeded. See README.md for how to manage each database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
