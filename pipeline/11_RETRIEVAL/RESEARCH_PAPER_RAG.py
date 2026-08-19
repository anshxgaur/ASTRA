"""
11_RETRIEVAL - RESEARCH_PAPER_RAG.py
Research Paper Information Retrieval using RAG.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PIPELINE_ROOT.parent

sys.path.insert(0, str(PIPELINE_ROOT / "11_RETRIEVAL"))
sys.path.insert(0, str(PIPELINE_ROOT / "09_EMBEDDINGS"))
sys.path.insert(0, str(PIPELINE_ROOT / "10_PGVECTOR"))

import GROQ_CLIENT

RESEARCH_PAPER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research_paper (
    paper_id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT,
    abstract TEXT, publication TEXT, year INTEGER, doi TEXT,
    keywords TEXT, file_path TEXT, total_chunks INTEGER DEFAULT 0,
    ingested_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS paper_chunk (
    chunk_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES research_paper(paper_id),
    chunk_index INTEGER NOT NULL, section TEXT, heading TEXT,
    content TEXT NOT NULL, char_count INTEGER,
    embedding VECTOR(384), created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_paper_chunk_embedding ON paper_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_paper_chunk_paper ON paper_chunk(paper_id);
"""


class ResearchPaperRAG:

    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self):
        from VECTOR_STORE import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for stmt in RESEARCH_PAPER_TABLE_SQL.split(";"):
                    if stmt.strip():
                        cur.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def _extract_from_pdf(self, pdf_path):
        sys.path.insert(0, str(PIPELINE_ROOT / "01_INGESTION"))
        from PDF_EXTRACTOR import PDFExtractor
        extractor = PDFExtractor(enable_ocr=False, max_pages=100)
        return extractor.extract_all(pdf_path)

    def _extract_metadata_from_text(self, text):
        lines = text.strip().split("\n")
        metadata = {"title": "", "authors": "", "abstract": "", "year": None, "doi": None, "keywords": None}
        for line in lines[:10]:
            clean = line.strip()
            if len(clean) > 10 and not clean.startswith(("Abstract", "ABSTRACT")):
                metadata["title"] = clean[:200]
                break
        for line in lines[:15]:
            clean = line.strip()
            if clean and clean != metadata["title"]:
                if ("," in clean or " and " in clean) and len(clean) < 300:
                    metadata["authors"] = clean
                    break
        abs_match = re.search(r"(?:abstract|ABSTRACT)[:\s]*\n?(.*?)(?:\n\s*\n|\n\s*(?:1\.|introduction|keywords))", text, re.DOTALL | re.IGNORECASE)
        if abs_match:
            metadata["abstract"] = abs_match.group(1).strip()[:2000]
        year_match = re.search(r"(?:19|20)\d{2}", text[:3000])
        if year_match:
            metadata["year"] = int(year_match.group())
        return metadata

    def _chunk_paper(self, text, chunk_size=1500, overlap=200):
        chunks = []
        section_pattern = re.compile(r"\n\s*(\d+\.?\s+[A-Z][A-Za-z ]+(?:\n|$))")
        sections = section_pattern.split(text)
        current_section = "Preamble"
        buffer = ""
        for part in sections:
            if re.match(r"\d+\.?\s+[A-Z]", part.strip()):
                if buffer.strip():
                    for chunk_text in self._sliding_window(buffer.strip(), chunk_size, overlap):
                        chunks.append({"section": current_section, "heading": current_section, "content": chunk_text, "char_count": len(chunk_text)})
                current_section = part.strip().title()
                buffer = ""
            else:
                buffer += part
        if buffer.strip():
            for chunk_text in self._sliding_window(buffer.strip(), chunk_size, overlap):
                chunks.append({"section": current_section, "heading": current_section, "content": chunk_text, "char_count": len(chunk_text)})
        if not chunks:
            for j, chunk_text in enumerate(self._sliding_window(text.strip(), chunk_size, overlap)):
                chunks.append({"section": "Full Text", "heading": "Chunk {}".format(j + 1), "content": chunk_text, "char_count": len(chunk_text)})
        return chunks

    def _sliding_window(self, text, size, overlap):
        chunks = []
        start = 0
        while start < len(text):
            end = start + size
            chunk = text[start:end]
            if len(chunk.strip()) > 50:
                chunks.append(chunk)
            start += size - overlap
        return chunks

    def ingest_paper(self, pdf_path, title=None, authors=None, abstract=None, year=None, chunk_size=1500, overlap=200):
        from EMBEDDING_GENERATOR import embed_texts
        from VECTOR_STORE import get_connection

        pdf_path = Path(pdf_path)
        file_hash = hashlib.md5(pdf_path.read_bytes()[:8192]).hexdigest()[:12]
        paper_id = "PAPER_{}".format(file_hash)

        pdf_data = self._extract_from_pdf(pdf_path)
        full_text = pdf_data["text"]
        auto_meta = self._extract_metadata_from_text(full_text)

        final_meta = {
            "title": title or auto_meta["title"] or pdf_path.stem,
            "authors": authors or auto_meta["authors"] or "Unknown",
            "abstract": abstract or auto_meta["abstract"] or "",
            "year": year or auto_meta["year"],
            "doi": auto_meta.get("doi"),
            "keywords": auto_meta.get("keywords") or "",
        }

        chunks = self._chunk_paper(full_text, chunk_size, overlap)
        chunk_texts = [c["content"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research_paper (paper_id, title, authors, abstract, year, doi, keywords, file_path, total_chunks)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (paper_id) DO UPDATE SET title = EXCLUDED.title, total_chunks = EXCLUDED.total_chunks
                """, (paper_id, final_meta["title"], final_meta["authors"], final_meta["abstract"],
                      final_meta["year"], final_meta["doi"], final_meta["keywords"], str(pdf_path), len(chunks)))

                for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                    chunk_id = "{}_chunk_{:04d}".format(paper_id, i)
                    cur.execute("""
                        INSERT INTO paper_chunk (chunk_id, paper_id, chunk_index, section, heading, content, char_count, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (chunk_id) DO NOTHING
                    """, (chunk_id, paper_id, i, chunk["section"], chunk["heading"], chunk["content"], chunk["char_count"], embedding))
            conn.commit()
        finally:
            conn.close()

        return {"paper_id": paper_id, "title": final_meta["title"], "chunks": len(chunks), "metadata": final_meta}

    def ingest_directory(self, pdf_dir, chunk_size=1500, overlap=200):
        pdf_dir = Path(pdf_dir)
        results = []
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            try:
                result = self.ingest_paper(pdf_path, chunk_size=chunk_size, overlap=overlap)
                results.append(result)
                print("  OK: {} -> {} ({} chunks)".format(pdf_path.name, result["paper_id"], result["chunks"]))
            except Exception as e:
                print("  FAIL: {}: {}".format(pdf_path.name, e))
                results.append({"file": pdf_path.name, "error": str(e)})
        return results

    def search(self, query, top_k=10, paper_id=None, section=None):
        from EMBEDDING_GENERATOR import embed_texts
        from VECTOR_STORE import get_connection

        query_embedding = embed_texts([query])[0]
        conn = get_connection()
        try:
            sql = """
                SELECT pc.chunk_id, pc.paper_id, pc.section, pc.heading, pc.content, pc.char_count,
                       rp.title, rp.authors, rp.year, rp.doi,
                       1 - (pc.embedding <=> %s::vector) AS similarity
                FROM paper_chunk pc JOIN research_paper rp ON pc.paper_id = rp.paper_id WHERE 1=1
            """
            params = [query_embedding]
            if paper_id:
                sql += " AND pc.paper_id = %s"
                params.append(paper_id)
            if section:
                sql += " AND lower(pc.section) = lower(%s)"
                params.append(section)
            sql += " ORDER BY pc.embedding <=> %s::vector LIMIT %s"
            params.extend([query_embedding, top_k])
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def ask(self, question, top_k=5, paper_id=None):
        chunks = self.search(question, top_k=top_k, paper_id=paper_id)
        if not chunks:
            return {"question": question, "answer": "No papers found.", "sources": [], "llm": "none"}

        context_parts = []
        for i, chunk in enumerate(chunks):
            source = "[{} by {} ({})]".format(chunk["title"], chunk.get("authors", ""), chunk.get("year", ""))
            context_parts.append("--- Source {} {} ---\n{}".format(i + 1, source, chunk["content"]))
        context_block = "\n\n".join(context_parts)

        prompt = "You are a research assistant. Answer using ONLY the content below. Cite with [Source N]. Be precise.\n\nQUESTION: {}\n\nCONTENT:\n{}".format(question, context_block)
        reply = GROQ_CLIENT.chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=2000)

        if reply is None:
            reply = "[Mock mode]\nRetrieved {} chunks:\n".format(len(chunks))
            for c in chunks:
                reply += "- {} ({:.2f})\n".format(c["title"], c["similarity"])

        return {
            "question": question, "answer": reply.strip(), "llm": "groq" if GROQ_CLIENT.is_available() else "mock",
            "sources": [{"paper_id": c["paper_id"], "title": c["title"], "authors": c.get("authors", ""),
                         "year": c.get("year"), "section": c["section"], "similarity": round(c["similarity"], 4),
                         "excerpt": c["content"][:200] + "..."} for c in chunks],
        }

    def list_papers(self):
        from VECTOR_STORE import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT paper_id, title, authors, year, keywords, total_chunks, ingested_at FROM research_paper ORDER BY ingested_at DESC")
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_paper(self, paper_id):
        from VECTOR_STORE import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM research_paper WHERE paper_id = %s", (paper_id,))
                cols = [d[0] for d in cur.description]
                row = cur.fetchone()
                return dict(zip(cols, row)) if row else None
        finally:
            conn.close()

    def delete_paper(self, paper_id):
        from VECTOR_STORE import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM paper_chunk WHERE paper_id = %s", (paper_id,))
                cur.execute("DELETE FROM research_paper WHERE paper_id = %s", (paper_id,))
            conn.commit()
            return True
        finally:
            conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PIPELINE_ROOT / ".env")
    import argparse
    parser = argparse.ArgumentParser(description="Research Paper RAG")
    sub = parser.add_subparsers(dest="command")
    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_ask = sub.add_parser("ask")
    p_ask.add_argument("question")
    sub.add_parser("list")
    args = parser.parse_args()

    rag = ResearchPaperRAG()
    if args.command == "ingest":
        path = Path(args.path)
        if path.is_dir():
            results = rag.ingest_directory(path)
            print("\nIngested {} papers".format(len([r for r in results if "paper_id" in r])))
        else:
            result = rag.ingest_paper(path)
            print("\nIngested: {} - {}".format(result["paper_id"], result["title"]))
    elif args.command == "search":
        for r in rag.search(args.query):
            print("[{:.3f}] {} | {}".format(r["similarity"], r["title"], r["section"]))
            print("  {}...".format(r["content"][:150]))
    elif args.command == "ask":
        result = rag.ask(args.question)
        print("\nAnswer: {}".format(result["answer"]))
    elif args.command == "list":
        for p in rag.list_papers():
            print("  {}: {} ({}) - {} chunks".format(p["paper_id"], p["title"], p.get("year", "?"), p["total_chunks"]))
    else:
        parser.print_help()
