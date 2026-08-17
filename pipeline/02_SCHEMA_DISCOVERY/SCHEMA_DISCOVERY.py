"""
02_SCHEMA_DISCOVERY — inspect each ingested source and produce a metadata
profile: columns, inferred dtype, nullability, and sample values. This
profile feeds the schema mapping step (03) and the field classifier (07).
"""
import pandas as pd


def profile_dataframe(df: pd.DataFrame, source_name: str) -> list[dict]:
    profile = []
    for col in df.columns:
        series = df[col]
        try:
            samples = series.dropna().unique().tolist()[:3]
        except TypeError:
            # unhashable values (e.g. nested lists/dicts from Mongo) — just take the first few
            samples = series.dropna().tolist()[:3]
        profile.append({
            "source": source_name,
            "column": col,
            "dtype": str(series.dtype),
            "nullable": bool(series.isna().any()),
            "sample_values": samples,
        })
    return profile


def discover_all(raw_sources: dict[str, pd.DataFrame]) -> list[dict]:
    profiles = []
    for name, df in raw_sources.items():
        profiles.extend(profile_dataframe(df, name))
    return profiles

    # TODO: for MongoDB sources, also walk nested/embedded fields
    # (e.g. faculty[].research_interests) rather than flattening once.


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_INGESTION"))
    from INGESTION_ENGINE import ingest_all

    sample_dir = Path(__file__).resolve().parents[1] / "DATA" / "SAMPLE"
    raw = ingest_all(sample_dir)
    for row in discover_all(raw):
        print(row)
