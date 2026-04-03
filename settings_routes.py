"""ReadOS — Settings Routes"""
import time
import threading
import logging
from flask import Blueprint, request, jsonify, current_app
from i18n import get_all_strings, available_languages

settings_bp = Blueprint("settings", __name__)
logger = logging.getLogger("ReadOS.Settings")

# ── Simple in-process update-check cache (avoids hammering GitHub API) ─────
_update_cache = {
    "checked_at": 0,       # unix timestamp of last check
    "result": None,        # last result dict
    "ttl": 3600,           # re-check every 1 hour
    "lock": threading.Lock(),
}


def _get_token():
    h = request.headers.get("Authorization", "")
    return h[7:] if h.startswith("Bearer ") else request.cookies.get("reados_token", "")


def optional_user():
    token = _get_token()
    if not token:
        return None
    return current_app.auth.validate_token(token)


@settings_bp.get("/languages")
def languages():
    return jsonify(available_languages())


@settings_bp.get("/i18n/<lang>")
def i18n_strings(lang):
    return jsonify(get_all_strings(lang))


@settings_bp.get("/")
def get_settings():
    user = optional_user()
    if user:
        settings = current_app.auth.get_settings(user["id"])
    else:
        settings = {}
    # Merge with defaults
    defaults = {
        "theme": current_app.config["READOS_CONFIG"].get("default_theme", "light"),
        "font_size": current_app.config["READOS_CONFIG"].get("default_font_size", 16),
        "language": current_app.config["READOS_CONFIG"].default_language,
        "font_family": "Georgia",
        "line_height": 1.7,
        "margin_size": "medium",
    }
    return jsonify({**defaults, **settings})


@settings_bp.put("/")
def save_settings():
    user = optional_user()
    data = request.get_json(silent=True) or {}
    if user:
        current_app.auth.update_settings(user["id"], data)
    return jsonify({"ok": True})


@settings_bp.get("/version")
def version():
    cfg = current_app.config["READOS_CONFIG"]
    return jsonify({
        "version": cfg.VERSION,
        "github_url": cfg.GITHUB_URL,
        "github_repo": cfg.GITHUB_REPO,
        "cloud_enabled": cfg.get("cloud.enabled", False),
        "cloud_provider": cfg.get("cloud.provider", "local"),
    })


@settings_bp.get("/version-check")
def version_check():
    """
    Poll GitHub API for the latest commit/release on read-os/reados-os.
    Returns whether an update is available compared to the running VERSION.
    Results are cached for 1 hour to avoid GitHub rate-limits.

    Response shape:
    {
        "current_version":  "1.0.0",
        "latest_version":   "1.2.0",   // from latest GitHub release tag, or null
        "latest_sha":       "abc1234", // short commit SHA of HEAD on main
        "latest_message":   "Fix PDF rendering on Kobo",
        "latest_date":      "2025-06-01T12:00:00Z",
        "update_available": true,
        "github_url":       "https://github.com/read-os/reados-os",
        "release_url":      "https://github.com/read-os/reados-os/releases/latest",
        "cached":           false,
        "error":            null
    }
    """
    cfg = current_app.config["READOS_CONFIG"]
    now = time.time()

    with _update_cache["lock"]:
        # Serve cached result if fresh
        if (
            _update_cache["result"] is not None
            and now - _update_cache["checked_at"] < _update_cache["ttl"]
        ):
            cached = dict(_update_cache["result"])
            cached["cached"] = True
            return jsonify(cached)

    # Fetch from GitHub API (outside the lock to avoid blocking)
    result = _fetch_github_update(cfg)

    with _update_cache["lock"]:
        _update_cache["result"] = result
        _update_cache["checked_at"] = now

    return jsonify(result)


def _fetch_github_update(cfg) -> dict:
    """Fetch latest commit + release info from GitHub REST API."""
    import requests as req

    base = {
        "current_version": cfg.VERSION,
        "latest_version": None,
        "latest_sha": None,
        "latest_message": None,
        "latest_date": None,
        "update_available": False,
        "github_url": cfg.GITHUB_URL,
        "release_url": f"{cfg.GITHUB_URL}/releases/latest",
        "cached": False,
        "error": None,
    }

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"ReadOS/{cfg.VERSION}",
    }

    try:
        # ── 1. Try latest release first ────────────────────────────
        rel_resp = req.get(
            f"{cfg.GITHUB_API}/releases/latest",
            headers=headers, timeout=8
        )
        if rel_resp.status_code == 200:
            rel = rel_resp.json()
            tag = rel.get("tag_name", "").lstrip("v")
            base["latest_version"] = tag or None
            base["latest_message"] = rel.get("name") or rel.get("tag_name", "")
            base["latest_date"]    = rel.get("published_at", "")
            base["release_url"]    = rel.get("html_url", base["release_url"])

        # ── 2. Always fetch latest commit on main for SHA + message ─
        commit_resp = req.get(
            f"{cfg.GITHUB_API}/commits/main",
            headers=headers, timeout=8
        )
        if commit_resp.status_code == 200:
            commit = commit_resp.json()
            sha = commit.get("sha", "")[:7]
            msg = (commit.get("commit", {}).get("message") or "").split("\n")[0][:120]
            date = commit.get("commit", {}).get("author", {}).get("date", "")
            base["latest_sha"]  = sha
            # Only overwrite message if no release name
            if not base["latest_message"]:
                base["latest_message"] = msg
            if not base["latest_date"]:
                base["latest_date"] = date

        # ── 3. Determine if update is available ────────────────────
        if base["latest_version"]:
            # Compare semver: split on dots, pad to 3 parts, compare as ints
            def ver_tuple(v):
                try:
                    parts = str(v).lstrip("v").split(".")
                    return tuple(int(x) for x in (parts + ["0", "0"])[:3])
                except Exception:
                    return (0, 0, 0)

            current_t = ver_tuple(cfg.VERSION)
            latest_t  = ver_tuple(base["latest_version"])
            base["update_available"] = latest_t > current_t
        else:
            # No release tags yet — flag update if commit SHA exists and differs
            # from what we shipped. We can't know the shipped SHA here,
            # so we conservatively leave update_available as False.
            base["update_available"] = False

    except req.exceptions.Timeout:
        base["error"] = "GitHub API timed out"
        logger.warning("Version check: GitHub API timed out")
    except req.exceptions.ConnectionError:
        base["error"] = "No network connection"
        logger.warning("Version check: no network")
    except Exception as e:
        base["error"] = str(e)
        logger.warning(f"Version check failed: {e}")

    return base
