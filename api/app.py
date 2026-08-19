"""
Phase 4 — AICTE Unified Search API (the "frontapi").

Wraps the hybrid retrieval engine (rules-first, then Groq text-to-SQL +
pgvector, then Groq answer synthesis) in a FastAPI service with a
government-grade browser frontend served at /.

Run:
    internalenv/Scripts/python.exe -m uvicorn api.app:app --port 8000

Endpoints:
    GET /                        search frontend (HTML)
    GET /health                  db + pgvector + Groq status + data coverage
    GET /search?q=...            hybrid results (rules / LLM SQL + enriched pgvector hits)
    GET /answer?q=...            grounded, cited answer (Groq synthesis)
    GET /entity/{canonical_id}   full canonical record + lineage
    GET /conflicts               ground-truth planted issues (summary + detail)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]          # Internal_Matte/
PIPELINE_ROOT = PROJECT_ROOT / "pipeline"
RETRIEVAL_ROOT = PIPELINE_ROOT / "11_RETRIEVAL"
PERF_LOG_PATH = PIPELINE_ROOT / "run_reports" / "api_performance.jsonl"
sys.path.insert(0, str(RETRIEVAL_ROOT))
sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))

import GROQ_CLIENT            # noqa: E402
import HYBRID_RETRIEVER       # noqa: E402

HYBRID_RETRIEVER._load_env()

from VECTOR_STORE import get_connection  # noqa: E402

app = FastAPI(
    title="AICTE Unified Search API",
    description="Hybrid search over the canonical AICTE store: PostgreSQL facts + pgvector context, "
                "grounded answers synthesized by Groq.",
    version="4.0.0",
)

ENTITY_PK = {
    "institution": "institution_id",
    "course": "course_id",
    "faculty": "faculty_id",
    "scholarship": "scholarship_id",
    "approval": "approval_id",
    "internship": "internship_id",
}


def _log_perf(event: dict) -> None:
    """Best-effort local performance log for dashboard analysis."""
    try:
        PERF_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **event,
        }
        with PERF_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _pg_ok() -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM context_document")
                n = cur.fetchone()[0]
        return True, f"connected (context_document={n} rows)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _pg_coverage() -> dict:
    """Row counts per canonical table + indexed context docs (header stats)."""
    tables = ["institution", "course", "faculty", "scholarship", "approval", "internship"]
    out = {t: 0 for t in tables}
    out["indexed_records"] = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for t in tables:
                    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                    out[t] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM context_document")
                out["indexed_records"] = cur.fetchone()[0]
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


@app.get("/health")
def health() -> dict:
    pg_ok, pg_msg = _pg_ok()
    coverage = _pg_coverage()
    return {
        "status": "ok" if pg_ok else "degraded",
        "database": {"ok": pg_ok, "detail": pg_msg},
        "coverage": coverage,
        "llm": {"provider": "groq", "configured": GROQ_CLIENT.is_available(),
                "model": __import__("os").getenv("GROQ_MODEL", GROQ_CLIENT.DEFAULT_MODEL)},
    }


@app.get("/search")
def search(
    q: str = Query(..., min_length=1, description="natural-language query"),
    top_k: int = Query(5, ge=1, le=20),
    entity_type: str | None = Query(None, pattern="^(institution|course|faculty|scholarship|approval|internship)$"),
    state: str | None = None,
    approval_status: str | None = Query(None, pattern="^(approved|not_approved)$"),
) -> dict:
    started = time.perf_counter()
    status = "ok"
    path = "unknown"
    count = 0
    try:
        result = HYBRID_RETRIEVER.hybrid_search(q, top_k=top_k,
                                                entity_type=entity_type, state=state,
                                                approval_status=approval_status)
        path = result.get("retrieval", {}).get("path", path)
        count = result.get("count", 0)
        return result
    except Exception as exc:  # noqa: BLE001
        status = "error"
        raise HTTPException(status_code=500, detail=f"search failed: {type(exc).__name__}: {exc}")
    finally:
        _log_perf({
            "endpoint": "search",
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "query_length": len(q),
            "top_k": top_k,
            "path": path,
            "result_count": count,
            "llm": "groq" if GROQ_CLIENT.is_available() else "mock",
        })


@app.get("/answer")
def answer(
    q: str = Query(..., min_length=1, description="natural-language question"),
    top_k: int = Query(5, ge=1, le=10),
) -> dict:
    started = time.perf_counter()
    status = "ok"
    try:
        text = HYBRID_RETRIEVER.answer_question(q, top_k=top_k)
        return {
            "query": q,
            "answer": text,
            "llm": "groq" if GROQ_CLIENT.is_available() else "mock",
        }
    except Exception as exc:  # noqa: BLE001
        status = "error"
        raise HTTPException(status_code=500, detail=f"answer failed: {type(exc).__name__}: {exc}")
    finally:
        _log_perf({
            "endpoint": "answer",
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "query_length": len(q),
            "top_k": top_k,
            "path": "answer_synthesis",
            "llm": "groq" if GROQ_CLIENT.is_available() else "mock",
        })


@app.get("/entity/{canonical_id}")
def entity(canonical_id: str) -> dict:
    """Full canonical record + lineage for any entity id (INST_*, CRS_*, FAC_*, APR_*, mongo _id)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT entity_type FROM entity_mapping WHERE master_entity_id = %s LIMIT 1",
                    (canonical_id,),
                )
                row = cur.fetchone()

                if row is None:
                    cur.execute(
                        "SELECT entity_type FROM context_document WHERE entity_id = %s LIMIT 1",
                        (canonical_id,),
                    )
                    row = cur.fetchone()

                if row is None:
                    raise HTTPException(status_code=404,
                                        detail=f"no entity with id '{canonical_id}'")

                entity_type = row[0]
                pk = ENTITY_PK.get(entity_type)
                record = None
                if pk is not None:
                    cur.execute(
                        f'SELECT * FROM "{entity_type}" WHERE "{pk}" = %s', (canonical_id,))
                    cols = [d[0] for d in cur.description]
                    found = cur.fetchone()
                    record = dict(zip(cols, found)) if found else None

                cur.execute(
                    "SELECT master_entity_id, entity_type, source_system, source_database, "
                    "source_table, source_record_id, match_score "
                    "FROM entity_mapping WHERE master_entity_id = %s", (canonical_id,))
                mcols = [d[0] for d in cur.description]
                mapping = [dict(zip(mcols, r)) for r in cur.fetchall()]

                cur.execute(
                    "SELECT source_system, source_database, source_table, source_record_id, "
                    "transformation_version, validation_status, ingestion_timestamp "
                    "FROM data_lineage WHERE canonical_entity_id = %s", (canonical_id,))
                lcols = [d[0] for d in cur.description]
                lineage = [dict(zip(lcols, r)) for r in cur.fetchall()]

        return {"entity_id": canonical_id, "entity_type": entity_type,
                "record": record, "mapping": mapping, "lineage": lineage}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"entity lookup failed: {type(exc).__name__}: {exc}")


