"""
11_RETRIEVAL — the bridge between the data layer and the LLM.
Routes a natural-language question to Postgres (structured), pgvector
(contextual), or both (hybrid), fuses the results, and hands grounded
context to the LLM for answer synthesis. LLM never invents facts — it only
reasons over what retrieval hands it (design doc Rule 2).

Flow (hybrid, rules-first):
  1. small deterministic rules answer the questions they know (counts,
     listings) — fast, zero LLM cost;
  2. when no rule matches, the LLM (Groq) writes a safe SELECT against the
     canonical tables (TEXT_TO_SQL) and we execute it, AND the question is
     embedded and matched against pgvector;
  3. the Groq LLM synthesizes the final answer from the retrieved data only,
     citing sources. Without GROQ_API_KEY everything falls back to a mock
     synthesizer that still shows the real retrieved context and rankings.
"""
import os
import re
import sys
from pathlib import Path

import GROQ_CLIENT
import TEXT_TO_SQL

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PIPELINE_ROOT.parent


def _load_env() -> None:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PIPELINE_ROOT / ".env")


def classify_query_type(question: str) -> str:
    """Heuristic router: count/list questions are structured, else hybrid."""
    structured_markers = ("how many", "count", "list all", "which state", "rank")
    q = question.lower()
    if any(m in q for m in structured_markers):
        return "structured"
    return "hybrid"


def run_structured_query(question: str) -> str:
    """Grounded text-to-SQL over the canonical Postgres tables."""
    _load_env()
    sys.path.insert(0, str(PIPELINE_ROOT / "08_POSTGRESQL"))
    from DB_LOADER import get_connection

    q = question.lower().strip().rstrip("?").strip()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # how many [approved] [<adj>] colleges/institutions in <state>
            m = re.search(
                r"how many (approved )?([a-z ]+?)(?:colleges?|institutions?|institutes?) "
                r"(?:are there )?in ([a-z ]+)", q)
            if m:
                approved = bool(m.group(1))
                state = re.sub(r"[^a-z ]", "", m.group(3)).strip().title()
                sql = "SELECT COUNT(*) FROM institution WHERE lower(state) = %s"
                params = [state.lower()]
                if approved:
                    sql += " AND approval_status = TRUE"
                cur.execute(sql, params)
                n = cur.fetchone()[0]
                return f"{n} {'approved ' if approved else ''}college(s) in {state}"

            # list [all] colleges/institutions in <state>
            m = re.search(r"list (?:all )?(colleges?|institutions?|institutes?) in ([a-z ]+)", q)
            if m:
                state = re.sub(r"[^a-z ]", "", m.group(2)).strip().title()
                cur.execute(
                    "SELECT institution_name FROM institution "
                    "WHERE lower(state) = %s ORDER BY institution_name LIMIT 20",
                    (state.lower(),),
                )
                names = [r[0] for r in cur.fetchall()]
                if not names:
                    return f"No colleges found in {state}"
                return f"{len(names)} college(s) in {state}: " + "; ".join(names)

            # how many courses at <institute>
            m = re.search(r"how many courses (?:are )?(?:offered )?at (.+)", q)
            if m:
                inst = m.group(1).strip()
                cur.execute(
                    "SELECT COUNT(*) FROM course c "
                    "JOIN institution i ON c.institution_id = i.institution_id "
                    "WHERE i.institution_name ILIKE %s",
                    (f"%{inst}%",),
                )
                return f"{cur.fetchone()[0]} course(s) matching '{inst}'"

            # how many faculty at <institute>
            m = re.search(r"how many faculty (?:are )?at (.+)", q)
            if m:
                inst = m.group(1).strip()
                cur.execute(
                    "SELECT COUNT(*) FROM faculty f "
                    "JOIN institution i ON f.institution_id = i.institution_id "
                    "WHERE i.institution_name ILIKE %s",
                    (f"%{inst}%",),
                )
                return f"{cur.fetchone()[0]} faculty member(s) matching '{inst}'"

            return "[structured retrieval: no rule matched this question]"
    finally:
        conn.close()


ENTITY_TABLE_PK = {
    "institution": ("institution", "institution_id"),
    "course": ("course", "course_id"),
    "faculty": ("faculty", "faculty_id"),
    "scholarship": ("scholarship", "scholarship_id"),
    "approval": ("approval", "approval_id"),
    "internship": ("internship", "internship_id"),
}


def run_vector_query(question: str, top_k: int = 5,
                     entity_type: str | None = None, state: str | None = None,
                     approval_status: bool | None = None) -> list[dict]:
    """Embed the question, then find the closest contextual documents in pgvector."""
    _load_env()
    sys.path.insert(0, str(PIPELINE_ROOT / "09_EMBEDDINGS"))
    sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))
    from EMBEDDING_GENERATOR import embed_texts
    from VECTOR_STORE import get_connection, similarity_search

    query_embedding = embed_texts([question])[0]
    conn = get_connection()
    results = similarity_search(conn, query_embedding, top_k=top_k,
                                entity_type=entity_type, state=state,
                                approval_status=approval_status)
    conn.close()
    return results


def _enrich_hits(hits: list[dict]) -> list[dict]:
    """Attach the canonical record for every hit (entity name + structured facts)."""
    sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))
    from VECTOR_STORE import get_connection

    conn = get_connection()
    try:
        for hit in hits:
            pair = ENTITY_TABLE_PK.get(hit.get("entity_type"))
            record = None
            if pair:
                table, pk = pair
                with conn.cursor() as cur:
                    cur.execute(f'SELECT * FROM "{table}" WHERE "{pk}" = %s',
                                (hit["entity_id"],))
                    cols = [d[0] for d in cur.description]
                    row = cur.fetchone()
                    record = dict(zip(cols, row)) if row else None
            hit["record"] = record
            hit["path"] = "semantic"
    finally:
        conn.close()
    return hits


