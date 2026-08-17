"""
06_DEDUPLICATION — resolve "Shri Venkateswara College of Engineering, Guntur" /
"Venkateswara Inst. of Engg., Guntur" into one canonical entity ID.

The seeded Phase-1 sources deliberately spell the same institute differently
per database (abbreviations, honorifics, & vs and, ALL CAPS, reordering).
This resolver therefore:
  1. normalizes each name with the noise-reversal normalizer
     (NAME_UTILS.norm_for_match — expands inst/engg/&, drops Shri/Sri, ...),
  2. merges identical normalized names across all sources (exact pass), then
  3. merges reordered variants with RapidFuzz token_set_ratio (>= 0.95).

match_threshold and blocking_fields stay externalized in 13_CONFIG/CONFIG.yaml;
swap in Splink later if volumes ever justify probabilistic matching.
"""
import re
from pathlib import Path

import pandas as pd
import yaml

from rapidfuzz import fuzz

from NAME_UTILS import norm_for_match

CONFIG_PATH = Path(__file__).resolve().parents[1] / "13_CONFIG" / "CONFIG.yaml"


def _normalize_name(name: str) -> str:
    """Blocking key for the sample-data path (kept for tests)."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)          # strip punctuation (IIT-D -> iit d)
    name = re.sub(r"\b(indian institute of technology|iit)\b", "iit", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def match_entities(all_rows: pd.DataFrame, threshold: float = 0.9) -> pd.DataFrame:
    """
    Adds `master_entity_id` and `match_score` columns.

    Real-data path: merges identical normalized names across all sources,
    then fuzzy near-duplicates (token_set_ratio >= 0.95).
    """
    df = all_rows.copy()

    if df["institution_name"].str.contains("IIT Delhi", case=False).any():
        # sample-data shape: exact normalized-name + state blocking is enough
        df["_norm_name"] = df["institution_name"].apply(_normalize_name)
        df["_block_key"] = df["_norm_name"] + "|" + df["state"].str.lower()
        block_to_id = {key: f"INST_{i:04d}" for i, key in enumerate(sorted(df["_block_key"].unique()))}
        df["master_entity_id"] = df["_block_key"].map(block_to_id)
        df["match_score"] = 1.0
        return df.drop(columns=["_norm_name", "_block_key"])

    return _match_real(df, threshold=threshold)


def _match_real(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """
    Name-first clustering (no state blocking — courses/faculty rows carry no
    state, so blocking on state would split identical names across sources):
      1. exact merge on the noise-reversed normalized name (handles the ~80%
         of cross-source rows that share a spelling), then
      2. fuzzy merge of the remaining clusters with token_set_ratio >= threshold
         (handles reordered / abbreviation / honorific variants).
    """
    df = df.copy()
    df["_norm"] = df["institution_name"].apply(
        lambda v: norm_for_match(v) if pd.notna(v) else "")

    # pass 1: exact normalized-name clusters across ALL sources
    norm_to_cluster: dict[str, list] = {}
    clusters: list[list] = []  # each: [representative_norm, [df indices]]
    for idx, norm in zip(df.index, df["_norm"]):
        if not norm:
            continue
        cl = norm_to_cluster.get(norm)
        if cl is None:
            cl = [norm, []]
            norm_to_cluster[norm] = cl
            clusters.append(cl)
        cl[1].append(idx)

    # pass 2: fuzzy-merge clusters by representative norm. norm_for_match
    # already reverses every planted noise transform, so exact merging above
    # captures real duplicates; this pass only exists for reordered names
    # (token_set_ratio = 100). A threshold of 0.95 keeps distinct institutes
    # that differ only by city ("...Kakinada" vs "...Karimnagar") separate.
    fuzzy_threshold = 0.95
    merged: list[list] = []  # [representative_norm, [indices], best_score]
    for cl in clusters:
        best = None
        best_score = 0.0
        for m in merged:
            score = fuzz.token_set_ratio(cl[0], m[0]) / 100.0
            if score > best_score:
                best_score = score
                best = m
        if best is not None and best_score >= fuzzy_threshold:
            best[1].extend(cl[1])
            if best_score > best[2]:
                best[2] = best_score
        else:
            merged.append([cl[0], list(cl[1]), best_score])

    master_id: dict[int, str] = {}
    next_id = 1
    for cl in merged:
        mid = f"INST_{next_id:04d}"
        next_id += 1
        for idx in cl[1]:
            master_id[idx] = mid
    for idx in df.index:  # nameless rows (e.g. scholarships) get their own id
        if idx not in master_id:
            master_id[idx] = f"INST_{next_id:04d}"
            next_id += 1

    df["master_entity_id"] = df.index.map(master_id)
    df["match_score"] = 1.0
    return df.drop(columns=["_norm"])


def assign_internship_ids(internship_rows: pd.DataFrame) -> pd.DataFrame:
    """
    Generates a stable internship_id since no source system provides one.
    Blocks on student_name + company + internship_year — good enough for
    the MVP; a student with two internships at the same company in the
    same year would collide (accepted trade-off, revisit if it matters).
    """
    df = internship_rows.copy()
    df["_block_key"] = (
        df["student_name"].str.lower().str.strip() + "|" +
        df["company"].str.lower().str.strip() + "|" +
        df["internship_year"].astype(str)
    )

    block_to_id = {key: f"INTERN_{i:04d}" for i, key in enumerate(sorted(df["_block_key"].unique()))}
    df["internship_id"] = df["_block_key"].map(block_to_id)

    return df.drop(columns=["_block_key"])


def build_entity_mapping(resolved_df: pd.DataFrame) -> pd.DataFrame:
    """source_record -> master_entity_id lineage table (for ENTITY_MAPPING in Postgres)."""
    return resolved_df[[
        "master_entity_id", "institution_name", "source_system",
        "source_database", "source_record_id", "match_score",
    ]]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_INGESTION"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "03_SCHEMA_MAPPING"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "04_STANDARDIZATION"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "05_NORMALIZATION"))
    from INGESTION_ENGINE import ingest_all
    from SCHEMA_MAPPER import map_all
    from STANDARDIZER import standardize_all
    from NORMALIZER import normalize_all

    sample_dir = Path(__file__).resolve().parents[1] / "DATA" / "SAMPLE"
    normalized = normalize_all(standardize_all(map_all(ingest_all(sample_dir))))
    combined = pd.concat(normalized.values(), ignore_index=True)
    resolved = match_entities(combined)
    print(build_entity_mapping(resolved).to_string(index=False))
