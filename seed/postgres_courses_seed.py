"""Source 2/5 — PostgreSQL (courses_db): Courses.

college_name is free text, NOT a foreign key to MySQL — different teams,
different spellings, no shared master ID.
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psycopg

from seed import db_utils
from seed.conflicts_log import add as log_conflict
from seed.name_noise_utils import noisy_variant

db_utils.load_env()

NOW = datetime(2026, 8, 16, 12, 0, 0)
CONFLICTS_PATH = PROJECT_ROOT / "conflicts_seeded.json"
DUPLICATE_COUNT = 24    # within-source duplicates to plant (was 12)
ORPHAN_COURSES = 6      # courses for institutes that don't exist in MySQL (was 3)
COVERAGE = 0.92         # share of institutes offering courses
COURSES_PER = (2, 4)    # courses per covered institute

CREATE_SQL = """
CREATE TABLE courses (
  course_id SERIAL PRIMARY KEY,
  college_name VARCHAR(255) NOT NULL,
  course_name VARCHAR(120) NOT NULL,
  department VARCHAR(80),
  duration_years INT,
  intake_capacity INT,
  fee_per_year NUMERIC(12,2),
  course_status VARCHAR(20),
  last_synced TIMESTAMP
)
"""

# Traditional + emerging-tech catalog (AI/ML, Data Science, IoT, Cybersecurity,
# Robotics ...) so semantic-search demo queries find real matching content.
COURSE_CATALOG = [
    ("B.Tech Computer Science and Engineering", "CSE", 4),
    ("B.Tech Electronics and Communication Engineering", "ECE", 4),
    ("B.Tech Mechanical Engineering", "MECH", 4),
    ("B.Tech Civil Engineering", "CIVIL", 4),
    ("B.Tech Information Technology", "IT", 4),
    ("B.Tech Artificial Intelligence and Data Science", "AIDS", 4),
    ("B.Tech Artificial Intelligence and Machine Learning", "AIML", 4),
    ("B.Tech Data Science", "DS", 4),
    ("B.Tech Internet of Things", "IOT", 4),
    ("B.Tech Cybersecurity and Digital Forensics", "CYS", 4),
    ("B.Tech Robotics and Automation", "ROB", 4),
    ("B.Tech Computer Science and Business Systems", "CSBS", 4),
    ("B.Tech Electrical and Electronics Engineering", "EEE", 4),
    ("B.Tech Chemical Engineering", "CHEM", 4),
    ("B.Tech Biotechnology", "BT", 4),
    ("B.Tech Aerospace Engineering", "AE", 4),
    ("B.Tech Automobile Engineering", "AUTO", 4),
    ("Diploma in Computer Engineering", "DIP-CSE", 3),
    ("Diploma in Mechanical Engineering", "DIP-MECH", 3),
    ("Diploma in Electrical Engineering", "DIP-EEE", 3),
    ("Diploma in Civil Engineering", "DIP-CIVIL", 3),
    ("Diploma in Artificial Intelligence", "DIP-AI", 3),
    ("M.Tech Computer Science", "PG-CSE", 2),
    ("M.Tech Artificial Intelligence", "PG-AI", 2),
    ("M.Tech Data Science", "PG-DS", 2),
    ("M.Tech VLSI Design", "PG-ECE", 2),
    ("M.Tech Power Systems", "PG-EEE", 2),
    ("MBA", "MBA", 2),
    ("MBA Business Analytics", "MBA-BA", 2),
    ("MCA", "MCA", 3),
    ("B.Pharm", "PHARM", 4),
    ("B.Arch", "ARCH", 5),
]

# College names that exist ONLY in this source (legacy/orphaned records)
ORPHAN_COLLEGES = [
    "Saraswati Vidya Niketan College of Engineering, Patna",
    "Bihar Institute of Engineering, Muzaffarpur",
    "Dakshina Kannada Polytechnic, Mangaluru",
    "Shivam Institute of Technology, Gorakhpur",
    "Utkarsh College of Engineering, Bareilly",
    "Navjeewan Polytechnic, Darbhanga",
]


def _connect():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["COURSES_DB"],
        autocommit=True,
    )


def _build_rows(institutes, plan, rng):
    """~1,400 rows over ~92% of institutes (coverage asymmetry), ~20% noisy names."""
    all_names = [i["name"] for i in institutes]
    excluded = set(rng.sample(all_names, int(len(all_names) * (1 - COVERAGE))))
    for name in plan["rejected_with_courses"]:  # rejected institutes MUST have courses (conflict)
        excluded.discard(name)
    covered = [n for n in all_names if n not in excluded]

    rows = []
    for name in covered:
        n_courses = rng.randint(*COURSES_PER)
        chosen = rng.sample(COURSE_CATALOG, min(n_courses, len(COURSE_CATALOG)))
        for cname, dept, dur in chosen:
            college = name
            if rng.random() < 0.20:
                college = noisy_variant(name, rng)
            synced = NOW - timedelta(days=rng.randint(400, 1900)) if rng.random() < 0.40 else NOW - timedelta(days=rng.randint(0, 120))
            rows.append({
                "college_name": college,
                "course_name": cname,
                "department": dept,
                "duration_years": dur,
                "intake_capacity": rng.randint(30, 180),
                "fee_per_year": rng.randint(25_000, 250_000),
                "course_status": "closed" if rng.random() < 0.10 else "active",
                "last_synced": synced,
            })
    return rows


def _plant_duplicates(cur, rows, rng) -> list[dict]:
    """Duplicate 24 existing rows: 12 exact, 12 near (fee/sync drift). Log each."""
    planted = []
    chosen = rng.sample(range(len(rows)), DUPLICATE_COUNT)
    for k, idx in enumerate(chosen):
        src = dict(rows[idx])
        dup_id = f"pg_courses_dup_{k + 1:02d}"
        if k % 2 == 0:
            row = src  # exact duplicate
            desc = "Exact duplicate course row"
        else:
            row = dict(src)
            row["fee_per_year"] = int(src["fee_per_year"] * rng.uniform(0.85, 1.25))
            row["last_synced"] = src["last_synced"] + timedelta(days=rng.randint(1, 5))
            desc = "Near-duplicate course row (fee and last_synced drift)"
        _insert(cur, row)
        log_conflict(CONFLICTS_PATH, {
            "type": "within_source_duplicate", "source": "postgres:courses_db", "id": dup_id,
            "description": desc,
            "institutes": [src["college_name"]],
            "detail": {"course_name": src["course_name"], "college_name": src["college_name"]},
        })
        planted.append(dup_id)
    return planted


def _insert(cur, row) -> None:
    cur.execute(
        "INSERT INTO courses (college_name, course_name, department, duration_years, "
        "intake_capacity, fee_per_year, course_status, last_synced) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (row["college_name"], row["course_name"], row["department"], row["duration_years"],
         row["intake_capacity"], row["fee_per_year"], row["course_status"], row["last_synced"]),
    )


def main() -> None:
    rng = random.Random(20260816 + 2)
    registry = json.loads((PROJECT_ROOT / "institute_registry.json").read_text(encoding="utf-8"))
    institutes = registry["institutes"]
    plan = registry["conflict_plan"]

    if not db_utils.wait_postgres():
        print("[postgres_courses] ERROR: could not reach PostgreSQL. Is it up? (docker compose up -d)")
        sys.exit(1)

    conn = _connect()
    try:
        conn.execute("DROP TABLE IF EXISTS courses")
        conn.execute(CREATE_SQL)

        rows = _build_rows(institutes, plan, rng)
        for row in rows:
            _insert(conn, row)

        planted = _plant_duplicates(conn, rows, rng)

        # orphaned rows: courses at colleges that don't exist anywhere else
        for k, college in enumerate(ORPHAN_COLLEGES):
            cname, dept, dur = COURSE_CATALOG[rng.randrange(len(COURSE_CATALOG))]
            _insert(conn, {
                "college_name": college, "course_name": cname, "department": dept,
                "duration_years": dur, "intake_capacity": rng.randint(30, 120),
                "fee_per_year": rng.randint(25_000, 150_000),
                "course_status": "active", "last_synced": NOW - timedelta(days=rng.randint(400, 1900)),
            })
            log_conflict(CONFLICTS_PATH, {
                "type": "orphaned_record", "source": "postgres:courses_db", "id": f"pg_courses_orphan_{k + 1:02d}",
                "description": "Course row references a college that does not exist in the MySQL institutes table",
                "institutes": [college],
            })

        # cross-source conflicts: rejected institutes that still offer courses
        for k, name in enumerate(plan["rejected_with_courses"]):
            cname, dept, dur = COURSE_CATALOG[rng.randrange(len(COURSE_CATALOG))]
            _insert(conn, {
                "college_name": name, "course_name": cname, "department": dept,
                "duration_years": dur, "intake_capacity": rng.randint(30, 180),
                "fee_per_year": rng.randint(25_000, 250_000),
                "course_status": "active", "last_synced": NOW - timedelta(days=rng.randint(0, 120)),
            })
            log_conflict(CONFLICTS_PATH, {
                "type": "cross_source_conflict", "source": "postgres:courses_db", "id": f"cs_rejected_courses_{k + 1:02d}",
                "description": "Institute is Rejected in MySQL but still offers courses in the courses DB",
                "institutes": [name],
                "sources": ["mysql", "postgres:courses_db"],
                "fields_involved": {"mysql.approval_status": "Rejected", "postgres.courses.college_name": name},
            })

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM courses")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT college_name) FROM courses")
            distinct = cur.fetchone()[0]
            cur.execute("SELECT course_status, COUNT(*) FROM courses GROUP BY 1 ORDER BY 1")
            by_status = dict(cur.fetchall())

        print("=" * 60)
        print(f"[postgres_courses] source: courses_db.courses ({os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']})")
        print(f"[postgres_courses] course rows seeded   : {len(rows)}")
        print(f"[postgres_courses] within-source dups   : {len(planted)}")
        print(f"[postgres_courses] orphaned rows        : {len(ORPHAN_COLLEGES)}")
        print(f"[postgres_courses] cross-source conflicts logged: {len(plan['rejected_with_courses'])}")
        print(f"[postgres_courses] total rows in table  : {total}  (distinct colleges: {distinct})")
        print(f"[postgres_courses] by status            : {by_status}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
