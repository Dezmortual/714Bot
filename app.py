# ============================================================
#  714 METHOD — web dashboard + engine launcher
#  Run:  python app.py           (starts engine thread + dashboard)
# ============================================================
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

# load .env if present
load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, jsonify, render_template
from state import STATE
from engine.engine import Engine, load_config

BASE = Path(__file__).parent
cfg = load_config(BASE / "config.yaml")

app = Flask(__name__)
engine = None
engine_thread = None


def start_engine():
    global engine, engine_thread
    if engine_thread and engine_thread.is_alive():
        return
    engine = Engine(cfg)
    engine_thread = threading.Thread(target=engine.run, daemon=True)
    engine_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    return jsonify(STATE.snapshot())


@app.route("/api/start", methods=["POST"])
def api_start():
    start_engine()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if engine:
        engine.stop()
    return jsonify({"ok": True})


@app.route("/api/close", methods=["POST"])
def api_close():
    import flask
    data = flask.request.get_json(force=True) or {}
    symbol = data.get("symbol")
    if engine and symbol:
        engine.broker.close_position(symbol)
        engine._mgmt.pop(symbol, None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    start_engine()
    host = cfg["dashboard"]["host"]
    # honor PORT env var (set by Render/Railway/Heroku etc.)
    port = int(os.environ.get("PORT", cfg["dashboard"]["port"]))
    print(f"714 Method dashboard on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