@app.get("/conflicts")
def conflicts(kind: str | None = Query(None, pattern="^(conflict|duplicate|orphan)$")) -> dict:
    """Ground truth of the deliberately planted issues (conflicts_seeded.json)."""
    path = PROJECT_ROOT / "conflicts_seeded.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="conflicts_seeded.json not found")
    gt = json.loads(path.read_text(encoding="utf-8"))
    summary = {
        "conflicts": len(gt.get("cross_source_conflicts", [])),
        "duplicates": len(gt.get("within_source_duplicates", [])),
        "orphans": len(gt.get("orphaned_records", [])),
    }
    key = {"conflict": "cross_source_conflicts",
           "duplicate": "within_source_duplicates",
           "orphan": "orphaned_records"}.get(kind or "")
    detail = gt.get(key, []) if key else gt
    return {"summary": summary, "detail": detail}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """Phase 4 search frontend — a government-grade data product UI."""
    return HTML


@app.get("/api")
def index() -> dict:
    return {
        "service": "AICTE Unified Search API (Phase 4)",
        "endpoints": ["/search?q=...", "/answer?q=...",
                      "/entity/{canonical_id}", "/conflicts", "/health"],
        "docs": "/docs",
    }


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AICTE Unified Search — Data Layer</title>
<style>
  /* ── design tokens ── */
  :root{
    --bg:#f2f4f8; --surface:#ffffff; --surface2:#f8fafc; --surface3:#eef1f6;
    --line:#dfe4ec; --line2:#c9d2e0;
    --navy:#1e3a8a; --blue:#2563eb; --blue2:#3b82f6;
    --ink:#0f172a; --ink2:#334155; --ink3:#64748b; --ink4:#94a3b8;
    --ok:#15803d; --warn:#b45309; --err:#be123c;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
    --sans:ui-sans-serif,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    /* muted category accents — small indicators only */
    --c-inst:#2563eb; --c-course:#0e7490; --c-faculty:#6d28d9;
    --c-scholar:#b45309; --c-approval:#be123c; --c-intern:#0f766e;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5}
  ::selection{background:rgba(37,99,235,.18)}
  .wrap{max-width:1120px;margin:0 auto;padding:0 24px 72px}

  /* ── header ── */
  header{position:sticky;top:0;z-index:40;background:rgba(255,255,255,.92);
         backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
  .head{max-width:1120px;margin:0 auto;padding:10px 24px;display:flex;align-items:center;gap:16px}
  .brand{display:flex;align-items:center;gap:12px}
  .mark{width:34px;height:34px;border-radius:8px;background:linear-gradient(160deg,var(--navy),var(--blue));
        color:#fff;display:grid;place-items:center;font-weight:800;font-size:14px;letter-spacing:.5px;
        box-shadow:0 2px 8px rgba(30,58,138,.35)}
  .brand h1{font-size:15px;font-weight:700;letter-spacing:.2px;line-height:1.2}
  .brand .sub{font-size:11px;color:var(--ink3);letter-spacing:.02em}
  .coverage{display:flex;gap:18px;margin-left:auto;flex-wrap:wrap}
  .cov{text-align:right}
  .cov .v{font-size:14px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--ink)}
  .cov .l{font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--ink4)}
  .badge{display:inline-flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;
         color:var(--ink3);border:1px solid var(--line);border-radius:6px;padding:4px 9px;background:var(--surface2)}
  .badge .dot{width:7px;height:7px;border-radius:50%}
  .dot.ok{background:var(--ok)} .dot.warn{background:var(--warn)} .dot.err{background:var(--err)}
  @media(max-width:860px){.coverage{display:none}}

  /* ── search panel ── */
  .panel{background:var(--surface);border:1px solid var(--line);border-radius:14px;margin-top:28px;
         box-shadow:0 1px 3px rgba(15,23,42,.05),0 12px 32px rgba(15,23,42,.06);overflow:hidden}
  .searchrow{display:flex;gap:0;border-bottom:1px solid var(--line)}
  .searchrow input{flex:1;border:none;outline:none;padding:18px 20px;font-size:16px;color:var(--ink);
                   background:transparent;font-family:var(--sans)}
  .searchrow input::placeholder{color:var(--ink4)}
  .go{padding:0 24px;border:none;background:var(--navy);color:#fff;font-weight:700;font-size:14px;
      cursor:pointer;display:flex;align-items:center;gap:8px;letter-spacing:.02em;transition:background .15s}
  .go:hover{background:var(--blue)}
  .go:disabled{opacity:.55;cursor:wait}
  .filters{display:flex;align-items:center;gap:6px;padding:10px 14px;flex-wrap:wrap;background:var(--surface2)}
  .flabel{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--ink4);margin-right:4px}
  .chip{font-size:12px;font-weight:600;color:var(--ink3);border:1px solid var(--line);background:#fff;
        border-radius:999px;padding:4px 12px;cursor:pointer;user-select:none;transition:all .12s;
        display:inline-flex;align-items:center;gap:6px}
  .chip:hover{border-color:var(--line2);color:var(--ink)}
  .chip.on{background:var(--navy);border-color:var(--navy);color:#fff}
  .chip .cd{width:7px;height:7px;border-radius:50%}
  .chip.on .cd{background:rgba(255,255,255,.85)}
  .cd.inst{background:var(--c-inst)} .cd.course{background:var(--c-course)} .cd.faculty{background:var(--c-faculty)}
  .cd.scholarship{background:var(--c-scholar)} .cd.approval{background:var(--c-approval)} .cd.intern{background:var(--c-intern)}
  .statein{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:4px 12px;width:150px;
           outline:none;background:#fff;color:var(--ink);font-family:var(--sans)}
  .statein:focus{border-color:var(--blue2)}
  .statein::placeholder{color:var(--ink4)}
  .fspacer{flex:1}
  .suggests{display:none;padding:12px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap;gap:8px;align-items:center}
  .suggests.show{display:flex}
  .suggests .t{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--ink4)}
  .pill{font-size:12px;color:var(--ink3);background:var(--surface2);border:1px solid var(--line);
        border-radius:999px;padding:4px 12px;cursor:pointer;transition:all .12s}
  .pill:hover{border-color:var(--line2);color:var(--ink);background:#fff}
  .pill.r{margin-left:2px}
  .pill .x{color:var(--ink4);margin-left:6px;font-weight:700}
  .pill .x:hover{color:var(--err)}

  /* ── sections ── */
  .sec{margin-top:30px}
  .sechead{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;border-bottom:1px solid var(--line);padding-bottom:8px}
  .sechead h2{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.13em;color:var(--ink3)}
  .sechead .n{font-size:11px;font-weight:700;color:var(--navy);background:rgba(37,99,235,.09);
              border:1px solid rgba(37,99,235,.25);border-radius:999px;padding:1px 9px}
  .sechead .meta{margin-left:auto;font-size:11px;color:var(--ink4);font-variant-numeric:tabular-nums}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
        box-shadow:0 1px 2px rgba(15,23,42,.04)}

  /* answer */
  .answer{padding:20px 24px}
  .answer .atext{font-size:17.5px;font-weight:550;line-height:1.7;color:var(--ink);letter-spacing:-.01em}
  .answer .atext strong{font-weight:750;color:var(--ink)}
  .answer .atext em{color:var(--ink2)}
  .answer .atext code{font-family:var(--mono);font-size:12.5px;background:var(--surface3);border:1px solid var(--line);
                      border-radius:4px;padding:1px 5px;color:var(--ink2)}
  .answer .atext table{border-collapse:collapse;margin:10px 0;font-size:12.5px;width:100%}
  .answer .atext th{text-align:left;font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;
                    color:var(--ink3);background:var(--surface2);border:1px solid var(--line);padding:6px 10px}
  .answer .atext td{border:1px solid var(--line);padding:6px 10px;color:var(--ink2)}
  .answer .cite{font-family:var(--mono);font-size:12px;font-weight:600;color:var(--navy);
                background:rgba(37,99,235,.07);border:1px solid rgba(37,99,235,.25);border-radius:5px;
                padding:1px 6px;margin:0 1px;white-space:nowrap}
  .ameta{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
  .tag{font-size:10px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;padding:3px 9px;border-radius:5px}
  .tag.groq{background:rgba(30,58,138,.08);color:var(--navy);border:1px solid rgba(30,58,138,.25)}
  .tag.mock{background:rgba(180,83,9,.08);color:var(--warn);border:1px solid rgba(180,83,9,.3)}
  .tag.lat{background:var(--surface2);color:var(--ink3);border:1px solid var(--line);text-transform:none;font-weight:600}
  .tag.mono{font-family:var(--mono);background:var(--surface2);color:var(--ink3);border:1px solid var(--line);text-transform:none;font-weight:600}

  /* transparency panel */
  details.how{margin-top:14px;border:1px solid var(--line);border-radius:10px;background:var(--surface2);overflow:hidden}
  details.how summary{padding:11px 16px;cursor:pointer;font-size:12px;font-weight:700;color:var(--ink3);
                      list-style:none;display:flex;align-items:center;gap:8px;user-select:none}
  details.how summary::-webkit-details-marker{display:none}
  details.how summary .tri{font-size:9px;color:var(--ink4);transition:transform .15s}
  details.how[open] summary .tri{transform:rotate(90deg)}
  details.how summary:hover{color:var(--ink)}
  .howbody{padding:4px 16px 16px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
  @media(max-width:760px){.howbody{grid-template-columns:1fr}}
  .hsec h4{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--ink4);margin-bottom:7px}
  .hsec .kv{display:flex;gap:8px;align-items:baseline;font-size:12.5px;padding:2px 0}
  .hsec .kv b{color:var(--ink2);font-weight:600;min-width:86px}
  .hsec .kv span{font-family:var(--mono);color:var(--ink2);font-size:12px;word-break:break-all}
  pre.sql{background:#0f172a;color:#cbd5e1;border-radius:8px;padding:12px 14px;font-family:var(--mono);
          font-size:12px;line-height:1.6;overflow-x:auto;white-space:pre-wrap;word-break:break-all}

  /* summary bar */
  .sumbar{display:flex;align-items:center;gap:14px;padding:13px 18px;flex-wrap:wrap;margin-bottom:14px}
  .sumbar .total{font-size:20px;font-weight:800;letter-spacing:-.02em;color:var(--ink);font-variant-numeric:tabular-nums}
  .sumbar .totall{font-size:11px;color:var(--ink4);text-transform:uppercase;letter-spacing:.08em;font-weight:700}
  .brk{display:flex;gap:6px;flex-wrap:wrap}
  .brkchip{font-size:11.5px;font-weight:600;color:var(--ink2);background:var(--surface2);border:1px solid var(--line);
           border-radius:6px;padding:3px 9px;display:inline-flex;align-items:center;gap:6px}
  .brkchip .b{font-family:var(--mono);font-weight:700;font-variant-numeric:tabular-nums}
  .brkchip .bd{width:7px;height:7px;border-radius:2px}
  .pathbadges{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
  .pbadge{font-size:10.5px;font-weight:700;color:var(--ink3);background:#fff;border:1px solid var(--line);
          border-radius:5px;padding:3px 8px;display:inline-flex;align-items:center;gap:5px}
  .pbadge.sem{border-color:rgba(14,116,144,.35);color:#0e7490;background:rgba(14,116,144,.06)}
  .pbadge.sql{border-color:rgba(37,99,235,.35);color:var(--navy);background:rgba(37,99,235,.06)}
  .pbadge .ic{font-size:10px}
  .lat{margin-left:auto;font-size:11px;color:var(--ink4);font-variant-numeric:tabular-nums;font-family:var(--mono)}

  /* sql table */
  .sqlsec{margin-bottom:20px}
  .sqlsec .card{padding:0}
  .sqlhead{display:flex;align-items:center;gap:10px;padding:12px 18px;border-bottom:1px solid var(--line);background:var(--surface2)}
  .sqlhead b{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--ink3)}
  .sqlhead .n{font-family:var(--mono);font-size:11px;color:var(--navy)}
  .sqlhead .q{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--ink4);max-width:55%;
              overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tblwrap{overflow-x:auto}
  table.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
  table.tbl th{text-align:left;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;
               color:var(--ink4);padding:8px 18px;border-bottom:1px solid var(--line);background:var(--surface2);white-space:nowrap}
  table.tbl td{padding:9px 18px;border-bottom:1px solid var(--surface3);color:var(--ink2);white-space:nowrap}
  table.tbl tr:last-child td{border-bottom:none}
  table.tbl tr:hover td{background:var(--surface2)}
  table.tbl td.num{font-variant-numeric:tabular-nums;color:var(--navy);font-weight:600}

  /* entity groups */
  .group{margin-bottom:26px}
  .ghead{display:flex;align-items:center;gap:9px;margin-bottom:8px}
  .gdot{width:9px;height:9px;border-radius:3px}
  .ghead h3{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--ink2)}
  .ghead .n{font-family:var(--mono);font-size:11px;color:var(--ink4)}
  .row{display:flex;gap:0;background:var(--surface);border:1px solid var(--line);border-radius:10px;
       margin-bottom:8px;transition:border-color .12s, box-shadow .12s}
  .row:hover{border-color:var(--line2);box-shadow:0 3px 10px rgba(15,23,42,.07)}
  .row .accent{width:3px;border-radius:10px 0 0 10px;flex-shrink:0}
  .row .main{flex:1;padding:13px 16px 12px;min-width:0}
  .row .title{font-size:15px;font-weight:650;color:var(--ink);letter-spacing:-.01em}
  .row .title .eid{font-family:var(--mono);font-size:10.5px;color:var(--ink4);font-weight:500;margin-left:8px}
  .facts{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:9px}
  .fact{display:flex;gap:7px;align-items:baseline;font-size:12px}
  .fact .l{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--ink4)}
  .fact .v{color:var(--ink2);font-weight:550}
  .fact .v.mono{font-family:var(--mono);font-size:11.5px}
  .row .side{display:flex;flex-direction:column;align-items:flex-end;justify-content:center;gap:8px;
             padding:12px 14px 12px 6px;flex-shrink:0}
  .score{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--ink3);background:var(--surface2);
         border:1px solid var(--line);border-radius:5px;padding:2px 7px;font-variant-numeric:tabular-nums}
  .score.hi{color:#15803d;border-color:rgba(21,128,61,.3)} .score.md{color:#b45309;border-color:rgba(180,83,9,.3)}
  .citebtn{font-family:var(--mono);font-size:10.5px;color:var(--ink3);background:#fff;border:1px solid var(--line);
           border-radius:5px;padding:3px 8px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;
           max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:all .12s}
  .citebtn:hover{border-color:var(--navy);color:var(--navy)}
  .citebtn .c{color:var(--ink4)}

  /* states */
  .skeleton .sk{background:linear-gradient(90deg,var(--surface3) 25%,#e6ebf3 45%,var(--surface3) 65%);
                background-size:400% 100%;animation:shimmer 1.3s infinite;border-radius:6px}
  @keyframes shimmer{0%{background-position:100% 0}100%{background-position:0 0}}
  .skrow{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:8px}
  .skrow .sk.title{height:15px;width:38%}
  .skrow .sk.fact{height:11px;width:26%;margin-top:10px;display:block}
  .skrow .sk.fact2{height:11px;width:18%;margin-top:6px;display:block}
  .empty{padding:44px 20px;text-align:center}
  .empty .ic{font-size:30px;margin-bottom:8px}
  .empty b{font-size:15px;color:var(--ink2)}
  .empty p{font-size:12.5px;color:var(--ink3);margin:5px 0 14px}
  .errbox{padding:16px 20px;border-left:3px solid var(--err);background:#fdf2f4;border-radius:8px}
  .errbox b{color:var(--err);font-size:13px}
  .errbox p{font-size:12.5px;color:var(--ink2);margin-top:4px}
  .errbox details{margin-top:10px}
  .errbox summary{font-size:11px;color:var(--ink4);cursor:pointer}
  .errbox pre{font-family:var(--mono);font-size:11px;background:#fff;border:1px solid var(--line);
              padding:10px;border-radius:6px;margin-top:8px;overflow-x:auto;color:var(--ink2)}

  /* modal */
  .overlay{position:fixed;inset:0;background:rgba(15,23,42,.5);backdrop-filter:blur(2px);
           display:none;align-items:flex-start;justify-content:center;padding:48px 20px;z-index:100;overflow-y:auto}
  .overlay.show{display:flex}
  .modal{background:#fff;border-radius:14px;width:100%;max-width:760px;box-shadow:0 24px 64px rgba(15,23,42,.3);
         overflow:hidden;animation:pop .18s ease}
  @keyframes pop{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:none}}
  .mhead{display:flex;align-items:center;gap:12px;padding:16px 22px;border-bottom:1px solid var(--line);background:var(--surface2)}
  .mhead b{font-size:14px;color:var(--ink)}
  .mhead .mid{font-family:var(--mono);font-size:12px;color:var(--ink4)}
  .mhead .close{margin-left:auto;border:none;background:transparent;font-size:20px;color:var(--ink4);cursor:pointer;line-height:1}
  .mhead .close:hover{color:var(--err)}
  .mbody{padding:18px 22px 22px}
  .mbody h5{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--ink4);margin:16px 0 8px}
  .mbody h5:first-child{margin-top:0}
  .mgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 26px}
  @media(max-width:640px){.mgrid{grid-template-columns:1fr}}
  .mgrid .kv{display:flex;justify-content:space-between;gap:14px;padding:5px 0;border-bottom:1px dashed var(--surface3);font-size:12.5px}
  .mgrid .kv b{color:var(--ink3);font-weight:600}
  .mgrid .kv span{color:var(--ink2);font-family:var(--mono);font-size:11.5px;text-align:right;word-break:break-all}
  table.lg{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:4px}
  table.lg th{text-align:left;font-size:9.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink4);padding:5px 8px;border-bottom:1px solid var(--line)}
  table.lg td{padding:6px 8px;border-bottom:1px solid var(--surface3);color:var(--ink2);font-family:var(--mono);font-size:11px}
  table.lg td b{color:var(--navy)}

  footer{margin-top:44px;display:flex;gap:18px;flex-wrap:wrap;font-size:11px;color:var(--ink4)}
  footer a{color:var(--ink3);text-decoration:none;border-bottom:1px dotted var(--line2)}
  footer a:hover{color:var(--navy)}
