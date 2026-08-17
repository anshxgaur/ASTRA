"""
MAIN — runs the integrated pipeline end-to-end.

Phase 2 (clean):  ingest -> discover -> map -> standardize -> normalize
                  -> entity resolve -> context classify
Phase 3 (store):  load canonical entities into PostgreSQL (08), embed
                  context text and push it into pgvector (09/10), build the
                  HNSW index.

Every stage is timed and its metrics recorded; the whole run (stage statuses,
row counts, errors, Postgres/pgvector totals) is written to
pipeline/run_reports/last_run.json — that file is what the dashboard reads.

Ingests the REAL Phase-1 seeded sources (MySQL / PostgreSQL / MongoDB /
legacy CSVs) when they are reachable, and falls back to DATA/SAMPLE for
offline demos (DB load + embedding are skipped in sample mode).
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:  # Windows console is often cp1252; box-drawing chars need UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

PIPELINE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_ROOT.parent  # Internal_Matte/
REPORTS_DIR = PIPELINE_ROOT / "run_reports"
REPORT_PATH = REPORTS_DIR / "last_run.json"

for folder in ["01_INGESTION", "02_SCHEMA_DISCOVERY", "03_SCHEMA_MAPPING",
               "04_STANDARDIZATION", "05_NORMALIZATION", "06_DEDUPLICATION",
               "07_CONTEXT_CLASSIFICATION", "08_POSTGRESQL", "09_EMBEDDINGS"]:
    sys.path.insert(0, str(PIPELINE_ROOT / folder))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PIPELINE_ROOT / ".env")

import pandas as pd  # noqa: E402
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from export_phase4 import export_phase4  # noqa: E402
from INGESTION_ENGINE import ingest_all, ingest_all_real  # noqa: E402
from SCHEMA_DISCOVERY import discover_all  # noqa: E402
from SCHEMA_MAPPER import map_all  # noqa: E402
from STANDARDIZER import standardize_all  # noqa: E402
from NORMALIZER import normalize_all  # noqa: E402
from ENTITY_RESOLVER import match_entities  # noqa: E402
from FIELD_CLASSIFIER import classify_columns  # noqa: E402
from DB_LOADER import ensure_database_and_schema, get_connection, load_all  # noqa: E402
from EMBEDDING_PIPELINE import process_all_entities  # noqa: E402
from VECTOR_STORE import build_vector_index  # noqa: E402


def build_entity_frames(resolved: pd.DataFrame) -> dict:
    """Split the resolved combined frame into per-entity load frames.

    The institution frame covers EVERY master id that other entities
    reference: per master id we keep the MySQL row when one exists, otherwise
    the first named row (orphan colleges/faculty become their own
    institutions), so foreign keys never dangle.
    """
    frames: dict = {}

    named = resolved[resolved["institution_name"].notna()].copy()
    priority = {"mysql": 0, "postgres": 1, "legacy_csv": 2, "mongodb": 3,
                "internships": 5}
    named["_prio"] = named["source_system"].map(priority).fillna(4)
    named = named.sort_values("_prio")
    inst = named.drop_duplicates("master_entity_id", keep="first").drop(columns=["_prio"])
    frames["institutions"] = inst

    courses = resolved[resolved["source_table"] == "courses"].copy()
    if len(courses):
        courses["course_id"] = "CRS_" + courses["course_id"].astype(str)
        courses["institution_id"] = courses["master_entity_id"]
        frames["courses"] = courses

    faculty = resolved[resolved["source_table"] == "faculty"].copy()
    if len(faculty):
        faculty["faculty_id"] = "FAC_" + faculty["faculty_id"].astype(str)
        faculty["institution_id"] = faculty["master_entity_id"]
        frames["faculty"] = faculty

    scholarships = resolved[resolved["source_system"] == "mongodb"].copy()
    if len(scholarships):
        scholarships["scholarship_id"] = scholarships["source_record_id"]
        frames["scholarships"] = scholarships

    approvals = resolved[resolved["source_system"] == "legacy_csv"].copy()
    if len(approvals):
        approvals["approval_id"] = ["APR_%05d" % (i + 1) for i in range(len(approvals))]
        approvals["institution_id"] = approvals["master_entity_id"]
        approvals["approval_type"] = approvals["source_table"].map({
            "nba_autonomous_status.csv": "nba",
            "closed_institutes.csv": "closed",
            "unapproved_list.csv": "unapproved",
        })
        frames["approvals"] = approvals

    internships = resolved[resolved["source_system"] == "internships"].copy()
    if len(internships):
        internships["internship_id"] = ["INT_%05d" % (i + 1) for i in range(len(internships))]
        internships["institution_id"] = internships["master_entity_id"]
        frames["internships"] = internships

    return frames


def _finish_stage(stage: dict, started: float, **extra) -> None:
    stage["seconds"] = round(time.time() - started, 2)
    stage.update(extra)


def _write_report(report: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"\n[report] wrote {REPORT_PATH}")


def run_pipeline() -> int:
    report = {
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": "ok",
        "error": "",
        "duration_seconds": 0.0,
        "use_real_sources": True,
        "stages": [],
        "sources": {},
        "phase3": {},
    }
    t_start = time.time()
    sample_dir = PIPELINE_ROOT / "DATA" / "SAMPLE"

    try:
        # ── 01 INGESTION ─────────────────────────────────────────────
        st = {"name": "01 Ingestion", "status": "ok", "note": ""}
        report["stages"].append(st)
        t0 = time.time()
        try:
            raw = ingest_all_real()
            report["use_real_sources"] = True
            st["note"] = "real Phase-1 sources"
        except Exception as exc:  # noqa: BLE001
            report["use_real_sources"] = False
            st["note"] = f"real sources unavailable ({type(exc).__name__}) — sample fallback"
            raw = ingest_all(sample_dir)
        report["sources"] = {
            name: {"rows": len(df),
                   "system": str(df["source_system"].iloc[0]) if len(df) else "n/a",
                   "table": str(df["source_table"].iloc[0]) if len(df) else "n/a"}
            for name, df in raw.items()
        }
        _finish_stage(st, t0, rows=sum(len(v) for v in raw.values()),
                      note=st["note"] + f" — {len(raw)} sources")
        print("── 01 INGESTION ──────────────────────────────────────")
        for name, df in raw.items():
            print(f"    {name}: {len(df)} rows")

        # ── 02 SCHEMA DISCOVERY ─────────────────────────────────────
        st = {"name": "02 Schema Discovery", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        profile = discover_all(raw)
        _finish_stage(st, t0, note=f"{len(profile)} fields profiled")
        print("\n── 02 SCHEMA DISCOVERY ───────────────────────────────")
        print(f"  {len(profile)} fields profiled across all sources")

        # ── 03 SCHEMA MAPPING ───────────────────────────────────────
        st = {"name": "03 Schema Mapping", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        mapped = map_all(raw)
        _finish_stage(st, t0, rows=sum(len(v) for v in mapped.values()),
                      note=f"{len(mapped)} sources mapped")
        print("\n── 03 SCHEMA MAPPING ─────────────────────────────────")
        print(f"  fields mapped to canonical names for {len(mapped)} sources")

        # ── 04 STANDARDIZATION ──────────────────────────────────────
        st = {"name": "04 Standardization", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        standardized = standardize_all(mapped)
        _finish_stage(st, t0, rows=sum(len(v) for v in standardized.values()),
                      note="states / booleans / text normalized")
        print("\n── 04 STANDARDIZATION ────────────────────────────────")
        print("  states / booleans / text normalized")

        # ── 05 NORMALIZATION ────────────────────────────────────────
        st = {"name": "05 Normalization", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        normalized = normalize_all(standardized)
        _finish_stage(st, t0, rows=sum(len(v) for v in normalized.values()),
                      note="dtypes cast to canonical schema")
        print("\n── 05 NORMALIZATION ──────────────────────────────────")
        print("  dtypes cast to canonical schema")

        # ── 06 ENTITY RESOLUTION ────────────────────────────────────
        st = {"name": "06 Entity Resolution", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        combined = pd.concat(normalized.values(), ignore_index=True)
        resolved = match_entities(combined)
        n_entities = resolved["master_entity_id"].nunique()
        _finish_stage(st, t0, rows=len(resolved),
                      note=f"{len(resolved)} records -> {n_entities} master entities")
        report["entity_resolution"] = {"records_in": len(resolved),
                                       "entities_out": n_entities}
        print("\n── 06 ENTITY RESOLUTION ──────────────────────────────")
        print(f"  {len(resolved)} source records -> {n_entities} master entities")

        # ── 07 CONTEXT CLASSIFICATION ───────────────────────────────
        st = {"name": "07 Context Classification", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        classification = classify_columns(list(combined.columns))
        contextual = [f for f, t in classification.items() if t == "contextual"]
        _finish_stage(st, t0, note=f"contextual fields: {contextual}")
        print("\n── 07 CONTEXT CLASSIFICATION ─────────────────────────")
        print(f"  contextual fields flagged for embedding: {contextual}")

        if not report["use_real_sources"]:
            report["status"] = "degraded"
            report["error"] = "Real sources unreachable — sample-data mode (no DB load)."
            report["duration_seconds"] = round(time.time() - t_start, 2)
            _write_report(report)
            print("\nPipeline run complete (sample data, phases 01-07).")
            return 1

        frames = build_entity_frames(resolved)
        frames = {k: v.astype(object).where(pd.notnull(v), None)
                  for k, v in frames.items()}

        # ── 08 POSTGRESQL (canonical store) ─────────────────────────
        st = {"name": "08 PostgreSQL Load", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        ensure_database_and_schema()
        conn = get_connection()
        load_all(conn, frames)
        table_counts = {}
        with conn.cursor() as cur:
            for table in ["institution", "course", "faculty", "scholarship",
                          "approval", "internship"]:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                table_counts[table] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM entity_mapping")
            table_counts["entity_mapping"] = cur.fetchone()[0]
        conn.close()
        _finish_stage(st, t0, rows=sum(table_counts.values()),
                      note="institution/course/faculty/scholarship/approval/internship + lineage")
        report["phase3"]["tables"] = table_counts
        print("\n── 08 POSTGRESQL (canonical store) ───────────────────")
        for table, n in table_counts.items():
            print(f"    {table:<16} {n:>5} rows")

        # ── 09 EMBEDDINGS ───────────────────────────────────────────
        st = {"name": "09 Embeddings", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        print("\n── 09 EMBEDDINGS ─────────────────────────────────────")
        print("  loading embedding model (all-MiniLM-L6-v2, fastembed)...")
        counts = process_all_entities(frames)
        _finish_stage(st, t0, rows=sum(counts.values()),
                      note="fastembed all-MiniLM-L6-v2 (384-dim)")

        # ── 10 PGVECTOR ─────────────────────────────────────────────
        st = {"name": "10 pgvector Index", "status": "ok"}
        report["stages"].append(st)
        t0 = time.time()
        conn = get_connection()
        build_vector_index(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), vector_dims(embedding) FROM context_document GROUP BY 2")
            ctx_total, dims = cur.fetchone()
            cur.execute("""
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'context_document' AND indexname = 'idx_context_embedding'
            """)
            hnsw = cur.fetchone() is not None
        conn.close()
        _finish_stage(st, t0, rows=ctx_total,
                      note=f"{ctx_total} embeddings, {dims}-dim, HNSW={'yes' if hnsw else 'no'}")
        report["phase3"]["context_document"] = {
            "rows": ctx_total, "dims": dims, "hnsw_index": hnsw,
        }
        print("\n── 10 PGVECTOR ───────────────────────────────────────")
        print(f"    context_document: {ctx_total} rows, {dims}-dim, "
              f"HNSW={'yes' if hnsw else 'no'}")

        report["duration_seconds"] = round(time.time() - t_start, 2)
        _write_report(report)
        print("\nPipeline run complete: Phase 1 sources cleaned + loaded into")
        print("PostgreSQL (aicte_canonical) with pgvector embeddings ready for")
        print("hybrid retrieval (see 11_RETRIEVAL/HYBRID_RETRIEVER.py).")
        print("\nExporting Phase 4 data package ...")
        export_phase4()
        return 0

    except Exception as exc:  # noqa: BLE001
        import traceback
        report["status"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        if report["stages"]:
            report["stages"][-1]["status"] = "error"
        report["duration_seconds"] = round(time.time() - t_start, 2)
        _write_report(report)
        print(f"\n[MAIN] pipeline FAILED at stage "
              f"'{report['stages'][-1]['name'] if report['stages'] else '?'}': {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(run_pipeline())
