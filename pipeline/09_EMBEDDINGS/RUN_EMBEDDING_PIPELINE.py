"""
Standalone runner: builds the internships_df exactly like MAIN.py's 06b step,
then pushes it into pgvector via EMBEDDING_PIPELINE.process_internship_records.
Kept separate from MAIN.py so you can test the DB write in isolation first.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ["01_INGESTION", "03_SCHEMA_MAPPING", "04_STANDARDIZATION",
               "05_NORMALIZATION", "06_DEDUPLICATION"]:
    sys.path.insert(0, str(ROOT / folder))

from dotenv import load_dotenv
load_dotenv(ROOT.parent / ".env")   # project root (DB credentials)
load_dotenv(ROOT / ".env")          # pipeline overrides (POSTGRES_DB, model)

import pandas as pd
from INGESTION_ENGINE import ingest_all
from SCHEMA_MAPPER import map_all
from STANDARDIZER import standardize_all
from NORMALIZER import normalize_all
from ENTITY_RESOLVER import match_entities, assign_internship_ids
from EMBEDDING_PIPELINE import process_internship_records

sample_dir = ROOT / "DATA" / "SAMPLE"
normalized = normalize_all(standardize_all(map_all(ingest_all(sample_dir))))
combined = pd.concat(normalized.values(), ignore_index=True)

internship_cols = ["student_name", "company", "duration", "internship_year",
                    "performance_remark", "institution_name",
                    "source_database", "source_table", "source_record_id"]
internships_df = combined.dropna(subset=["student_name", "company"])[internship_cols]
internships_df["internship_year"] = internships_df["internship_year"].astype(int)
internships_df = assign_internship_ids(internships_df)

process_internship_records(internships_df)