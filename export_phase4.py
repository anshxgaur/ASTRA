"""Export the Phase-3 canonical store into phase4_data/ for the Phase-4 build.

After running pipeline/MAIN.py, dump every aicte_canonical table plus the
pgvector context_document (chunk text + metadata + raw 384-dim embeddings) as
CSVs, so the Phase-4 engineer can build the FastAPI hybrid-search API WITHOUT
connecting to the databases.

Usage:
    internalenv/Scripts/python.exe export_phase4.py

(Also runs automatically at the end of pipeline/MAIN.py.)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seed import db_utils  # noqa: E402
db_utils.load_env()
from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / "pipeline" / ".env")

import pandas as pd  # noqa: E402
import psycopg  # noqa: E402

OUT_DIR = PROJECT_ROOT / "phase4_data"
DB = os.environ.get("POSTGRES_DB", "aicte_canonical")

# Transient Windows open failures (errno 22 = EINVAL, 13 = EACCES, 32 =
# ERROR_SHARING_VIOLATION) that antivirus / search indexers / editors can
# briefly raise when a freshly churned file is opened for writing.
_TRANSIENT_ERRNOS = {22, 13, 32}


def _atomic_write_csv(df, path: Path, attempts: int = 5) -> None:
    """Write ``df`` as a utf-8-sig CSV via a temp file + atomic rename.

    Hardening for Windows: a plain ``df.to_csv(path)`` opens the destination
    directly, so a transient lock (antivirus scan, indexer, preview pane)
    raised ``OSError: [Errno 22] Invalid argument`` and aborted the whole
    export at the tail of long pipeline runs. Writing to a fresh temp name in
    the same directory is never blocked by whoever holds the old file, and the
    final ``os.replace`` is atomic (readers never see a half-written CSV).
    Transient errors are retried with a short backoff.
    """
    last_err: OSError | None = None
    for attempt in range(attempts):
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=path.stem + "_", suffix=".tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_err = exc
            try:
                tmp.unlink()
            except OSError:
                pass
            if exc.errno not in _TRANSIENT_ERRNOS:
                raise
            time.sleep(0.5 * (attempt + 1))
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
    if last_err is not None:
        raise last_err

RELATIONAL_TABLES = [
    "institution", "course", "faculty", "scholarship", "approval", "internship",
    "entity_mapping", "data_lineage",
]

CONTEXT_COLUMNS = [
    "context_id", "entity_id", "entity_type", "context_type", "context_text",
    "embedding",  # pgvector text form: "[0.123,-0.456,...]" (384 floats)
    "source_database", "source_table", "source_record_id",
    "confidence", "created_at", "data_version",
]


def _conn():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=DB,
    )


def _write_readme(counts: dict) -> None:
    text = f"""# Phase 4 — data package (generated from aicte_canonical)

Generated {datetime.now().isoformat(timespec='seconds')} by `export_phase4.py`
from the Phase-3 canonical store (database `{DB}`). Everything needed to build
the Phase-4 FastAPI hybrid-search API is here — no database access required.

## Files

### Relational facts (PostgreSQL tables)

| File | Rows | Contents |
|------|------|----------|
| `institution.csv` | {counts['institution.csv']} | canonical institutions (`institution_id` PK, name, state, district, city, type, ownership, approval/current status, autonomous, NBA, year, AICTE code) |
| `course.csv` | {counts['course.csv']} | courses with FK `institution_id`, department, duration, intake, fee, course_status |
| `faculty.csv` | {counts['faculty.csv']} | faculty with FK `institution_id`, designation, qualification, specialization, department, years_of_experience |
| `scholarship.csv` | {counts['scholarship.csv']} | scholarship schemes (amount, applicable_states) |
| `approval.csv` | {counts['approval.csv']} | nba / closed / unapproved records with FK `institution_id` |
| `internship.csv` | {counts['internship.csv']} | internship-portal openings with FK `institution_id`, domain, org, stipend, mode, PPO, program_source |
| `entity_mapping.csv` | {counts['entity_mapping.csv']} | every canonical id -> source record (lineage) |
| `data_lineage.csv` | {counts['data_lineage.csv']} | every row -> source system/table/record + timestamp |

