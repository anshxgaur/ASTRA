"""Load the phase4_data/ package into ANY PostgreSQL + pgvector instance.

Recreates the canonical schema (institution, course, faculty, scholarship,
approval, entity_mapping, data_lineage, context_document), loads every CSV
from phase4_data/ (parsing the 384-dim embeddings back into real vectors),
and builds the HNSW index — so a Phase-4 engineer can stand up the data on
their own machine with one command, no access to the original databases.

Usage:
    internalenv/Scripts/python.exe load_phase4_data.py --db aicte_phase4
    # connection defaults to POSTGRES_HOST/PORT/USER/PASSWORD from .env;
    # override with --host/--port/--user/--password as needed.
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seed import db_utils  # noqa: E402
db_utils.load_env()

import pandas as pd  # noqa: E402
import psycopg  # noqa: E402
from pgvector.psycopg import register_vector  # noqa: E402

DATA_DIR = PROJECT_ROOT / "phase4_data"
EMBEDDING_DIMS = 384

SCHEMA = {
    "institution": [
        ("institution_id", "TEXT", "PK"), ("institution_name", "TEXT", "NOT NULL"),
        ("state", "TEXT", ""), ("district", "TEXT", ""), ("city", "TEXT", ""),
        ("institute_type", "TEXT", ""), ("ownership", "TEXT", ""),
        ("approval_status", "BOOLEAN", ""), ("current_status", "TEXT", ""),
        ("is_autonomous", "BOOLEAN", ""), ("nba_accredited", "BOOLEAN", ""),
        ("accreditation_valid_until", "TEXT", ""), ("year_established", "INTEGER", ""),
        ("aicte_code", "TEXT", ""), ("last_updated", "TEXT", ""),
        ("nirf_rank", "INTEGER", ""), ("naac_grade", "TEXT", ""),
    ],
    "course": [
        ("course_id", "TEXT", "PK"), ("institution_id", "TEXT", ""),
        ("course_name", "TEXT", "NOT NULL"), ("department", "TEXT", ""),
        ("duration_years", "INTEGER", ""), ("intake_capacity", "INTEGER", ""),
        ("fee_per_year", "NUMERIC(12,2)", ""), ("course_status", "TEXT", ""),
        ("last_updated", "TEXT", ""),
    ],
    "faculty": [
        ("faculty_id", "TEXT", "PK"), ("institution_id", "TEXT", ""),
        ("faculty_name", "TEXT", "NOT NULL"), ("designation", "TEXT", ""),
        ("qualification", "TEXT", ""), ("specialization", "TEXT", ""),
        ("department", "TEXT", ""), ("years_of_experience", "INTEGER", ""),
        ("date_joined", "TEXT", ""), ("last_updated", "TEXT", ""),
    ],
    "scholarship": [
        ("scholarship_id", "TEXT", "PK"), ("scheme_name", "TEXT", "NOT NULL"),
        ("administering_body", "TEXT", ""), ("amount", "TEXT", ""),
        ("applicable_states", "TEXT", ""), ("last_updated", "TEXT", ""),
    ],
    "approval": [
        ("approval_id", "TEXT", "PK"), ("institution_id", "TEXT", ""),
        ("approval_type", "TEXT", "NOT NULL"), ("nba_status", "TEXT", ""),
        ("valid_until", "TEXT", ""), ("closure_year", "TEXT", ""),
        ("reason", "TEXT", ""), ("state", "TEXT", ""), ("last_updated", "TEXT", ""),
    ],
    "internship": [
        ("internship_id", "TEXT", "PK"), ("institution_id", "TEXT", ""),
        ("domain", "TEXT", ""), ("organization_name", "TEXT", ""),
        ("duration_weeks", "INTEGER", ""), ("stipend_amount", "NUMERIC(12,2)", ""),
        ("mode", "TEXT", ""), ("is_ppo_linked", "BOOLEAN", ""),
        ("program_source", "TEXT", ""),
    ],
    "entity_mapping": [
        ("id", "INTEGER", "PK"), ("master_entity_id", "TEXT", "NOT NULL"),
        ("entity_type", "TEXT", "NOT NULL"), ("source_system", "TEXT", "NOT NULL"),
        ("source_database", "TEXT", "NOT NULL"), ("source_table", "TEXT", ""),
        ("source_record_id", "TEXT", "NOT NULL"), ("match_score", "REAL", ""),
        ("created_at", "TIMESTAMPTZ", ""),
    ],
    "data_lineage": [
        ("id", "INTEGER", "PK"), ("canonical_entity_id", "TEXT", "NOT NULL"),
        ("source_system", "TEXT", "NOT NULL"), ("source_database", "TEXT", "NOT NULL"),
        ("source_table", "TEXT", ""), ("source_record_id", "TEXT", ""),
        ("transformation_version", "TEXT", ""), ("validation_status", "TEXT", ""),
        ("ingestion_timestamp", "TIMESTAMPTZ", ""),
    ],
    "context_document": [
        ("context_id", "INTEGER", "PK"), ("entity_id", "TEXT", "NOT NULL"),
        ("entity_type", "TEXT", "NOT NULL"), ("context_type", "TEXT", ""),
        ("context_text", "TEXT", "NOT NULL"),
        ("embedding", f"VECTOR({EMBEDDING_DIMS})", ""),
        ("source_database", "TEXT", ""), ("source_table", "TEXT", ""),
        ("source_record_id", "TEXT", ""), ("confidence", "REAL", ""),
        ("created_at", "TIMESTAMPTZ", ""), ("data_version", "TEXT", ""),
    ],
}

# column name -> value converter applied after '' -> None
CONVERTERS: dict[str, callable] = {
    "INTEGER": lambda v: int(float(v)),
    "REAL": float,
    "NUMERIC(12,2)": float,
    "BOOLEAN": lambda v: {"True": True, "False": False, "true": True, "false": False}[str(v)],
    "TIMESTAMPTZ": str,
    f"VECTOR({EMBEDDING_DIMS})": lambda v: ast.literal_eval(v),
}

_NULLISH = {"", "nan", "nat", "none", "n/a", "null"}


def _cell(v):
    """None out every NULL-ish representation pandas may produce."""
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # numpy/float NaN
        return None
    s = str(v).strip()
    return None if s.lower() in _NULLISH else s


def _cfg(args) -> dict:
    return {
        "host": args.host or os.environ.get("POSTGRES_HOST", "localhost"),
        "port": args.port or int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": args.user or os.environ.get("POSTGRES_USER", "postgres"),
        "password": args.password or os.environ.get("POSTGRES_PASSWORD", ""),
    }


def ensure_database(cfg: dict, dbname: str) -> None:
    admin = dict(cfg, dbname="postgres")
    with psycopg.connect(**admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{dbname}"')
                print(f"[load] created database '{dbname}'")
            else:
                print(f"[load] database '{dbname}' already exists")


def create_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for table, cols in SCHEMA.items():
            defs = []
            for name, ctype, extra in cols:
                d = f'"{name}" {ctype}'
                if extra == "PK":
                    d += " PRIMARY KEY"
                elif extra == "NOT NULL":
                    d += " NOT NULL"
                defs.append(d)
            cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
            cur.execute(f'CREATE TABLE "{table}" ({", ".join(defs)})')
        cur.execute("CREATE INDEX IF NOT EXISTS idx_institution_state ON institution(state)")
    conn.commit()
    print("[load] schema created (9 tables, vector extension enabled)")


def load_table(conn, table: str, columns: list[tuple]) -> int:
    path = DATA_DIR / f"{table}.csv"
    if not path.exists():
        print(f"[load] skip {table}: {path.name} not found")
        return 0
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    records = []
    for _, row in df.iterrows():
        vals = []
        for name, ctype, _extra in columns:
            v = _cell(row.get(name))  # clean raw cell; never round-trip through pandas dtype inference
            if v is None:
                vals.append(None)
                continue
            conv = CONVERTERS.get(ctype)
            vals.append(conv(v) if conv else str(v))
        records.append(tuple(vals))

    col_names = ", ".join(f'"{c[0]}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        cur.executemany(
            f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})', records)
    conn.commit()
    print(f"[load] {table:<16} {len(records):>6} rows")
    return len(records)


def build_index(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_context_embedding
            ON context_document USING hnsw (embedding vector_cosine_ops)
        """)
    conn.commit()
    print("[load] HNSW index built on context_document.embedding")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load phase4_data/ into a PostgreSQL+pgvector database")
    parser.add_argument("--db", default="aicte_phase4", help="target database name")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    cfg = _cfg(args)
    ensure_database(cfg, args.db)

    conn = psycopg.connect(**dict(cfg, dbname=args.db))
    try:
        create_schema(conn)            # enables the vector extension first
        register_vector(conn)
        total = 0
        for table, columns in SCHEMA.items():
            total += load_table(conn, table, columns)
        build_index(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), vector_dims(embedding) FROM context_document GROUP BY 2")
            row = cur.fetchone()
        print("=" * 56)
        print(f" LOAD COMPLETE — '{args.db}' is ready")
        print(f"  total rows: {total} · context_document: {row[0]} × {row[1]}-dim vectors")
        print("=" * 56)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
