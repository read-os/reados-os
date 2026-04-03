"""
ReadOS — Anna's Archive Integration
Based on the official open-source codebase: https://github.com/LilyLoops/annas-archive
(mirror of https://software.annas-archive.li/AnnaArchivist/annas-archive)

════════════════════════════════════════════════════════════════
API ENDPOINTS (from the Anna's Archive Flask source — allthethings/app.py):
  Search:    GET  /search?q=<query>&ext=<fmt>&lang=<lang>&sort=&page=<n>
  Book info: GET  /md5/<md5>.json           (JSON metadata response)
  Fast DL:   GET  /dyn/api/fast_download.json?md5=<md5>&key=<api_key>
  Slow DL:   GET  /slow_download/<md5>/0/0  (no key, rate-limited fallback)

AUTHENTICATION:
  - Search:    No key required.
  - Downloads: API key recommended (free with a donation to Anna's Archive).
    Get your key at https://annas-archive.org/faq#api after donating.
    Set in config.yaml:  annas_archive.api_key: "your_key_here"
    Or env var:          ANNAS_ARCHIVE_KEY=your_key_here
  - Without a key, slow download fallback is attempted (often captcha-blocked).

MAINTAINER NOTE — Swapping mirrors when a domain goes down:
  Option A: Edit config.yaml → annas_archive.sources → restart.
  Option B: Settings UI → "Anna's Archive Sources" → edit list → Save.
            Changes apply IMMEDIATELY, no restart required.
  Option C: PUT /api/archive/sources {"sources": ["https://new.mirror"]}
  No code changes needed — all domain logic is config-driven.
════════════════════════════════════════════════════════════════
"""

import os
import re
import json
import time
import uuid
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, urljoin, urlencode

logger = logging.getLogger("ReadOS.AnnasArchive")

# ── Official API paths (from LilyLoops/annas-archive source code) ─────────
SEARCH_PATH      = "/search"
MD5_JSON_PATH    = "/md5/{md5}.json"
FAST_DL_API_PATH = "/dyn/api/fast_download.json"
SLOW_DL_PATH     = "/slow_download/{md5}/0/0"

# ── Fallback mirror list (config overrides these) ─────────────────────────
DEFAULT_SOURCES = [
    "https://annas-archive.gl",
    "https://annas-archive.pk",
    "https://annas-archive.gd",
]

SUPPORTED_EXTENSIONS = {"epub", "pdf", "txt", "mobi", "azw3", "djvu", "fb2"}

CONTENT_TYPE_MAP = {
    "application/epub+zip":              ".epub",
    "application/pdf":                   ".pdf",
    "text/plain":                        ".txt",
    "application/x-mobipocket-ebook":    ".mobi",
    "application/vnd.amazon.ebook":      ".azw3",
}


