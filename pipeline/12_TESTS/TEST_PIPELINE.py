"""
12_TESTS — smoke tests that run the pipeline stages against sample data.
Run with: pytest 12_TESTS/TEST_PIPELINE.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ["01_INGESTION", "03_SCHEMA_MAPPING", "04_STANDARDIZATION",
               "05_NORMALIZATION", "06_DEDUPLICATION", "07_CONTEXT_CLASSIFICATION"]:
    sys.path.insert(0, str(ROOT / folder))

from INGESTION_ENGINE import ingest_all
from SCHEMA_MAPPER import map_all
from STANDARDIZER import standardize_all
from NORMALIZER import normalize_all
from ENTITY_RESOLVER import match_entities
from FIELD_CLASSIFIER import classify_columns

SAMPLE_DIR = ROOT / "DATA" / "SAMPLE"


def test_ingestion_produces_lineage_columns():
    raw = ingest_all(SAMPLE_DIR)
    for df in raw.values():
        for col in ("source_system", "source_record_id", "ingestion_timestamp"):
            assert col in df.columns


def test_schema_mapping_produces_canonical_columns():
    mapped = map_all(ingest_all(SAMPLE_DIR))
    for df in mapped.values():
        assert "institution_name" in df.columns


def test_standardization_normalizes_states():
    std = standardize_all(map_all(ingest_all(SAMPLE_DIR)))
    for df in std.values():
        assert "MH" not in df["state"].values
        assert "DL" not in df["state"].values


def test_entity_resolution_assigns_ids():
    import pandas as pd
    normalized = normalize_all(standardize_all(map_all(ingest_all(SAMPLE_DIR))))
    combined = pd.concat(normalized.values(), ignore_index=True)
    resolved = match_entities(combined)
    assert "master_entity_id" in resolved.columns
    assert resolved["master_entity_id"].notna().all()


def test_field_classifier_flags_remarks_as_contextual():
    result = classify_columns(["performance_remark", "institution_name"])
    assert result["performance_remark"] == "contextual"
    assert result["institution_name"] == "structured"


def test_entity_resolution_merges_all_iit_delhi_variants():
    """
    Regression test: SOURCE_C's state field is deliberately malformed
    ("uttar pradesh's neighbor - Delhi"). All three IIT Delhi records
    (mysql/postgres/mongo) must resolve to the SAME master_entity_id, and
    the same for the two IIT Bombay variants — i.e. 6 source rows should
    collapse to exactly 2 master entities, not 3.
    """
    import pandas as pd
    normalized = normalize_all(standardize_all(map_all(ingest_all(SAMPLE_DIR))))
    combined = pd.concat(normalized.values(), ignore_index=True)
    resolved = match_entities(combined)

    assert resolved["master_entity_id"].nunique() == 2

    delhi_ids = resolved[resolved["institution_name"].str.contains("Delhi", case=False)]["master_entity_id"]
    assert delhi_ids.nunique() == 1

    bombay_ids = resolved[resolved["institution_name"].str.contains("Bombay", case=False)]["master_entity_id"]
    assert bombay_ids.nunique() == 1
