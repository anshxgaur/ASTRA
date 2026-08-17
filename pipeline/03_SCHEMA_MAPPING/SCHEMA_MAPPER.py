"""
03_SCHEMA_MAPPING — rename each source's fields to the canonical AICTE
schema using the explicit mapping table in MAPPING_RULES.yaml. This is the
"semantic bridge" between legacy field names and the unified model.
"""
from pathlib import Path

import pandas as pd
import yaml

RULES_PATH = Path(__file__).parent / "MAPPING_RULES.yaml"


def load_mapping_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text())


def apply_mapping(df: pd.DataFrame, source_name: str, rules: dict) -> pd.DataFrame:
    mapping = rules.get(source_name, {})
    unmapped = [c for c in df.columns if c not in mapping and c not in
                {"source_system", "source_database", "source_table", "source_record_id", "ingestion_timestamp"}]
    if unmapped:
        # TODO: route these to 07_CONTEXT_CLASSIFICATION instead of silently dropping —
        # for the MVP they are kept as-is under their original name.
        pass
    return df.rename(columns=mapping)


def map_all(raw_sources: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    rules = load_mapping_rules()
    return {name: apply_mapping(df, name, rules) for name, df in raw_sources.items()}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_INGESTION"))
    from INGESTION_ENGINE import ingest_all

    sample_dir = Path(__file__).resolve().parents[1] / "DATA" / "SAMPLE"
    raw = ingest_all(sample_dir)
    mapped = map_all(raw)
    for name, df in mapped.items():
        print(f"\n=== {name} ===")
        print(df.columns.tolist())
