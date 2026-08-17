#!/usr/bin/env bash
# ============================================================================
# manage.sh — one script to control the AICTE mock DB stack
#
# Usage:  bash manage.sh <command>     (or ./manage.sh <command>)
# Run     bash manage.sh help          for the full command list.
#
# Everything runs in Docker (MySQL/PostgreSQL/MongoDB) + this folder (CSVs).
# All credentials come from .env. The seed venv lives in internalenv/.
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PY="internalenv/Scripts/python.exe"
[ -x "$PY" ] || PY="python"
PIP="internalenv/Scripts/pip.exe"
COMPOSE="docker compose"

# ---------------------------------------------------------------------------
# Docker credential workaround
# Docker Desktop's credential helper (docker-credential-desktop) is not on the
# Git Bash PATH, so image PULLS fail with "error getting credentials".
# We only override DOCKER_CONFIG for pulls / first up; normal commands run as-is.
# Set NO_CREDS_WORKAROUND=1 to disable (e.g. after fixing your PATH).
# ---------------------------------------------------------------------------
CFG_DIR="$SCRIPT_DIR/.docker-config"
mkdir -p "$CFG_DIR"
if [ ! -f "$CFG_DIR/config.json" ]; then
  printf '{"auths":{}}' > "$CFG_DIR/config.json"
fi
compose_with_creds() {
  if [ "${NO_CREDS_WORKAROUND:-0}" = "1" ]; then
    $COMPOSE "$@"
  else
    DOCKER_CONFIG="$CFG_DIR" $COMPOSE "$@"
  fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_up() {
  echo ">> Starting containers (MySQL:3307, Postgres:5433, Mongo:27017, Adminer:8080) ..."
  compose_with_creds up -d --wait
}

cmd_pull() {
  echo ">> Pulling latest images ..."
  compose_with_creds pull
}

cmd_status() {
  $COMPOSE ps
}

cmd_logs() {
  $COMPOSE logs --tail=50 "${1:-}"
}

cmd_seed() {
  echo ">> Seeding all 6 sources (registry -> legacy CSVs -> internships -> MySQL -> Postgres -> Mongo) ..."
  "$PY" seed_all.py
}

cmd_stop() {
  echo ">> Stopping containers (data is kept in Docker volumes) ..."
  $COMPOSE stop
}

cmd_down() {
  echo ">> Removing containers (data is kept in Docker volumes) ..."
  $COMPOSE down
}

cmd_wipe() {
  if ! confirm "WARNING: this DELETES all seeded data (docker compose down -v). Continue?"; then
    echo "Aborted."
    return 1
  fi
  echo ">> Removing containers AND volumes (all data deleted) ..."
  $COMPOSE down -v
  rm -rf "$CFG_DIR"
}

cmd_fresh() {
  if ! confirm "WARNING: this wipes all data, then rebuilds + reseeds from scratch. Continue?"; then
    echo "Aborted."
    return 1
  fi
  cmd_wipe
  cmd_up
  cmd_seed
}

cmd_counts() {
  echo "=================================================================="
  echo " ROW COUNTS — all 6 sources"
  echo "=================================================================="
  echo ""
  echo "--- 1. MySQL  aicte_institutes.institutes (port 3307) ---"
  docker exec aicte-mysql mysql -uaicte_app -paicte_pass aicte_institutes \
    -e "SELECT COUNT(*) AS row_count FROM institutes;" 2>/dev/null \
    || echo "(mysql not reachable — run: bash manage.sh up)"
  echo ""
  echo "--- 2. PostgreSQL  courses_db.courses (port 5433) ---"
  docker exec aicte-postgres psql -Upostgres -d courses_db \
    -c "SELECT COUNT(*) AS row_count, COUNT(DISTINCT college_name) AS distinct_colleges FROM courses;" 2>/dev/null \
    || echo "(postgres not reachable — run: bash manage.sh up)"
  echo ""
  echo "--- 3. PostgreSQL  faculty_db.faculty (port 5433) ---"
  docker exec aicte-postgres psql -Upostgres -d faculty_db \
    -c "SELECT COUNT(*) AS row_count, COUNT(DISTINCT institute_ref) AS distinct_refs FROM faculty;" 2>/dev/null \
    || echo "(postgres not reachable — run: bash manage.sh up)"
  echo ""
  echo "--- 4. MongoDB  aicte_scholarships.scholarships (port 27017) ---"
  docker exec aicte-mongo mongosh aicte_scholarships --quiet \
    --eval "print('docs: ' + db.scholarships.countDocuments())" 2>/dev/null \
    || echo "(mongo not reachable — run: bash manage.sh up)"
  echo ""
  echo "--- 5. Legacy CSVs  data/legacy/ ---"
  "$PY" - <<'PY'
import pandas as pd
for f in ["nba_autonomous_status.csv", "closed_institutes.csv", "unapproved_list.csv"]:
    df = pd.read_csv("data/legacy/" + f, dtype=str)
    print(f"{f:32s} -> {len(df)} rows")
PY
  echo ""
  echo "--- 6. Internships CSV  data/internships.csv ---"
  "$PY" - <<'PY'
import pandas as pd
df = pd.read_csv("data/internships.csv", dtype=str)
print(f"internships.csv              -> {len(df)} rows  ({df['domain'].nunique()} domains)")
PY
  echo ""
  echo "--- Planted issues (ground truth: conflicts_seeded.json) ---"
  "$PY" - <<'PY'
import json
d = json.load(open("conflicts_seeded.json", encoding="utf-8"))
print(f"cross_source_conflicts   : {len(d['cross_source_conflicts'])}")
print(f"within_source_duplicates : {len(d['within_source_duplicates'])}")
print(f"orphaned_records         : {len(d['orphaned_records'])}")
PY
}

cmd_samples() {
  echo "--- MySQL: 5 institutes ---"
  docker exec aicte-mysql mysql -uaicte_app -paicte_pass aicte_institutes \
    -e "SELECT Institute_Name, State, Approval_Status, Last_Updated FROM institutes LIMIT 5;" 2>/dev/null
  echo ""
  echo "--- Postgres: 5 courses ---"
  docker exec aicte-postgres psql -Upostgres -d courses_db \
    -c "SELECT college_name, course_name, fee_per_year FROM courses LIMIT 5;" 2>/dev/null
  echo ""
  echo "--- Postgres: 5 faculty ---"
  docker exec aicte-postgres psql -Upostgres -d faculty_db \
    -c "SELECT full_name, institute_ref, designation FROM faculty LIMIT 5;" 2>/dev/null
  echo ""
  echo "--- Mongo: 1 scholarship doc ---"
  docker exec aicte-mongo mongosh aicte_scholarships --quiet \
    --eval "printjson(db.scholarships.findOne())" 2>/dev/null
}

cmd_shell_mysql()  { docker exec -it aicte-mysql mysql -uaicte_app -paicte_pass aicte_institutes; }
cmd_shell_courses() { docker exec -it aicte-postgres psql -Upostgres -d courses_db; }
cmd_shell_faculty() { docker exec -it aicte-postgres psql -Upostgres -d faculty_db; }
cmd_shell_mongo()  { docker exec -it aicte-mongo mongosh aicte_scholarships; }

cmd_adminer() {
  echo ">> Opening Adminer at http://localhost:8080 (MySQL + Postgres management UI)"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) cmd.exe /c start "" "http://localhost:8080" ;;
    Darwin*) open "http://localhost:8080" ;;
    Linux*) xdg-open "http://localhost:8080" >/dev/null 2>&1 || echo "Open http://localhost:8080" ;;
    *) echo "Open http://localhost:8080" ;;
  esac
}

