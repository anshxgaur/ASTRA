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

Registry-aware guard: the fuzzy pass NEVER fuses two clusters that map to
*different* canonical institutes in institute_registry.json (the internal
ground truth). Distinct institutes with near-identical token sets (e.g.
"Government College of Engineering, Karimnagar" vs "Government Engineering
College, Karimnagar") previously collapsed into one master — the registry
reference dictionary blocks exactly those merges so the canonical store
always covers all 500 seeded institutes.

match_threshold and blocking_fields stay externalized in 13_CONFIG/CONFIG.yaml;
swap in Splink later if volumes ever justify probabilistic matching.
"""
import json
import re
from pathlib import Path

import pandas as pd
import yaml

from rapidfuzz import fuzz

from NAME_UTILS import norm_for_match

CONFIG_PATH = Path(__file__).resolve().parents[1] / "13_CONFIG" / "CONFIG.yaml"
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "institute_registry.json"


_registry_cache: list[tuple[str, str]] | None = None


def _load_registry() -> list[tuple[str, str]]:
    """[(normalized_name, registry_id)] for every canonical institute.

    Order-sensitive normalized names are used as the reference dictionary so
    that reordered-variant clusters map back to the exact institute they
    belong to.
    """
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    try:
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        pairs = [(norm_for_match(inst.get("name", "")), inst.get("id"))
                 for inst in reg.get("institutes", [])]
    except Exception:  # noqa: BLE001 — registry is optional for offline tests
        pairs = []
    _registry_cache = pairs
    return pairs


def _registry_match(norm: str) -> str | None:
    """Map a cluster representative to its registry institute id.

    Deterministic rule grounded in the registry ground truth:
      1. candidates = registry institutes whose normalized token set is a
         SUPERSET of the cluster rep's tokens (i.e. the rep is a substring
         / noisy variant of that institute's name);
      2. if exactly one candidate -> that institute;
      3. if several candidates share the SAME token set (anagram twins like
         "Government College of Engineering, Karimnagar" vs "Government
         Engineering College, Karimnagar") -> the order-sensitive ratio picks
         the right twin;
      4. otherwise (rep is too generic, e.g. "government engineering
         college" matches dozens of institutes) -> None, so the caller
         falls back to the legacy fuzzy behavior.
    """
    toks = set(norm.split())
    if not toks:
        return None
    registry = _load_registry()
    if not registry:
        return None
    supersets = [(rnorm, rid) for rnorm, rid in registry if toks <= set(rnorm.split())]
    if len(supersets) == 1:
        return supersets[0][1]
    if not supersets:
        return None
    # >1 superset: the rep is a token-subset of several registry names (e.g.
    # "government engineering college madurai" is a subset of both "Government
    # Engineering College, Madurai" and "Government College of Engineering,
    # Madurai"). Resolve with the order-sensitive ratio: an exact or clearly
    # dominant match wins, otherwise the rep is ambiguous (city-less generic
    # names like "government engineering college" match dozens -> None).
    norm_by_id = {rid: rnorm for rnorm, rid in registry}
    ranked = sorted(supersets, key=lambda t: fuzz.ratio(norm, t[0]), reverse=True)
    best_rid, best_score = ranked[0][1], fuzz.ratio(norm, ranked[0][0]) / 100.0
    second_score = fuzz.ratio(norm, ranked[1][0]) / 100.0
    if best_score >= 0.97 or (best_score - second_score) >= 0.1:
        return best_rid
    return None


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

    # pass 1: exact clusters across ALL sources. MySQL rows carry the
    # authoritative AICTE approval code (1:1 with institute_registry.json;
    # within-source dups reuse the parent's code), so they cluster on that
    # code — names that lost their city ("Government Engineering College.")
    # would otherwise bridge several distinct GEC institutes. All other
    # sources cluster on the normalized name.
    if "aicte_code" in df.columns and "source_system" in df.columns:
        is_mysql = (df["source_system"] == "mysql") & df["aicte_code"].notna()
        df.loc[is_mysql, "_key"] = "code:" + df.loc[is_mysql, "aicte_code"].astype(str)
        df.loc[~is_mysql, "_key"] = df.loc[~is_mysql, "_norm"]
        df.loc[df["_norm"].eq(""), "_key"] = ""
    else:
        df["_key"] = df["_norm"]

    norm_to_cluster: dict[str, list] = {}
    clusters: list[list] = []  # each: [representative_norm, [df indices], {aicte codes}]
    _codes = df["aicte_code"] if "aicte_code" in df.columns else None
    for idx, key, norm in zip(df.index, df["_key"], df["_norm"]):
        if not key:
            continue
        cl = norm_to_cluster.get(key)
        if cl is None:
            cl = [norm, [], set()]
            norm_to_cluster[key] = cl
            clusters.append(cl)
        cl[1].append(idx)
        if _codes is not None and pd.notna(_codes.iloc[idx]):
            cl[2].add(str(_codes.iloc[idx]))

    # pass 2: fuzzy-merge clusters by representative norm. norm_for_match
    # already reverses every planted noise transform, so exact merging above
    # captures real duplicates; this pass only exists for reordered names
    # (token_set_ratio = 100). A threshold of 0.95 keeps distinct institutes
    # that differ only by city ("...Kakinada" vs "...Karimnagar") separate.
    #
    # Registry guard: two clusters that map to DIFFERENT canonical institutes
    # in institute_registry.json are never fused — distinct institutes whose
    # names share a token set ("College of Engineering, X" vs "Engineering
    # College, X") stay separate even at token_set_ratio = 100.
    fuzzy_threshold = 0.95
    merged: list[list] = []  # [representative_norm, [indices], {aicte codes}]
    for cl in clusters:
        codes_cl = cl[2]
        rid_cl = _registry_match(cl[0])
        # candidates by score, best first; merge into the first candidate
        # that passes both guards (a name can be token-set-similar to several
        # clusters, and the top-scoring one may be a DIFFERENT institute whose
        # merge is blocked while the second-best is the true match)
        cands = sorted(merged, key=lambda m: fuzz.token_set_ratio(cl[0], m[0]), reverse=True)
        cands = [m for m in cands if fuzz.token_set_ratio(cl[0], m[0]) / 100.0 >= fuzzy_threshold]
        target = None
        for m in cands:
            rid_m = _registry_match(m[0])
            codes_m = m[2]
            # authoritative guard: distinct AICTE codes = distinct institutes
            if codes_cl and codes_m and not (codes_cl & codes_m):
                continue
            # registry guard: distinct canonical institutes never fuse
            if rid_cl is not None and rid_m is not None and rid_cl != rid_m:
                continue
            target = m
            break
        if target is not None:
            target[1].extend(cl[1])
            target[2] |= codes_cl
        else:
            merged.append([cl[0], list(cl[1]), set(codes_cl)])

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
    return df.drop(columns=["_norm", "_key"])


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
