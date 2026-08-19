# Phase 4 — data package (generated from aicte_canonical)

Generated 2026-08-19T01:42:28 by `export_phase4.py`
from the Phase-3 canonical store (database `aicte_canonical`). Everything needed to build
the Phase-4 FastAPI hybrid-search API is here — no database access required.

## Files

### Relational facts (PostgreSQL tables)

| File | Rows | Contents |
|------|------|----------|
| `institution.csv` | 530 | canonical institutions (`institution_id` PK, name, state, district, city, type, ownership, approval/current status, autonomous, NBA, year, AICTE code) |
| `course.csv` | 1403 | courses with FK `institution_id`, department, duration, intake, fee, course_status |
| `faculty.csv` | 864 | faculty with FK `institution_id`, designation, qualification, specialization, department, years_of_experience |
| `scholarship.csv` | 151 | scholarship schemes (amount, applicable_states) |
| `approval.csv` | 273 | nba / closed / unapproved records with FK `institution_id` |
| `internship.csv` | 650 | internship-portal openings with FK `institution_id`, domain, org, stipend, mode, PPO, program_source |
| `entity_mapping.csv` | 3871 | every canonical id -> source record (lineage) |
| `data_lineage.csv` | 3871 | every row -> source system/table/record + timestamp |

### Semantic context (pgvector)

| File | Rows | Contents |
|------|------|----------|
| `context_document.csv` | 3871 | one row per embedding: `context_text` (rich sentence + [Source: ...] citation), `embedding` (384 floats), and metadata (`entity_id`, `entity_type`, `context_type`, source lineage) |

## How to use in Phase 4

1. **Search**: embed the user query with the SAME model used here —
   `sentence-transformers/all-MiniLM-L6-v2` (via fastembed), 384 dims — then
   compute cosine similarity against `embedding` (parse the CSV value as
   `[float,...]`). `context_text` ends with a `[Source: ..., record ...]`
   citation you can return to the user.
2. **Filters / facts**: join `context_document.entity_id` to the relational
   tables (e.g. `institution.institution_id` for colleges, `course.course_id`
   for courses) to filter by state / approval_status / entity_type.
3. **Grounded answers**: every fact carries lineage — `entity_mapping` /
   `data_lineage` tell you exactly which source record a canonical id came
   from. Never let the LLM invent facts outside retrieved context.
4. **Load back into Postgres** (optional) instead of reading CSVs:
   ```sql
   \copy institution FROM 'phase4_data/institution.csv' WITH (FORMAT csv, HEADER true);
   \copy context_document(context_id, entity_id, entity_type, context_type, context_text,
       embedding, source_database, source_table, source_record_id, confidence, created_at, data_version)
   FROM 'phase4_data/context_document.csv' WITH (FORMAT csv, HEADER true);
   ```

## Join-key cheat sheet

- `context_document.entity_type` = `institution` | `course` | `faculty` | `scholarship` | `approval` | `internship`
- `institution` ↔ `entity_mapping.master_entity_id` (id `INST_xxxx`)
- `course.course_id` = `CRS_<source course_id>` · `faculty.faculty_id` = `FAC_<source faculty_id>`
- `scholarship.scholarship_id` = Mongo `_id` · `approval.approval_id` = `APR_xxxxx`
