"""Phase 1/2 — Extract resume PDFs into the phase1_resumes raw store.

Reads every .pdf in resumes_1000/, extracts full text + metadata (name,
email, phone, skills, experience), and inserts into the phase1_resumes
Postgres database.  This is the "raw data lake" step — embeddings and
pgvector happen later in the pipeline.

Usage:
    python -m seed.phase1_resume_seed
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seed import db_utils

# ---------------------------------------------------------------------------
# PDF text extraction (pypdf — already in requirements.txt via fast_ingest)
# ---------------------------------------------------------------------------

def _extract_text(pdf_path: str) -> str:
    """Return the full text of a PDF, or empty string on failure."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        parts = []
        for page in reader.pages:
            txt = page.extract_text()
            if txt:
                parts.append(txt)
        return "\n".join(parts)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Metadata extraction (same logic as fast_ingest.py / RESUME_RAG.py)
# ---------------------------------------------------------------------------

def _extract_metadata(text: str) -> dict:
    meta = {
        "candidate_name": "",
        "email": "",
        "phone": "",
        "linkedin_url": "",
        "location": "",
        "skills": "",
        "experience_years": 0,
        "education": "",
        "summary": "",
    }
    # email
    m = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    if m:
        meta["email"] = m.group()
    # phone
    m = re.search(r"[\+]?[\d\-\(\)]{10,15}", text)
    if m:
        meta["phone"] = m.group()
    # linkedin
    m = re.search(r"linkedin\.com/in/[a-zA-Z0-9_-]+", text)
    if m:
        meta["linkedin_url"] = "https://" + m.group()
    # experience
    m = re.search(
        r"(\d+)[\+]?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)",
        text, re.IGNORECASE,
    )
    if m:
        meta["experience_years"] = int(m.group(1))
    # candidate name — first short Title-Case line
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:8]:
        words = line.split()
        if 2 <= len(words) <= 6 and all(
            w[0].isupper() for w in words if len(w) > 1
        ):
            if not any(c in line.lower() for c in ["@", "http", "phone", "email"]):
                meta["candidate_name"] = line[:100]
                break
    # skills
    m = re.search(
        r"(?:skills?|technical skills?|technologies?)[:\s]*(.*?)(?:\n\s*\n|\n\s*(?:experience|education|projects))",
        text, re.DOTALL | re.IGNORECASE,
    )
    if m:
        meta["skills"] = m.group(1).strip()[:2000]
    # education
    m = re.search(
        r"(?:education|qualification)[:\s]*(.*?)(?:\n\s*\n|\n\s*(?:experience|skills|projects|certifications))",
        text, re.DOTALL | re.IGNORECASE,
    )
    if m:
        meta["education"] = m.group(1).strip()[:1000]
    return meta


# ---------------------------------------------------------------------------
# Worker function for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _process_one(args: tuple) -> dict | None:
    """Extract text + metadata from a single PDF. Returns a dict or None."""
    pdf_path, file_name = args
    text = _extract_text(pdf_path)
    if not text.strip():
        return None
    meta = _extract_metadata(text)
    file_hash = hashlib.md5(open(pdf_path, "rb").read()[:8192]).hexdigest()[:12]
    return {
        "resume_id": f"RESUME_{file_hash}",
        "file_name": file_name,
        "file_path": str(pdf_path),
        "candidate_name": meta["candidate_name"] or file_name.replace(".pdf", ""),
        "email": meta["email"],
        "phone": meta["phone"],
        "linkedin_url": meta["linkedin_url"],
        "location": meta["location"],
        "skills": meta["skills"],
        "experience_years": meta["experience_years"],
        "education": meta["education"],
        "summary": meta["summary"],
        "full_text": text,
        "char_count": len(text),
    }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_resumes (
    resume_id       TEXT PRIMARY KEY,
    file_name       TEXT NOT NULL,
    file_path       TEXT,
    candidate_name  TEXT,
    email           TEXT,
    phone           TEXT,
    linkedin_url    TEXT,
    location        TEXT,
    skills          TEXT,
    experience_years INTEGER,
    education       TEXT,
    summary         TEXT,
    full_text       TEXT,
    char_count      INTEGER,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);
"""


def _get_connection():
    import psycopg
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["PHASE1_DB"],
        autocommit=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    db_utils.load_env()

    resume_dir = PROJECT_ROOT / "resumes_1000"
    if not resume_dir.is_dir():
        print(f"[phase1] ERROR: resume directory not found: {resume_dir}")
        return 1

    pdf_files = sorted(resume_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"[phase1] ERROR: no PDF files in {resume_dir}")
        return 1

    print("=" * 60)
    print(f" Phase 1 Resume Seed — {len(pdf_files)} PDFs found")
    print("=" * 60)

    # Wait for Postgres
    if not db_utils.wait_postgres():
        print("[phase1] ERROR: Postgres not reachable.")
        return 1

    # Create table
    conn = _get_connection()
    conn.execute("DROP TABLE IF EXISTS raw_resumes")
    conn.execute(CREATE_TABLE_SQL)
    print("[phase1] Table raw_resumes created (dropped old if any)")

    # Parallel PDF extraction
    print(f"[phase1] Extracting text from {len(pdf_files)} PDFs (8 workers) ...")
    t0 = time.time()
    tasks = [(str(p), p.name) for p in pdf_files]
    rows = []
    failed = 0

    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_process_one, t): t for t in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                rows.append(result)
            else:
                failed += 1
            if i % 50 == 0 or i == len(futures):
                elapsed = time.time() - t0
                print(f"  [{i}/{len(futures)}] extracted {len(rows)} OK, {failed} failed ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    print(f"[phase1] Extraction done: {len(rows)} resumes in {elapsed:.1f}s ({failed} failed)")

    # Batch insert
    print(f"[phase1] Inserting {len(rows)} rows into phase1_resumes.raw_resumes ...")
    insert_sql = """
        INSERT INTO raw_resumes
            (resume_id, file_name, file_path, candidate_name, email, phone,
             linkedin_url, location, skills, experience_years, education,
             summary, full_text, char_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (resume_id) DO NOTHING
    """
    BATCH = 100
    inserted = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        tuples = [
            (r["resume_id"], r["file_name"], r["file_path"], r["candidate_name"],
             r["email"], r["phone"], r["linkedin_url"], r["location"],
             r["skills"], r["experience_years"], r["education"],
             r["summary"], r["full_text"], r["char_count"])
            for r in batch
        ]
        with conn.cursor() as cur:
            cur.executemany(insert_sql, tuples)
        inserted += len(batch)

    # Final count
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM raw_resumes")
        total = cur.fetchone()[0]
    conn.close()

    print("=" * 60)
    print(f" Phase 1 Seed Complete")
    print(f"   Resumes extracted : {len(rows)}")
    print(f"   Failed            : {failed}")
    print(f"   Rows in DB        : {total}")
    print(f"   Database          : phase1_resumes @ {os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
