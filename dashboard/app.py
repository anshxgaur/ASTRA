"""
Dashboard — live visualization of the AICTE pipeline (Phases 1-3).

Run:
    internalenv/Scripts/python.exe -m uvicorn dashboard.app:app --port 8000
Then open http://localhost:8000

/api/status returns:
  - Phase 1: live row counts per source + ground-truth planted issues
  - Phase 2: the last pipeline run report (per-stage status/timing/rows/errors)
  - Phase 3: live aicte_canonical table + pgvector counts
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # Internal_Matte/
PIPELINE_ROOT = PROJECT_ROOT / "pipeline"
REPORT_PATH = PIPELINE_ROOT / "run_reports" / "last_run.json"
PERF_LOG_PATH = PIPELINE_ROOT / "run_reports" / "api_performance.jsonl"

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PIPELINE_ROOT / ".env")

app = FastAPI(title="AICTE Unified Search — Pipeline Dashboard")

CORE_TABLES = ["institution", "course", "faculty", "scholarship", "approval", "internship"]
DB_TIMEOUT_SECONDS = 2.5


def _run_with_timeout(fn, timeout: float = DB_TIMEOUT_SECONDS):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(f"database call timed out after {timeout}s") from exc


# ---------------------------------------------------------------------------
# Live data collectors (each one degrades gracefully to ok=False)
# ---------------------------------------------------------------------------

def _pg_canonical_counts() -> dict:
    import psycopg

    def _fetch() -> dict:
        with psycopg.connect(
            host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
            user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
            dbname=os.environ.get("POSTGRES_DB", "aicte_canonical"),
            connect_timeout=2,
        ) as conn:
            with conn.cursor() as cur:
                out = {"tables": {}}
                for t in CORE_TABLES:
                    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                    out["tables"][t] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*), COALESCE(vector_dims(embedding), 0) "
                            "FROM context_document GROUP BY 2")
                row = cur.fetchone()
                out["context_document"] = {"rows": row[0] if row else 0,
                                           "dims": row[1] if row else 0}
                cur.execute("""
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'context_document' AND indexname = 'idx_context_embedding'
                """)
                out["hnsw_index"] = cur.fetchone() is not None
                return out
    return _run_with_timeout(_fetch)


def _phase1_sources() -> list[dict]:
    import pandas as pd
    import psycopg
    import pymysql
    from pymongo import MongoClient

    sources = []

    def mysql_count() -> int:
        conn = pymysql.connect(
            host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
            user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
            database=os.environ["MYSQL_DATABASE"], connect_timeout=2, read_timeout=2, write_timeout=2)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM institutes")
                return cur.fetchone()[0]
        finally:
            conn.close()

    def pg_count(table: str, db: str) -> int:
        with psycopg.connect(
            host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
            user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
            dbname=db, connect_timeout=2,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                return cur.fetchone()[0]

    def mongo_count() -> int:
        client = MongoClient(os.environ["MONGO_HOST"], int(os.environ["MONGO_PORT"]),
                             serverSelectionTimeoutMS=2000)
        try:
            return client[os.environ["MONGO_DB"]]["scholarships"].count_documents({})
        finally:
            client.close()

    def csv_count(fname: str) -> int:
        return len(pd.read_csv(PROJECT_ROOT / "data" / "legacy" / fname, dtype=str))

    specs = [
        ("MySQL institutes", "MySQL 8", ":3307", lambda: mysql_count()),
        ("PG courses", "PostgreSQL 16", ":5433", lambda: pg_count("courses", os.environ["COURSES_DB"])),
        ("PG faculty", "PostgreSQL 16", ":5433", lambda: pg_count("faculty", os.environ["FACULTY_DB"])),
        ("Mongo scholarships", "MongoDB 7", ":27017", lambda: mongo_count()),
        ("Legacy CSVs (3 files)", "Flat files", "data/legacy/",
         lambda: sum(csv_count(f) for f in
                     ["nba_autonomous_status.csv", "closed_institutes.csv", "unapproved_list.csv"])),
        ("Internships CSV", "Flat file", "data/internships.csv",
         lambda: len(pd.read_csv(PROJECT_ROOT / "data" / "internships.csv", dtype=str))),
    ]
    for name, tech, where, fn in specs:
        try:
            rows = _run_with_timeout(fn, timeout=DB_TIMEOUT_SECONDS)
            sources.append({"name": name, "tech": tech, "where": where,
                            "rows": rows, "ok": True, "error": ""})
        except Exception as exc:  # noqa: BLE001
            sources.append({"name": name, "tech": tech, "where": where,
                            "rows": None, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return sources


def _ground_truth() -> dict:
    gt = json.loads((PROJECT_ROOT / "conflicts_seeded.json").read_text(encoding="utf-8"))
    return {
        "conflicts": len(gt.get("cross_source_conflicts", [])),
        "duplicates": len(gt.get("within_source_duplicates", [])),
        "orphans": len(gt.get("orphaned_records", [])),
    }


def _registry_count() -> int:
    reg = json.loads((PROJECT_ROOT / "institute_registry.json").read_text(encoding="utf-8"))
    return len(reg.get("institutes", []))


def _last_run_report() -> dict | None:
    if REPORT_PATH.exists():
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return None


def _llm_configured() -> dict:
    key = os.environ.get("GROQ_API_KEY", "")
    model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    configured = bool(key and key not in ("your_key_here", "gsk_...", ""))
    return {"provider": "groq", "configured": configured, "model": model}


def _rate(rows: int | None, seconds: float | None) -> float | None:
    if rows is None or not seconds:
        return None
    return round(rows / seconds, 2)


def _performance_matrix(report: dict | None) -> dict:
    if not report:
        return {"summary": {}, "stages": [], "groups": []}

    stages = []
    for stage in report.get("stages", []):
        rows = stage.get("rows")
        seconds = stage.get("seconds")
        stages.append({
            "name": stage.get("name", ""),
            "status": stage.get("status", "unknown"),
            "seconds": seconds,
            "rows": rows,
            "rows_per_sec": _rate(rows, seconds),
            "note": stage.get("note", ""),
        })

    def group(names: list[str]) -> dict:
        picked = [s for s in stages if any(s["name"].startswith(name) for name in names)]
        seconds = round(sum(float(s.get("seconds") or 0) for s in picked), 2)
        rows = sum(int(s.get("rows") or 0) for s in picked)
        return {
            "name": " + ".join(names),
            "seconds": seconds,
            "rows": rows,
            "rows_per_sec": _rate(rows, seconds),
            "stages": len(picked),
        }

    groups = [
        {"label": "Ingestion", **group(["01"])},
        {"label": "Transform pipeline", **group(["02", "03", "04", "05", "06", "07"])},
        {"label": "Canonical load", **group(["08"])},
        {"label": "Embeddings", **group(["09"])},
        {"label": "Vector index", **group(["10"])},
    ]
    total_seconds = report.get("duration_seconds") or sum(float(s.get("seconds") or 0) for s in stages)
    slowest = max(stages, key=lambda s: s.get("seconds") or 0, default={})
    return {
        "summary": {
            "status": report.get("status"),
            "total_seconds": round(float(total_seconds), 2),
            "source_rows": sum(v.get("rows", 0) for v in report.get("sources", {}).values()),
            "entities_out": report.get("entity_resolution", {}).get("entities_out"),
            "embedding_rows": report.get("phase3", {}).get("context_document", {}).get("rows"),
            "slowest_stage": slowest.get("name"),
            "slowest_seconds": slowest.get("seconds"),
        },
        "stages": stages,
        "groups": groups,
    }


def _api_performance(limit: int = 200) -> dict:
    if not PERF_LOG_PATH.exists():
        return {
            "events": [],
            "summary": {"total": 0, "ok": 0, "errors": 0, "avg_ms": None, "p95_ms": None},
            "by_endpoint": {},
            "by_path": {},
        }

    lines = PERF_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    durations = [float(e.get("duration_ms", 0)) for e in events if e.get("duration_ms") is not None]
    durations_sorted = sorted(durations)
    p95 = None
    if durations_sorted:
        idx = min(len(durations_sorted) - 1, int(round((len(durations_sorted) - 1) * 0.95)))
        p95 = round(durations_sorted[idx], 2)

    def bucket(key: str) -> dict:
        out = {}
        for event in events:
            name = event.get(key) or "unknown"
            out.setdefault(name, []).append(event)
        return {
            name: {
                "count": len(items),
                "avg_ms": round(mean(float(i.get("duration_ms", 0)) for i in items), 2),
                "errors": sum(1 for i in items if i.get("status") != "ok"),
            }
            for name, items in out.items()
        }

    return {
        "events": events[-20:],
        "summary": {
            "total": len(events),
            "ok": sum(1 for e in events if e.get("status") == "ok"),
            "errors": sum(1 for e in events if e.get("status") != "ok"),
            "avg_ms": round(mean(durations), 2) if durations else None,
            "p95_ms": p95,
            "llm": events[-1].get("llm") if events else None,
        },
        "by_endpoint": bucket("endpoint"),
        "by_path": bucket("path"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/status")
def api_status() -> dict:
    report = _last_run_report()
    phase3 = {"ok": False, "error": "", **({} or {"tables": {}})}
    try:
        phase3 = _pg_canonical_counts()
        phase3["ok"] = True
    except Exception as exc:  # noqa: BLE001
        phase3["error"] = f"{type(exc).__name__}: {exc}"

    return {
        "phase1": {
            "registry": _registry_count(),
            "ground_truth": _ground_truth(),
            "sources": _phase1_sources(),
        },
        "phase2": report,
        "phase3": phase3,
        "performance": _performance_matrix(report),
        "phase4": {
            "llm_configured": _llm_configured(),
            "api_performance": _api_performance(),
        },
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return HTML


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AICTE Pipeline Dashboard</title>
<style>
  :root { --ok:#22c55e; --err:#ef4444; --warn:#f59e0b; --bg:#0f172a; --card:#1e293b;
          --line:#334155; --txt:#e2e8f0; --dim:#94a3b8; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--txt); font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif; padding:24px; }
  h1 { font-size:20px; }
  h2 { font-size:14px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); margin:28px 0 12px; }
  .top { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .pill { padding:4px 12px; border-radius:999px; font-size:13px; font-weight:600; }
  .pill.ok { background:#052e16; color:var(--ok); border:1px solid var(--ok); }
  .pill.err { background:#450a0a; color:var(--err); border:1px solid var(--err); }
  .pill.warn { background:#451a03; color:var(--warn); border:1px solid var(--warn); }
  .meta { color:var(--dim); font-size:12px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }
  .card .name { font-size:13px; color:var(--dim); }
  .card .big { font-size:26px; font-weight:700; margin-top:4px; }
  .card .sub { font-size:11px; color:var(--dim); margin-top:4px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  .dot.ok { background:var(--ok); } .dot.err { background:var(--err); } .dot.warn { background:var(--warn); }
  .flow { display:flex; gap:6px; flex-wrap:wrap; align-items:stretch; }
  .stage { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; min-width:130px; }
  .stage .sname { font-size:12px; font-weight:600; }
  .stage .sinfo { font-size:11px; color:var(--dim); margin-top:4px; line-height:1.45; }
  .stage.ok { border-top:3px solid var(--ok); } .stage.err { border-top:3px solid var(--err); }
  .stage.degraded { border-top:3px solid var(--warn); }
  .arrow { align-self:center; color:var(--dim); font-size:18px; }
  .issue { background:#3b0d0d; border:1px solid #7f1d1d; border-radius:8px; padding:10px 14px; font-size:13px; margin-top:10px; }
  .chip { display:inline-block; background:var(--card); border:1px solid var(--line); border-radius:999px;
          padding:3px 10px; font-size:12px; margin:2px 4px 2px 0; }
  .err { color:var(--err); font-size:12px; }
  .matrix { display:grid; gap:12px; }
  .matrix-summary { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:12px; }
  .matrix-group { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }
  .matrix-group-title { font-size:12px; color:var(--dim); margin-bottom:8px; }
  .matrix-group-bar { height:8px; background:#0b1220; border-radius:999px; overflow:hidden; border:1px solid var(--line); }
  .matrix-group-fill { height:100%; background:linear-gradient(90deg,#22c55e,#38bdf8); border-radius:999px; }
  .matrix-row { display:grid; grid-template-columns: 180px minmax(140px,1fr) 140px; gap:10px; align-items:center; padding:8px 0; border-bottom:1px solid var(--line); }
  .matrix-row:last-child { border-bottom:none; }
  .matrix-name { font-size:12px; color:var(--txt); }
  .matrix-bar { background:#0b1220; height:8px; border-radius:999px; overflow:hidden; border:1px solid var(--line); }
  .matrix-fill { height:100%; border-radius:999px; background:linear-gradient(90deg,#22c55e,#f59e0b,#ef4444); }
  .matrix-metric { font-size:11px; color:var(--dim); text-align:right; }
  footer { margin-top:32px; color:var(--dim); font-size:12px; }
  code { background:#0b1220; padding:1px 5px; border-radius:4px; font-size:11px; }
</style>
</head>
<body>
  <div class="top">
    <h1>AICTE Unified Search — Pipeline Dashboard</h1>
    <span id="statusPill" class="pill warn">loading…</span>
    <span class="meta" id="meta"></span>
  </div>

  <h2>Phase 1 — Fragmented sources (live)</h2>
  <div class="grid" id="phase1"></div>
  <div id="groundTruth"></div>

  <h2>Phase 2 — Pipeline run (last execution)</h2>
  <div id="phase2"></div>

  <h2>Performance Matrix</h2>
  <div id="performanceMatrix"></div>

  <h2>Phase 3 — Canonical store (live)</h2>
  <div class="grid" id="phase3"></div>

  <footer>Auto-refreshes every 5 s &nbsp;·&nbsp; run pipeline with <code>internalenv/Scripts/python.exe pipeline/MAIN.py</code>
  &nbsp;·&nbsp; dashboard: <code>internalenv/Scripts/python.exe -m uvicorn dashboard.app:app --port 8000</code></footer>

<script>
const $ = id => document.getElementById(id);
function esc(s){ const d=document.createElement('div'); d.textContent=(s??'').toString(); return d.innerHTML; }

function card(name, big, sub, cls){ return `<div class="card"><div class="name">${esc(name)}</div>
  <div class="big" style="color:${cls==='err'?'var(--err)':'inherit'}">${big}</div>
  <div class="sub">${esc(sub)}</div></div>`; }

function renderPhase1(p1){
  let html = p1.sources.map(s =>
    card(`${s.ok?'':'⚠ '}${s.name}`, s.ok ? s.rows.toLocaleString() : '—',
         `${s.tech} · ${s.where}${s.ok?'': ' · '+s.error}`, s.ok?'ok':'err')).join('');
  $('phase1').innerHTML = html;
  const gt = p1.ground_truth;
  $('groundTruth').innerHTML = `<div style="margin-top:10px">
    <span class="chip">Registry: <b>${p1.registry}</b> canonical institutes</span>
    <span class="chip">Planted conflicts: <b>${gt.conflicts}</b></span>
    <span class="chip">Planted duplicates: <b>${gt.duplicates}</b></span>
    <span class="chip">Orphaned records: <b>${gt.orphans}</b></span>
  </div>`;
}

function renderPhase2(p2){
  if(!p2){ $('phase2').innerHTML = `<div class="issue">No pipeline run yet — run <code>pipeline/MAIN.py</code> first.</div>`; return; }
  const stages = (p2.stages||[]).map(s =>
    `<div class="stage ${esc(s.status)}">
       <div class="sname"><span class="dot ${esc(s.status)}"></span>${esc(s.name)}</div>
       <div class="sinfo">${esc(s.note||'')}<br>${s.seconds.toFixed(1)} s · ${s.rows!=null?'rows: '+s.rows.toLocaleString():''}</div>
     </div>`).join('<div class="arrow">→</div>');
  const er = p2.entity_resolution;
  const erLine = er ? ` <span class="chip">records in: <b>${er.records_in.toLocaleString()}</b></span>
    <span class="chip">master entities: <b>${er.entities_out.toLocaleString()}</b></span>` : '';
  const ctx = p2.phase3 && p2.phase3.context_document ? `<span class="chip">embeddings: <b>${p2.phase3.context_document.rows.toLocaleString()}</b></span>` : '';
  let issues = '';
  if(p2.status !== 'ok') issues = `<div class="issue"><b>${esc(p2.status.toUpperCase())}:</b> ${esc(p2.error)}</div>`;
  if(!p2.use_real_sources) issues += `<div class="issue"><b>WARNING:</b> sample-data mode — no DB load happened.</div>`;
  $('phase2').innerHTML = `<div class="flow">${stages}</div>
    <div style="margin-top:10px">${erLine}${ctx}</div>${issues}`;
}

function renderPerformance(perf){
  const box = $('performanceMatrix');
  if(!perf || (!perf.summary && !(perf.stages||[]).length && !(perf.groups||[]).length)){
    box.innerHTML = '<div class="issue">No performance metrics yet — run the pipeline to generate the report.</div>';
    return;
  }

  const summary = perf.summary || {};
  const stages = perf.stages || [];
  const groups = perf.groups || [];
  const maxGroup = Math.max(...groups.map(g => Number(g.seconds || 0)), 1);
  const maxStage = Math.max(...stages.map(s => Number(s.seconds || 0)), 1);

  const cards = [
    card('Total duration', `${Number(summary.total_seconds || 0).toFixed(1)} s`, 'Pipeline run time', summary.status === 'ok' ? 'ok' : 'warn'),
    card('Slowest stage', summary.slowest_stage || '—', summary.slowest_seconds ? `${Number(summary.slowest_seconds).toFixed(1)} s` : 'n/a', 'ok'),
    card('Source rows', summary.source_rows != null ? Number(summary.source_rows).toLocaleString() : '—', 'Across all seed sources', 'ok'),
    card('Entities out', summary.entities_out != null ? Number(summary.entities_out).toLocaleString() : '—', 'Entity resolution output', 'ok')
  ].join('');

  const groupHtml = groups.map(g => `
    <div class="matrix-group">
      <div class="matrix-group-title">${esc(g.label || g.name || 'Group')} · ${Number(g.seconds || 0).toFixed(1)} s</div>
      <div class="matrix-group-bar"><div class="matrix-group-fill" style="width:${Math.max((Number(g.seconds||0) / maxGroup) * 100, 5)}%"></div></div>
      <div style="margin-top:8px; color:var(--dim); font-size:11px;">${Number(g.rows || 0).toLocaleString()} rows · ${Number(g.rows_per_sec || 0).toFixed(2)} rows/s</div>
    </div>
  `).join('');

  const stageHtml = stages.map(s => {
    const pct = Math.max((Number(s.seconds || 0) / maxStage) * 100, 4);
    return `
      <div class="matrix-row">
        <div class="matrix-name"><span class="dot ${esc(s.status || 'warn')}"></span>${esc(s.name || 'Stage')}</div>
        <div class="matrix-bar"><div class="matrix-fill" style="width:${pct}%"></div></div>
        <div class="matrix-metric">${Number(s.seconds || 0).toFixed(1)} s<br>${(s.rows_per_sec != null ? Number(s.rows_per_sec).toFixed(2) : '0.00')} rows/s</div>
      </div>
    `;
  }).join('');

  box.innerHTML = `
    <div class="matrix">
      <div class="matrix-summary">${cards}</div>
      <div class="matrix-group" style="padding:12px 14px;">
        <div style="font-size:12px; color:var(--dim); margin-bottom:10px;">Pipeline groups</div>
        <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px;">${groupHtml}</div>
      </div>
      <div class="matrix-group">
        <div style="font-size:12px; color:var(--dim); margin-bottom:8px;">Stage timings</div>
        ${stageHtml}
      </div>
    </div>
  `;
}

function renderPhase3(p3){
  if(!p3.ok){ $('phase3').innerHTML = `<div class="issue">Canonical store unreachable: ${esc(p3.error)}</div>`; return; }
  const rows = p3.tables||{};
  let html = Object.entries(rows).map(([t,n]) => card(t, n.toLocaleString(), 'aicte_canonical')).join('');
  const cd = p3.context_document||{};
  html += card('pgvector context_document', (cd.rows||0).toLocaleString(),
    `${cd.dims} dims · HNSW index: ${p3.hnsw_index?'yes':'no'}`, 'ok');
  $('phase3').innerHTML = html;
}

function overallStatus(data){
  const p1down = (data.phase1.sources||[]).some(s=>!s.ok);
  if(data.phase2 && data.phase2.status==='error') return ['err','PIPELINE ERROR'];
  if(data.phase2 && data.phase2.status!=='ok') return ['warn','DEGRADED'];
  if(!data.phase3.ok) return ['warn','STORE UNREACHABLE'];
  if(p1down) return ['warn','SOURCE DOWN'];
  return ['ok','ALL SYSTEMS OK'];
}

async function refresh(){
  try{
    const r = await fetch('/api/status'); const data = await r.json();
    const [cls,label] = overallStatus(data);
    $('statusPill').className = 'pill '+cls; $('statusPill').textContent = label;
    $('meta').textContent = data.phase2 ? `last pipeline run: ${data.phase2.run_timestamp} (${data.phase2.duration_seconds}s)` : 'no pipeline run yet';
    renderPhase1(data.phase1); renderPhase2(data.phase2); renderPerformance(data.performance); renderPhase3(data.phase3);
  }catch(e){ $('statusPill').className='pill err'; $('statusPill').textContent='DASHBOARD ERROR'; $('meta').textContent=String(e); }
}
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>
"""
