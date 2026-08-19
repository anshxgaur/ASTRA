"""
10_PGVECTOR — persist context_text + embedding + entity metadata into the
CONTEXT_DOCUMENT table (lives inside PostgreSQL via the pgvector extension —
see 08_POSTGRESQL/DATABASE_SCHEMA.sql for the CREATE EXTENSION line).

Embeddings are 384-dim (all-MiniLM-L6-v2 via fastembed — see 09_EMBEDDINGS).
The index is HNSW: unlike ivfflat it is not corrupted when built on an empty
table, and it gives better recall at these volumes.
"""
import os

try:
    import psycopg
    from pgvector.psycopg import register_vector
except ImportError:
    psycopg = None

EMBEDDING_DIMS = int(os.getenv("EMBEDDING_DIMENSION", "384"))

CREATE_CONTEXT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS context_document (
    context_id          SERIAL PRIMARY KEY,
    entity_id             TEXT NOT NULL,
    entity_type            TEXT NOT NULL,
    context_type           TEXT,
    context_text           TEXT NOT NULL,
    embedding                VECTOR({EMBEDDING_DIMS}),
    source_database          TEXT,
    source_table              TEXT,
    source_record_id          TEXT,
    confidence                 REAL DEFAULT 1.0,
    created_at                  TIMESTAMPTZ DEFAULT now(),
    updated_at                  TIMESTAMPTZ DEFAULT now(),
    data_version                  TEXT DEFAULT 'v1'
);
"""

BUILD_VECTOR_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_context_embedding
    ON context_document USING hnsw (embedding vector_cosine_ops);
"""


def build_vector_index(conn) -> None:
    """Safe to call any time; HNSW is not corrupted by empty tables."""
    with conn.cursor() as cur:
        cur.execute(BUILD_VECTOR_INDEX_SQL)
    conn.commit()


def get_connection():
    if psycopg is None:
        raise RuntimeError("psycopg/pgvector not installed — pip install -r REQUIREMENTS/REQUIREMENTS.txt")
    url = os.getenv("DATABASE_URL")
    if url:
        conn = psycopg.connect(url)
    else:
        conn = psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5432"),
            dbname=os.getenv("POSTGRES_DB", "aicte_canonical"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )
    register_vector(conn)
    return conn


def insert_context(conn, entity_id: str, entity_type: str, context_type: str,
                    context_text: str, embedding: list[float], **lineage) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO context_document
                (entity_id, entity_type, context_type, context_text, embedding,
                 source_database, source_table, source_record_id)
            VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s)
            """,
            (entity_id, entity_type, context_type, context_text, embedding,
             lineage.get("source_database"), lineage.get("source_table"), lineage.get("source_record_id")),
        )
    conn.commit()


def similarity_search(conn, query_embedding: list[float], top_k: int = 5,
                      entity_type: str | None = None, state: str | None = None,
                      approval_status: bool | None = None) -> list[dict]:
    """
    pgvector cosine search over context_document.

    Filters are applied before ranking:
      entity_type      — only documents of that entity type (institution | course |
                         faculty | scholarship | approval);
      state            — only documents whose entity lives in that state (joined
                         through institution for course/faculty, approval.state for
                         approvals; scholarships have no state and are excluded);
      approval_status  — True = approved / False = not approved, only meaningful
                         for institution-linked documents (institution.approval_status).
    """
    sql = """
        SELECT entity_id, entity_type, context_text,
               source_database, source_table, source_record_id,
               1 - (embedding <=> %s::vector) AS similarity
        FROM context_document
        WHERE (%s::text IS NULL OR entity_type = %s)
          AND (%s::text IS NULL
               OR entity_id IN (SELECT institution_id FROM institution WHERE lower(state) = lower(%s))
               OR entity_id IN (SELECT course_id FROM course c JOIN institution i ON c.institution_id = i.institution_id WHERE lower(i.state) = lower(%s))
               OR entity_id IN (SELECT faculty_id FROM faculty f JOIN institution i ON f.institution_id = i.institution_id WHERE lower(i.state) = lower(%s))
               OR entity_id IN (SELECT approval_id FROM approval WHERE lower(state) = lower(%s))
               OR entity_id IN (SELECT internship_id FROM internship t JOIN institution i ON t.institution_id = i.institution_id WHERE lower(i.state) = lower(%s)))
          AND (%s::boolean IS NULL
               OR entity_id IN (SELECT institution_id FROM institution WHERE approval_status = %s))
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params = (query_embedding, entity_type, entity_type,
              state, state, state, state, state, state,
              approval_status, approval_status,
              query_embedding, top_k)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


if __name__ == "__main__":
    print("This script expects a live Postgres+pgvector connection (.env).")
    print("Run CREATE_CONTEXT_TABLE_SQL against your DB first.")
