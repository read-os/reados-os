"""
ReadOS Library Manager
Handles book discovery, parsing, cover extraction, and metadata for
EPUB, PDF, and TXT formats.
"""

import os
import io
import uuid
import json
import shutil
import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("ReadOS.Library")

# ── Optional heavy imports (graceful degradation) ──────────────────────────
try:
    import ebooklib
    from ebooklib import epub
    HAS_EBOOKLIB = True
except ImportError:
    HAS_EBOOKLIB = False
    logger.warning("ebooklib not installed — EPUB support limited")

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    logger.warning("PyMuPDF not installed — PDF support limited")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


SUPPORTED_FORMATS = {".epub", ".pdf", ".txt", ".mobi"}


class LibraryManager:
    """Manages the local book library: scanning, parsing, indexing."""

    def __init__(self, config):
        from database import Database
        self.cfg = config
        self.db = Database(config.db_path)
        self.books_dir = Path(config.books_dir)
        self.covers_dir = Path(config.covers_dir)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LibraryManager ready — books_dir={self.books_dir}")

    # ── Public API ─────────────────────────────────────────────────────────

    def scan_library(self) -> List[Dict]:
        """Scan the books directory and index any new books."""
        found = []
        if not self.books_dir.exists():
            logger.warning(f"Books directory does not exist: {self.books_dir}")
            return found

        for path in self.books_dir.rglob("*"):
            if path.suffix.lower() in SUPPORTED_FORMATS and path.is_file():
                try:
                    book = self._index_book(path)
                    if book:
                        found.append(book)
                except Exception as e:
                    logger.error(f"Failed to index {path}: {e}")
        logger.info(f"Library scan complete — {len(found)} books indexed")
        return found

    def get_all_books(self) -> List[Dict]:
        return self.db.get_all_books()

    def get_book(self, book_id: str) -> Optional[Dict]:
        return self.db.get_book(book_id)

    def add_book_file(self, file_path: str, user_id: Optional[str] = None) -> Dict:
        """Add a book from a file path and return its metadata."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Book file not found: {file_path}")
        if path.suffix.lower() not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {path.suffix}")

        # Copy to books dir if not already there
        dest = self.books_dir / path.name
        if path.resolve() != dest.resolve():
            shutil.copy2(str(path), str(dest))

        book = self._index_book(dest, user_id=user_id)
        return book

    def delete_book(self, book_id: str, delete_file: bool = False) -> bool:
        book = self.db.get_book(book_id)
        if not book:
            return False
        if delete_file and book.get("file_path"):
            try:
                os.remove(book["file_path"])
            except OSError as e:
                logger.warning(f"Could not delete file: {e}")
        if book.get("cover_path"):
            try:
                os.remove(book["cover_path"])
            except OSError:
                pass
        self.db.delete_book(book_id)
        return True

    def get_cover_path(self, book_id: str) -> Optional[str]:
        book = self.db.get_book(book_id)
        if book and book.get("cover_path") and os.path.exists(book["cover_path"]):
            return book["cover_path"]
        return None

    def search_books(self, query: str, user_id: Optional[str] = None) -> List[Dict]:
        q = f"%{query.lower()}%"
        if user_id:
            books = self.db.get_user_books(user_id)
        else:
            books = self.db.get_all_books()
        return [
            b for b in books
            if q.strip("%") in b.get("title", "").lower()
            or q.strip("%") in b.get("author", "").lower()
        ]

    # ── Indexing ───────────────────────────────────────────────────────────

    def _index_book(self, path: Path, user_id: Optional[str] = None) -> Dict:
        """Parse and store a book, returning its metadata dict."""
        book_id = self._file_id(path)
        existing = self.db.get_book(book_id)

        # If already indexed and file hasn't changed, return existing
        if existing:
            stat = path.stat()
            if existing.get("file_size") == stat.st_size:
                return existing

        fmt = path.suffix.lower().lstrip(".")
        meta = self._parse_metadata(path, fmt)

        cover_path = self._extract_cover(path, book_id, fmt)

        book = {
            "id": book_id,
            "title": meta.get("title") or path.stem,
            "author": meta.get("author", "Unknown"),
            "format": fmt,
            "file_path": str(path.resolve()),
            "cover_path": cover_path,
            "language": meta.get("language", ""),
            "publisher": meta.get("publisher", ""),
            "year": meta.get("year", ""),
            "isbn": meta.get("isbn", ""),
            "description": meta.get("description", ""),
            "file_size": path.stat().st_size,
            "page_count": meta.get("page_count", 0),
            "source": "local",
            "metadata": meta,
        }

        self.db.upsert_book(book)

        if user_id:
            self._link_book_to_user(book_id, user_id)

        logger.debug(f"Indexed: {book['title']} [{fmt}]")
        return book

    def _link_book_to_user(self, book_id: str, user_id: str):
        now = datetime.utcnow().isoformat()
        self.db.execute("""
            INSERT OR IGNORE INTO user_books (user_id, book_id, added_at)
            VALUES (?, ?, ?)
        """, (user_id, book_id, now))

    # ── Metadata extraction ────────────────────────────────────────────────

    def _parse_metadata(self, path: Path, fmt: str) -> Dict:
        if fmt == "epub":
            return self._parse_epub_meta(path)
        elif fmt == "pdf":
            return self._parse_pdf_meta(path)
        elif fmt == "txt":
            return self._parse_txt_meta(path)
        return {}

    def _parse_epub_meta(self, path: Path) -> Dict:
        if not HAS_EBOOKLIB:
            return {"title": path.stem}
        try:
            book = epub.read_epub(str(path), options={"ignore_ncx": True})
            meta = {}

            title = book.get_metadata("DC", "title")
            meta["title"] = title[0][0] if title else path.stem

            creator = book.get_metadata("DC", "creator")
            meta["author"] = creator[0][0] if creator else "Unknown"

            language = book.get_metadata("DC", "language")
            meta["language"] = language[0][0] if language else ""

            publisher = book.get_metadata("DC", "publisher")
            meta["publisher"] = publisher[0][0] if publisher else ""

            date = book.get_metadata("DC", "date")
            if date:
                meta["year"] = str(date[0][0])[:4]

            identifier = book.get_metadata("DC", "identifier")
            if identifier:
                meta["isbn"] = identifier[0][0]

            description = book.get_metadata("DC", "description")
            if description:
                meta["description"] = str(description[0][0])[:2000]

            # Count chapters/spine items
            meta["page_count"] = len(list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)))
            return meta
        except Exception as e:
            logger.warning(f"EPUB meta parse failed for {path}: {e}")
            return {"title": path.stem}

    def _parse_pdf_meta(self, path: Path) -> Dict:
        if not HAS_PYMUPDF:
            return {"title": path.stem}
        try:
            doc = fitz.open(str(path))
            info = doc.metadata or {}
            meta = {
                "title": info.get("title") or path.stem,
                "author": info.get("author") or "Unknown",
                "publisher": info.get("producer", ""),
                "year": str(info.get("creationDate", ""))[:4].strip("D:"),
                "page_count": doc.page_count,
            }
            doc.close()
            return meta
        except Exception as e:
            logger.warning(f"PDF meta parse failed for {path}: {e}")
            return {"title": path.stem, "page_count": 0}

    def _parse_txt_meta(self, path: Path) -> Dict:
        """Extract basic metadata from TXT — first non-empty lines as title."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [l.strip() for l in f.readlines()[:10] if l.strip()]
            title = lines[0] if lines else path.stem
            author = lines[1] if len(lines) > 1 else "Unknown"
            # Rough page count: ~250 words per page
            word_count = sum(len(l.split()) for l in lines)
            return {"title": title[:120], "author": author[:80], "page_count": max(1, word_count // 250)}
        except Exception:
            return {"title": path.stem}

    # ── Cover extraction ───────────────────────────────────────────────────

    def _extract_cover(self, path: Path, book_id: str, fmt: str) -> Optional[str]:
        out = self.covers_dir / f"{book_id}.jpg"
        if out.exists():
            return str(out)
        try:
            if fmt == "epub":
                return self._epub_cover(path, out)
            elif fmt == "pdf":
                return self._pdf_cover(path, out)
        except Exception as e:
            logger.warning(f"Cover extraction failed for {path}: {e}")
        return None

    def _epub_cover(self, path: Path, out: Path) -> Optional[str]:
        if not HAS_EBOOKLIB:
            return None
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
        # Try cover image item
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_COVER:
                out.write_bytes(item.get_content())
                return str(out)
        # Fallback: first image
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
            content = item.get_content()
            if len(content) > 5000:  # Skip tiny icons
                out.write_bytes(content)
                return str(out)
        return None

    def _pdf_cover(self, path: Path, out: Path) -> Optional[str]:
        if not HAS_PYMUPDF:
            return None
        doc = fitz.open(str(path))
        page = doc[0]
        mat = fitz.Matrix(0.5, 0.5)  # Smaller thumbnail
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(out))
        doc.close()
        return str(out)

    # ── Reader content ─────────────────────────────────────────────────────

    def get_epub_chapters(self, book_id: str) -> List[Dict]:
        """Return list of {id, title, content_html} for an EPUB."""
        book = self.db.get_book(book_id)
        if not book or book["format"] != "epub":
            return []
        if not HAS_EBOOKLIB:
            return [{"id": "0", "title": "Content", "content_html": "<p>ebooklib not installed</p>"}]
        try:
            eb = epub.read_epub(book["file_path"], options={"ignore_ncx": True})
            chapters = []
            for i, item in enumerate(eb.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
                content = item.get_content().decode("utf-8", errors="replace")
                # Strip script/style for safety
                import re
                content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
                content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
                chapters.append({
                    "id": str(i),
                    "item_id": item.id,
                    "title": item.get_name(),
                    "content_html": content,
                })
            return chapters
        except Exception as e:
            logger.error(f"EPUB chapter read failed: {e}")
            return []

    def get_pdf_page(self, book_id: str, page_num: int) -> Optional[Dict]:
        """Return a single PDF page as text + image data URI."""
        book = self.db.get_book(book_id)
        if not book or book["format"] != "pdf":
            return None
        if not HAS_PYMUPDF:
            return {"text": "PyMuPDF not installed", "image": None, "page": page_num}
        try:
            doc = fitz.open(book["file_path"])
            if page_num < 0 or page_num >= doc.page_count:
                page_num = 0
            page = doc[page_num]
            text = page.get_text("text")
            mat = fitz.Matrix(1.2, 1.2)
            pix = page.get_pixmap(matrix=mat)
            import base64
            img_b64 = base64.b64encode(pix.tobytes("png")).decode()
            doc.close()
            return {
                "text": text,
                "image": f"data:image/png;base64,{img_b64}",
                "page": page_num,
                "total_pages": doc.page_count if not doc.is_closed else 0,
            }
        except Exception as e:
            logger.error(f"PDF page read failed: {e}")
            return None

    def get_txt_content(self, book_id: str) -> Optional[str]:
        book = self.db.get_book(book_id)
        if not book or book["format"] != "txt":
            return None
        try:
            with open(book["file_path"], "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            logger.error(f"TXT read failed: {e}")
            return None

    # ── Utils ──────────────────────────────────────────────────────────────

    def _file_id(self, path: Path) -> str:
        """Stable ID from file path + size."""
        stat = path.stat()
        seed = f"{path.resolve()}{stat.st_size}"
        return hashlib.sha256(seed.encode()).hexdigest()[:16]
