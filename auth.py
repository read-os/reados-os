"""
ReadOS Authentication Manager
User accounts, session management, and JWT-based auth
"""

import uuid
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger("ReadOS.Auth")


class AuthManager:
    """Handles user registration, login, session tokens."""

    def __init__(self, config):
        from database import Database
        self.cfg = config
        self.db = Database(config.db_path)
        self._jwt_expiry = timedelta(hours=config.get("jwt_expiry_hours", 72))

    # ── Account ────────────────────────────────────────────────────────────

    def register(self, email: str, username: str, password: str) -> Dict:
        email = email.strip().lower()
        username = username.strip()

        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not email or "@" not in email:
            raise ValueError("Invalid email address")

        # Check existing
        existing = self.db.fetchone("SELECT id FROM users WHERE email=?", (email,))
        if existing:
            raise ValueError("Email already registered")

        user_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        pw_hash = self._hash_password(password)

        self.db.execute("""
            INSERT INTO users (id, email, username, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, email, username, pw_hash, now, now))

        logger.info(f"New user registered: {email}")
        token = self._create_token(user_id)
        return {"user_id": user_id, "email": email, "username": username, "token": token}

    def login(self, email: str, password: str) -> Dict:
        email = email.strip().lower()
        user = self.db.fetchone("SELECT * FROM users WHERE email=?", (email,))

        if not user:
            raise ValueError("Invalid email or password")
        if not self._verify_password(password, user["password_hash"]):
            raise ValueError("Invalid email or password")

        token = self._create_token(user["id"])
        logger.info(f"User logged in: {email}")
        return {
            "user_id": user["id"],
            "email": user["email"],
            "username": user["username"],
            "token": token,
        }

    def logout(self, token: str) -> bool:
        self.db.delete_session(token)
        return True

    def validate_token(self, token: str) -> Optional[Dict]:
        """Returns user dict if token is valid, None otherwise."""
        session = self.db.get_session(token)
        if not session:
            return None
        user = self.db.fetchone("SELECT * FROM users WHERE id=?", (session["user_id"],))
        if not user:
            return None
        # Don't expose password hash
        user.pop("password_hash", None)
        return user

    def get_user(self, user_id: str) -> Optional[Dict]:
        user = self.db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
        if user:
            user.pop("password_hash", None)
        return user

    def update_settings(self, user_id: str, settings: Dict) -> bool:
        import json
        now = datetime.utcnow().isoformat()
        self.db.execute(
            "UPDATE users SET settings=?, updated_at=? WHERE id=?",
            (json.dumps(settings), now, user_id)
        )
        return True

    def get_settings(self, user_id: str) -> Dict:
        import json
        user = self.db.fetchone("SELECT settings FROM users WHERE id=?", (user_id,))
        if not user:
            return {}
        try:
            return json.loads(user["settings"] or "{}")
        except Exception:
            return {}

    def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters")
        user = self.db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))
        if not user or not self._verify_password(old_password, user["password_hash"]):
            raise ValueError("Current password is incorrect")
        new_hash = self._hash_password(new_password)
        now = datetime.utcnow().isoformat()
        self.db.execute(
            "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
            (new_hash, now, user_id)
        )
        return True

    # ── Token management ───────────────────────────────────────────────────

    def _create_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(48)
        expires_at = (datetime.utcnow() + self._jwt_expiry).isoformat()
        self.db.create_session(token, user_id, expires_at)
        return token

    # ── Password hashing (PBKDF2-SHA256) ──────────────────────────────────

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return f"pbkdf2:sha256:260000:{salt}:{h.hex()}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            parts = stored_hash.split(":")
            if parts[0] != "pbkdf2" or len(parts) < 5:
                return False
            _, algo, iters, salt, expected = parts
            h = hashlib.pbkdf2_hmac(algo, password.encode(), salt.encode(), int(iters))
            return secrets.compare_digest(h.hex(), expected)
        except Exception as e:
            logger.error(f"Password verify error: {e}")
            return False

    # ── Middleware helper ──────────────────────────────────────────────────

    def require_auth(self, token: str) -> Dict:
        """Raises ValueError if token invalid; returns user dict if valid."""
        user = self.validate_token(token)
        if not user:
            raise PermissionError("Authentication required")
        return user