</style>
</head>
<body>
<header>
  <div class="head">
    <div class="brand">
      <div class="mark">A</div>
      <div>
        <h1>AICTE Unified Search</h1>
        <div class="sub">Canonical data layer · hybrid retrieval · PG + pgvector + Groq</div>
      </div>
    </div>
    <div class="coverage" id="coverage"></div>
    <div class="badge" id="sysBadge"><span class="dot warn"></span> connecting…</div>
  </div>
</header>

<div class="wrap">

  <!-- search -->
  <div class="panel">
    <div class="searchrow">
      <input id="q" type="text" autocomplete="off" spellcheck="false" aria-label="Search query">
      <button class="go" id="goBtn" onclick="ask()">Search</button>
    </div>
    <div class="filters">
      <span class="flabel">Entity</span>
      <span class="chip on" data-et=""><span class="cd" style="background:var(--ink4)"></span>all</span>
      <span class="chip" data-et="institution"><span class="cd inst"></span>institution</span>
      <span class="chip" data-et="course"><span class="cd course"></span>course</span>
      <span class="chip" data-et="faculty"><span class="cd faculty"></span>faculty</span>
      <span class="chip" data-et="scholarship"><span class="cd scholarship"></span>scholarship</span>
      <span class="chip" data-et="approval"><span class="cd approval"></span>approval</span>
      <span class="chip" data-et="internship"><span class="cd intern"></span>internship</span>
      <span style="width:14px"></span>
      <span class="flabel">State</span>
      <input class="statein" id="state" type="text" placeholder="any state">
      <span style="width:14px"></span>
      <span class="flabel">Status</span>
      <span class="chip on" data-ap="">any</span>
      <span class="chip" data-ap="approved">approved</span>
      <span class="chip" data-ap="not_approved">not approved</span>
      <span class="fspacer"></span>
      <span class="flabel" style="margin-right:0">press <span style="font-family:var(--mono)">/</span> to focus</span>
    </div>
    <div class="suggests" id="suggests"></div>
  </div>

  <!-- answer -->
  <section class="sec" id="answerSec" style="display:none">
    <div class="sechead"><h2>Answer</h2><span class="n" id="answerTag">—</span>
      <span class="meta" id="answerMeta"></span></div>
    <div class="card">
      <div class="answer"><p class="atext" id="answerText"></p></div>
      <div class="ameta" id="answerChips"></div>
      <details class="how" id="howPanel">
        <summary><span class="tri">▶</span> How this was answered — routing, SQL, retrieval parameters</summary>
        <div class="howbody" id="howBody"></div>
      </details>
    </div>
  </section>

  <!-- results -->
  <section class="sec" id="resultsSec" style="display:none">
    <div class="sechead"><h2>Results</h2><span class="meta" id="resultsMeta"></span></div>
    <div class="card sumbar" id="sumbar"></div>
    <div id="results"></div>
  </section>

  <!-- idle -->
  <section class="sec" id="idleSec">
    <div class="card empty">
      <div class="ic">▦</div>
      <b>Search the AICTE canonical data layer</b>
      <p>Ask a natural-language question, or pick an example. Results are grouped by entity type<br>
         with lineage citations on every record.</p>
      <div id="idlePills" style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center"></div>
    </div>
  </section>

  <!-- loading -->
  <section class="sec" id="loadingSec" style="display:none">
    <div class="skeleton">
      <div class="card" style="padding:18px 20px"><div class="sk" style="height:16px;width:52%"></div>
        <div class="sk" style="height:12px;width:34%;margin-top:12px;display:block"></div></div>
      <div style="height:14px"></div>
      <div class="sk skrow"><div class="sk title"></div><span class="sk fact"></span><span class="sk fact2"></span></div>
      <div class="sk skrow"><div class="sk title" style="width:30%"></div><span class="sk fact"></span><span class="sk fact2"></span></div>
      <div class="sk skrow"><div class="sk title" style="width:44%"></div><span class="sk fact"></span><span class="sk fact2"></span></div>
    </div>
  </section>

  <!-- error -->
  <section class="sec" id="errorSec" style="display:none">
    <div class="card errbox" id="errorBox"></div>
  </section>

  <footer>
    <span>Phase 4 · PostgreSQL facts + pgvector HNSW (384-dim) + Groq</span>
    <a href="/docs">API docs</a><a href="/api">endpoints</a><a href="/health">health</a>
    <span id="foot"></span>
  </footer>
