"""
11_RETRIEVAL - RESUME_RAG.py
Resume Information Retrieval using RAG.
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


class ResumeRAG:

    def __init__(self):
        pass

    def _extract_from_pdf(self, pdf_path):
        sys.path.insert(0, str(PIPELINE_ROOT / "01_INGESTION"))
        from PDF_EXTRACTOR import PDFExtractor
        extractor = PDFExtractor(enable_ocr=False, max_pages=20)
        return extractor.extract_all(pdf_path)

    def _extract_metadata(self, text):
        metadata = {"candidate_name": "", "email": "", "phone": "", "linkedin_url": "",
                     "location": "", "summary": "", "skills": "", "experience_years": 0,
                     "education": "", "certifications": "", "languages": ""}
        email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        if email_match:
            metadata["email"] = email_match.group()
        phone_match = re.search(r"[\+]?[\d\-\(\)]{10,15}", text)
        if phone_match:
            metadata["phone"] = phone_match.group()
        linkedin_match = re.search(r"linkedin\.com/in/[a-zA-Z0-9_-]+", text)
        if linkedin_match:
            metadata["linkedin_url"] = "https://" + linkedin_match.group()
        exp_match = re.search(r"(\d+)[\+]?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)", text, re.IGNORECASE)
        if exp_match:
            metadata["experience_years"] = int(exp_match.group(1))
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:5]:
            words = line.split()
            if 2 <= len(words) <= 5 and all(w[0].isupper() for w in words if len(w) > 1):
                if not any(c in line.lower() for c in ["@", "http", "phone", "email"]):
                    metadata["candidate_name"] = line[:100]
                    break
        skills_match = re.search(r"(?:skills?|technical skills?|technologies?)[:\s]*(.*?)(?:\n\s*\n|\n\s*(?:experience|education|projects))", text, re.DOTALL | re.IGNORECASE)
        if skills_match:
            metadata["skills"] = skills_match.group(1).strip()[:1000]
        return metadata

    def _chunk_resume(self, text, chunk_size=1200, overlap=200):
        chunks = []
        section_pattern = re.compile(r"\n\s*([A-Z][A-Za-z ]+(?:\n|$))")
        parts = section_pattern.split(text)
        current_section = "Header"
        buffer = ""
        for part in parts:
            if re.match(r"[A-Z][A-Za-z ]+$", part.strip()):
                if buffer.strip():
                    for chunk_text in self._sliding_window(buffer.strip(), chunk_size, overlap):
                        chunks.append({"section": current_section, "content": chunk_text, "char_count": len(chunk_text)})
                current_section = part.strip().title()
                buffer = ""
            else:
                buffer += part
        if buffer.strip():
            for chunk_text in self._sliding_window(buffer.strip(), chunk_size, overlap):
                chunks.append({"section": current_section, "content": chunk_text, "char_count": len(chunk_text)})
        if not chunks:
            for chunk_text in self._sliding_window(text.strip(), chunk_size, overlap):
                chunks.append({"section": "Full Resume", "content": chunk_text, "char_count": len(chunk_text)})
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

    def ingest_resume(self, pdf_path, candidate_name=None):
        from EMBEDDING_GENERATOR import embed_texts
        from VECTOR_STORE import get_connection

        pdf_path = Path(pdf_path)
        file_hash = hashlib.md5(pdf_path.read_bytes()[:8192]).hexdigest()[:12]
        resume_id = "RESUME_{}".format(file_hash)

        pdf_data = self._extract_from_pdf(pdf_path)
        full_text = pdf_data["text"]
        auto_meta = self._extract_metadata(full_text)

        final_meta = {
            "candidate_name": candidate_name or auto_meta["candidate_name"] or pdf_path.stem,
            "email": auto_meta["email"], "phone": auto_meta["phone"],
            "linkedin_url": auto_meta["linkedin_url"], "location": auto_meta["location"],
            "summary": auto_meta["summary"], "skills": auto_meta["skills"],
            "experience_years": auto_meta["experience_years"],
            "education": auto_meta["education"], "certifications": auto_meta["certifications"],
            "languages": auto_meta["languages"],
        }

        chunks = self._chunk_resume(full_text)
        chunk_texts = [c["content"] for c in chunks]
        embeddings = embed_texts(chunk_texts)

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO resume (resume_id, candidate_name, email, phone, linkedin_url,
                                        location, summary, skills, experience_years, education,
                                        certifications, languages, total_sections, file_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (resume_id) DO UPDATE SET candidate_name = EXCLUDED.candidate_name
                """, (resume_id, final_meta["candidate_name"], final_meta["email"], final_meta["phone"],
                      final_meta["linkedin_url"], final_meta["location"], final_meta["summary"],
                      final_meta["skills"], final_meta["experience_years"], final_meta["education"],
                      final_meta["certifications"], final_meta["languages"], len(chunks), str(pdf_path)))

                for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                    chunk_id = "{}_chunk_{:04d}".format(resume_id, i)
                    cur.execute("""
                        INSERT INTO resume_chunk (chunk_id, resume_id, chunk_index, section,
                                                  content, char_count, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (chunk_id) DO NOTHING
                    """, (chunk_id, resume_id, i, chunk["section"], chunk["content"], chunk["char_count"], embedding))
            conn.commit()
        finally:
            conn.close()

        return {"resume_id": resume_id, "candidate_name": final_meta["candidate_name"], "chunks": len(chunks), "metadata": final_meta}

    def ingest_directory(self, pdf_dir):
        pdf_dir = Path(pdf_dir)
        results = []
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            try:
                result = self.ingest_resume(pdf_path)
                results.append(result)
                print("  OK: {} -> {} ({} chunks)".format(pdf_path.name, result["resume_id"], result["chunks"]))
            except Exception as e:
                print("  FAIL: {}: {}".format(pdf_path.name, e))
                results.append({"file": pdf_path.name, "error": str(e)})
        return results

    def search(self, query, top_k=10, resume_id=None):
        from EMBEDDING_GENERATOR import embed_texts
        from VECTOR_STORE import get_connection

        query_embedding = embed_texts([query])[0]
        conn = get_connection()
        try:
            sql = """
                SELECT rc.chunk_id, rc.resume_id, rc.section, rc.content, rc.char_count,
                       r.candidate_name, r.email, r.skills, r.experience_years,
                       r.location, r.education,
                       1 - (rc.embedding <=> %s::vector) AS similarity
                FROM resume_chunk rc JOIN resume r ON rc.resume_id = r.resume_id WHERE 1=1
            """
            params = [query_embedding]
            if resume_id:
                sql += " AND rc.resume_id = %s"
                params.append(resume_id)
            sql += " ORDER BY rc.embedding <=> %s::vector LIMIT %s"
            params.extend([query_embedding, top_k])
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def ask(self, question, top_k=5):
        chunks = self.search(question, top_k=top_k)
        if not chunks:
            return {"question": question, "answer": "No resumes found.", "sources": [], "llm": "none"}

        context_parts = []
        for i, chunk in enumerate(chunks):
            source = "[{}]".format(chunk.get("candidate_name", "Unknown"))
            context_parts.append("--- Candidate {} {} ---\n{}".format(i + 1, source, chunk["content"]))
        context_block = "\n\n".join(context_parts)

        prompt = "You are an HR assistant. Answer using ONLY resume content below. Cite with [Candidate N].\n\nQUESTION: {}\n\nRESUMES:\n{}".format(question, context_block)
        reply = GROQ_CLIENT.chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=2000)

        if reply is None:
            reply = "[Mock mode]\nRetrieved {} chunks:\n".format(len(chunks))
            for c in chunks:
                reply += "- {} ({:.2f})\n".format(c.get("candidate_name", "?"), c["similarity"])

        return {
            "question": question, "answer": reply.strip(), "llm": "groq" if GROQ_CLIENT.is_available() else "mock",
            "sources": [{"resume_id": c["resume_id"], "candidate_name": c.get("candidate_name", ""),
                         "skills": c.get("skills", "")[:200], "section": c["section"],
                         "similarity": round(c["similarity"], 4),
                         "excerpt": c["content"][:200] + "..."} for c in chunks],
        }

    def list_resumes(self):
        from VECTOR_STORE import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT resume_id, candidate_name, email, skills, experience_years, location, ingested_at FROM resume ORDER BY ingested_at DESC")
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def delete_resume(self, resume_id):
        from VECTOR_STORE import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM resume_chunk WHERE resume_id = %s", (resume_id,))
                cur.execute("DELETE FROM resume WHERE resume_id = %s", (resume_id,))
            conn.commit()
            return True
        finally:
            conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PIPELINE_ROOT / ".env")
    import argparse
    parser = argparse.ArgumentParser(description="Resume RAG")
    sub = parser.add_subparsers(dest="command")
    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_ask = sub.add_parser("ask")
    p_ask.add_argument("question")
    sub.add_parser("list")
    args = parser.parse_args()

    rag = ResumeRAG()
    if args.command == "ingest":
        path = Path(args.path)
        if path.is_dir():
            results = rag.ingest_directory(path)
            print("\nIngested {} resumes".format(len([r for r in results if "resume_id" in r])))
        else:
            result = rag.ingest_resume(path)
            print("\nIngested: {} - {}".format(result["resume_id"], result["candidate_name"]))
    elif args.command == "search":
        for r in rag.search(args.query):
            print("[{:.3f}] {} | {}".format(r["similarity"], r.get("candidate_name", "?"), r["section"]))
            print("  {}...".format(r["content"][:150]))
    elif args.command == "ask":
        result = rag.ask(args.question)
        print("\nAnswer: {}".format(result["answer"]))
    elif args.command == "list":
        for r in rag.list_resumes():
            print("  {}: {} ({} years exp)".format(r["resume_id"], r.get("candidate_name", "?"), r.get("experience_years", 0)))
    else:
        parser.print_help()
