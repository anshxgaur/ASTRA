"""
Scale down existing AICTE data and set up Resume + Research Paper storage.
This prepares the database for 1000 resumes + 100 research papers.
"""
import os
import sys
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_ROOT.parent

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PIPELINE_ROOT / ".env")

sys.path.insert(0, str(PIPELINE_ROOT / "08_POSTGRESQL"))
sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))


def get_connection():
    import psycopg
    from pgvector.psycopg import register_vector
    url = os.getenv("DATABASE_URL")
    if url:
        conn = psycopg.connect(url)
    else:
        conn = psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5433"),
            dbname=os.getenv("POSTGRES_DB", "aicte_canonical"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )
    register_vector(conn)
    return conn


def scale_down_existing_data():
    conn = get_connection()
    print("=" * 60)
    print("STEP 1: Scaling down existing AICTE data")
    print("=" * 60)

    with conn.cursor() as cur:
        # Get the 20 institutions to keep
        cur.execute("SELECT institution_id FROM institution LIMIT 20")
        keep_ids = [r[0] for r in cur.fetchall()]
        keep_tuple = tuple(keep_ids)

        # Delete child tables first (FK constraints)
        cur.execute("DELETE FROM context_document")
        print("  context_document: cleared")

        cur.execute("DELETE FROM entity_mapping")
        print("  entity_mapping: cleared")

        cur.execute("DELETE FROM data_lineage")
        print("  data_lineage: cleared")

        placeholders = ','.join(['%s'] * len(keep_ids))
        cur.execute("DELETE FROM course WHERE institution_id NOT IN ({})".format(placeholders), keep_ids)
        print("  courses: deleted {} (kept linked)".format(cur.rowcount))

        cur.execute("DELETE FROM faculty WHERE institution_id NOT IN ({})".format(placeholders), keep_ids)
        print("  faculty: deleted {} (kept linked)".format(cur.rowcount))

        cur.execute("DELETE FROM internship WHERE institution_id NOT IN ({})".format(placeholders), keep_ids)
        print("  internships: deleted {} (kept linked)".format(cur.rowcount))

        cur.execute("DELETE FROM approval WHERE institution_id NOT IN ({})".format(placeholders), keep_ids)
        print("  approvals: deleted {} (kept linked)".format(cur.rowcount))

        # Now delete parent table
        cur.execute("DELETE FROM institution WHERE institution_id NOT IN ({})".format(placeholders), keep_ids)
        print("  institutions: kept 20 (deleted {})".format(cur.rowcount))

        cur.execute("SELECT COUNT(*) FROM scholarship")
        print("  scholarships: kept {}".format(cur.fetchone()[0]))

    conn.commit()

    with conn.cursor() as cur:
        print("\n  Current counts after scale-down:")
        for table in ["institution", "course", "faculty", "scholarship", "approval", "internship"]:
            cur.execute('SELECT COUNT(*) FROM "{}"'.format(table))
            print("    {}: {}".format(table, cur.fetchone()[0]))

    conn.close()
    print("  Done!")


def create_resume_schema():
    conn = get_connection()
    print("\n" + "=" * 60)
    print("STEP 2: Creating Resume storage schema")
    print("=" * 60)

    with conn.cursor() as cur:
        cur.execute("""
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
            )
        """)
        print("  Created table: resume")

        cur.execute("""
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
            )
        """)
        print("  Created table: resume_chunk")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_resume_chunk_embedding ON resume_chunk USING hnsw (embedding vector_cosine_ops)")
        print("  Created index: idx_resume_chunk_embedding")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_resume_chunk_resume ON resume_chunk(resume_id)")
        print("  Created index: idx_resume_chunk_resume")

    conn.commit()
    conn.close()
    print("  Resume schema ready!")


def create_paper_schema():
    conn = get_connection()
    print("\n" + "=" * 60)
    print("STEP 3: Creating Research Paper storage schema")
    print("=" * 60)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS research_paper (
                paper_id        TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                authors         TEXT,
                abstract        TEXT,
                publication     TEXT,
                year            INTEGER,
                doi             TEXT,
                keywords        TEXT,
                file_path       TEXT,
                total_chunks    INTEGER DEFAULT 0,
                ingested_at     TIMESTAMPTZ DEFAULT now()
            )
        """)
        print("  Created table: research_paper")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS paper_chunk (
                chunk_id        TEXT PRIMARY KEY,
                paper_id        TEXT NOT NULL REFERENCES research_paper(paper_id),
                chunk_index     INTEGER NOT NULL,
                section         TEXT,
                heading         TEXT,
                content         TEXT NOT NULL,
                char_count      INTEGER,
                embedding       VECTOR(384),
                created_at      TIMESTAMPTZ DEFAULT now()
            )
        """)
        print("  Created table: paper_chunk")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_paper_chunk_embedding ON paper_chunk USING hnsw (embedding vector_cosine_ops)")
        print("  Created index: idx_paper_chunk_embedding")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_paper_chunk_paper ON paper_chunk(paper_id)")
        print("  Created index: idx_paper_chunk_paper")

    conn.commit()
    conn.close()
    print("  Research Paper schema ready!")


