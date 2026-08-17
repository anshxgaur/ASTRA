"""
08_POSTGRESQL — load resolved, classified structured/relational data into
PostgreSQL. ensure_database_and_schema() creates the database, enables
pgvector and runs DATABASE_SCHEMA.sql + the pgvector context table for you.

Every upsert here is paired with an entity_mapping + data_lineage row —
that pairing is not optional decoration, it's what makes every answer
traceable back to its source record (design doc Rule 4).

Reloads are idempotent: all canonical tables are TRUNCATED first, then
rebuilt from the current pipeline output.
"""
import os
from pathlib import Path

import pandas as pd

try:
    import psycopg
except ImportError:
    psycopg = None  # allows dry-run / unit tests without a live DB

SCHEMA_PATH = Path(__file__).resolve().parent / "DATABASE_SCHEMA.sql"
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # pipeline/


def get_connection():
    if psycopg is None:
        raise RuntimeError("psycopg not installed — pip install -r REQUIREMENTS/REQUIREMENTS.txt")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "aicte_canonical"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def _admin_connection():
    if psycopg is None:
        raise RuntimeError("psycopg not installed")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname="postgres",
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        autocommit=True,
    )


def ensure_database_and_schema() -> None:
    """Create POSTGRES_DB if missing, enable vector, run the schema DDL."""
    dbname = os.getenv("POSTGRES_DB", "aicte_canonical")

    with _admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{dbname}"')
                print(f"[08] created database '{dbname}'")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # execute DATABASE_SCHEMA.sql statement by statement
            for stmt in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
                if stmt.strip():
                    cur.execute(stmt)
            # pgvector context table (see 10_PGVECTOR)
            import sys
            sys.path.insert(0, str(PROJECT_ROOT / "10_PGVECTOR"))
            from VECTOR_STORE import CREATE_CONTEXT_TABLE_SQL
            cur.execute(CREATE_CONTEXT_TABLE_SQL)
        conn.commit()
    print(f"[08] schema ready in '{dbname}' (institution, course, faculty, "
          f"scholarship, approval, lineage, context_document)")


# ── lineage helpers ──────────────────────────────────────────────────────

def _record_entity_mapping(conn, master_entity_id: str, entity_type: str, row: pd.Series) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_mapping
                (master_entity_id, entity_type, source_system, source_database,
                 source_table, source_record_id, match_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (master_entity_id, entity_type, row.get("source_system"), row.get("source_database"),
             row.get("source_table"), row.get("source_record_id"), row.get("match_score", 1.0)),
        )


def _record_lineage(conn, canonical_entity_id: str, row: pd.Series) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO data_lineage
                (canonical_entity_id, source_system, source_database, source_table,
                 source_record_id, ingestion_timestamp, validation_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (canonical_entity_id, row.get("source_system"), row.get("source_database"),
             row.get("source_table"), row.get("source_record_id"),
             row.get("ingestion_timestamp"), "loaded"),
        )


# ── entity loaders ───────────────────────────────────────────────────────

def upsert_institutions(conn, institutions_df: pd.DataFrame) -> None:
    for _, row in institutions_df.drop_duplicates("master_entity_id").iterrows():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO institution (institution_id, institution_name, state, district,
                                          city, institute_type, ownership, approval_status,
                                          current_status, is_autonomous, nba_accredited,
                                          accreditation_valid_until, year_established,
                                          aicte_code, last_updated, nirf_rank, naac_grade)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (institution_id) DO UPDATE
                SET institution_name = EXCLUDED.institution_name,
                    state = EXCLUDED.state,
                    approval_status = COALESCE(EXCLUDED.approval_status, institution.approval_status)
                """,
                (row["master_entity_id"], row["institution_name"], row.get("state"),
                 row.get("district"), row.get("city"), row.get("institute_type"),
                 row.get("ownership"), row.get("approval_status"), row.get("current_status"),
                 row.get("is_autonomous"), row.get("nba_accredited"),
                 row.get("accreditation_valid_until"), row.get("year_established"),
                 row.get("aicte_code"), row.get("last_updated"),
                 row.get("nirf_rank"), row.get("naac_grade")),
            )
        _record_entity_mapping(conn, row["master_entity_id"], "institution", row)
        _record_lineage(conn, row["master_entity_id"], row)
    conn.commit()


def upsert_courses(conn, courses_df: pd.DataFrame) -> None:
    for _, row in courses_df.drop_duplicates("course_id").iterrows():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO course (course_id, institution_id, course_name, department,
                                    duration_years, intake_capacity, fee_per_year,
                                    course_status, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (course_id) DO UPDATE
                SET course_name = EXCLUDED.course_name,
                    institution_id = EXCLUDED.institution_id
                """,
                (row["course_id"], row["institution_id"], row["course_name"], row.get("department"),
                 row.get("duration_years"), row.get("intake_capacity"), row.get("fee_per_year"),
                 row.get("course_status"), row.get("last_updated")),
            )
        _record_entity_mapping(conn, row["course_id"], "course", row)
        _record_lineage(conn, row["course_id"], row)
    conn.commit()