class AnnasArchiveClient:
    """
    Client for Anna's Archive using official API endpoints from the
    open-source LilyLoops/annas-archive codebase.

    Search is always available. Downloads work best with an API key.
    All domain/mirror logic is config-driven for easy volunteer maintenance.
    """

    def __init__(self, config):
        self.cfg = config
        self.sources: List[str] = [
            s.rstrip("/") for s in (config.annas_sources or DEFAULT_SOURCES)
            if s.strip().startswith("http")
        ]
        self.timeout: int = int(config.get("annas_archive.timeout_seconds", 15))
        self.max_results: int = int(config.get("annas_archive.max_results", 50))
        self.api_key: str = (
            config.get("annas_archive.api_key", "")
            or os.environ.get("ANNAS_ARCHIVE_KEY", "")
        ).strip()

        self.download_dir = Path(config.books_dir)
        self._active_downloads: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._http = None  # lazy requests.Session

        key_info = "API key configured ✓" if self.api_key else "no API key (slow downloads only)"
        logger.info(f"AnnasArchive ready — {len(self.sources)} mirrors, {key_info}")

    # ── HTTP ───────────────────────────────────────────────────────────────

    def _session(self):
        """Lazily create and return a persistent requests.Session."""
        if self._http is None:
            import requests
            self._http = requests.Session()
            self._http.headers.update({
                "User-Agent": "Mozilla/5.0 (Linux; Kobo ReadOS/1.0) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.9",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "DNT": "1",
            })
        return self._http

    def _get(self, url: str, **kwargs):
        return self._session().get(url, timeout=self.timeout, **kwargs)

    # ── Public API ─────────────────────────────────────────────────────────

    def search(self, query: str, ext: str = "", lang: str = "",
               page: int = 1, sort: str = "") -> Dict:
        """
        Search Anna's Archive. No API key required.
        Tries each configured mirror in order.

        Returns: {results: [...], source: str, query: str, has_more: bool}
        """
        if not query.strip():
            return {"results": [], "query": query, "error": "empty_query"}

        for source in self.sources:
            try:
                results = self._search_source(source, query, ext, lang, page, sort)
                logger.info(f"Search '{query}' → {len(results)} results [{source}]")
                return {
                    "results": results,
                    "source": source,
                    "query": query,
                    "page": page,
                    "has_more": len(results) >= 18,
                    "api_key_set": bool(self.api_key),
                }
            except Exception as e:
                logger.warning(f"Search failed on {source}: {type(e).__name__}: {e}")

        logger.error(f"All mirrors failed for query: '{query}'")
        return {
            "results": [], "source": None, "query": query,
            "error": "all_sources_unreachable",
        }

    def get_book_info(self, md5: str) -> Optional[Dict]:
        """
        Get detailed metadata for a book via the official /md5/<md5>.json endpoint.
        """
        md5 = md5.lower().strip()
        for source in self.sources:
            try:
                url = source + MD5_JSON_PATH.format(md5=md5)
                resp = self._get(url)
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        return self._normalize_md5_json(resp.json(), source, md5)
                    else:
                        # HTML fallback — parse the /md5/ page
                        return self._parse_md5_page(resp.text, source, md5)
            except Exception as e:
                logger.warning(f"Book info failed [{source}]: {e}")
        return None

    def get_download_url(self, md5: str) -> Optional[str]:
        """
        Resolve a direct file download URL for the given MD5 hash.

        Priority:
          1. Fast download JSON API (requires api_key)
          2. Slow download fallback (no key, often rate-limited/captcha-blocked)
        """
        md5 = md5.lower().strip()

        # ── 1. Fast download API ───────────────────────────────────────────
        if self.api_key:
            for source in self.sources:
                try:
                    url = source + FAST_DL_API_PATH
                    resp = self._get(url, params={"md5": md5, "key": self.api_key})
                    if resp.status_code == 200:
                        data = resp.json()
                        # Response shape from official source:
                        # {"download_url": "https://..."} or {"urls": ["https://..."]}
                        dl = (
                            data.get("download_url")
                            or (data.get("urls") or [None])[0]
                            or data.get("url")
                        )
                        if dl:
                            logger.debug(f"Fast DL resolved: {dl}")
                            return dl
                    elif resp.status_code == 401:
                        logger.warning("Anna's Archive API key rejected — verify key in Settings")
                        break  # Same key won't work on other mirrors
                    elif resp.status_code == 429:
                        logger.warning(f"Rate limited on {source}, trying next mirror")
                except Exception as e:
                    logger.warning(f"Fast DL API failed [{source}]: {e}")

        # ── 2. Slow download fallback ──────────────────────────────────────
        for source in self.sources:
            url = source + SLOW_DL_PATH.format(md5=md5)
            try:
                resp = self._session().head(
                    url, timeout=self.timeout, allow_redirects=True
                )
                if resp.status_code < 400:
                    logger.debug(f"Slow DL URL: {url}")
                    return resp.url or url  # Follow redirect to final URL
            except Exception as e:
                logger.debug(f"Slow DL head check failed [{source}]: {e}")

        return None

    def start_download(self, md5: str, filename: str,
                       user_id: Optional[str] = None) -> str:
        """
        Queue an async download. Returns a job_id string.
        Poll with get_download_status(job_id).
        When status == 'complete', file_path contains the local path.
        """
        job_id = str(uuid.uuid4())
        safe_name = self._safe_filename(filename)
        with self._lock:
            self._active_downloads[job_id] = {
                "id": job_id,
                "md5": md5,
                "filename": safe_name,
                "status": "queued",
                "progress": 0,
                "user_id": user_id,
                "error": None,
                "file_path": None,
            }
        threading.Thread(
            target=self._download_worker, args=(job_id, md5, safe_name),
            daemon=True, name=f"dl-{job_id[:8]}"
        ).start()
        return job_id

    def get_download_status(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            job = self._active_downloads.get(job_id)
            return dict(job) if job else None

    def get_all_downloads(self) -> List[Dict]:
        with self._lock:
            return [dict(v) for v in self._active_downloads.values()]

    def list_sources(self) -> List[str]:
        return list(self.sources)

    def update_sources(self, new_sources: List[str]) -> None:
        """
        Replace the mirror list at runtime — for volunteer maintenance.
        No restart needed. Validates that each entry is an HTTP(S) URL.
        """
        cleaned = [s.rstrip("/") for s in new_sources
                   if s.strip().startswith("http")]
        if not cleaned:
            raise ValueError("Provide at least one valid https:// URL")
        self.sources = cleaned
        logger.info(f"Mirrors updated to: {self.sources}")

    def set_api_key(self, key: str) -> None:
        """Update the API key at runtime (called from Settings UI)."""
        self.api_key = key.strip()
        logger.info("Anna's Archive API key updated")

    # ── Search HTML parser ─────────────────────────────────────────────────

    def _search_source(self, source: str, query: str, ext: str,
                       lang: str, page: int, sort: str) -> List[Dict]:
        from bs4 import BeautifulSoup

        params: Dict = {"q": query, "page": str(page)}
        if ext:  params["ext"] = ext
        if lang: params["lang"] = lang
        if sort: params["sort"] = sort

        url = source + SEARCH_PATH + "?" + urlencode(params)
        resp = self._get(url)
        resp.raise_for_status()
        return self._parse_search_html(resp.text, source)

    def _parse_search_html(self, html: str, source: str) -> List[Dict]:
        """
        Parse Anna's Archive search result HTML.
        The official source uses consistent anchor-based result blocks.
        Multiple selector strategies are tried for resilience.
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        seen_md5s: set = set()
        results: List[Dict] = []

        for a in soup.select("a[href*='/md5/']"):
            if len(results) >= self.max_results:
                break
            try:
                item = self._parse_result_anchor(a, source)
                if item and item["md5"] not in seen_md5s:
                    seen_md5s.add(item["md5"])
                    results.append(item)
            except Exception:
                continue

        return results

    def _parse_result_anchor(self, a: "Tag", source: str) -> Optional[Dict]:
        href = a.get("href", "")
        m = re.search(r"/md5/([a-f0-9]{32})", href, re.I)
        if not m:
            return None
        md5 = m.group(1).lower()

        full_text = a.get_text(" ", strip=True)

        # Title: first large/bold element, else first line of text
        title = ""
        for sel in ["h3", "[class*='font-bold']", "[class*='text-xl']",
                    "[class*='text-lg']", "strong", "b"]:
            el = a.select_one(sel)
            if el:
                title = el.get_text(strip=True)
                break
        if not title:
            title = full_text[:80]

        # Author: italic / secondary color elements
        author = ""
        for sel in [".italic", "i", "[class*='text-gray']",
                    "[class*='text-slate']", "[class*='author']"]:
            el = a.select_one(sel)
            if el:
                author = el.get_text(strip=True)
                break

        # Format, size, language from small metadata spans
        fmt = size = lang = year = ""
        for el in a.select(".text-xs, .text-sm, [class*='text-xs'], [class*='metadata']"):
            t = el.get_text(strip=True)
            tl = t.lower()
            if not fmt:
                for f in SUPPORTED_EXTENSIONS:
                    if f in tl:
                        fmt = f
                        break
            if not size and re.search(r"\d[\d.]*\s*(mb|kb|gb)", tl):
                size = t
            if not year:
                ym = re.search(r"\b(19|20)\d{2}\b", t)
                if ym:
                    year = ym.group(0)

        # Cover image
        cover_url = ""
        img = a.select_one("img[src], img[data-src]")
        if img:
            src = img.get("src") or img.get("data-src", "")
            cover_url = src if src.startswith("http") else urljoin(source, src)

        return {
            "md5": md5,
            "title": title[:200].strip(),
            "author": author[:120].strip(),
            "format": fmt,
            "size": size,
            "language": lang,
            "year": year,
            "cover_url": cover_url,
            "url": urljoin(source, href),
            "source": source,
            "has_fast_download": bool(self.api_key),
        }

    def _parse_md5_page(self, html: str, source: str, md5: str) -> Dict:
        """Fallback: parse the /md5/<hash> HTML page for metadata."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        return {
            "md5": md5, "title": title, "author": "", "format": "",
            "download_links": [], "source": source,
            "url": urljoin(source, f"/md5/{md5}"),
        }

    def _normalize_md5_json(self, data: dict, source: str, md5: str) -> Dict:
        """
        Normalize /md5/<md5>.json response.
        The official Flask source stores unified metadata in file_unified_data.
        """
        # Support both flat and nested schemas
        udata = data.get("file_unified_data", data)
        return {
            "md5": md5,
            "title":       udata.get("title_best")    or data.get("title", ""),
            "author":      udata.get("author_best")   or data.get("author", ""),
            "format":      udata.get("extension_best")or data.get("extension", ""),
            "language":    udata.get("language_best") or data.get("language", ""),
            "year":  str(  udata.get("year_best","")  or data.get("year", "")),
            "publisher":   udata.get("publisher_best")or data.get("publisher", ""),
            "description": udata.get("description_best") or data.get("description", ""),
            "isbn":        udata.get("isbn13_best")   or data.get("isbn13", ""),
            "cover_url":   udata.get("cover_url_best")or data.get("cover_url", ""),
            "file_size":   udata.get("filesize_best") or data.get("filesize", 0),
            "source": source,
            "url": urljoin(source, f"/md5/{md5}"),
            "has_fast_download": bool(self.api_key),
        }

    # ── Download worker ────────────────────────────────────────────────────

    def _download_worker(self, job_id: str, md5: str, filename: str):
        """Background thread: resolves and streams a book file to disk."""

        def update(**kw):
            with self._lock:
                if job_id in self._active_downloads:
                    self._active_downloads[job_id].update(kw)

        update(status="resolving", progress=5)
        dest = self.download_dir / filename

        # Step 1: resolve URL
        dl_url = self.get_download_url(md5)
        if not dl_url:
            msg = "Could not resolve a download URL."
            if not self.api_key:
                msg += (" Add an Anna's Archive API key in Settings for "
                        "reliable downloads (requires donation at annas-archive.org).")
            update(status="failed", error=msg)
            return

        # Step 2: stream download
        update(status="downloading", progress=12)
        logger.info(f"Downloading {filename} ← {dl_url}")
        try:
            with self._session().get(
                dl_url, stream=True,
                timeout=self.timeout * 8,
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0

                # Auto-detect extension from Content-Type
                if not Path(filename).suffix or Path(filename).suffix == ".epub":
                    ct = resp.headers.get("content-type", "")
                    detected = CONTENT_TYPE_MAP.get(ct.split(";")[0].strip(), "")
                    if detected and detected != Path(filename).suffix:
                        stem = Path(filename).stem
                        filename = stem + detected
                        dest = self.download_dir / filename
                        update(filename=filename)

                with open(str(dest), "wb") as f:
                    for chunk in resp.iter_content(65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = 12 + int(downloaded / total * 85)
                                update(progress=min(97, pct))

            # Step 3: validate file size
            size = dest.stat().st_size
            if size < 2048:
                dest.unlink(missing_ok=True)
                hint = (" Set an API key in Settings for captcha-free downloads."
                        if not self.api_key else "")
                raise ValueError(f"File too small ({size} bytes) — possible captcha page.{hint}")

            update(status="complete", progress=100, file_path=str(dest))
            logger.info(f"Download complete: {dest} ({size:,} bytes)")

        except Exception as e:
            dest.unlink(missing_ok=True)
            update(status="failed", error=str(e))
            logger.error(f"Download failed md5={md5}: {e}")

    # ── Utilities ──────────────────────────────────────────────────────────

    def _safe_filename(self, name: str) -> str:
        base = Path(name)
        stem = re.sub(r"[^\w\s\-]", "_", base.stem)
        stem = re.sub(r"\s+", "_", stem).strip("_") or "book"
        ext = base.suffix.lower()
        if ext.lstrip(".") not in SUPPORTED_EXTENSIONS:
            ext = ".epub"
        return f"{stem[:100]}{ext}"
