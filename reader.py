"""
ReadOS Reader Engine
Manages active reading sessions, progress tracking, bookmarks,
and highlights for all supported formats.
"""

import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("ReadOS.Reader")


class ReaderEngine:
    """Core reading session and progress management."""

    def __init__(self, config):
        from database import Database
        from library import LibraryManager
        self.cfg = config
        self.db = Database(config.db_path)
        self.library = LibraryManager(config)

    # ── Session & Content ──────────────────────────────────────────────────

    def open_book(self, book_id: str, user_id: Optional[str] = None) -> Dict:
        """
        Open a book for reading. Returns full session data including
        content, progress, bookmarks, and table of contents.
        """
        book = self.library.get_book(book_id)
        if not book:
            raise FileNotFoundError(f"Book {book_id} not found")

        fmt = book["format"]
        session = {
            "book": book,
            "format": fmt,
            "progress": None,
            "bookmarks": [],
            "toc": [],
        }

        # Load saved progress
        if user_id:
            session["progress"] = self.db.get_progress(user_id, book_id)
            session["bookmarks"] = self.db.get_bookmarks(user_id, book_id)

        # Load format-specific content index
        if fmt == "epub":
            chapters = self.library.get_epub_chapters(book_id)
            session["chapters"] = chapters
            session["toc"] = [{"id": c["id"], "title": c["title"]} for c in chapters]
            session["total_chapters"] = len(chapters)
        elif fmt == "pdf":
            session["total_pages"] = book.get("page_count", 0)
            session["toc"] = [{"id": str(i), "title": f"Page {i+1}"}
                              for i in range(session["total_pages"])]
        elif fmt == "txt":
            content = self.library.get_txt_content(book_id)
            session["content"] = content or ""
            # Split into chunks for pagination
            session["chunks"] = self._paginate_text(content or "", chunk_chars=3000)
            session["total_pages"] = len(session["chunks"])

        return session

    def get_epub_chapter(self, book_id: str, chapter_id: str) -> Optional[Dict]:
        """Get a specific EPUB chapter by index."""
        chapters = self.library.get_epub_chapters(book_id)
        for ch in chapters:
            if ch["id"] == chapter_id:
                return ch
        return None

    def get_pdf_page(self, book_id: str, page_num: int) -> Optional[Dict]:
        return self.library.get_pdf_page(book_id, page_num)

    def get_txt_chunk(self, book_id: str, chunk_index: int) -> Optional[Dict]:
        content = self.library.get_txt_content(book_id)
        if not content:
            return None
        chunks = self._paginate_text(content, chunk_chars=3000)
        if chunk_index < 0 or chunk_index >= len(chunks):
            return None
        return {
            "chunk": chunk_index,
            "total": len(chunks),
            "text": chunks[chunk_index],
        }

    def _paginate_text(self, text: str, chunk_chars: int = 3000) -> List[str]:
        """Split plain text into reading-sized chunks at paragraph boundaries."""
        paragraphs = text.split("\n\n")
        chunks = []
        current = []
        current_len = 0
        for para in paragraphs:
            if current_len + len(para) > chunk_chars and current:
                chunks.append("\n\n".join(current))
                current = [para]
                current_len = len(para)
            else:
                current.append(para)
                current_len += len(para)
        if current:
            chunks.append("\n\n".join(current))
        return chunks or [""]

    # ── Progress ───────────────────────────────────────────────────────────

    def save_progress(self, user_id: str, book_id: str,
                      position: str, chapter: int = 0,
                      percentage: float = 0.0) -> Dict:
        """Save reading progress and queue for cloud sync."""
        self.db.save_progress(user_id, book_id, position, chapter, percentage)
        # Queue for background cloud sync
        sync_id = f"progress_{user_id}_{book_id}"
        self.db.queue_sync(sync_id, user_id, "progress", {
            "book_id": book_id,
            "position": position,
            "chapter": chapter,
            "percentage": percentage,
            "updated_at": datetime.utcnow().isoformat(),
        })
        return {"saved": True, "book_id": book_id, "percentage": percentage}

    def get_progress(self, user_id: str, book_id: str) -> Optional[Dict]:
        return self.db.get_progress(user_id, book_id)

    def get_all_progress(self, user_id: str) -> List[Dict]:
        return self.db.fetchall(
            "SELECT * FROM reading_progress WHERE user_id=? ORDER BY updated_at DESC",
            (user_id,)
        )

    # ── Bookmarks ──────────────────────────────────────────────────────────

    def add_bookmark(self, user_id: str, book_id: str,
                     position: str, chapter: int = 0,
                     note: Optional[str] = None) -> Dict:
        bm = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "book_id": book_id,
            "position": position,
            "chapter": chapter,
            "note": note,
        }
        self.db.add_bookmark(bm)
        self.db.queue_sync(f"bm_{bm['id']}", user_id, "bookmark_add", bm)
        return bm

    def delete_bookmark(self, user_id: str, bookmark_id: str) -> bool:
        result = self.db.delete_bookmark(bookmark_id, user_id)
        self.db.queue_sync(f"bm_del_{bookmark_id}", user_id, "bookmark_delete",
                           {"bookmark_id": bookmark_id})
        return result

    def get_bookmarks(self, user_id: str, book_id: str) -> List[Dict]:
        return self.db.get_bookmarks(user_id, book_id)

    # ── Highlights ─────────────────────────────────────────────────────────

    def add_highlight(self, user_id: str, book_id: str,
                      text: str, position: str,
                      color: str = "yellow",
                      note: Optional[str] = None) -> Dict:
        hl = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "book_id": book_id,
            "text": text[:2000],
            "position": position,
            "color": color,
            "note": note,
            "created_at": datetime.utcnow().isoformat(),
        }
        cols = ", ".join(hl.keys())
        ph = ", ".join("?" * len(hl))
        self.db.execute(f"INSERT INTO highlights ({cols}) VALUES ({ph})", tuple(hl.values()))
        return hl

    def get_highlights(self, user_id: str, book_id: str) -> List[Dict]:
        return self.db.fetchall(
            "SELECT * FROM highlights WHERE user_id=? AND book_id=? ORDER BY created_at ASC",
            (user_id, book_id)
        )

    def delete_highlight(self, user_id: str, highlight_id: str) -> bool:
        self.db.execute(
            "DELETE FROM highlights WHERE id=? AND user_id=?",
            (highlight_id, user_id)
        )
        return True