</div>

<!-- lineage modal -->
<div class="overlay" id="overlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="mhead"><b id="mTitle">Entity</b><span class="mid" id="mId"></span>
      <button class="close" onclick="closeModal()">×</button></div>
    <div class="mbody" id="mBody"><div class="empty" style="padding:24px"><p>Loading lineage…</p></div></div>
  </div>
</div>

<script>
/* ───────────────────────── helpers ───────────────────────── */
const $ = id => document.getElementById(id);
const esc = s => { const d=document.createElement('div'); d.textContent=(s==null?'':String(s)); return d.innerHTML; };
const fmt = n => (n==null?'—':Number(n).toLocaleString('en-IN'));
const inr = v => { const n=Number(v); return isNaN(n) ? (v==null?'—':esc(v)) : '₹ '+n.toLocaleString('en-IN'); };

const EXAMPLES = [
  "scholarships for meritorious students in Tamil Nadu",
  "How many approved engineering colleges are there in Uttar Pradesh?",
  "courses with intake above 120 in Telangana",
  "Which colleges offer M.Tech Computer Science?",
  "faculty at IIT Delhi",
];
const PLACEHOLDERS = [
  "Ask the data layer… e.g. scholarships for meritorious students in Tamil Nadu",
  "Try: approved engineering colleges in Maharashtra",
  "Try: courses with intake above 120 in Telangana",
  "Try: faculty at IIT Delhi with PhD qualifications",
];