def upsert_faculty(conn, faculty_df: pd.DataFrame) -> None:
    for _, row in faculty_df.drop_duplicates("faculty_id").iterrows():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO faculty (faculty_id, institution_id, faculty_name, designation,
                                     qualification, specialization, department,
                                     years_of_experience, date_joined, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (faculty_id) DO UPDATE
                SET faculty_name = EXCLUDED.faculty_name,
                    institution_id = EXCLUDED.institution_id
                """,
                (row["faculty_id"], row["institution_id"], row["faculty_name"], row.get("designation"),
                 row.get("qualification"), row.get("specialization"), row.get("department"),
                 row.get("years_of_experience"), row.get("date_joined"),
                 row.get("last_updated")),
            )
        _record_entity_mapping(conn, row["faculty_id"], "faculty", row)
        _record_lineage(conn, row["faculty_id"], row)
    conn.commit()

    # NOTE: research_interests is contextual — it is NOT inserted here.
    # It goes to pgvector via 09_EMBEDDINGS instead.


def upsert_scholarships(conn, scholarships_df: pd.DataFrame) -> None:
    for _, row in scholarships_df.drop_duplicates("scholarship_id").iterrows():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scholarship (scholarship_id, scheme_name, administering_body,
                                         amount, applicable_states, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (scholarship_id) DO UPDATE
                SET scheme_name = EXCLUDED.scheme_name,
                    administering_body = EXCLUDED.administering_body
                """,
                (row["scholarship_id"], row["scheme_name"], row.get("administering_body"),
                 row.get("amount"), row.get("applicable_states"), row.get("last_updated")),
            )
        _record_entity_mapping(conn, row["scholarship_id"], "scholarship", row)
        _record_lineage(conn, row["scholarship_id"], row)
    conn.commit()

    # NOTE: eligibility is contextual — it goes to pgvector via 09_EMBEDDINGS.


def upsert_internships(conn, internships_df: pd.DataFrame) -> None:
    for _, row in internships_df.drop_duplicates("internship_id").iterrows():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO internship (internship_id, institution_id, domain,
                                        organization_name, duration_weeks, stipend_amount,
                                        mode, is_ppo_linked, program_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (internship_id) DO UPDATE
                SET institution_id = EXCLUDED.institution_id,
                    domain = EXCLUDED.domain
                """,
                (row["internship_id"], row.get("institution_id"), row.get("domain"),
                 row.get("organization_name"), row.get("duration_weeks"),
                 row.get("stipend_amount"), row.get("mode"), row.get("is_ppo_linked"),
                 row.get("program_source")),
            )
        _record_entity_mapping(conn, row["internship_id"], "internship", row)
        _record_lineage(conn, row["internship_id"], row)
    conn.commit()

    # NOTE: description is contextual — it goes to pgvector via 09_EMBEDDINGS.


def upsert_approvals(conn, approvals_df: pd.DataFrame) -> None:
    for _, row in approvals_df.drop_duplicates("approval_id").iterrows():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO approval (approval_id, institution_id, approval_type, nba_status,
                                      valid_until, closure_year, reason, state, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (approval_id) DO UPDATE
                SET approval_type = EXCLUDED.approval_type
                """,
                (row["approval_id"], row.get("institution_id"), row["approval_type"],
                 row.get("nba_status"), row.get("valid_until"), row.get("closure_year"),
                 row.get("reason"), row.get("state"), row.get("last_updated")),
            )
        _record_entity_mapping(conn, row["approval_id"], "approval", row)
        _record_lineage(conn, row["approval_id"], row)
    conn.commit()


# ── orchestration ────────────────────────────────────────────────────────

CANONICAL_TABLES = [
    "institution", "course", "faculty", "scholarship", "approval",
    "student", "internship", "entity_mapping", "data_lineage", "context_document",
]


def load_all(conn, frames: dict[str, pd.DataFrame]) -> None:
    """
    frames: {'institutions': df, 'courses': df, 'faculty': df,
             'scholarships': df, 'approvals': df}
    Each frame must carry its canonical id columns, master_entity_id where
    relevant, plus the source_* lineage columns.
    """
    with conn.cursor() as cur:
        for table in CANONICAL_TABLES:
            cur.execute(f'TRUNCATE "{table}" CASCADE')
    conn.commit()

    if frames.get("institutions") is not None and len(frames["institutions"]):
        upsert_institutions(conn, frames["institutions"])
    if frames.get("courses") is not None and len(frames["courses"]):
        upsert_courses(conn, frames["courses"])
    if frames.get("faculty") is not None and len(frames["faculty"]):
        upsert_faculty(conn, frames["faculty"])
    if frames.get("scholarships") is not None and len(frames["scholarships"]):
        upsert_scholarships(conn, frames["scholarships"])
    if frames.get("approvals") is not None and len(frames["approvals"]):
        upsert_approvals(conn, frames["approvals"])
    if frames.get("internships") is not None and len(frames["internships"]):
        upsert_internships(conn, frames["internships"])
    print("[08] canonical tables loaded (all 6 entities + lineage)")


if __name__ == "__main__":
    print("This script expects a live Postgres connection (.env). "
          "Call ensure_database_and_schema() once, then load_all(conn, frames).")
