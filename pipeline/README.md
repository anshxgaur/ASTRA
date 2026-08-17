# pipeline/ — the integrated harmonization pipeline

This folder is Phase 2 + 3 of the AICTE Unified Search System: a 13-stage
pipeline that ingests the 5 real Phase-1 sources (MySQL / PostgreSQL /
MongoDB / legacy CSVs), cleans + deduplicates them, and loads the canonical
result into PostgreSQL (`aicte_canonical`) with pgvector embeddings.

See the **single project README** at [`../README.md`](../README.md) for the
full architecture, the interactive workflow (how to run it), what is done
and what is left. Stage-by-stage details live in each numbered folder and in
[`14_DOCUMENTATION/ARCHITECTURE.md`](14_DOCUMENTATION/ARCHITECTURE.md).

Quick reference:

```bash
internalenv/Scripts/python.exe pipeline/MAIN.py          # run the whole pipeline
internalenv/Scripts/python.exe -m pytest pipeline/12_TESTS/TEST_PIPELINE.py -v
```