const TYPES = { institution:'institution', course:'course', faculty:'faculty',
                scholarship:'scholarship', approval:'approval', internship:'internship' };
const TCOLOR = { institution:'var(--c-inst)', course:'var(--c-course)', faculty:'var(--c-faculty)',
                 scholarship:'var(--c-scholar)', approval:'var(--c-approval)', internship:'var(--c-intern)' };
const TLABEL = { institution:'Institutions', course:'Courses', faculty:'Faculty',
                 scholarship:'Scholarships', approval:'Approvals', internship:'Internships' };

const TITLE_FIELD = { institution:'institution_name', course:'course_name',
                      faculty:'faculty_name', scholarship:'scheme_name',
                      internship:'domain' };
/* [field, label, mono? | formatter] */
const FACTS = {
  institution: [['state','State'],['district','District'],['institute_type','Type'],
                ['year_established','Est.'],['approval_status','Status',v=>v?'Approved':'Not approved'],
                ['naac_grade','NAAC'],['nirf_rank','NIRF']],
  course: [['department','Dept.'],['duration_years','Duration',v=>v? v+' yrs':'—'],
           ['intake_capacity','Intake'],['fee_per_year','Fee / yr',inr]],
  faculty: [['designation','Designation'],['qualification','Qualification'],
            ['department','Dept.'],['date_joined','Joined']],
  scholarship: [['administering_body','Body'],['amount','Amount'],['applicable_states','States']],
  approval: [['approval_type','Type'],['nba_status','NBA'],['valid_until','Valid until'],
             ['state','State'],['closure_year','Closure']],
  internship: [['organization_name','Org'],['duration_weeks','Weeks'],['stipend_amount','Stipend',inr],
               ['mode','Mode'],['is_ppo_linked','PPO',v=>v?'yes':'no'],['program_source','Program']],
};