def _dedupe_hits(hits: list[dict]) -> list[dict]:
    """One card per entity — keep the highest-similarity context doc."""
    seen: set[str] = set()
    out = []
    for hit in sorted(hits, key=lambda h: h.get("similarity", 0), reverse=True):
        key = hit.get("entity_id")
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def _mock_synthesize(question: str, structured_result: str, contextual_results: list[dict]) -> str:
    """Demo-mode answer synthesis — no LLM call."""
    if not contextual_results and not structured_result.strip("[]").strip():
        return f'No grounded data was found to answer: "{question}"'

    lines = ["Answer (mock synthesis, no LLM call — retrieved context only):", ""]

    if structured_result and "no rule matched" not in structured_result and "not yet wired up" not in structured_result:
        lines.append(f"Structured (PostgreSQL): {structured_result}")
        lines.append("")

    if contextual_results:
        lines.append("Retrieved context (pgvector):")
        for r in contextual_results:
            lines.append(f"  • [{r['entity_id']}, similarity={r['similarity']:.2f}] {r['context_text']}")

    return "\n".join(lines)


def synthesize_answer(question: str, structured_result: str, contextual_results: list[dict]) -> str:
    """Groq answer synthesis grounded ONLY in the retrieved data (with citations)."""
    context_block = "\n".join(f"- {r.get('context_text')}" for r in contextual_results) or "(none)"
    prompt = (
        f"Answer the question using ONLY the grounded data below. "
        f"The STRUCTURED DATA is the authoritative result of a SQL query against "
        f"the canonical store — answer directly from it when it answers the question. "
        f"CONTEXTUAL DATA is descriptive context retrieved by semantic similarity. "
        f"Cite which source each fact came from (the [Source: ...] markers). "
        f"If the data does not answer the question, say so. Do not invent facts.\n\n"
        f"QUESTION: {question}\n\n"
        f"STRUCTURED DATA (from PostgreSQL): {structured_result or '(none)'}\n\n"
        f"CONTEXTUAL DATA (from pgvector): {context_block}"
    )
    reply = GROQ_CLIENT.chat(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1000,
    )
    if reply is None:
        return _mock_synthesize(question, structured_result, contextual_results)
    return reply.strip()


def _format_structured_rows(rows: list[dict]) -> str:
    """Compact, JSON-ish rendering of text-to-SQL rows for the LLM prompt."""
    import json
    if not rows:
        return ""
    return "SQL result rows: " + json.dumps(rows[:8], default=str)


def llm_hybrid_answer(question: str, top_k: int = 5) -> str:
    """LLM-driven fallback: Groq writes the SQL, we execute it + pgvector, Groq synthesizes."""
    sql_rows, _sql = TEXT_TO_SQL.text_to_sql(question)
    contextual_results = run_vector_query(question, top_k=top_k)
    structured_result = _format_structured_rows(sql_rows) if sql_rows else ""
    return synthesize_answer(question, structured_result, contextual_results)


def answer_question(question: str, top_k: int = 5) -> str:
    """Rules first; if no rule matched, hand the question to the LLM (SQL + pgvector)."""
    structured_result = run_structured_query(question)
    rule_matched = bool(structured_result) and "no rule matched" not in structured_result
    if rule_matched:
        contextual_results = run_vector_query(question, top_k=top_k)
        return synthesize_answer(question, structured_result, contextual_results)
    return llm_hybrid_answer(question, top_k=top_k)


def hybrid_search(question: str, top_k: int = 5,
                  entity_type: str | None = None, state: str | None = None,
                  approval_status: str | None = None) -> dict:
    """
    Raw hybrid results for the API.
    - vector hits are enriched with their canonical record, deduped per entity,
      and tagged path="semantic";
    - when no rule matched, the LLM-written SQL and its exact text are returned
      (sql_rows + sql_query) for the transparency panel;
    - breakdown counts hits per entity type; filters echoes what was applied.
    """
    _load_env()
    approval_bool = None
    if approval_status in ("approved", "true", "1"):
        approval_bool = True
    elif approval_status in ("not_approved", "not approved", "false", "0"):
        approval_bool = False

    structured_result = run_structured_query(question)
    rule_matched = bool(structured_result) and "no rule matched" not in structured_result
    vector = run_vector_query(question, top_k=top_k, entity_type=entity_type, state=state,
                              approval_status=approval_bool)
    vector = _dedupe_hits(_enrich_hits(vector))

    sql_rows = None
    sql_query = None
    if not rule_matched:
        sql_rows, sql_query = TEXT_TO_SQL.text_to_sql(question)

    breakdown: dict[str, int] = {}
    for hit in vector:
        t = hit.get("entity_type", "other")
        breakdown[t] = breakdown.get(t, 0) + 1

    return {
        "query": question,
        "query_type": classify_query_type(question),
        "rule_matched": rule_matched,
        "structured": structured_result if rule_matched else None,
        "sql_rows": sql_rows,
        "sql_query": sql_query,
        "vector": vector,
        "count": len(vector),
        "breakdown": breakdown,
        "filters": {
            "entity_type": entity_type,
            "state": state,
            "approval_status": approval_bool,
        },
        "retrieval": {
            "path": "rules" if rule_matched else ("sql+vector" if sql_rows else "vector"),
            "top_k": top_k,
        },
    }


if __name__ == "__main__":
    _load_env()
    print(answer_question("How many approved engineering colleges are there in Uttar Pradesh?"))
