"""
TEXT_TO_SQL — let the LLM answer structured questions by writing SQL.

This is the "LLM decides what to retrieve" path of Phase 4 (design doc
Rule 2: the LLM never becomes the database — it writes a query, we execute
it against the canonical Postgres store, and the *results* are what the LLM
reasons over).

Guardrails (a generated query is executable input, so it is treated as
untrusted):
  1. SELECT-only — any INSERT/UPDATE/DELETE/DDL keyword rejects the query.
  2. Table whitelist — only the canonical aicte_canonical tables.
  3. Statement timeout — a runaway/cartesian query dies in 8s, not forever.
  4. LIMIT enforcement — bare row queries are capped at 25 rows.
  5. One retry — if the SQL fails to execute, the error is sent back to the
     LLM once to fix it; a second failure means "no structured result".
"""
from __future__ import annotations

import re

import GROQ_CLIENT

# ── schema description handed to the LLM (mirrors DATABASE_SCHEMA.sql) ────

SCHEMA_DESCRIPTION = """
You are writing PostgreSQL queries against a canonical AICTE store (database aicte_canonical).
Tables and columns:

institution(institution_id TEXT PK, institution_name TEXT, state TEXT, district TEXT,
            city TEXT, institute_type TEXT, ownership TEXT, approval_status BOOLEAN,
            current_status TEXT, is_autonomous BOOLEAN, nba_accredited BOOLEAN,
            accreditation_valid_until TEXT, year_established INT,
            aicte_code TEXT, last_updated TEXT, nirf_rank INT, naac_grade TEXT)
course(course_id TEXT PK, institution_id TEXT -> institution.institution_id,
       course_name TEXT, department TEXT, duration_years INT,
       intake_capacity INT, fee_per_year NUMERIC, course_status TEXT, last_updated TEXT)
faculty(faculty_id TEXT PK, institution_id TEXT -> institution.institution_id,
        faculty_name TEXT, designation TEXT, qualification TEXT, specialization TEXT,
        department TEXT, years_of_experience INT, date_joined TEXT, last_updated TEXT)
scholarship(scholarship_id TEXT PK, scheme_name TEXT, administering_body TEXT,
            amount TEXT, applicable_states TEXT, last_updated TEXT)
approval(approval_id TEXT PK, institution_id TEXT -> institution.institution_id,
         approval_type TEXT, nba_status TEXT, valid_until TEXT, closure_year TEXT,
         reason TEXT, state TEXT, last_updated TEXT)
internship(internship_id TEXT PK, institution_id TEXT -> institution.institution_id,
           domain TEXT, organization_name TEXT, duration_weeks INT,
           stipend_amount NUMERIC, mode TEXT, is_ppo_linked BOOLEAN, program_source TEXT)
entity_mapping(id INT PK, master_entity_id TEXT, entity_type TEXT, source_system TEXT,
               source_database TEXT, source_table TEXT, source_record_id TEXT, match_score REAL)
data_lineage(id INT PK, canonical_entity_id TEXT, source_system TEXT, source_database TEXT,
             source_table TEXT, source_record_id TEXT, transformation_version TEXT,
             validation_status TEXT, ingestion_timestamp TIMESTAMPTZ)

Rules:
- SELECT only. No INSERT / UPDATE / DELETE / DROP / ALTER / CREATE / TRUNCATE.
- approval_status is BOOLEAN: TRUE = approved, FALSE = not approved.
- current_status / course_status hold lowercase values: 'active' | 'closed' | 'unapproved'.
- nba_accredited / is_autonomous / is_ppo_linked are BOOLEAN.
- state / district / institute_type hold full names, e.g. 'Uttar Pradesh', 'Maharashtra'.
- Join course/faculty/internship to institution via institution_id to filter by state/name.
- Use ILIKE with % wildcards for fuzzy name matching.
- Return plain SQL. No markdown, no explanation, no trailing semicolon.
""".strip()

ALLOWED_TABLES = {
    "institution", "course", "faculty", "scholarship", "approval", "internship",
    "entity_mapping", "data_lineage", "context_document",
}

BANNED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|vacuum|merge|replace|comment|do|call|listen|notify)\b",
    re.IGNORECASE,
)
AGGREGATES = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)


