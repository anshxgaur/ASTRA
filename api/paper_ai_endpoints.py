"""
API Endpoints for Research Paper RAG and AI Detection.
Add these to api/app.py or use as a separate router.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import JSONResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_ROOT = PROJECT_ROOT / "pipeline"

sys.path.insert(0, str(PIPELINE_ROOT / "11_RETRIEVAL"))
sys.path.insert(0, str(PIPELINE_ROOT / "09_EMBEDDINGS"))
sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))

router = APIRouter()


# ---------------------------------------------------------------------------
# Research Paper RAG Endpoints
# ---------------------------------------------------------------------------

@router.get("/papers/list")
def list_papers():
    """List all ingested research papers."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()
    papers = rag.list_papers()
    return {"count": len(papers), "papers": papers}


@router.post("/papers/ingest")
def ingest_paper(
    pdf_path: str = Query(..., description="Path to PDF file"),
    title: str | None = Query(None, description="Paper title"),
    authors: str | None = Query(None, description="Author names"),
):
    """Ingest a research paper PDF into the RAG system."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()

    try:
        result = rag.ingest_paper(pdf_path, title=title, authors=authors)
        return {
            "status": "success",
            "paper_id": result["paper_id"],
            "title": result["title"],
            "chunks": result["chunks"],
            "metadata": result["metadata"],
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"PDF not found: {pdf_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {type(e).__name__}: {e}")


@router.post("/papers/ingest-directory")
def ingest_directory(
    pdf_dir: str = Query(..., description="Directory containing PDFs"),
):
    """Ingest all PDFs from a directory."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()

    try:
        results = rag.ingest_directory(pdf_dir)
        success = [r for r in results if "paper_id" in r]
        failed = [r for r in results if "error" in r]
        return {
            "status": "success",
            "total": len(results),
            "ingested": len(success),
            "failed": len(failed),
            "papers": success,
            "errors": failed,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Directory ingestion failed: {e}")


@router.get("/papers/search")
def search_papers(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=50),
    paper_id: str | None = Query(None, description="Filter by paper ID"),
    section: str | None = Query(None, description="Filter by section"),
):
    """Semantic search across research papers."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()
    results = rag.search(q, top_k=top_k, paper_id=paper_id, section=section)
    return {
        "query": q,
        "count": len(results),
        "results": [
            {
                "chunk_id": r["chunk_id"],
                "paper_id": r["paper_id"],
                "title": r["title"],
                "authors": r.get("authors", ""),
                "year": r.get("year"),
                "section": r["section"],
                "similarity": round(r["similarity"], 4),
                "content": r["content"][:300] + "..." if len(r["content"]) > 300 else r["content"],
            }
            for r in results
        ],
    }


@router.get("/papers/ask")
def ask_paper_question(
    q: str = Query(..., description="Your question about the papers"),
    top_k: int = Query(5, ge=1, le=20),
    paper_id: str | None = Query(None, description="Restrict to specific paper"),
):
    """Ask a question about research papers (RAG-powered answer with citations)."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()
    result = rag.ask(q, top_k=top_k, paper_id=paper_id)
    return result


# ---------------------------------------------------------------------------
# AI Detection Endpoints
# ---------------------------------------------------------------------------

@router.post("/ai-detect")
def detect_ai(
    text: str = Query(..., description="Text to analyze"),
    use_llm: bool = Query(True, description="Use Groq LLM for deep analysis"),
):
    """Detect whether text is AI-generated or human-written."""
    from AI_DETECTOR import AIDetector
    detector = AIDetector()
    result = detector.analyze(text, use_llm=use_llm)
    return result


@router.post("/ai-detect/batch")
def detect_ai_batch(
    texts: list[str] = Query(..., description="List of texts to analyze"),
    use_llm: bool = Query(True, description="Use Groq LLM for deep analysis"),
):
    """Batch AI detection for multiple texts."""
    from AI_DETECTOR import AIDetector
    detector = AIDetector()
    result = detector.analyze_batch(texts, use_llm=use_llm)
    return result


@router.post("/ai-detect/compare")
def compare_texts(
    text_a: str = Query(..., description="First text"),
    text_b: str = Query(..., description="Second text"),
):
    """Compare two texts to see which is more AI-like."""
    from AI_DETECTOR import AIDetector
    detector = AIDetector()
    result = detector.compare_texts(text_a, text_b)
    return result


