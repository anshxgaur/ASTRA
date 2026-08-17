"""
05_NORMALIZATION — cast fields into their canonical dtypes (per
13_CONFIG/CANONICAL_SCHEMA.yaml) and drop the source-specific columns that
have already served their purpose (e.g. after mapping+standardization).
"""
from pathlib import Path

import pandas as pd
import yaml

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "13_CONFIG" / "CANONICAL_SCHEMA.yaml"

def _to_number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


DTYPE_CASTERS = {
    "string": str,
    "integer": lambda v: int(v) if pd.notna(v) else None,
    "number": _to_number,
    "boolean": lambda v: bool(v) if pd.notna(v) else None,
}


def load_canonical_schema() -> dict:
    return yaml.safe_load(SCHEMA_PATH.read_text())


def normalize_dataframe(df: pd.DataFrame, canonical_schema: dict) -> pd.DataFrame:
    df = df.copy()
    all_fields = {}
    for entity_fields in canonical_schema.values():
        all_fields.update(entity_fields)

    for col in df.columns:
        field_def = all_fields.get(col)
        if not field_def or "dtype" not in field_def:
            continue
        caster = DTYPE_CASTERS.get(field_def["dtype"])
        if caster:
            df[col] = df[col].apply(lambda v: caster(v) if pd.notna(v) else None)
    return df

    # TODO: enforce not-null / valid-range rules here via Great Expectations


def normalize_all(standardized_sources: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    schema = load_canonical_schema()
    return {name: normalize_dataframe(df, schema) for name, df in standardized_sources.items()}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_INGESTION"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_SCHEMA_MAPPING"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "04_STANDARDIZATION"))
    from INGESTION_ENGINE import ingest_all
    from SCHEMA_MAPPER import map_all
    from STANDARDIZER import standardize_all

    sample_dir = Path(__file__).resolve().parents[1] / "DATA" / "SAMPLE"
    normalized = normalize_all(standardize_all(map_all(ingest_all(sample_dir))))
    for name, df in normalized.items():
        print(name, df.dtypes.to_dict())
