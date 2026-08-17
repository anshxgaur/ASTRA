"""
09_EMBEDDINGS — glue script: contextual fields -> context text -> embedding -> pgvector.

process_entity_records() is generic: give it a dataframe plus a context-builder
callable and an entity type, and every row becomes one context_document.
process_internship_records() keeps the original internship-specific wrapper.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "10_PGVECTOR"))

from CONTEXT_BUILDER import (  # noqa: E402
    build_approval_context, build_course_context, build_faculty_context,
    build_institution_context, build_internship_context, build_scholarship_context,
)
from EMBEDDING_GENERATOR import embed_texts  # noqa: E402
from VECTOR_STORE import get_connection, insert_context  # noqa: E402


def process_entity_records(df, entity_type: str, entity_id_col: str,
                           context_builder, context_type: str) -> int:
    """Embed every row of `df` and store it as a context_document."""
    if df is None or len(df) == 0:
        return 0

    conn = get_connection()
    texts = [context_builder(row) for _, row in df.iterrows()]
    embeddings = embed_texts(texts)

    for i, ((_, row), embedding) in enumerate(zip(df.iterrows(), embeddings)):
        insert_context(
            conn,
            entity_id=str(row[entity_id_col]),
            entity_type=entity_type,
            context_type=context_type,
            context_text=texts[i],
            embedding=embedding,
            source_database=row.get("source_database"),
            source_table=row.get("source_table"),
            source_record_id=row.get("source_record_id"),
        )
    conn.close()
    return len(df)


def process_internship_records(internships_df):
    """
    Takes a dataframe of internship rows (domain, institution_name,
    organization_name, duration_weeks, stipend_amount, mode, is_ppo_linked,
    program_source, description + lineage cols) and pushes each one into
    pgvector as a context document (context_type="description").
    """
    if internships_df is None or len(internships_df) == 0:
        return 0
    conn = get_connection()
    inserted = 0
    for _, row in internships_df.iterrows():
        context_text = build_internship_context(row)
        embedding = embed_texts([context_text])[0]
        insert_context(
            conn,
            entity_id=row["internship_id"],
            entity_type="internship",
            context_type="description",
            context_text=context_text,
            embedding=embedding,
            source_database=row.get("source_database"),
            source_table=row.get("source_table"),
            source_record_id=row.get("source_record_id"),
        )
        inserted += 1
    conn.close()
    print(f"[09] embedded {inserted} internships -> context_document")
    return inserted


def process_all_entities(frames: dict) -> dict[str, int]:
    """frames: {name: dataframe}; embeds institutions/courses/faculty/
    scholarships/approvals/internships into pgvector."""
    spec = {
        "institutions": ("institution", "master_entity_id", build_institution_context, "profile"),
        "courses": ("course", "course_id", build_course_context, "course"),
        "faculty": ("faculty", "faculty_id", build_faculty_context, "faculty"),
        "scholarships": ("scholarship", "scholarship_id", build_scholarship_context, "eligibility"),
        "approvals": ("approval", "approval_id", build_approval_context, "approval"),
    }
    counts = {}
    for name, (etype, id_col, builder, ctype) in spec.items():
        df = frames.get(name)
        if df is None or len(df) == 0:
            counts[name] = 0
            continue
        counts[name] = process_entity_records(df, etype, id_col, builder, ctype)
        print(f"[09] embedded {counts[name]} {name} -> context_document")
    counts["internships"] = process_internship_records(frames.get("internships"))
    return counts


if __name__ == "__main__":
    print("This script expects a live Postgres+pgvector connection (.env) "
          "and entity frames; call process_all_entities(frames) with them.")
