#!/usr/bin/env bash
cd "$(dirname "$0")"
# Load .env files via Python dotenv (shell source doesn't work with Windows .env)
eval $(./internalenv/Scripts/python.exe -c "
from dotenv import load_dotenv
import os
load_dotenv('.env')
load_dotenv('pipeline/.env')
for k,v in os.environ.items():
    if k in ('GROQ_API_KEY','GROQ_MODEL','POSTGRES_HOST','POSTGRES_PORT','POSTGRES_USER','POSTGRES_PASSWORD','POSTGRES_DB','MYSQL_HOST','MYSQL_PORT','MYSQL_USER','MYSQL_PASSWORD','MYSQL_DATABASE','MONGO_HOST','MONGO_PORT','MONGO_DB','COURSES_DB','FACULTY_DB'):
        print(f'export {k}=\"{v}\"')
")
exec ./internalenv/Scripts/python.exe -m uvicorn api.app:app --host 0.0.0.0 --port "${1:-8000}"
