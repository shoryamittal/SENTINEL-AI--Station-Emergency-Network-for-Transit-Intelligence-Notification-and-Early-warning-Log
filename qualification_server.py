#!/usr/bin/env python3
"""SENTINEL QUALIFICATION BACKEND — localhost-only, NOT PRODUCTION."""
from __future__ import annotations

import os
import hmac
from pathlib import Path

from flask import Flask, jsonify, request

from src.qualification_backend import QualificationEventStore, QualificationResult


def create_app(store: QualificationEventStore | None = None, api_token: str | None = None) -> Flask:
    app = Flask(__name__)
    event_store = store or QualificationEventStore(os.environ.get("QUALIFICATION_DB_PATH", str(Path("data") / "qualification-sync.db")))
    event_store.initialize()
    expected_token = api_token if api_token is not None else os.environ.get("QUALIFICATION_API_TOKEN")

    @app.before_request
    def require_api_token():
        if not expected_token or not request.path.startswith("/api/events"):
            return None
        supplied = request.headers.get("Authorization", "")
        prefix = "Bearer "
        valid = supplied.startswith(prefix) and hmac.compare_digest(supplied[len(prefix):], expected_token)
        if not valid:
            return jsonify({"error": "unauthorized"}), 401
        return None

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "sentinel-qualification-sync"})

    @app.post("/api/events")
    def accept_event():
        payload = request.get_json(silent=True)
        result, event_id = event_store.accept_event(payload)
        if result == QualificationResult.MALFORMED:
            return jsonify({"result": result, "error": "event_id must be a non-empty string"}), 400
        status = {QualificationResult.ACCEPTED: 201, QualificationResult.ALREADY_ACCEPTED: 200, QualificationResult.IDEMPOTENCY_CONFLICT: 409}[result]
        return jsonify({"result": result, "event_id": event_id}), status

    @app.get("/api/events/<event_id>")
    def get_event(event_id):
        event = event_store.get_event(event_id)
        return (jsonify(event), 200) if event else (jsonify({"error": "not found"}), 404)

    @app.get("/api/events")
    def list_events():
        return jsonify({"count": event_store.count_events(), "events": event_store.list_events()})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=os.environ.get("QUALIFICATION_HOST", "127.0.0.1"), port=int(os.environ.get("QUALIFICATION_PORT", "5051")), debug=False, threaded=True)
