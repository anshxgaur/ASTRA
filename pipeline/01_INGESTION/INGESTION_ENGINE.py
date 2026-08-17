"""
01_INGESTION — pull raw records from every source system and stamp them
with lineage metadata before anything is transformed.

Two paths:
  - ingest_all(sample_dir)  — reads the sample CSV/JSON files in DATA/SAMPLE/
                              (kept for the test suite and offline demos).
  - ingest_all_real()       — reads the REAL Phase-1 seeded sources of this
                              project: MySQL institutes, PostgreSQL
                              courses/faculty, MongoDB scholarships and the
                              legacy CSVs under data/legacy/. Credentials come
                              from the project root .env.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

RAW_METADATA_FIELDS = [
    "source_system", "source_database", "source_table",
    "source_record_id", "ingestion_timestamp",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # Internal_Matte/


def _stamp(df: pd.DataFrame, source_system: str, source_database: str, source_table: str) -> pd.DataFrame:
    df = df.copy()
    df["source_system"] = source_system
    df["source_database"] = source_database
    df["source_table"] = source_table
    df["source_record_id"] = df.index.astype(str)
    df["ingestion_timestamp"] = datetime.now(timezone.utc).isoformat()
    return df


def load_from_sample(path: Path, source_system: str, source_database: str, source_table: str) -> pd.DataFrame:
    if path.suffix == ".csv":
        df = pd.read_csv(path)
    elif path.suffix == ".json":
        df = pd.json_normalize(json.loads(path.read_text()))
    else:
        raise ValueError(f"Unsupported sample format: {path.suffix}")
    return _stamp(df, source_system, source_database, source_table)


def ingest_all(sample_dir: Path) -> dict[str, pd.DataFrame]:
    """Sample-data path (deterministic, used by tests)."""
    return {
        "institution_db": load_from_sample(
            sample_dir / "SOURCE_A_INSTITUTION.csv", "mysql", "institution_db", "institution"
        ),
        "course_db": load_from_sample(
            sample_dir / "SOURCE_B_INSTITUTION.csv", "postgres", "course_db", "institution"
        ),
        "faculty_db": load_from_sample(
            sample_dir / "SOURCE_C_INSTITUTION.json", "mongo", "faculty_db", "institution"
        ),
    }


# ---------------------------------------------------------------------------
# Real Phase-1 source connectors
# ---------------------------------------------------------------------------

def _load_mysql_institutes() -> pd.DataFrame:
    import pymysql

    conn = pymysql.connect(
        host=os_env("MYSQL_HOST"), port=int(os_env("MYSQL_PORT")),
        user=os_env("MYSQL_USER"), password=os_env("MYSQL_PASSWORD"),
        database=os_env("MYSQL_DATABASE"),
    )
    try:
        df = pd.read_sql("SELECT * FROM institutes", conn)
    finally:
        conn.close()
    return df


def _load_postgres(table: str, dbname: str) -> pd.DataFrame:
    import psycopg

    with psycopg.connect(
        host=os_env("POSTGRES_HOST"), port=int(os_env("POSTGRES_PORT")),
        user=os_env("POSTGRES_USER"), password=os_env("POSTGRES_PASSWORD"),
        dbname=dbname,
    ) as conn:
        return pd.read_sql(f'SELECT * FROM "{table}"', conn)


def _load_mongo_scholarships() -> pd.DataFrame:
    from pymongo import MongoClient

    client = MongoClient(os_env("MONGO_HOST"), int(os_env("MONGO_PORT")),
                         serverSelectionTimeoutMS=5000)
    try:
        docs = list(client[os_env("MONGO_DB")]["scholarships"].find())
    finally:
        client.close()

    rows = []
    for doc in docs:
        rows.append({
            "scheme_name": doc.get("scheme_name"),
            "administering_body": doc.get("administering_body"),
            "eligibility": json.dumps(doc.get("eligibility"), ensure_ascii=False) if doc.get("eligibility") else "",
            "applicable_states": "|".join(doc.get("applicable_states", []) or []),
            "applicable_institutes": "; ".join(doc.get("applicable_institutes", []) or []),
            "amount": doc.get("amount"),
            "last_updated": str(doc.get("last_updated")),
            "_id": str(doc.get("_id")),
        })
    return pd.DataFrame(rows)


def _load_legacy_csvs() -> dict[str, pd.DataFrame]:
    legacy_dir = PROJECT_ROOT / "data" / "legacy"
    out = {}
    for fname in ["nba_autonomous_status.csv", "closed_institutes.csv", "unapproved_list.csv"]:
        df = pd.read_csv(legacy_dir / fname, encoding="utf-8-sig", dtype=str)
        df = df.drop(columns=[c for c in df.columns if c == "" or c.startswith("Unnamed")],
                     errors="ignore")
        out[fname] = df
    return out


def os_env(key: str) -> str:
    import os
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing env var {key} — is the project root .env loaded?")
    return value


def ingest_all_real() -> dict[str, pd.DataFrame]:
    """Ingest the real Phase-1 seeded sources. Returns {source_name: df}."""
    import os

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")

    sources = {}

    mysql = _load_mysql_institutes()
    mysql["source_record_id"] = mysql["id"].astype(str)
    sources["mysql_institutes"] = _stamp(
        mysql, "mysql", os_env("MYSQL_DATABASE"), "institutes")

    courses = _load_postgres("courses", os_env("COURSES_DB"))
    courses["source_record_id"] = courses["course_id"].astype(str)
    sources["postgres_courses"] = _stamp(
        courses, "postgres", os_env("COURSES_DB"), "courses")

    faculty = _load_postgres("faculty", os_env("FACULTY_DB"))
    faculty["source_record_id"] = faculty["faculty_id"].astype(str)
    sources["postgres_faculty"] = _stamp(
        faculty, "postgres", os_env("FACULTY_DB"), "faculty")

    mongo = _load_mongo_scholarships()
    mongo["source_record_id"] = mongo["_id"]
    mongo = mongo.drop(columns=["_id"])
    sources["mongodb_scholarships"] = _stamp(
        mongo, "mongodb", os_env("MONGO_DB"), "scholarships")

    for fname, df in _load_legacy_csvs().items():
        key = {"nba_autonomous_status.csv": "legacy_nba",
               "closed_institutes.csv": "legacy_closed",
               "unapproved_list.csv": "legacy_unapproved"}[fname]
        sources[key] = _stamp(df, "legacy_csv", "legacy", fname)

    # Internships — clean 6th source (data/internships.csv)
    int_path = PROJECT_ROOT / "data" / "internships.csv"
    if int_path.exists():
        internships = pd.read_csv(int_path, dtype=str)
        sources["internships_csv"] = _stamp(
            internships, "internships", "internships_portal", "internships.csv")

    return sources


if __name__ == "__main__":
    sample_dir = Path(__file__).resolve().parents[1] / "DATA" / "SAMPLE"
    raw = ingest_all(sample_dir)
    for name, df in raw.items():
        print(f"\n=== {name} ({len(df)} rows) ===")
        print(df.head())