cmd_deps() {
  echo ">> Installing Python dependencies into internalenv/ ..."
  "$PIP" install -r requirements.txt
}

confirm() {
  read -r -p "$1 [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

usage() {
  cat <<'EOF'
manage.sh — control the AICTE mock DB stack (6 fragmented sources)

USAGE
  bash manage.sh <command>

START / STOP
  up | start        Start all containers (MySQL:3307, Postgres:5433, Mongo:27017, Adminer:8080) and wait until healthy
  pull              Pull the Docker images (needed only once / after image changes)
  setup | all       up + seed  (the full "everything" command)
  fresh             WIPE all data, rebuild containers, reseed from scratch  [asks for confirmation]
  stop              Stop containers (data kept)
  down              Remove containers (data kept in volumes)
  wipe              Remove containers AND delete all seeded data  [asks for confirmation]

SEEDING
  seed | reseed     Seed / re-seed all 6 sources (idempotent — safe to re-run)
  deps              Install Python dependencies into internalenv/

INSPECT
  status | ps       Show container health
  logs [service]    Tail logs (e.g. bash manage.sh logs mysql)
  counts            Row counts for all 6 sources + planted-issue counts
  samples           Peek at sample rows from each source
  adminer | ui      Open the Adminer web UI (http://localhost:8080)

SHELLS (interactive)
  mysql             MySQL shell (aicte_institutes)
  courses           psql shell (courses_db)
  faculty           psql shell (faculty_db)
  mongo             mongosh shell (aicte_scholarships)

  help              Show this help

EXAMPLES
  bash manage.sh setup          # first time: containers + data
  bash manage.sh seed           # re-seed after code changes
  bash manage.sh counts         # demo summary for judges
  bash manage.sh mysql          # interactive MySQL shell
EOF
}

# ---------------------------------------------------------------------------
case "${1:-help}" in
  up|start)            cmd_up ;;
  pull)                cmd_pull ;;
  setup|all)           cmd_up && cmd_seed ;;
  fresh)               cmd_fresh ;;
  seed|reseed|reset)   cmd_seed ;;
  deps|venv)           cmd_deps ;;
  stop)                cmd_stop ;;
  down)                cmd_down ;;
  wipe)                cmd_wipe ;;
  status|ps|health)    cmd_status ;;
  logs)                cmd_logs "${2:-}" ;;
  counts|summary)      cmd_counts ;;
  samples|peek)        cmd_samples ;;
  mysql)               cmd_shell_mysql ;;
  courses)             cmd_shell_courses ;;
  faculty)             cmd_shell_faculty ;;
  mongo)               cmd_shell_mongo ;;
  adminer|ui)          cmd_adminer ;;
  help|--help|-h|"")   usage ;;
  *)                   echo "Unknown command: $1"; echo; usage ;;
esac
