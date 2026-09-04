# ASTRA — AI-Powered Unified Search System

> 6 Fragmented Data Sources → 1000 Resumes → 20 Research Papers → One Search Portal

A full-stack AI-powered search platform that ingests resumes, research papers, and AICTE institutional data, stores them in PostgreSQL + pgvector, and provides semantic search + AI-powered Q&A using Groq LLM.

---

## 🚀 Quick Start

### Prerequisites
- Docker Desktop running
- Python 3.11+ with venv at `internalenv/`

### Setup (4 commands)

```bash
# 1. Install dependencies
./internalenv/Scripts/pip.exe -r requirements.txt

# 2. Start databases (MySQL, PostgreSQL, MongoDB)
bash manage.sh up

# 3. Setup database schema for resumes + papers
./internalenv/Scripts/python.exe pipeline/SCALE_DOWN_AND_SETUP.py

# 4. Start the API server
bash start_api.sh 8000
```

Open **http://localhost:8000** — you're live!

---

## 📊 What's Inside

| Data Source | Count | Storage |
|-------------|-------|---------|
| Resumes (PDF) | 1,000 | PostgreSQL + pgvector |
| Research Papers (PDF) | 20 | PostgreSQL + pgvector |
| AICTE Institutions | 20 | PostgreSQL |
| Courses | 65 | PostgreSQL |
| Faculty | 31 | PostgreSQL |
| Scholarships | 151 | PostgreSQL |
| **Total Embeddings** | **7,462** | pgvector (384-dim, HNSW) |

---

## 🔍 API Endpoints

### Resume Search
```bash
# Semantic search across 1000 resumes
curl "http://localhost:8000/resumes/search?q=Python+machine+learning&top_k=5"

# AI-powered Q&A about resumes
curl "http://localhost:8000/resumes/ask?q=Which+candidates+have+NLP+skills?"

# List all resumes
curl "http://localhost:8000/resumes/list"
```

### Research Paper Search
```bash
# Semantic search across papers
curl "http://localhost:8000/papers/search?q=data+lake+architecture&top_k=5"

# AI-powered Q&A about papers
curl "http://localhost:8000/papers/ask?q=What+is+the+main+contribution?"

# List all papers
curl "http://localhost:8000/papers/list"
```

### AI Detection
```bash
# Detect if text is AI-generated
curl -X POST "http://localhost:8000/ai-detect?text=Your+suspicious+text+here"

# Batch detection
curl -X POST "http://localhost:8000/ai-detect/batch?texts=text1&texts=text2"

# Compare two texts
curl -X POST "http://localhost:8000/ai-detect/compare?text_a=AI+text&text_b=Human+text"
```

### AICTE Search
```bash
# Hybrid search (rules + Groq text-to-SQL + pgvector)
curl "http://localhost:8000/search?q=How+many+colleges+in+UP"

# Groq-powered answer
curl "http://localhost:8000/answer?q=courses+with+intake+above+120"
```

---

## 🏗️ Architecture

```
PDF Resumes (1000) ──┐
PDF Papers (20) ─────┤
AICTE Data (6 srcs) ─┼──► PostgreSQL + pgvector ──► FastAPI + Groq LLM ──► Search UI
                      │
AI Detection ─────────┘
```

### Tech Stack
- **Backend**: FastAPI + Uvicorn
- **Database**: PostgreSQL 16 + pgvector (HNSW index)
- **Embeddings**: fastembed (all-MiniLM-L6-v2, 384-dim)
- **LLM**: Groq (openai/gpt-oss-120b) for text-to-SQL + answer synthesis
- **PDF Extraction**: pypdf (parallel) + PyMuPDF + pdfplumber
- **Docker**: MySQL, PostgreSQL, MongoDB, Adminer

---

## 📁 Project Structure

```
ASTRA/
├── api/
│   ├── app.py                    # Main FastAPI app
│   └── paper_ai_endpoints.py     # Resume/Paper/AI Detection endpoints
├── pipeline/
│   ├── 01_INGESTION/
│   │   ├── INGESTION_ENGINE.py   # Data ingestion
│   │   └── PDF_EXTRACTOR.py      # PDF text/table extraction
│   ├── 08_POSTGRESQL/
│   │   └── DB_LOADER.py          # PostgreSQL loader
│   ├── 09_EMBEDDINGS/
│   │   └── EMBEDDING_GENERATOR.py # fastembed embeddings
│   ├── 10_PGVECTOR/
│   │   └── VECTOR_STORE.py       # pgvector operations
│   ├── 11_RETRIEVAL/
│   │   ├── HYBRID_RETRIEVER.py   # Rules + Groq text-to-SQL
│   │   ├── GROQ_CLIENT.py        # Groq LLM wrapper
│   │   ├── RESEARCH_PAPER_RAG.py # Paper RAG engine
│   │   ├── RESUME_RAG.py         # Resume RAG engine
│   │   ├── TEXT_TO_SQL.py        # LLM SQL generation
│   │   └── AI_DETECTOR.py        # AI text detection
│   ├── SCALE_DOWN_AND_SETUP.py   # Database setup script
│   └── MAIN.py                   # ETL pipeline
├── data/
│   ├── resumes/                  # Your 1000 resume PDFs
│   └── papers/                   # Your research paper PDFs
├── fast_ingest.py                # Fast parallel ingestion
├── start_api.sh                  # Server launcher
├── manage.sh                     # Docker management
├── docker-compose.yml            # Database containers
├── .env                          # Credentials (GROQ_API_KEY, DB passwords)
└── requirements.txt              # Python dependencies
```