@router.get("/ai-detect/health")
def ai_detect_health():
    """Check AI detection capabilities."""
    import GROQ_CLIENT
    from AI_DETECTOR import StatisticalAnalyzer

    return {
        "statistical_analysis": True,
        "llm_analysis": GROQ_CLIENT.is_available(),
        "groq_model": __import__("os").getenv("GROQ_MODEL", GROQ_CLIENT.DEFAULT_MODEL),
    }


# ---------------------------------------------------------------------------
# Resume RAG Endpoints
# ---------------------------------------------------------------------------

@router.get("/resumes/list")
def list_resumes():
    """List all ingested resumes."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()
    resumes = rag.list_resumes()
    return {"count": len(resumes), "resumes": resumes}


@router.post("/resumes/ingest")
def ingest_resume(
    pdf_path: str = Query(..., description="Path to resume PDF"),
    candidate_name: str | None = Query(None, description="Candidate name"),
):
    """Ingest a resume PDF into the RAG system."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()

    try:
        result = rag.ingest_resume(pdf_path, candidate_name=candidate_name)
        return {
            "status": "success",
            "resume_id": result["resume_id"],
            "candidate_name": result["candidate_name"],
            "chunks": result["chunks"],
            "metadata": result["metadata"],
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"PDF not found: {pdf_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {type(e).__name__}: {e}")


@router.post("/resumes/ingest-directory")
def ingest_resume_directory(
    pdf_dir: str = Query(..., description="Directory containing resume PDFs"),
):
    """Ingest all resume PDFs from a directory."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()

    try:
        results = rag.ingest_directory(pdf_dir)
        success = [r for r in results if "resume_id" in r]
        failed = [r for r in results if "error" in r]
        return {
            "status": "success",
            "total": len(results),
            "ingested": len(success),
            "failed": len(failed),
            "resumes": success,
            "errors": failed,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Directory ingestion failed: {e}")


@router.get("/resumes/search")
def search_resumes(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=50),
    resume_id: str | None = Query(None, description="Filter by resume ID"),
):
    """Semantic search across resumes."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()
    results = rag.search(q, top_k=top_k, resume_id=resume_id)
    return {
        "query": q,
        "count": len(results),
        "results": [
            {
                "chunk_id": r["chunk_id"],
                "resume_id": r["resume_id"],
                "candidate_name": r.get("candidate_name", ""),
                "skills": r.get("skills", "")[:200],
                "experience_years": r.get("experience_years", 0),
                "location": r.get("location", ""),
                "section": r["section"],
                "similarity": round(r["similarity"], 4),
                "content": r["content"][:300] + "..." if len(r["content"]) > 300 else r["content"],
            }
            for r in results
        ],
    }


@router.get("/resumes/ask")
def ask_resume_question(
    q: str = Query(..., description="Your question about the resumes"),
    top_k: int = Query(5, ge=1, le=20),
):
    """Ask a question about resumes (RAG-powered answer with citations)."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()
    result = rag.ask(q, top_k=top_k)
    return result


# ---------------------------------------------------------------------------
# Detail endpoints (MUST come after /search, /ask to avoid route conflicts)
# ---------------------------------------------------------------------------

@router.get("/papers/detail/{paper_id}")
def get_paper(paper_id: str):
    """Get details of a specific paper."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()
    paper = rag.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
    return {"paper": paper}


@router.delete("/papers/detail/{paper_id}")
def delete_paper(paper_id: str):
    """Delete a paper and its chunks."""
    from RESEARCH_PAPER_RAG import ResearchPaperRAG
    rag = ResearchPaperRAG()
    rag.delete_paper(paper_id)
    return {"status": "deleted", "paper_id": paper_id}


@router.get("/resumes/detail/{resume_id}")
def get_resume(resume_id: str):
    """Get details of a specific resume."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()
    resumes = rag.list_resumes()
    resume = next((r for r in resumes if r["resume_id"] == resume_id), None)
    if not resume:
        raise HTTPException(status_code=404, detail=f"Resume not found: {resume_id}")
    return {"resume": resume}


@router.delete("/resumes/detail/{resume_id}")
def delete_resume(resume_id: str):
    """Delete a resume and its chunks."""
    from RESUME_RAG import ResumeRAG
    rag = ResumeRAG()
    rag.delete_resume(resume_id)
    return {"status": "deleted", "resume_id": resume_id}