/* ───────────────────────── state ───────────────────────── */
let et='', ap='';
const recent = JSON.parse(localStorage.getItem('aicte_recent')||'[]');

function saveRecent(q){
  const list = [q, ...recent.filter(x=>x!==q)].slice(0,6);
  localStorage.setItem('aicte_recent', JSON.stringify(list));
  recent.length=0; recent.push(...list);
  renderSuggests();
}

/* ───────────────────────── search UI ───────────────────────── */
$('q').addEventListener('input', () => { renderSuggests(); });
$('q').addEventListener('focus', () => { renderSuggests(); });
document.addEventListener('keydown', e => {
  if(e.key==='/' && document.activeElement!==$('q')){ e.preventDefault(); $('q').focus(); }
  if(e.key==='Enter' && document.activeElement===$('q')) ask();
});
document.querySelectorAll('.chip[data-et]').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('.chip[data-et]').forEach(x=>x.classList.remove('on'));
  c.classList.add('on'); et=c.dataset.et; }));
document.querySelectorAll('.chip[data-ap]').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('.chip[data-ap]').forEach(x=>x.classList.remove('on'));
  c.classList.add('on'); ap=c.dataset.ap; }));

/* rotating placeholder */
let phIdx=0, phTimer;
function rotatePh(){
  const q=$('q');
  if(!q.value){ q.placeholder=PLACEHOLDERS[phIdx%PLACEHOLDERS.length]; phIdx++; }
}
phTimer=setInterval(rotatePh, 4000); rotatePh();
$('q').addEventListener('input', ()=>{ clearInterval(phTimer); });

function renderSuggests(){
  const box=$('suggests');
  const q=$('q').value.trim();
  const show = !q;
  box.classList.toggle('show', show);
  if(!show) return;
  let html='<span class="t">Recent</span>';
  if(recent.length){
    html += recent.map(r=>`<span class="pill r" onclick="useQuery('${esc(r).replace(/'/g,"\\'")}')">${esc(r)}<span class="x" onclick="event.stopPropagation();removeRecent('${esc(r).replace(/'/g,"\\'")}')">×</span></span>`).join('');
  } else {
    html += '<span class="pill" style="cursor:default;color:var(--ink4)">none yet</span>';
  }
  html += '<span class="t" style="margin-left:10px">Try</span>';
  html += EXAMPLES.map(e=>`<span class="pill r" onclick="useQuery('${esc(e).replace(/'/g,"\\'")}')">${esc(e)}</span>`).join('');
  box.innerHTML=html;
}
function useQuery(q){ $('q').value=q; $('suggests').classList.remove('show'); ask(); }
function removeRecent(q){
  const list=recent.filter(x=>x!==q);
  localStorage.setItem('aicte_recent', JSON.stringify(list));
  recent.length=0; recent.push(...list); renderSuggests();
}
renderSuggests();

/* ───────────────────────── api ───────────────────────── */
function params(){
  const p=new URLSearchParams();
  p.set('q', $('q').value.trim());
  if(et) p.set('entity_type', et);
  if($('state').value.trim()) p.set('state', $('state').value.trim());
  if(ap) p.set('approval_status', ap);
  p.set('top_k','6');
  return p;
}
async function get(url){
  const r=await fetch(url);
  if(!r.ok){ let m='HTTP '+r.status; try{ m=(await r.json()).detail||m; }catch(e){} throw new Error(m); }
  return r.json();
}
function setBusy(on){
  $('goBtn').disabled=on;
  $('goBtn').textContent=on?'Searching…':'Search';
}