---

## 🔧 Ingest Your Own Data

### Ingest Resumes
```bash
# Place PDFs in data/resumes/
cp /path/to/your/resumes/*.pdf data/resumes/

# Fast ingestion (parallel, ~10 min for 1000 PDFs)
./internalenv/Scripts/python.exe fast_ingest.py data/resumes/
```

### Ingest Research Papers
```bash
# Place PDFs in data/papers/
cp /path/to/your/papers/*.pdf data/papers/

# Ingest via CLI
./internalenv/Scripts/python.exe pipeline/11_RETRIEVAL/RESEARCH_PAPER_RAG.py ingest data/papers/

# Or via API
curl -X POST "http://localhost:8000/papers/ingest-directory?pdf_dir=data/papers/"
```

### Ingest via API
```bash
# Single resume
curl -X POST "http://localhost:8000/resumes/ingest?pdf_path=data/resumes/my_resume.pdf"

# Single paper
curl -X POST "http://localhost:8000/papers/ingest?pdf_path=data/papers/my_paper.pdf&title=My+Paper"
```

---

## 🧪 Demo Queries to Try

| Query | What It Tests |
|-------|---------------|
| `Python developer with machine learning` | Resume semantic search |
| `Which candidates have NLP skills?` | Resume AI Q&A |
| `data lake architecture` | Paper semantic search |
| `What is the main contribution of these papers?` | Paper AI Q&A |
| `Furthermore, it is important to note that AI...` | AI text detection |
| `How many approved colleges in UP?` | AICTE structured query |
| `courses with intake above 120 in Telangana` | Groq text-to-SQL |

---

## 🐳 Docker Services

| Service | Port | Purpose |
|---------|------|---------|
| PostgreSQL + pgvector | 5433 | Main database |
| MySQL | 3307 | AICTE institutes |
| MongoDB | 27017 | Scholarships |
| Adminer | 8080 | DB browser UI |

---

## ⚡ Performance

| Operation | Time |
|-----------|------|
| Ingest 1000 resumes (parallel) | ~10 minutes |
| Ingest 20 papers | ~2 minutes |
| Resume semantic search | < 1 second |
| Paper semantic search | < 1 second |
| Groq answer synthesis | 2-5 seconds |
| AI text detection | 3-8 seconds |

---

## 📝 Environment Variables

```bash
# .env file
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-120b

POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=aicte_canonical
```

---

## ☁️ Deploy to Production

The deployable unit is the **API container** (`Dockerfile`) — it serves the
search UI, hybrid retrieval, and RAG endpoints. The data lives in a hosted
**PostgreSQL + pgvector** instance; the container never needs MySQL/MongoDB.

### 1. Prepare the hosted database

Create a PostgreSQL + pgvector database on any provider (Neon, Supabase,
Fly Postgres, Railway, ...), then load the packaged data into it:

```bash
# load phase4_data/ (all tables + 384-dim embeddings + HNSW index)
./internalenv/Scripts/python.exe load_phase4_data.py --url "$DATABASE_URL"
```

### 2. Set environment variables on the platform

| Variable | Required | Notes |
|----------|----------|-------|
| `DATABASE_URL` | yes | e.g. `postgresql://user:pass@host:5432/aicte_canonical?sslmode=require` |
| `GROQ_API_KEY` | yes (for LLM answers) | without it, the API serves mock (retrieved-context-only) answers |
| `GROQ_MODEL` | no | defaults to `openai/gpt-oss-120b` |

### 3. Deploy the container

- **Koyeb** — `koyeb app init aicte-search --git github.com/you/repo --git-run-command "uvicorn api.app:app --host 0.0.0.0 --port 8000" --ports 8000:http --routes /:8000` (config in `koyeb.yaml`)
- **Fly.io** — `fly launch` then `fly secrets set DATABASE_URL=... GROQ_API_KEY=...` (config in `fly.toml`)
- **Railway / Render** — point at the `Dockerfile`, port `8000`, same env vars

### 4. Verify

```bash
curl https://your-app/health        # status ok + data coverage
curl "https://your-app/search?q=How+many+approved+colleges+in+Uttar+Pradesh"
```

> ⚠️ The open ingest/delete endpoints (`/resumes/ingest`, `/papers/delete/...`)
> are intended for the hackathon demo. If you expose this publicly, protect
> them (auth proxy, disable via env) or restrict the service to a VPN.

---

## 🤝 Team

Built for Smart India Hackathon 2026

---

**Star ⭐ this repo if you find it useful!**
