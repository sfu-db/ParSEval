from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from .evaluation_runtime import build_evaluation_runtime
from .models import db, migrate_legacy_flask_schema
from .routes import api

import logging
from parseval.utils import Logger


Logger(forbidden={"coverage": True})

logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format="[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s",
)


def default_database_uri() -> str:
    db_path = Path(__file__).resolve().with_name("parseval.sqlite3")
    return f"sqlite:///{db_path}"


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", default_database_uri()),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JSON_SORT_KEYS=False,
        EVALUATION_WORKERS=1,
        EVALUATION_QUEUE_MAXSIZE=0,
        EVALUATION_WRITE_ARTIFACTS=True,
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}})
    app.register_blueprint(api)

    @app.get("/")
    def root():
        return {"message": "API is running"}

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.errorhandler(404)
    def not_found(_: Exception):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def internal_error(error: Exception):
        db.session.rollback()
        if app.config.get("TESTING"):
            raise error
        return jsonify({"error": "Internal server error"}), 500

    with app.app_context():
        db.create_all()
        migrate_legacy_flask_schema(app.root_path)

    app.extensions["evaluation_runtime"] = build_evaluation_runtime(app)
    return app
