"""
07_CONTEXT_CLASSIFICATION — decide, per field, whether it belongs in
PostgreSQL (structured/relational) or pgvector (contextual).

Deterministic rules first (cheap, fast, auditable); LLM fallback only for
genuinely ambiguous field names. This mirrors the design doc's Section 11.
"""
from pathlib import Path

import yaml

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "13_CONFIG" / "CANONICAL_SCHEMA.yaml"

# Field-name patterns that are contextual almost by definition.
CONTEXTUAL_NAME_HINTS = {
    "remark", "remarks", "comment", "comments", "description",
    "feedback", "observation", "observations", "additional_information",
    "research_interests", "performance_remark",
}

AMBIGUOUS_FALLBACK_NEEDED = {"notes", "summary", "details"}  # -> LLM classifier


def load_field_types() -> dict[str, str]:
    """Flatten CANONICAL_SCHEMA.yaml into {field_name: 'structured'|'relational'|'contextual'}."""
    schema = yaml.safe_load(SCHEMA_PATH.read_text())
    field_types = {}
    for entity_fields in schema.values():
        for field, definition in entity_fields.items():
            field_types[field] = definition["type"]
    return field_types


def classify_field(field_name: str, known_types: dict[str, str]) -> str:
    if field_name in known_types:
        return known_types[field_name]
    lname = field_name.lower()
    if lname in CONTEXTUAL_NAME_HINTS or any(hint in lname for hint in CONTEXTUAL_NAME_HINTS):
        return "contextual"
    if lname in AMBIGUOUS_FALLBACK_NEEDED:
        return llm_classify_field(field_name)  # TODO: wire to Anthropic API
    return "structured"  # safe default for unrecognized, non-text-like fields


def llm_classify_field(field_name: str) -> str:
    """
    TODO: call the Anthropic API with a short prompt like:
    "Classify this AICTE database field as structured, relational, or
    contextual: '{field_name}'. Reply with one word."
    Kept as a stub so the deterministic path never blocks on network/API cost.
    """
    return "contextual"  # conservative default until wired up


def classify_columns(columns: list[str]) -> dict[str, str]:
    known = load_field_types()
    return {col: classify_field(col, known) for col in columns}


if __name__ == "__main__":
    sample_columns = [
        "institution_id", "institution_name", "state", "approval_status",
        "performance_remark", "research_interests", "notes", "random_field",
    ]
    for col, cls in classify_columns(sample_columns).items():
        print(f"{col:25s} -> {cls}")
