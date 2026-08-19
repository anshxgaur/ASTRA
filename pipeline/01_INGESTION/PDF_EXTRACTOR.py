"""
01_INGESTION — PDF_EXTRACTOR.py
Extract structured data (tables + text) from AICTE PDF documents.

Handles:
  - Scanned PDFs (OCR with pytesseract)
  - Digital PDFs (direct text extraction)
  - Table extraction (tabula-py + pdfplumber)
  - Batch processing for 1000+ PDFs
  - Error handling + logging
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pandas as pd

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    import tabula
    TABULA_AVAILABLE = True
except ImportError:
    TABULA_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image
    import io
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PDF_EXTRACTOR")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PDFExtractor:
    """Extract structured data from PDF documents."""

    def __init__(self, enable_ocr: bool = True, max_pages: int = 50):
        self.enable_ocr = enable_ocr and OCR_AVAILABLE
        self.max_pages = max_pages

        backends = []
        if PDFPLUMBER_AVAILABLE:
            backends.append("pdfplumber")
        if PYMUPDF_AVAILABLE:
            backends.append("PyMuPDF")
        if TABULA_AVAILABLE:
            backends.append("tabula")
        if self.enable_ocr:
            backends.append("OCR/pytesseract")

        logger.info(f"[PDF] Available backends: {', '.join(backends) or 'NONE'}")

        if not backends:
            raise RuntimeError(
                "No PDF libraries installed. Run:\n"
                "pip install pdfplumber PyMuPDF pytesseract tabula-py"
            )

    # ---- TEXT EXTRACTION ----

    def extract_text(self, pdf_path: str | Path) -> str:
        pdf_path = Path(pdf_path)
        if PYMUPDF_AVAILABLE:
            return self._extract_text_pymupdf(pdf_path)
        elif PDFPLUMBER_AVAILABLE:
            return self._extract_text_pdfplumber(pdf_path)
        else:
            raise RuntimeError("No text extraction backend available")

    def _extract_text_pymupdf(self, pdf_path: Path) -> str:
        doc = fitz.open(str(pdf_path))
        text_parts = []
        for i, page in enumerate(doc):
            if i >= self.max_pages:
                break
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)

    def _extract_text_pdfplumber(self, pdf_path: Path) -> str:
        text_parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= self.max_pages:
                    break
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        return "\n".join(text_parts)

    # ---- TABLE EXTRACTION ----

    def extract_tables(self, pdf_path: str | Path) -> list[pd.DataFrame]:
        pdf_path = Path(pdf_path)
        tables = []

        if TABULA_AVAILABLE:
            try:
                tables = self._extract_tables_tabula(pdf_path)
                if tables:
                    return tables
            except Exception as e:
                logger.warning(f"[PDF] Tabula failed: {e}")

        if PDFPLUMBER_AVAILABLE:
            try:
                tables = self._extract_tables_pdfplumber(pdf_path)
                if tables:
                    return tables
            except Exception as e:
                logger.warning(f"[PDF] pdfplumber failed: {e}")

        if PYMUPDF_AVAILABLE:
            try:
                tables = self._extract_tables_pymupdf(pdf_path)
            except Exception as e:
                logger.warning(f"[PDF] PyMuPDF failed: {e}")

        return tables

    def _extract_tables_tabula(self, pdf_path: Path) -> list[pd.DataFrame]:
        dfs = tabula.read_pdf(
            str(pdf_path), pages="all", multiple_tables=True,
            pandas_options={"header": 0},
        )
        return [df for df in dfs if len(df) >= 2 and len(df.columns) >= 2]

    def _extract_tables_pdfplumber(self, pdf_path: Path) -> list[pd.DataFrame]:
        tables = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= self.max_pages:
                    break
                page_tables = page.extract_tables()
                for table in page_tables:
                    if table and len(table) >= 2:
                        df = pd.DataFrame(table[1:], columns=table[0])
                        df = df.dropna(how="all")
                        if len(df) >= 2 and len(df.columns) >= 2:
                            tables.append(df)
        return tables

    def _extract_tables_pymupdf(self, pdf_path: Path) -> list[pd.DataFrame]:
        tables = []
        doc = fitz.open(str(pdf_path))
        for i, page in enumerate(doc):
            if i >= self.max_pages:
                break
            found = page.find_tables()
            for table in found:
                df = table.to_pandas()
                if len(df) >= 2 and len(df.columns) >= 2:
                    tables.append(df)
        doc.close()
        return tables

    # ---- OCR ----

    def extract_with_ocr(self, pdf_path: str | Path) -> str:
        if not OCR_AVAILABLE:
            raise RuntimeError("OCR not available")

        pdf_path = Path(pdf_path)
        text_parts = []

        if PYMUPDF_AVAILABLE:
            doc = fitz.open(str(pdf_path))
            for i, page in enumerate(doc):
                if i >= self.max_pages:
                    break
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img)
                text_parts.append(text)
            doc.close()

        return "\n".join(text_parts)

    def is_scanned(self, pdf_path: str | Path) -> bool:
        text = self.extract_text(pdf_path)
        return len(text.strip()) < 100

    # ---- FULL EXTRACTION ----

    def extract_all(self, pdf_path: str | Path) -> dict:
        pdf_path = Path(pdf_path)

        if PYMUPDF_AVAILABLE:
            doc = fitz.open(str(pdf_path))
            page_count = len(doc)
            doc.close()
        elif PDFPLUMBER_AVAILABLE:
            with pdfplumber.open(str(pdf_path)) as pdf:
                page_count = len(pdf.pages)
        else:
            page_count = 0

        is_scanned = self.is_scanned(pdf_path)

        if is_scanned and self.enable_ocr:
            text = self.extract_with_ocr(pdf_path)
        else:
            text = self.extract_text(pdf_path)

        tables = self.extract_tables(pdf_path)

        return {
            "text": text,
            "tables": tables,
            "page_count": page_count,
            "is_scanned": is_scanned,
            "file_path": str(pdf_path),
        }


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------

def process_pdf_batch(
    pdf_dir: str | Path,
    output_dir: str | Path,
    max_files: int = 1000,
    enable_ocr: bool = True,
) -> Generator[dict, None, None]:
    """Process a batch of PDFs from a directory."""
    pdf_dir = Path(pdf_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = list(pdf_dir.glob("*.pdf"))[:max_files]
    total = len(pdf_files)

    logger.info(f"[PDF] Found {total} PDFs to process")

    extractor = PDFExtractor(enable_ocr=enable_ocr)

    results = {
        "total_pdfs": total, "processed": 0, "failed": 0,
        "scanned": 0, "digital": 0, "total_tables": 0,
        "total_text_chars": 0, "errors": [],
        "start_time": datetime.now(timezone.utc).isoformat(),
    }

    for i, pdf_path in enumerate(pdf_files):
        yield {"current": i + 1, "total": total, "file": pdf_path.name, "status": "processing"}

        try:
            data = extractor.extract_all(pdf_path)

            for j, table in enumerate(data["tables"]):
                csv_path = output_dir / f"{pdf_path.stem}_table_{j}.csv"
                table.to_csv(csv_path, index=False)
                results["total_tables"] += 1

            text_path = output_dir / f"{pdf_path.stem}_text.json"
            text_data = {
                "file": pdf_path.name, "text": data["text"],
                "page_count": data["page_count"], "is_scanned": data["is_scanned"],
                "table_count": len(data["tables"]),
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            }
            text_path.write_text(json.dumps(text_data, ensure_ascii=False, indent=2))

            results["processed"] += 1
            results["total_text_chars"] += len(data["text"])
            if data["is_scanned"]:
                results["scanned"] += 1
            else:
                results["digital"] += 1

            yield {"current": i + 1, "total": total, "file": pdf_path.name,
                   "status": "success", "tables": len(data["tables"]),
                   "text_chars": len(data["text"])}

        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"file": pdf_path.name, "error": str(e)})
            yield {"current": i + 1, "total": total, "file": pdf_path.name,
                   "status": "failed", "error": str(e)}

    results["end_time"] = datetime.now(timezone.utc).isoformat()
    summary_path = output_dir / "extraction_summary.json"
    summary_path.write_text(json.dumps(results, indent=2))

    logger.info(f"[PDF] Done: {results['processed']}/{total} processed, {results['failed']} failed")
    yield {"status": "complete", "results": results}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract data from PDFs")
    parser.add_argument("pdf_dir", help="Directory containing PDF files")
    parser.add_argument("-o", "--output", default="./extracted_data", help="Output directory")
    parser.add_argument("-n", "--max-files", type=int, default=1000, help="Max PDFs")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR")
    args = parser.parse_args()

    for progress in process_pdf_batch(args.pdf_dir, args.output,
                                       max_files=args.max_files,
                                       enable_ocr=not args.no_ocr):
        if progress.get("status") == "complete":
            print(f"\nDone! {json.dumps(progress['results'], indent=2)}")
        elif progress.get("status") == "success":
            print(f"[{progress['current']}/{progress['total']}] OK {progress['file']}")
        elif progress.get("status") == "failed":
            print(f"[{progress['current']}/{progress['total']}] FAIL {progress['file']}: {progress.get('error')}")
