"""
ReadOS Book Importer
Handles importing books from Google Drive, email attachments,
and direct file uploads.
"""

import os
import json
import uuid
import shutil
import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("ReadOS.Importer")

ALLOWED_EXTENSIONS = {".epub", ".pdf", ".txt", ".mobi"}
MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB


class BookImporter:
    """Handles all book import pipelines."""

    def __init__(self, config):
        self.cfg = config
        self.books_dir = Path(config.books_dir)
        self.temp_dir = Path(config.temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    # ── Local file upload ──────────────────────────────────────────────────

    def import_local_file(self, file_data: bytes, filename: str,
                          user_id: Optional[str] = None) -> Dict:
        """Import a book from raw bytes (e.g. multipart upload)."""
        self._validate_file(filename, len(file_data))

        safe_name = self._safe_filename(filename)
        dest = self.books_dir / safe_name

        # Avoid overwriting existing files
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            dest = self.books_dir / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"

        dest.write_bytes(file_data)
        logger.info(f"Imported local file: {dest}")

        from library import LibraryManager
        lib = LibraryManager(self.cfg)
        book = lib.add_book_file(str(dest), user_id=user_id)
        return {"book": book, "source": "local_upload"}

    # ── Google Drive ───────────────────────────────────────────────────────

    def get_gdrive_auth_url(self) -> str:
        """Return OAuth2 URL for Google Drive authorization."""
        client_id = self.cfg.get("google_drive.client_id", "")
        redirect_uri = self.cfg.get("google_drive.redirect_uri", "")
        if not client_id:
            raise ValueError("Google Drive not configured — set READOS_GDRIVE_CLIENT_ID")

        scope = "https://www.googleapis.com/auth/drive.readonly"
        params = (
            f"client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={scope}"
            f"&access_type=offline"
            f"&prompt=consent"
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"

    def exchange_gdrive_code(self, code: str) -> Dict:
        """Exchange OAuth2 code for access token."""
        import requests
        client_id = self.cfg.get("google_drive.client_id", "")
        client_secret = self.cfg.get("google_drive.client_secret", "")
        redirect_uri = self.cfg.get("google_drive.redirect_uri", "")

        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=15)
        resp.raise_for_status()
        return resp.json()  # {access_token, refresh_token, ...}

    def list_gdrive_books(self, access_token: str) -> List[Dict]:
        """List supported book files from Google Drive."""
        import requests
        mime_map = {
            "application/epub+zip": "epub",
            "application/pdf": "pdf",
            "text/plain": "txt",
            "application/x-mobipocket-ebook": "mobi",
        }
        mime_query = " or ".join(f"mimeType='{m}'" for m in mime_map)
        params = {
            "q": f"({mime_query}) and trashed=false",
            "fields": "files(id,name,mimeType,size,modifiedTime,webContentLink)",
            "pageSize": 100,
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            params=params, headers=headers, timeout=15
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        return [
            {
                "gdrive_id": f["id"],
                "name": f["name"],
                "format": mime_map.get(f.get("mimeType", ""), ""),
                "size": int(f.get("size", 0)),
                "modified": f.get("modifiedTime", ""),
            }
            for f in files
        ]

    def download_from_gdrive(self, gdrive_file_id: str, filename: str,
                              access_token: str, user_id: Optional[str] = None) -> Dict:
        """Download a file from Google Drive and import it."""
        import requests
        self._validate_file(filename, 0)  # Extension check only

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://www.googleapis.com/drive/v3/files/{gdrive_file_id}?alt=media"

        with requests.get(url, headers=headers, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            if total > MAX_FILE_BYTES:
                raise ValueError(f"File too large: {total} bytes")

            safe_name = self._safe_filename(filename)
            dest = self.books_dir / safe_name
            with open(str(dest), "wb") as f:
                for chunk in resp.iter_content(65536):
                    f.write(chunk)

        logger.info(f"Imported from Google Drive: {dest}")
        from library import LibraryManager
        lib = LibraryManager(self.cfg)
        book = lib.add_book_file(str(dest), user_id=user_id)
        return {"book": book, "source": "google_drive"}

    # ── Email attachment import ────────────────────────────────────────────

    def import_from_email_attachment(self, attachment_data: bytes,
                                      filename: str,
                                      user_id: Optional[str] = None) -> Dict:
        """
        Import a book from an email attachment.
        The email frontend (or webhook) passes the raw bytes here.
        """
        return self.import_local_file(attachment_data, filename, user_id=user_id)

    def get_email_import_address(self, user_id: str) -> str:
        """
        Return a user-specific import-by-email address.
        Requires email webhook setup (e.g. Mailgun, SendGrid inbound parse).
        """
        import hashlib
        h = hashlib.sha256(f"{user_id}{self.cfg.get('secret_key', '')}".encode()).hexdigest()[:12]
        domain = self.cfg.get("email_import.domain", "import.reados.example.com")
        return f"books+{h}@{domain}"

    # ── Validation & utils ─────────────────────────────────────────────────

    def _validate_file(self, filename: str, size: int):
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}. Allowed: {ALLOWED_EXTENSIONS}")
        if size > MAX_FILE_BYTES:
            raise ValueError(f"File too large ({size} bytes). Max {MAX_FILE_BYTES} bytes.")

    def _safe_filename(self, name: str) -> str:
        import re
        base = Path(name)
        safe = re.sub(r"[^\w\s\-.]", "_", base.stem)
        safe = re.sub(r"\s+", "_", safe).strip("_")
        return f"{safe or 'book'}{base.suffix.lower()}"
