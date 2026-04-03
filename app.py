"""
ReadOS - Production E-Reader for Kobo and compatible devices
Main application entry point
"""

import os
import sys
import logging
from flask import Flask, send_from_directory
from flask_cors import CORS

# Core modules
from library import LibraryManager
from reader import ReaderEngine
from cloud import CloudSync
from annas_archive import AnnasArchiveClient
from importer import BookImporter
from auth import AuthManager
from config import Config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("reados.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("ReadOS")


def create_app(config_path: str = "config.yaml") -> Flask:
    """Factory function - creates and configures the Flask application."""
    app = Flask(__name__, static_folder="static", template_folder="templates")
    CORS(app)

    # Load config
    cfg = Config(config_path)
    app.config.update(cfg.to_flask_config())

    # Initialize core subsystems
    app.library = LibraryManager(cfg)
    app.reader = ReaderEngine(cfg)
    app.cloud = CloudSync(cfg)
    app.archive = AnnasArchiveClient(cfg)
    app.importer = BookImporter(cfg)
    app.auth = AuthManager(cfg)

    # Register blueprints
    from routes.library_routes import library_bp
    from routes.reader_routes import reader_bp
    from routes.cloud_routes import cloud_bp
    from routes.archive_routes import archive_bp
    from routes.import_routes import import_bp
    from routes.auth_routes import auth_bp
    from routes.settings_routes import settings_bp

    app.register_blueprint(library_bp, url_prefix="/api/library")
    app.register_blueprint(reader_bp, url_prefix="/api/reader")
    app.register_blueprint(cloud_bp, url_prefix="/api/cloud")
    app.register_blueprint(archive_bp, url_prefix="/api/archive")
    app.register_blueprint(import_bp, url_prefix="/api/import")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(settings_bp, url_prefix="/api/settings")

    # Serve frontend SPA
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path):
        if path and os.path.exists(os.path.join(app.static_folder, path)):
            return send_from_directory(app.static_folder, path)
        return send_from_directory(app.template_folder, "index.html")

    # Health check
    @app.route("/health")
    def health():
        return {"status": "ok", "version": cfg.VERSION}

    logger.info(f"ReadOS v{cfg.VERSION} initialized")
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("READOS_PORT", 8080))
    debug = os.environ.get("READOS_DEBUG", "false").lower() == "true"
    logger.info(f"Starting ReadOS on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
