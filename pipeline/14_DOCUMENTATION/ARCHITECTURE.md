# ARCHITECTURE

## One-line description
A heterogeneous data harmonization and hybrid retrieval platform that
converts fragmented AICTE databases into a canonical, deduplicated and
governed data layer, stores authoritative structured information in
PostgreSQL, stores contextual knowledge as embeddings in pgvector, and
enables an LLM to answer natural-language queries using grounded retrieval
from both layers.

## Pipeline
```
CONNECT → INGEST → DISCOVER → MAP → STANDARDIZE → NORMALIZE → DEDUPLICATE
  → CLASSIFY → STORE → EMBED → INDEX → RETRIEVE → REASON → ANSWER
```

## Three types of information
| Type | Examples | Storage |
|---|---|---|
| Structured | state, rank, salary, approval_status | PostgreSQL |
| Relational | student → institution, faculty → department | PostgreSQL foreign keys |
| Contextual | remarks, descriptions, feedback | Embedding → pgvector |

## Core rules
1. **Don't destroy source data** — RAW → TRANSFORMED, never RAW → overwrite RAW.
2. **Don't let the LLM become the database** — it understands, decides what
   to retrieve, receives authoritative data, synthesizes. It never invents facts.
3. **Don't put everything into vectors** — structured/relational data belongs
   in PostgreSQL; only genuinely unstructured context goes to pgvector.
4. **Every answer must be traceable** — answer → retrieved data → canonical
   record → transformation → source record → source database.

## Known MVP limitations (fix these next)
- Entity resolution is exact-block matching, not probabilistic (Splink is
  the designed upgrade — see 06_DEDUPLICATION/ENTITY_RESOLVER.py docstring).
  In the current sample run it fails to merge one of the three "IIT Delhi"
  records because of a malformed state value — a good first bug to fix.
- `07_CONTEXT_CLASSIFICATION`'s LLM fallback is stubbed, not wired to the
  Anthropic API yet.
- `08_POSTGRESQL`, `10_PGVECTOR`, `11_RETRIEVAL` all require a live
  Postgres+pgvector instance to actually run (they're not exercised by
  `MAIN.py` on sample data alone).
- Text-to-SQL in `11_RETRIEVAL` is not implemented — currently a stub.

See the root README.md for folder-by-folder responsibilities and quick start.