def rebuild_context_embeddings():
    print("\n" + "=" * 60)
    print("STEP 4: Rebuilding AICTE context embeddings")
    print("=" * 60)

    sys.path.insert(0, str(PIPELINE_ROOT / "09_EMBEDDINGS"))
    from EMBEDDING_GENERATOR import embed_texts

    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute("SELECT institution_id, institution_name, state, city, institute_type FROM institution")
        institutions = cur.fetchall()
        print("  Building context for {} institutions...".format(len(institutions)))

        context_docs = []
        for inst in institutions:
            inst_id, name, state, city, itype = inst
            context = "{} is a {} located in {}, {}.".format(name, itype or "college", city or "", state or "India")
            context_docs.append({
                "entity_id": inst_id,
                "entity_type": "institution",
                "context_text": context,
                "source_database": "aicte_canonical",
                "source_table": "institution",
                "source_record_id": inst_id,
            })

        texts = [d["context_text"] for d in context_docs]
        embeddings = embed_texts(texts)

        for doc, emb in zip(context_docs, embeddings):
            cur.execute("""
                INSERT INTO context_document
                (entity_id, entity_type, context_text, embedding,
                 source_database, source_table, source_record_id)
                VALUES (%s, %s, %s, %s::vector, %s, %s, %s)
            """, (doc["entity_id"], doc["entity_type"], doc["context_text"],
                  emb, doc["source_database"], doc["source_table"], doc["source_record_id"]))

        conn.commit()
        cur.execute("SELECT COUNT(*) FROM context_document")
        print("  Rebuilt {} context embeddings".format(cur.fetchone()[0]))

    conn.close()
    print("  Done!")


def show_final_status():
    conn = get_connection()
    print("\n" + "=" * 60)
    print("FINAL DATABASE STATUS")
    print("=" * 60)

    with conn.cursor() as cur:
        tables = [
            ("institution", "AICTE"),
            ("course", "AICTE"),
            ("faculty", "AICTE"),
            ("scholarship", "AICTE"),
            ("approval", "AICTE"),
            ("internship", "AICTE"),
            ("context_document", "AICTE embeddings"),
            ("resume", "YOUR DATA - Resumes"),
            ("resume_chunk", "YOUR DATA - Resume chunks"),
            ("research_paper", "YOUR DATA - Papers"),
            ("paper_chunk", "YOUR DATA - Paper chunks"),
        ]

        print("\n  Table                    | Rows  | Status")
        print("  " + "-" * 50)
        for table, desc in tables:
            try:
                cur.execute('SELECT COUNT(*) FROM "{}"'.format(table))
                count = cur.fetchone()[0]
                status = "READY (empty)" if count == 0 else "HAS DATA"
                print("  {:24s} | {:5d} | {}".format(table, count, status))
            except Exception:
                print("  {:24s} |   N/A | NOT CREATED".format(table))

    conn.close()


if __name__ == "__main__":
    print("AICTE Database Setup for 1000 Resumes + 100 Research Papers")
    print("=" * 60)

    scale_down_existing_data()
    create_resume_schema()
    create_paper_schema()
    rebuild_context_embeddings()
    show_final_status()

    print("\n" + "=" * 60)
    print("SETUP COMPLETE!")
    print("=" * 60)
    print("""
Next steps:
1. Place your 1000 resumes (PDF) in: data/resumes/
2. Place your 100 research papers (PDF) in: data/papers/
3. Run resume ingestion:
   ./internalenv/Scripts/python.exe pipeline/11_RETRIEVAL/RESUME_RAG.py ingest data/resumes/
4. Run paper ingestion:
   ./internalenv/Scripts/python.exe pipeline/11_RETRIEVAL/RESEARCH_PAPER_RAG.py ingest data/papers/
5. Or use API:
   curl -X POST 'http://localhost:8000/papers/ingest-directory?pdf_dir=data/papers/'
   curl -X POST 'http://localhost:8000/resumes/ingest-directory?pdf_dir=data/resumes/'
""")
