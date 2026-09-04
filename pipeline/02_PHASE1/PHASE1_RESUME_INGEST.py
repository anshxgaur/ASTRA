"""
02_PHASE1 — PHASE1_RESUME_INGEST.py

Reads the raw resume data that seed/phase1_resume_seed.py stored in the
phase1_resumes Postgres database, chunks the text, generates 384-dim
embeddings via fastembed, and stores everything in the aicte_canonical
database (resume + resume_chunk tables with pgvector).

This is the bridge between Phase 1 (raw data lake) and Phase 3
(vector store + LLM retrieval).

Usage:
    python pipeline/02_PHASE1/PHASE1_RESUME_INGEST.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent.parent  # pipeline/
PROJECT_ROOT = PIPELINE_ROOT.parent  # project root

# Load env
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PIPELINE_ROOT / ".env")

sys.path.insert(0, str(PIPELINE_ROOT / "09_EMBEDDINGS"))
sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))

import psycopg
from pgvector.psycopg import register_vector


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _phase1_conn():
    """Connect to the phase1_resumes raw data database."""
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        dbname=os.getenv("PHASE1_DB", "phase1_resumes"),
    )


def _canonical_conn():
    """Connect to the aicte_canonical database (with pgvector)."""
    url = os.getenv("DATABASE_URL")
    if url:
        conn = psycopg.connect(url)
    else:
        conn = psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5433")),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            dbname=os.getenv("POSTGRES_DB", "aicte_canonical"),
        )
    register_vector(conn)
    return conn


# ---------------------------------------------------------------------------
# Schema creation in aicte_canonical
# ---------------------------------------------------------------------------

RESUME_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resume (
    resume_id       TEXT PRIMARY KEY,
    candidate_name  TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    linkedin_url    TEXT,
    location        TEXT,
    summary         TEXT,
    skills          TEXT,
    experience_years INTEGER,
    education       TEXT,
    certifications  TEXT,
    languages       TEXT,
    total_sections  INTEGER DEFAULT 0,
    file_path       TEXT,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resume_chunk (
    chunk_id        TEXT PRIMARY KEY,
    resume_id       TEXT NOT NULL REFERENCES resume(resume_id),
    chunk_index     INTEGER NOT NULL,
    section         TEXT,
    heading         TEXT,
    content         TEXT NOT NULL,
    char_count      INTEGER,
    embedding       VECTOR(384),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_resume_chunk_embedding
    ON resume_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_resume_chunk_resume
    ON resume_chunk(resume_id);
"""