/* ───────────────────────── render: answer ───────────────────────── */
/* minimal markdown for the LLM answer: tables, bold, italics, inline code */
function mdInline(s){
  return s
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g,'$1<em>$2</em>')
    .replace(/`([^`]+)`/g,'<code>$1</code>');
}
function mdTable(rows){
  const cells=r=>r.replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>c.trim());
  const data=rows.map(cells);
  let head=data[0]||[], body=data.slice(1);
  if(body.length && body[0].every(c=>/^:?-{2,}:?$/.test(c))) body=body.slice(1);
  const h='<thead><tr>'+head.map(c=>`<th>${mdInline(c)}</th>`).join('')+'</tr></thead>';
  const b='<tbody>'+body.map(r=>'<tr>'+r.map(c=>`<td>${mdInline(c)}</td>`).join('')+'</tr>').join('')+'</tbody>';
  return `<table>${h}${b}</table>`;
}
function mdAnswer(t){
  const safe=esc(t);
  const cite=safe.replace(/\[Source:\s*([^\]]+)\]/g,'<span class="cite">[src: $1]</span>');
  const lines=cite.split(String.fromCharCode(10));
  let html='', buf=[];
  const flush=()=>{ if(buf.length){ html+=mdTable(buf); buf=[]; } };
  for(const ln of lines){
    const tr=ln.trim();
    if(tr.startsWith('|')&&tr.endsWith('|')&&tr.indexOf('|',1)>-1){ buf.push(tr); }
    else{ flush(); html+=(tr? mdInline(ln)+'<br>':''); }
  }
  flush();
  return html;
}
function renderAnswer(d, ms){
  $('answerSec').style.display='';
  $('answerText').innerHTML=mdAnswer(d.answer);
  $('answerTag').textContent=(d.llm==='groq'?'Groq LLM':'Mock synth');
  $('answerTag').className='tag '+(d.llm==='groq'?'groq':'mock');
  $('answerMeta').textContent=fmt(ms)+' ms';
  $('answerChips').innerHTML=`<span class="tag mono">${esc(d.query)}</span>`;
}

function renderHow(d){
  const f=d.filters||{};
  const chip=v=>v==null?'—':esc(String(v));
  let filters='';
  if(f.entity_type) filters+=`<div class="kv"><b>Entity</b><span>${chip(f.entity_type)}</span></div>`;
  if(f.state) filters+=`<div class="kv"><b>State</b><span>${chip(f.state)}</span></div>`;
  if(f.approval_status!=null) filters+=`<div class="kv"><b>Status</b><span>${f.approval_status?'approved':'not approved'}</span></div>`;
  if(!filters) filters='<div class="kv"><span style="color:var(--ink4)">none</span></div>';
  const path=d.rule_matched ? 'deterministic rules (no LLM)' :
    (d.sql_query ? 'LLM text-to-SQL + semantic (pgvector)' : 'semantic only (pgvector)');
  const sql = d.sql_query
    ? `<div class="hsec" style="grid-column:1/-1"><h4>SQL executed</h4>
        <pre class="sql">${esc(d.sql_query)}</pre></div>`
    : (d.rule_matched ? `<div class="hsec" style="grid-column:1/-1"><h4>Rule matched</h4>
        <pre class="sql">${esc(d.structured||'')}</pre></div>` : '');
  $('howBody').innerHTML=`
    <div class="hsec"><h4>Routing</h4>
      <div class="kv"><b>Type</b><span>${esc(d.query_type||'hybrid')}</span></div>
      <div class="kv"><b>Path</b><span>${esc(path)}</span></div>
      <div class="kv"><b>Top-k</b><span>${d.retrieval?d.retrieval.top_k:'6'}</span></div>
      <div class="kv"><b>Embedding</b><span>all-MiniLM-L6-v2 · 384d</span></div>
    </div>
    <div class="hsec"><h4>Filters applied</h4>${filters}</div>${sql}`;
}

/* ───────────────────────── render: summary ───────────────────────── */
function renderSummary(d, ms){
  const brk=(d.breakdown||{});
  const order=['institution','course','faculty','scholarship','approval','internship'];
  const chips=order.filter(t=>brk[t]).map(t=>
    `<span class="brkchip"><span class="bd" style="background:${TCOLOR[t]}"></span>${TLABEL[t]}
     <span class="b">${brk[t]}</span></span>`).join('');
  const sqlN=d.sql_rows?d.sql_rows.length:0;
  let badges='';
  if(d.rule_matched) badges+=`<span class="pbadge sql"><span class="ic">≡</span> exact match</span>`;
  if(sqlN) badges+=`<span class="pbadge sql"><span class="ic">⌕</span> SQL · ${sqlN} rows</span>`;
  if(d.vector&&d.vector.length) badges+=`<span class="pbadge sem"><span class="ic">≋</span> semantic · ${d.vector.length}</span>`;
  $('sumbar').innerHTML=`
    <div><div class="totall">Total matches</div><div class="total">${d.count}</div></div>
    <div class="brk">${chips}</div>
    <div class="pathbadges">${badges}</div>
    <div class="lat">${fmt(ms)} ms</div>`;
  $('resultsMeta').textContent=`query · ${fmt(ms)} ms`;
}

/* ───────────────────────── render: groups ───────────────────────── */
function titleOf(hit){
  const rec=hit.record||{};
  const f=TITLE_FIELD[hit.entity_type];
  return (f&&rec[f]) || rec.nba_status || hit.entity_type + ' ' + hit.entity_id;
}
function factsHtml(hit){
  const rec=hit.record||{};
  const defs=FACTS[hit.entity_type]||[];
  const items=defs.map(([f,l,opt])=>{
    let v=rec[f];
    if(v==null||v==='') return '';
    if(typeof opt==='function') v=opt(v);
    const monoCls=(opt===1||f==='aicte_code')?' mono':'';
    return `<div class="fact"><span class="l">${l}</span><span class="v${monoCls}">${esc(v)}</span></div>`;
  }).filter(Boolean);
  if(!items.length && hit.context_text){
    const txt=(hit.context_text||'').replace(/\s*\[Source:[^\]]+\]\s*$/,'');
    return `<div class="fact"><span class="v" style="font-size:12px;color:var(--ink3);max-width:640px">${esc(txt)}</span></div>`;
  }
  return items.join('');
}
function renderGroup(t, hits){
  const rows=hits.map(h=>{
    const s=h.similarity||0;
    const scoreCls=s>=.7?'hi':s>=.5?'md':'';
    const db=h.source_database||'canonical';
    const rid=h.source_record_id!=null?' · r'+h.source_record_id:'';
    return `<div class="row">
      <div class="accent" style="background:${TCOLOR[t]}"></div>
      <div class="main">
        <div class="title">${esc(titleOf(h))}<span class="eid">${esc(h.entity_id)}</span></div>
        <div class="facts">${factsHtml(h)}</div>
      </div>
      <div class="side">
        <span class="score ${scoreCls}">${Math.round(s*100)}%</span>
        <button class="citebtn" onclick="openLineage('${esc(h.entity_id)}')">
          <span class="c">src</span> ${esc(db)}${esc(rid)}</button>
      </div>
    </div>`;
  }).join('');
  return `<div class="group">
    <div class="ghead"><span class="gdot" style="background:${TCOLOR[t]}"></span>
      <h3>${TLABEL[t]}</h3><span class="n">${hits.length}</span></div>
    ${rows}
  </div>`;
}
function renderSql(d){
  if(!d.sql_rows||!d.sql_rows.length) return '';
  const cols=Object.keys(d.sql_rows[0]);
  const head=cols.map(c=>`<th>${esc(c)}</th>`).join('');
  const body=d.sql_rows.slice(0,15).map(r=>`<tr>${cols.map(c=>{
    const v=r[c]; const isNum=typeof v==='number';
    return `<td${isNum?' class="num"':''}>${esc(v)}</td>`;
  }).join('')}</tr>`).join('');
  const more=d.sql_rows.length>15?`<tr><td colspan="${cols.length}" style="color:var(--ink4);font-size:11px;padding:8px 18px">… ${d.sql_rows.length-15} more rows</td></tr>`:'';
  return `<div class="sqlsec"><div class="card">
    <div class="sqlhead"><b>SQL result</b><span class="n">${d.sql_rows.length} rows</span>
      <span class="q" title="${esc(d.sql_query||'')}">${esc(d.sql_query||'')}</span></div>
    <div class="tblwrap"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${body}${more}</tbody></table></div>
  </div></div>`;
}
function renderGroups(d){
  const order=['institution','course','faculty','scholarship','approval','internship'];
  const groups={};
  (d.vector||[]).forEach(h=>{ (groups[h.entity_type]=groups[h.entity_type]||[]).push(h); });
  const html=order.filter(t=>groups[t]).map(t=>renderGroup(t,groups[t])).join('');
  return renderSql(d)+html;
}

/* ───────────────────────── render: states ───────────────────────── */
function renderEmpty(d){
  $('resultsSec').style.display='';
  $('sumbar').innerHTML=`<div class="brkchip" style="padding:6px 12px"><b>0 results</b></div>
    <div class="pathbadges">${d.rule_matched?'<span class="pbadge sql">exact match</span>':''}</div>`;
  const sugg=EXAMPLES.map(e=>`<span class="pill" onclick="useQuery('${esc(e).replace(/'/g,"\\'")}')">${esc(e)}</span>`).join('');
  $('results').innerHTML=`<div class="card empty"><div class="ic">∅</div><b>No results found</b>
    <p>No indexed record matches your query with the current filters.<br>Try reformulating or removing a filter.</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center">${sugg}</div></div>`;
}
function renderError(e){
  $('errorSec').style.display='';
  $('errorBox').innerHTML=`<b>Search failed</b><p>Something went wrong while retrieving or synthesizing the answer.</p>
    <details><summary>Technical details</summary><pre>${esc(e.message||e)}</pre></details>`;
}

