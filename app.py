"""Chat app: a Flask backend that relays messages to Ollama and stores them in MySQL."""

import json
import os
import re
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import db

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:4b")
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a friendly assistant. Answer briefly and in the same language as the user.",
)
HISTORY_LIMIT = int(os.environ.get("HISTORY_LIMIT", "10"))
MAX_MESSAGE_LEN = 2000
SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

app = Flask(__name__)

# The app runs behind Nginx under a sub-path (/viikko5/). Nginx strips the prefix and
# sends it in the X-Forwarded-Prefix header. ProxyFix sets it as Flask's SCRIPT_NAME,
# so url_for() and request.script_root produce correct paths (/viikko5/static/...).
# The headers can be trusted because the app is only reachable through Nginx (Unix socket).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_prefix=1)


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


@app.get("/")
def index():
    return render_template("index.html", model=OLLAMA_MODEL)


@app.get("/health")
def health():
    revision_file = Path(__file__).with_name("REVISION")
    if revision_file.is_file():
        return revision_file.read_text(encoding="utf-8")
    return "local\n"


@app.get("/api/history/<session_id>")
def history(session_id: str):
    if not SESSION_RE.match(session_id):
        return jsonify(error="Invalid session_id"), 400
    try:
        return jsonify(db.get_history(session_id, limit=50))
    except db.DatabaseError as exc:
        app.logger.error("Database error: %s", exc)
        return jsonify(error="Database error"), 503


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "")).strip()
    message = str(data.get("message", "")).strip()

    if not SESSION_RE.match(session_id):
        return jsonify(error="Invalid session_id"), 400
    # if not message:
     #   return jsonify(error="Message is missing"), 400
    if len(message) > MAX_MESSAGE_LEN:
        return jsonify(error=f"Message is too long (max {MAX_MESSAGE_LEN} characters)"), 400

    try:
        past = db.get_history(session_id, limit=HISTORY_LIMIT)
        db.save_message(session_id, "user", message)
    except db.DatabaseError as exc:
        app.logger.error("Database error: %s", exc)
        return jsonify(error="Database error - the message could not be saved."), 503

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + [{"role": m["role"], "content": m["content"]} for m in past]
        + [{"role": "user", "content": message}]
    )

    def generate():
        parts = []
        try:
            with requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": OLLAMA_MODEL, "messages": messages, "stream": True},
                stream=True,
                timeout=(5, 300),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        parts.append(token)
                        yield _ndjson({"token": token})
                    if chunk.get("done"):
                        break
        except (requests.RequestException, ValueError) as exc:
            app.logger.error("Ollama request failed: %s", exc)
            yield _ndjson({"error": "The language model did not respond. Try again shortly."})
            return

        reply = "".join(parts)
        if reply:
            try:
                db.save_message(session_id, "assistant", reply)
            except db.DatabaseError as exc:
                # The reply has already been shown to the user; log the error.
                app.logger.error("Saving the reply failed: %s", exc)
        yield _ndjson({"done": True})

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
