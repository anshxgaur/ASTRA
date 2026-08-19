import hashlib, os, re, sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(".env")
load_dotenv("pipeline/.env")
from pypdf import PdfReader

def parse_pdf(args):
    file_path, file_name = args
    try:
        reader = PdfReader(file_path)
        text = "".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return (file_path, file_name, text.strip())
    except:
        pass
    return None

def extract_metadata(text):
    meta = {"candidate_name": "", "email": "", "phone": "", "skills": "", "experience_years": 0}
    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    if email_match: meta["email"] = email_match.group()
    phone_match = re.search(r"[\+]?[\d\-\(\)]{10,15}", text)
    if phone_match: meta["phone"] = phone_match.group()
    exp_match = re.search(r"(\d+)[\+]?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)", text, re.IGNORECASE)
    if exp_match: meta["experience_years"] = int(exp_match.group(1))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:5]:
        words = line.split()
        if 2 <= len(words) <= 5 and all(w[0].isupper() for w in words if len(w) > 1):
            if not any(c in line.lower() for c in ["@", "http", "phone", "email"]):
                meta["candidate_name"] = line[:100]
                break
    skills_match = re.search(r"(?:skills?|technical skills?|technologies?)[:\s]*(.*?)(?:\n\s*\n|\n\s*(?:experience|education|projects))", text, re.DOTALL | re.IGNORECASE)
    if skills_match: meta["skills"] = skills_match.group(1).strip()[:1000]
    return meta

def chunk_text(text, size=1200, overlap=200):
    chunks, start = [], 0
    while start < len(text):
        chunk = text[start:start + size]
        if len(chunk.strip()) > 50: chunks.append(chunk)
        start += size - overlap
    return chunks

def main():
    FOLDER_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/resumes/"
    print("=" * 60)
    print("FAST RESUME INGESTION")
    print("=" * 60)
    pdf_files = [(str(p), p.name) for p in Path(FOLDER_PATH).glob("*.pdf")]
    print("Found {} PDF files".format(len(pdf_files)))

    import psycopg
    from pgvector.psycopg import register_vector
    conn = psycopg.connect(host=os.getenv("POSTGRES_HOST","localhost"), port=int(os.getenv("POSTGRES_PORT","5433")),
                           dbname=os.getenv("POSTGRES_DB","aicte_canonical"), user=os.getenv("POSTGRES_USER","postgres"),
                           password=os.getenv("POSTGRES_PASSWORD",""))
    register_vector(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT file_path FROM resume")
        existing = set(r[0] for r in cur.fetchall())
    remaining = [(fp, fn) for fp, fn in pdf_files if fp not in existing]
    print("Already: {}, remaining: {}".format(len(existing), len(remaining)))
    if not remaining:
        print("Nothing to do!")
        conn.close()
        return

    print("Extracting text (parallel)...")
    documents = []
    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(parse_pdf, args): args for args in remaining}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result: documents.append(result)
            if (i + 1) % 100 == 0:
                print("  Extracted {}/{}".format(i+1, len(remaining)))
    print("Extracted {} PDFs".format(len(documents)))

    print("Loading embedding model...")
    from fastembed import TextEmbedding
    model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")

    print("Embedding and inserting...")
    BATCH_SIZE = 50
    total_ok, total_fail = 0, 0

    for batch_start in range(0, len(documents), BATCH_SIZE):
        batch = documents[batch_start:batch_start + BATCH_SIZE]
        rows, chunk_rows = [], []
        for file_path, file_name, text in batch:
            file_hash = hashlib.md5(open(file_path, "rb").read()[:8192]).hexdigest()[:12]
            resume_id = "RESUME_{}".format(file_hash)
            meta = extract_metadata(text)
            candidate_name = meta["candidate_name"] or file_name.replace(".pdf", "")
            rows.append((resume_id, candidate_name, meta["email"], meta["phone"],
                         "", "", "", meta["skills"], meta["experience_years"],
                         "", "", "", len(chunk_text(text)), file_path))
            chunks = chunk_text(text)
            embeddings = list(model.embed(chunks, batch_size=32))
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = "{}_chunk_{:04d}".format(resume_id, i)
                chunk_rows.append((chunk_id, resume_id, i, "Resume", chunk, len(chunk), emb.tolist()))

        try:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO resume (resume_id, candidate_name, email, phone, linkedin_url,
                    location, summary, skills, experience_years, education, certifications, languages,
                    total_sections, file_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (resume_id) DO NOTHING""", rows)
                cur.executemany("""INSERT INTO resume_chunk (chunk_id, resume_id, chunk_index, section,
                    content, char_count, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT (chunk_id) DO NOTHING""", chunk_rows)
            conn.commit()
            total_ok += len(rows)
        except Exception as e:
            conn.rollback()
            total_fail += len(rows)
            print("  Batch error: {}".format(e))

        done = batch_start + len(batch)
        if done % 200 == 0 or done >= len(documents):
            print("  Progress: {}/{} ({} ok, {} fail)".format(done, len(documents), total_ok, total_fail))

    conn.close()
    conn = psycopg.connect(host=os.getenv("POSTGRES_HOST","localhost"), port=int(os.getenv("POSTGRES_PORT","5433")),
                           dbname=os.getenv("POSTGRES_DB","aicte_canonical"), user=os.getenv("POSTGRES_USER","postgres"),
                           password=os.getenv("POSTGRES_PASSWORD",""))
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM resume")
        print("\nTotal resumes: {}".format(cur.fetchone()[0]))
        cur.execute("SELECT COUNT(*) FROM resume_chunk")
        print("Total chunks: {}".format(cur.fetchone()[0]))
    conn.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
