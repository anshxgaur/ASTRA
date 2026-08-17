"""
Name normalization shared by entity resolution.

Reverses every noise transform planted by the Phase-1 seeder
(seed/name_noise_utils.py): lowercase, expand abbreviations
(inst -> institute, engg -> engineering, ...), drop honorifics
(shri/sri/smt/dr/the/...), strip punctuation and collapse whitespace.
"""
from __future__ import annotations

import re

ABBREV_EXPANSIONS = {
    "inst": "institute",
    "engg": "engineering",
    "colg": "college",
    "coll": "college",
    "poly": "polytechnic",
    "tech": "technology",
    "dept": "department",
    "univ": "university",
}

HONORIFICS = ["shri ", "sri ", "smt. ", "smt ", "dr. ", "dr ", "st. ", "the ", "sree ", "seth "]

_ABBREV_RE = {k: re.compile(rf"\b{re.escape(k)}\.?\b") for k in ABBREV_EXPANSIONS}


def norm_for_match(name: str) -> str:
    """Normalized string used ONLY for similarity matching."""
    s = str(name).lower()
    s = s.replace("&", " and ")
    for abbr, full in ABBREV_EXPANSIONS.items():
        s = _ABBREV_RE[abbr].sub(full, s)
    for h in HONORIFICS:
        if s.startswith(h):
            s = s[len(h):]
            break
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_name(name: str) -> str:
    """Light cleaning for display: trim whitespace, collapse spaces, drop
    stray trailing commas/periods left by the legacy noise generator."""
    s = str(name).strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[,\s]+$", "", s)
    return s
