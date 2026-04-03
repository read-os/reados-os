"""
ReadOS Database Layer
SQLite-backed persistent store for books, users, progress, and settings
"""

import sqlite3
import logging
import json
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ReadOS.Database")

# Thread-local connection storage
_local = threading.local()


class Database:
    """Thread-safe SQLite database wrapper."""

    SCHEMA_VERSION = 4

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if not hasattr(_local, "conn") or _local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            _local.conn = conn
        return _local.conn

    @contextmanager
    def cursor(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def _init_db(self):
        """Create all tables if they don't exist."""
        with self.cursor() as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS users (
                    id          TEXT PRIMARY KEY,
                    email       TEXT UNIQUE NOT NULL,
                    username    TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    cloud_token TEXT,
                    settings    TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS books (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    author      TEXT DEFAULT 'Unknown',
                    format      TEXT NOT NULL,
                    file_path   TEXT NOT NULL,
                    cover_path  TEXT,
                    language    TEXT DEFAULT 'en',
                    publisher   TEXT,
                    year        TEXT,
                    isbn        TEXT,
                    description TEXT,
                    file_size   INTEGER DEFAULT 0,
                    page_count  INTEGER DEFAULT 0,
                    added_at    TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    source      TEXT DEFAULT 'local',
                    cloud_id    TEXT,
                    metadata    TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS user_books (
                    user_id     TEXT NOT NULL,
                    book_id     TEXT NOT NULL,
                    added_at    TEXT NOT NULL,
                    is_favorite INTEGER DEFAULT 0,
                    tags        TEXT DEFAULT '[]',
                    PRIMARY KEY (user_id, book_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS reading_progress (
                    user_id     TEXT NOT NULL,
                    book_id     TEXT NOT NULL,
                    position    TEXT NOT NULL,
                    chapter     INTEGER DEFAULT 0,
                    percentage  REAL DEFAULT 0.0,
                    updated_at  TEXT NOT NULL,
                    synced_at   TEXT,
                    PRIMARY KEY (user_id, book_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS bookmarks (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    book_id     TEXT NOT NULL,
                    position    TEXT NOT NULL,
                    chapter     INTEGER DEFAULT 0,
                    note        TEXT,
                    created_at  TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS highlights (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    book_id     TEXT NOT NULL,
                    text        TEXT NOT NULL,
                    position    TEXT NOT NULL,
                    color       TEXT DEFAULT 'yellow',
                    note        TEXT,
                    created_at  TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sync_queue (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    attempts    INTEGER DEFAULT 0,
                    last_error  TEXT
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token       TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_books_title   ON books(title);
                CREATE INDEX IF NOT EXISTS idx_books_author  ON books(author);
                CREATE INDEX IF NOT EXISTS idx_progress_user ON reading_progress(user_id);
                CREATE INDEX IF NOT EXISTS idx_sync_user     ON sync_queue(user_id);
            """)
        logger.info("Database schema initialized")

    # ── Generic helpers ────────────────────────────────────────────────────

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid

    def executemany(self, sql: str, params_list: list) -> None:
        with self.cursor() as cur:
            cur.executemany(sql, params_list)

    # ── Book helpers ───────────────────────────────────────────────────────

    def upsert_book(self, book: Dict) -> str:
        now = datetime.utcnow().isoformat()
        book.setdefault("added_at", now)
        book["updated_at"] = now
        if "metadata" in book and isinstance(book["metadata"], dict):
            book["metadata"] = json.dumps(book["metadata"])
        cols = ", ".join(book.keys())
        placeholders = ", ".join("?" * len(book))
        updates = ", ".join(f"{k}=excluded.{k}" for k in book if k != "id")
        sql = f"""
            INSERT INTO books ({cols}) VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {updates}
        """
        self.execute(sql, tuple(book.values()))
        return book["id"]

    def get_book(self, book_id: str) -> Optional[Dict]:
        b = self.fetchone("SELECT * FROM books WHERE id=?", (book_id,))
        if b and b.get("metadata"):
            try:
                b["metadata"] = json.loads(b["metadata"])
            except Exception:
                b["metadata"] = {}
        return b

    def get_all_books(self) -> List[Dict]:
        books = self.fetchall("SELECT * FROM books ORDER BY title ASC")
        for b in books:
            if b.get("metadata"):
                try:
                    b["metadata"] = json.loads(b["metadata"])
                except Exception:
                    b["metadata"] = {}
        return books

    def get_user_books(self, user_id: str) -> List[Dict]:
        sql = """
            SELECT b.*, ub.is_favorite, ub.tags, ub.added_at as shelf_added
            FROM books b
            JOIN user_books ub ON b.id = ub.book_id
            WHERE ub.user_id = ?
            ORDER BY b.title ASC
        """
        books = self.fetchall(sql, (user_id,))
        for b in books:
            if b.get("metadata"):
                try:
                    b["metadata"] = json.loads(b["metadata"])
                except Exception:
                    b["metadata"] = {}
        return books

    def delete_book(self, book_id: str) -> bool:
        self.execute("DELETE FROM books WHERE id=?", (book_id,))
        return True

    # ── Progress helpers ───────────────────────────────────────────────────

    def save_progress(self, user_id: str, book_id: str, position: str,
                      chapter: int = 0, percentage: float = 0.0) -> None:
        now = datetime.utcnow().isoformat()
        self.execute("""
            INSERT INTO reading_progress (user_id, book_id, position, chapter, percentage, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                position=excluded.position,
                chapter=excluded.chapter,
                percentage=excluded.percentage,
                updated_at=excluded.updated_at
        """, (user_id, book_id, position, chapter, percentage, now))

    def get_progress(self, user_id: str, book_id: str) -> Optional[Dict]:
        return self.fetchone(
            "SELECT * FROM reading_progress WHERE user_id=? AND book_id=?",
            (user_id, book_id)
        )

    # ── Bookmark helpers ───────────────────────────────────────────────────

    def add_bookmark(self, bookmark: Dict) -> str:
        now = datetime.utcnow().isoformat()
        bookmark["created_at"] = now
        cols = ", ".join(bookmark.keys())
        ph = ", ".join("?" * len(bookmark))
        self.execute(f"INSERT INTO bookmarks ({cols}) VALUES ({ph})", tuple(bookmark.values()))
        return bookmark["id"]

    def get_bookmarks(self, user_id: str, book_id: str) -> List[Dict]:
        return self.fetchall(
            "SELECT * FROM bookmarks WHERE user_id=? AND book_id=? ORDER BY created_at ASC",
            (user_id, book_id)
        )

    def delete_bookmark(self, bookmark_id: str, user_id: str) -> bool:
        self.execute("DELETE FROM bookmarks WHERE id=? AND user_id=?", (bookmark_id, user_id))
        return True

    # ── Session helpers ────────────────────────────────────────────────────

    def create_session(self, token: str, user_id: str, expires_at: str) -> None:
        now = datetime.utcnow().isoformat()
        self.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, expires_at)
        )

    def get_session(self, token: str) -> Optional[Dict]:
        return self.fetchone(
            "SELECT * FROM sessions WHERE token=? AND expires_at > ?",
            (token, datetime.utcnow().isoformat())
        )

    def delete_session(self, token: str) -> None:
        self.execute("DELETE FROM sessions WHERE token=?", (token,))

    def cleanup_expired_sessions(self) -> None:
        self.execute("DELETE FROM sessions WHERE expires_at <= ?",
                     (datetime.utcnow().isoformat(),))

    # ── Sync queue helpers ─────────────────────────────────────────────────

    def queue_sync(self, item_id: str, user_id: str, action: str, payload: Dict) -> None:
        now = datetime.utcnow().isoformat()
        self.execute("""
            INSERT OR IGNORE INTO sync_queue (id, user_id, action, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (item_id, user_id, action, json.dumps(payload), now))

    def get_pending_syncs(self, user_id: str) -> List[Dict]:
        return self.fetchall(
            "SELECT * FROM sync_queue WHERE user_id=? AND attempts < 5 ORDER BY created_at ASC",
            (user_id,)
        )

    def mark_sync_done(self, item_id: str) -> None:
        self.execute("DELETE FROM sync_queue WHERE id=?", (item_id,))

    def mark_sync_failed(self, item_id: str, error: str) -> None:
        self.execute(
            "UPDATE sync_queue SET attempts=attempts+1, last_error=? WHERE id=?",
            (error, item_id)
        )
