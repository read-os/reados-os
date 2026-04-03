"""
ReadOS Cloud Sync
Pluggable cloud backend for syncing progress, libraries, and books.
Supports: local (no-op), REST API, Firebase, Supabase
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("ReadOS.Cloud")


class CloudSync:
    """
    Background cloud synchronization engine.
    Processes the sync queue and keeps the cloud and local state in sync.
    """

    def __init__(self, config):
        from database import Database
        self.cfg = config
        self.db = Database(config.db_path)
        self.enabled = config.get("cloud.enabled", False)
        self._backend = self._init_backend()
        self._sync_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if self.enabled:
            self._start_background_sync()
        logger.info(f"CloudSync initialized — provider={config.get('cloud.provider')} enabled={self.enabled}")

    def _init_backend(self):
        provider = self.cfg.get("cloud.provider", "local")
        if provider == "local" or not self.enabled:
            return LocalBackend(self.cfg)
        elif provider == "rest":
            return RESTBackend(self.cfg)
        elif provider == "firebase":
            return FirebaseBackend(self.cfg)
        elif provider == "supabase":
            return SupabaseBackend(self.cfg)
        else:
            logger.warning(f"Unknown cloud provider '{provider}', falling back to local")
            return LocalBackend(self.cfg)

    # ── Public API ─────────────────────────────────────────────────────────

    def sync_now(self, user_id: str) -> Dict:
        """Force-sync all pending items for a user. Returns sync report."""
        if not self.enabled:
            return {"synced": 0, "errors": 0, "provider": "local"}
        return self._process_queue(user_id)

    def push_progress(self, user_id: str, book_id: str, data: Dict) -> bool:
        """Push reading progress immediately."""
        if not self.enabled:
            return True
        try:
            self._backend.push_progress(user_id, book_id, data)
            return True
        except Exception as e:
            logger.warning(f"Progress push failed: {e}")
            return False

    def pull_progress(self, user_id: str) -> List[Dict]:
        """Pull all reading progress from cloud for user."""
        if not self.enabled:
            return []
        try:
            return self._backend.pull_progress(user_id)
        except Exception as e:
            logger.warning(f"Progress pull failed: {e}")
            return []

    def push_book(self, user_id: str, file_path: str, metadata: Dict) -> Optional[str]:
        """Upload a book file to cloud storage."""
        if not self.enabled:
            return None
        try:
            cloud_id = self._backend.push_book(user_id, file_path, metadata)
            return cloud_id
        except Exception as e:
            logger.error(f"Book upload failed: {e}")
            return None

    def pull_books(self, user_id: str) -> List[Dict]:
        """Get list of cloud-stored books for a user."""
        if not self.enabled:
            return []
        try:
            return self._backend.pull_books(user_id)
        except Exception as e:
            logger.warning(f"Book list pull failed: {e}")
            return []

    def sync_status(self) -> Dict:
        return {
            "enabled": self.enabled,
            "provider": self.cfg.get("cloud.provider", "local"),
            "backend_ok": self._backend.health_check(),
        }

    # ── Background sync ────────────────────────────────────────────────────

    def _start_background_sync(self, interval_seconds: int = 60):
        self._stop_event.clear()
        self._sync_thread = threading.Thread(
            target=self._sync_loop,
            args=(interval_seconds,),
            daemon=True,
            name="ReadOS-CloudSync",
        )
        self._sync_thread.start()
        logger.info("Background cloud sync thread started")

    def _sync_loop(self, interval: int):
        while not self._stop_event.is_set():
            try:
                # Get all users with pending syncs
                users = self.db.fetchall(
                    "SELECT DISTINCT user_id FROM sync_queue WHERE attempts < 5"
                )
                for u in users:
                    self._process_queue(u["user_id"])
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
            self._stop_event.wait(interval)

    def _process_queue(self, user_id: str) -> Dict:
        items = self.db.get_pending_syncs(user_id)
        synced = 0
        errors = 0
        for item in items:
            try:
                payload = json.loads(item["payload"])
                action = item["action"]

                if action == "progress":
                    self._backend.push_progress(user_id, payload["book_id"], payload)
                elif action == "bookmark_add":
                    self._backend.push_bookmark(user_id, payload)
                elif action == "bookmark_delete":
                    self._backend.delete_bookmark(user_id, payload["bookmark_id"])
                elif action == "book_upload":
                    self._backend.push_book(user_id, payload.get("file_path", ""), payload)

                self.db.mark_sync_done(item["id"])
                synced += 1
            except Exception as e:
                self.db.mark_sync_failed(item["id"], str(e))
                errors += 1
                logger.warning(f"Sync item {item['id']} failed: {e}")

        return {"synced": synced, "errors": errors, "provider": self.cfg.get("cloud.provider")}

    def shutdown(self):
        self._stop_event.set()
        if self._sync_thread:
            self._sync_thread.join(timeout=5)


# ── Cloud Backends ─────────────────────────────────────────────────────────

class LocalBackend:
    """No-op backend for local-only operation."""
    def __init__(self, cfg): pass
    def push_progress(self, *a, **k): pass
    def pull_progress(self, *a, **k): return []
    def push_bookmark(self, *a, **k): pass
    def delete_bookmark(self, *a, **k): pass
    def push_book(self, *a, **k): return None
    def pull_books(self, *a, **k): return []
    def health_check(self): return True


class RESTBackend:
    """
    Generic REST API backend.
    Expects endpoints:
      POST   /sync/progress
      GET    /sync/progress/{user_id}
      POST   /sync/bookmark
      DELETE /sync/bookmark/{id}
      POST   /books/upload
      GET    /books/{user_id}
    """

    def __init__(self, cfg):
        self.base_url = cfg.get("cloud.base_url", "").rstrip("/")
        self.api_key = cfg.get("cloud.api_key", "")
        self._session = None

    def _get_session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-ReadOS-Version": "1.0.0",
            })
        return self._session

    def _post(self, path: str, data: dict) -> dict:
        r = self._get_session().post(f"{self.base_url}{path}", json=data, timeout=10)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        r = self._get_session().get(f"{self.base_url}{path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def push_progress(self, user_id: str, book_id: str, data: dict):
        self._post("/sync/progress", {"user_id": user_id, "book_id": book_id, **data})

    def pull_progress(self, user_id: str) -> list:
        return self._get(f"/sync/progress/{user_id}").get("items", [])

    def push_bookmark(self, user_id: str, data: dict):
        self._post("/sync/bookmark", {"user_id": user_id, **data})

    def delete_bookmark(self, user_id: str, bookmark_id: str):
        s = self._get_session()
        s.delete(f"{self.base_url}/sync/bookmark/{bookmark_id}", timeout=10)

    def push_book(self, user_id: str, file_path: str, metadata: dict) -> Optional[str]:
        if not file_path or not os.path.exists(file_path):
            return None
        import os
        s = self._get_session()
        with open(file_path, "rb") as f:
            r = s.post(
                f"{self.base_url}/books/upload",
                files={"file": f},
                data={"user_id": user_id, "metadata": json.dumps(metadata)},
                timeout=120
            )
        r.raise_for_status()
        return r.json().get("cloud_id")

    def pull_books(self, user_id: str) -> list:
        return self._get(f"/books/{user_id}").get("books", [])

    def health_check(self) -> bool:
        try:
            r = self._get_session().get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


class FirebaseBackend(RESTBackend):
    """Firebase Firestore + Storage backend (via REST API)."""

    def __init__(self, cfg):
        self.project_id = cfg.get("cloud.firebase_project_id", "")
        self.api_key = cfg.get("cloud.api_key", "")
        self.base_url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents"
        self._session = None

    def health_check(self) -> bool:
        return bool(self.project_id)


class SupabaseBackend(RESTBackend):
    """Supabase backend using PostgREST + Storage APIs."""

    def __init__(self, cfg):
        supabase_url = cfg.get("cloud.base_url", "")
        self.base_url = supabase_url.rstrip("/")
        self.api_key = cfg.get("cloud.api_key", "")
        self._session = None

    def health_check(self) -> bool:
        try:
            s = self._get_session()
            r = s.get(f"{self.base_url}/rest/v1/", timeout=5)
            return r.status_code < 500
        except Exception:
            return False