def _clean_sql(raw: str) -> str:
    """Strip markdown fences / code blocks / trailing semicolon."""
    sql = raw.strip()
    sql = re.sub(r"^```(?:sql|postgres)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    sql = sql.strip().rstrip(";").strip()
    return sql


def _validate(sql: str) -> tuple[bool, str]:
    """Return (ok, reason). Rejects anything that is not a safe SELECT."""
    lowered = sql.lower()
    if not lowered.startswith("select"):
        return False, "query must be a SELECT"
    if ";" in lowered:
        return False, "multiple statements are not allowed"
    if "--" in lowered or "/*" in lowered:
        return False, "comment tokens are not allowed"
    if BANNED_KEYWORDS.search(lowered):
        return False, f"banned keyword in query: {BANNED_KEYWORDS.search(lowered).group(0)}"
    # every table referenced (FROM/JOIN) must be canonical
    referenced = set(re.findall(r"\b(?:from|join)\s+([a-z_]+)", lowered))
    unknown = referenced - ALLOWED_TABLES
    if unknown:
        return False, f"non-canonical table(s) referenced: {sorted(unknown)}"
    # cap bare row queries (aggregates answer in one row; no cap needed)
    if "limit" not in lowered and not AGGREGATES.search(lowered):
        return False, "add a LIMIT clause"
    return True, ""


def _enforce_limit(sql: str, cap: int = 25) -> str:
    """Make sure row-returning queries can't flood the response."""
    if re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
        return sql
    if AGGREGATES.search(sql.lower()):
        return sql
    return sql.rstrip() + f" LIMIT {cap}"


def _rows_to_dicts(cur) -> list[dict]:
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _execute(sql: str, timeout_s: int = 8) -> list[dict]:
    """Execute on a dedicated connection with a hard statement timeout."""
    import os
    import psycopg

    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "aicte_canonical"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        options=f"-c statement_timeout={timeout_s * 1000}",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return _rows_to_dicts(cur)
    finally:
        conn.close()


def generate_sql(question: str) -> str | None:
    """Ask Groq to translate the question into a safe SELECT. Returns cleaned SQL."""
    reply = GROQ_CLIENT.chat(
        [
            {"role": "system", "content": SCHEMA_DESCRIPTION},
            {"role": "user", "content": f"Question: {question}\nWrite the SQL query."},
        ],
        temperature=0.0,
        max_tokens=512,
    )
    return _clean_sql(reply) if reply else None


def _retry_sql(question: str, failed_sql: str, error: str) -> str | None:
    reply = GROQ_CLIENT.chat(
        [
            {"role": "system", "content": SCHEMA_DESCRIPTION},
            {"role": "user", "content":
                f"Question: {question}\n\nYour previous SQL failed:\n{failed_sql}\n\n"
                f"Error: {error}\n\nFix the SQL. Return only the corrected SQL."},
        ],
        temperature=0.0,
        max_tokens=512,
    )
    return _clean_sql(reply) if reply else None


def text_to_sql(question: str) -> tuple[list[dict] | None, str | None]:
    """
    Full pipeline: generate SQL → validate → execute.
    Returns (rows, sql) — the result rows AND the exact SQL that ran
    (needed by the API for the "how this was answered" transparency panel).
    Returns (None, None) when no structured answer could be produced safely.
    """
    def attempt(sql: str) -> list[dict] | None:
        sql = _enforce_limit(sql)      # cap bare row queries first, then validate
        ok, reason = _validate(sql)
        if not ok:
            print(f"[text-to-sql] rejected: {reason}")
            return None
        try:
            return _execute(sql)
        except Exception as exc:  # noqa: BLE001 — one retry, then give up gracefully
            error = f"{type(exc).__name__}: {exc}"
            print(f"[text-to-sql] attempt failed ({error}); asking LLM to fix it")
            fixed = _retry_sql(question, sql, error)
            if not fixed:
                return None
            fixed = _enforce_limit(fixed)
            ok, reason = _validate(fixed)
            if not ok:
                print(f"[text-to-sql] rejected after retry: {reason}")
                return None
            try:
                return _execute(fixed)
            except Exception as exc2:  # noqa: BLE001
                print(f"[text-to-sql] retry also failed: {type(exc2).__name__}: {exc2}")
                return None

    sql = generate_sql(question)
    if not sql:
        return None, None
    rows = attempt(sql)
    if rows is None:
        return None, None
    return rows, sql
