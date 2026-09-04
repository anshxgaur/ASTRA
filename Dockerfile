# AICTE Unified Search — Phase-4 API container (Koyeb / any Docker host).
# Serves the search UI + hybrid retrieval API. Data lives in a hosted
# PostgreSQL+pgvector (Neon); only the API runtime is deployed here.
FROM python:3.11-slim

WORKDIR /app

# slim runtime deps (no seed/pipeline build deps needed at serving time)
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# the API imports retrieval modules from pipeline/11_RETRIEVAL and
# 10_PGVECTOR via relative paths rooted at /app — copy the whole tree
COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
