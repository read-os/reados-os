"""
ReadOS Configuration Manager
Handles app-wide configuration from YAML + environment variables
"""

import os
import yaml
import logging
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger("ReadOS.Config")


class Config:
    VERSION = "1.0.0"

    DEFAULTS = {
        "books_dir": "./books",
        "db_path": "./reados.db",
        "cache_dir": "./cache",
        "covers_dir": "./cache/covers",
        "temp_dir": "./tmp",
        "secret_key": "CHANGE_ME_IN_PRODUCTION",
        "jwt_expiry_hours": 72,
        "max_upload_mb": 100,
        "default_language": "en",
        "default_theme": "light",
        "default_font_size": 16,
        "cloud": {
            "enabled": False,
            "provider": "local",  # local | firebase | supabase | custom
            "base_url": "",
            "api_key": "",
        },
        "annas_archive": {
            "sources": [
                "https://annas-archive.gl",
                "https://annas-archive.pk",
                "https://annas-archive.gd",
            ],
            "timeout_seconds": 15,
            "max_results": 50,
            "api_key": "",  # Set for fast reliable downloads
        },
        "google_drive": {
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "http://localhost:8080/api/import/gdrive/callback",
        },
        "logging": {
            "level": "INFO",
            "max_bytes": 5242880,
            "backup_count": 3,
        },
    }

    def __init__(self, config_path: str = "config.yaml"):
        self._data = dict(self.DEFAULTS)
        self._load_file(config_path)
        self._load_env()
        self._ensure_dirs()
        logger.info(f"Config loaded from {config_path}")

    def _load_file(self, path: str):
        """Load YAML config file if it exists."""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                file_cfg = yaml.safe_load(f) or {}
            self._deep_merge(self._data, file_cfg)

    def _load_env(self):
        """Override config values from environment variables."""
        env_map = {
            "READOS_SECRET_KEY": "secret_key",
            "READOS_DB_PATH": "db_path",
            "READOS_BOOKS_DIR": "books_dir",
            "READOS_CLOUD_URL": "cloud.base_url",
            "READOS_CLOUD_KEY": "cloud.api_key",
            "READOS_GDRIVE_CLIENT_ID": "google_drive.client_id",
            "READOS_GDRIVE_CLIENT_SECRET": "google_drive.client_secret",
        }
        for env_var, cfg_key in env_map.items():
            val = os.environ.get(env_var)
            if val:
                self._set_nested(cfg_key, val)

    def _ensure_dirs(self):
        """Create required directories."""
        for key in ["books_dir", "cache_dir", "covers_dir", "temp_dir"]:
            path = self._data.get(key, "")
            if path:
                Path(path).mkdir(parents=True, exist_ok=True)

    def _deep_merge(self, base: dict, override: dict):
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v

    def _set_nested(self, key: str, value: Any):
        parts = key.split(".")
        d = self._data
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value using dot notation (e.g. 'cloud.enabled')."""
        parts = key.split(".")
        d = self._data
        for part in parts:
            if not isinstance(d, dict):
                return default
            d = d.get(part, None)
            if d is None:
                return default
        return d

    def to_flask_config(self) -> Dict[str, Any]:
        return {
            "SECRET_KEY": self._data["secret_key"],
            "MAX_CONTENT_LENGTH": self._data["max_upload_mb"] * 1024 * 1024,
            "READOS_CONFIG": self,
        }

    # Convenience properties
    @property
    def books_dir(self) -> str:
        return self._data["books_dir"]

    @property
    def db_path(self) -> str:
        return self._data["db_path"]

    @property
    def cache_dir(self) -> str:
        return self._data["cache_dir"]

    @property
    def covers_dir(self) -> str:
        return self._data["covers_dir"]

    @property
    def temp_dir(self) -> str:
        return self._data["temp_dir"]

    @property
    def annas_sources(self) -> list:
        return self._data["annas_archive"]["sources"]

    @property
    def default_language(self) -> str:
        return self._data["default_language"]
