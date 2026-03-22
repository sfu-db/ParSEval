"""
app.py — Flask application factory.

Usage
-----
  # Development
  flask --app querylens_api.app run --debug

  # Production (gunicorn)
  gunicorn "querylens_api.app:create_app()"
"""

import os
from flask import Flask, jsonify
from marshmallow import ValidationError

from .models import db
from .routes import api


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)

    # ── Default configuration ─────────────────────────────────────────────────
    app.config.setdefault(
        "SQLALCHEMY_DATABASE_URI",
        os.environ.get("DATABASE_URL", "sqlite:///querylens.db"),
    )
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("JSON_SORT_KEYS", False)

    if config:
        app.config.update(config)

    # ── Extensions ────────────────────────────────────────────────────────────
    db.init_app(app)

    # ── Blueprints ────────────────────────────────────────────────────────────
    app.register_blueprint(api)

    # ── Create tables (dev only — use Flask-Migrate in production) ────────────
    with app.app_context():
        db.create_all()

    # ── Error handlers ────────────────────────────────────────────────────────
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": str(e)}), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return jsonify({"error": "Internal server error"}), 500

    @app.errorhandler(ValidationError)
    def handle_marshmallow(e):
        return jsonify({"error": "Validation failed", "details": e.messages}), 400

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