def _ensure_schema(conn):
    """Create resume / resume_chunk tables if they don't exist."""
    for stmt in RESUME_SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    """Sliding-window chunking with overlap."""
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + size]
        if len(chunk.strip()) > 50:
            chunks.append(chunk)
        start += size - overlap
    return chunks


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print(" Phase 1 → Phase 3: Resume Ingestion Pipeline")
    print(" phase1_resumes (raw) → aicte_canonical (embeddings + pgvector)")
    print("=" * 60)

    # 1. Read raw resumes from phase1_resumes
    print("\n[1/5] Reading raw resumes from phase1_resumes ...")
    p1_conn = _phase1_conn()
    with p1_conn.cursor() as cur:
        cur.execute(
            "SELECT resume_id, file_name, file_path, candidate_name, email, "
            "phone, linkedin_url, location, skills, experience_years, "
            "education, summary, full_text "
            "FROM raw_resumes ORDER BY resume_id"
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    p1_conn.close()
    print(f"  Found {len(rows)} raw resumes")

    if not rows:
        print("  Nothing to ingest. Run seed/phase1_resume_seed.py first.")
        return 1

    # 2. Ensure schema in aicte_canonical
    print("\n[2/5] Ensuring resume schema in aicte_canonical ...")
    canon_conn = _canonical_conn()
    _ensure_schema(canon_conn)

    # Check what's already ingested (resume ids)
    with canon_conn.cursor() as cur:
        cur.execute("SELECT resume_id FROM resume")
        existing = {r[0] for r in cur.fetchall()}
    remaining = [r for r in rows if r["resume_id"] not in existing]
    print(f"  Already ingested: {len(existing)}, remaining: {len(remaining)}")

    if not remaining:
        print("  All resumes already ingested. Nothing to do.")
        canon_conn.close()
        return 0

    # 3. Load embedding model
    print("\n[3/5] Loading embedding model (all-MiniLM-L6-v2) ...")
    t0 = time.time()
    from EMBEDDING_GENERATOR import embed_texts
    # Warm up the model
    _ = embed_texts(["warmup"])
    print(f"  Model loaded in {time.time() - t0:.1f}s")

    # 4. Process in batches
    print(f"\n[4/5] Embedding + inserting {len(remaining)} resumes ...")
    BATCH_SIZE = 50
    total_ok = 0
    total_fail = 0
    t_start = time.time()

    for batch_start in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[batch_start:batch_start + BATCH_SIZE]

        resume_rows = []
        chunk_rows = []

        for r in batch:
            resume_id = r["resume_id"]
            full_text = r["full_text"] or ""
            chunks = _chunk_text(full_text)
            if not chunks:
                chunks = [full_text[:1200]] if full_text else ["(empty)"]

            # Embed all chunks for this resume
            try:
                embeddings = embed_texts(chunks)
            except Exception as e:
                print(f"  SKIP {r['file_name']}: embed error: {e}")
                total_fail += 1
                continue

            resume_rows.append((
                resume_id,
                r["candidate_name"] or r["file_name"].replace(".pdf", ""),
                r["email"],
                r["phone"],
                r["linkedin_url"],
                r["location"],
                r["summary"],
                r["skills"],
                r["experience_years"],
                r["education"],
                "",  # certifications
                "",  # languages
                len(chunks),
                r["file_path"],
            ))

            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = f"{resume_id}_chunk_{i:04d}"
                chunk_rows.append((
                    chunk_id, resume_id, i, "Resume",
                    chunk, len(chunk), emb.tolist(),
                ))

        # Insert batch
        try:
            with canon_conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO resume
                        (resume_id, candidate_name, email, phone, linkedin_url,
                         location, summary, skills, experience_years, education,
                         certifications, languages, total_sections, file_path)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (resume_id) DO NOTHING""",
                    resume_rows,
                )
                cur.executemany(
                    """INSERT INTO resume_chunk
                        (chunk_id, resume_id, chunk_index, section,
                         content, char_count, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::vector)
                       ON CONFLICT (chunk_id) DO NOTHING""",
                    chunk_rows,
                )
            canon_conn.commit()
            total_ok += len(resume_rows)
        except Exception as e:
            canon_conn.rollback()
            total_fail += len(resume_rows)
            print(f"  Batch error: {e}")

        done = batch_start + len(batch)
        if done % 100 == 0 or done >= len(remaining):
            elapsed = time.time() - t_start
            print(f"  [{done}/{len(remaining)}] {total_ok} ok, {total_fail} fail ({elapsed:.1f}s)")

    # 5. Final stats
    print(f"\n[5/5] Final stats:")
    with canon_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM resume")
        resume_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM resume_chunk")
        chunk_count = cur.fetchone()[0]
        cur.execute("SELECT vector_dims(embedding) FROM resume_chunk LIMIT 1")
        dims_row = cur.fetchone()
        dims = dims_row[0] if dims_row else "N/A"
    canon_conn.close()

    elapsed = time.time() - t_start
    print("=" * 60)
    print(f" Phase 1 → Phase 3 Ingestion Complete")
    print(f"   Resumes ingested : {total_ok}")
    print(f"   Failed           : {total_fail}")
    print(f"   Total in DB      : {resume_count} resumes, {chunk_count} chunks")
    print(f"   Embedding dims   : {dims}")
    print(f"   Time             : {elapsed:.1f}s")
    print(f"   Target DB        : aicte_canonical @ {os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
