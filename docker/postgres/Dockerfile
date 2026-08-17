# Custom PostgreSQL image for the AICTE mock environment.
#
# Based on pgvector/pgvector:pg16 (postgres:16 + the `vector` extension),
# needed for Phase 3 semantic search.
#
# Java clients such as DBeaver on Windows send their system timezone name
# ("Asia/Calcutta") as the TimeZone startup parameter, and the server rejects
# it unless the legacy alias exists:
#   FATAL: invalid value for parameter "TimeZone": "Asia/Calcutta"
#
# pgvector/pgvector:pg16 normally ships Asia/Calcutta -> Asia/Kolkata already;
# the -f flag makes this a no-op in that case and fixes it if it ever isn't.
FROM pgvector/pgvector:pg16

RUN ln -sf /usr/share/zoneinfo/Asia/Kolkata /usr/share/zoneinfo/Asia/Calcutta