/* ───────────────────────── main flow ───────────────────────── */
async function ask(){
  const q=$('q').value.trim(); if(!q) return;
  saveRecent(q);
  setBusy(true);
  $('idleSec').style.display='none';
  $('errorSec').style.display='none';
  $('answerSec').style.display='none';
  $('resultsSec').style.display='none';
  $('loadingSec').style.display='';
  const t0=performance.now();
  try{
    const [a,s]=await Promise.all([get('/answer?'+params()), get('/search?'+params())]);
    const ms=Math.round(performance.now()-t0);
    $('loadingSec').style.display='none';
    renderAnswer(a,ms);
    renderHow(s);
    if(s.count||s.sql_rows||s.rule_matched){
      renderSummary(s,ms);
      $('results').innerHTML=renderGroups(s);
      $('resultsSec').style.display='';
    } else {
      renderEmpty(s);
    }
    window.scrollTo({top:0,behavior:'smooth'});
  }catch(e){
    $('loadingSec').style.display='none';
    renderError(e);
  }finally{ setBusy(false); }
}

/* ───────────────────────── lineage modal ───────────────────────── */
async function openLineage(id){
  const ov=$('overlay'); ov.classList.add('show');
  $('mId').textContent=id;
  $('mTitle').textContent='Entity lineage';
  $('mBody').innerHTML='<div class="empty" style="padding:24px"><p>Loading lineage…</p></div>';
  try{
    const d=await get('/entity/'+encodeURIComponent(id));
    const rec=d.record||{};
    $('mTitle').textContent=TLABEL[d.entity_type]||'Entity';
    const kv=Object.entries(rec).map(([k,v])=>
      `<div class="kv"><b>${esc(k)}</b><span>${esc(v==null?'—':String(v))}</span></div>`).join('');
    const mapRows=(d.mapping||[]).map(m=>
      `<tr><td><b>${esc(m.source_system)}</b></td><td>${esc(m.source_database)}</td><td>${esc(m.source_table)}</td><td>${esc(m.source_record_id)}</td><td>${m.match_score!=null?m.match_score.toFixed(2):'—'}</td></tr>`).join('');
    const lgRows=(d.lineage||[]).map(l=>
      `<tr><td><b>${esc(l.source_system)}</b></td><td>${esc(l.source_database)}</td><td>${esc(l.source_table)}</td><td>${esc(l.source_record_id)}</td><td>${esc(l.validation_status||'')}</td></tr>`).join('');
    $('mBody').innerHTML=`
      <h5>Canonical record · ${esc(d.entity_type)}</h5>
      <div class="mgrid">${kv||'<p style="color:var(--ink4)">No structured record</p>'}</div>
      <h5>Source mapping (entity_mapping)</h5>
      <table class="lg"><thead><tr><th>system</th><th>database</th><th>table</th><th>record</th><th>score</th></tr></thead>
      <tbody>${mapRows||'<tr><td colspan="5" style="color:var(--ink4)">none</td></tr>'}</tbody></table>
      <h5>Data lineage</h5>
      <table class="lg"><thead><tr><th>system</th><th>database</th><th>table</th><th>record</th><th>status</th></tr></thead>
      <tbody>${lgRows||'<tr><td colspan="5" style="color:var(--ink4)">none</td></tr>'}</tbody></table>`;
  }catch(e){
    $('mBody').innerHTML=`<div class="errbox"><b>Lineage unavailable</b><p>${esc(e.message||e)}</p></div>`;
  }
}
function closeModal(){ $('overlay').classList.remove('show'); }
document.addEventListener('keydown', e=>{ if(e.key==='Escape') closeModal(); });

/* ───────────────────────── boot ───────────────────────── */
(async function(){
  try{
    const h=await get('/health');
    const c=h.coverage||{};
    $('coverage').innerHTML=[
      ['Institutions', c.institution],['Courses', c.course],['Faculty', c.faculty],
      ['Scholarships', c.scholarship],['Internships', c.internship],['Indexed', c.indexed_records],
    ].map(([l,v])=>`<div class="cov"><div class="v">${fmt(v)}</div><div class="l">${l}</div></div>`).join('');
    const ok=h.database.ok&&h.llm.configured;
    const badge=$('sysBadge');
    badge.innerHTML=`<span class="dot ${ok?'ok':'warn'}"></span>`+
      (ok?`groq · ${esc(h.llm.model)}`:'mock LLM (no key)');
    badge.title=h.database.detail;
    $('foot').textContent=' · '+h.database.detail;
  }catch(e){
    $('sysBadge').innerHTML='<span class="dot err"></span> api unreachable';
  }
  const urlQ=new URLSearchParams(location.search).get('q');
  if(urlQ){ $('q').value=urlQ; ask(); }
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Research Paper RAG + AI Detection Endpoints
# ---------------------------------------------------------------------------
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from paper_ai_endpoints import router as paper_ai_router
    app.include_router(paper_ai_router, tags=["Research Papers", "AI Detection"])
    print("[api] Research Paper RAG + AI Detection endpoints loaded")
except Exception as _e:
    print(f"[api] WARNING: Could not load paper/AI endpoints: {_e}")