### Semantic context (pgvector)

| File | Rows | Contents |
|------|------|----------|
| `context_document.csv` | {counts['context_document.csv']} | one row per embedding: `context_text` (rich sentence + [Source: ...] citation), `embedding` (384 floats), and metadata (`entity_id`, `entity_type`, `context_type`, source lineage) |

## How to use in Phase 4

1. **Search**: embed the user query with the SAME model used here —
   `sentence-transformers/all-MiniLM-L6-v2` (via fastembed), 384 dims — then
   compute cosine similarity against `embedding` (parse the CSV value as
   `[float,...]`). `context_text` ends with a `[Source: ..., record ...]`
   citation you can return to the user.
2. **Filters / facts**: join `context_document.entity_id` to the relational
   tables (e.g. `institution.institution_id` for colleges, `course.course_id`
   for courses) to filter by state / approval_status / entity_type.
3. **Grounded answers**: every fact carries lineage — `entity_mapping` /
   `data_lineage` tell you exactly which source record a canonical id came
   from. Never let the LLM invent facts outside retrieved context.
4. **Load back into Postgres** (optional) instead of reading CSVs:
   ```sql
   \\copy institution FROM 'phase4_data/institution.csv' WITH (FORMAT csv, HEADER true);
   \\copy context_document(context_id, entity_id, entity_type, context_type, context_text,
       embedding, source_database, source_table, source_record_id, confidence, created_at, data_version)
   FROM 'phase4_data/context_document.csv' WITH (FORMAT csv, HEADER true);
   ```

## Join-key cheat sheet

- `context_document.entity_type` = `institution` | `course` | `faculty` | `scholarship` | `approval` | `internship`
- `institution` ↔ `entity_mapping.master_entity_id` (id `INST_xxxx`)
- `course.course_id` = `CRS_<source course_id>` · `faculty.faculty_id` = `FAC_<source faculty_id>`
- `scholarship.scholarship_id` = Mongo `_id` · `approval.approval_id` = `APR_xxxxx`
"""
    (OUT_DIR / "README.md").write_text(text, encoding="utf-8")


def export_phase4() -> dict:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    with _conn() as conn:
        with conn.cursor() as cur:
            for table in RELATIONAL_TABLES:
                cur.execute(f'SELECT * FROM "{table}" ORDER BY 1')
                cols = [d[0] for d in cur.description]
                df = pd.DataFrame(cur.fetchall(), columns=cols)
                _atomic_write_csv(df, OUT_DIR / f"{table}.csv")
                counts[f"{table}.csv"] = len(df)

            cur.execute(
                "SELECT context_id, entity_id, entity_type, context_type, context_text,"
                " embedding::text, source_database, source_table, source_record_id,"
                " confidence, created_at, data_version"
                " FROM context_document ORDER BY context_id"
            )
            cols = [d[0] for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
            _atomic_write_csv(df, OUT_DIR / "context_document.csv")
            counts["context_document.csv"] = len(df)

    manifest = {
        "name": "Phase 4 data package",
        "generated_by": "export_phase4.py",
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "source_database": DB,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2 (fastembed)",
        "embedding_dimensions": 384,
        "counts": counts,
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_readme(counts)

    print("=" * 60)
    print(" PHASE 4 DATA PACKAGE — exported to phase4_data/")
    print("=" * 60)
    for fname, n in counts.items():
        print(f"  {fname:<26} {n:>6} rows")
    print("=" * 60)
    print(f" Includes README.md + manifest.json. Embeddings: 384-dim"
          f" ({manifest['embedding_model']}).")
    return counts


if __name__ == "__main__":
    raise SystemExit(0 if export_phase4() else 1)
