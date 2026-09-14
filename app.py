# ============================================================
#  714 METHOD — web dashboard + engine launcher
#  Run:  python app.py           (starts engine thread + dashboard)
# ============================================================
import os
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

# load .env if present
load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, jsonify, render_template, request, send_from_directory
from state import STATE
from engine.engine import Engine, load_config
from backtest import run_backtest, render_chart, fetch_bars

BASE = Path(__file__).parent
CHART_DIR = BASE / "data" / "charts"
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


# ------------------------------------------------------------
#  Pages
# ------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/backtest")
def backtest_page():
    return render_template("backtest.html", symbols=cfg["strategy"]["symbols"])


# ------------------------------------------------------------
#  Engine control
# ------------------------------------------------------------
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
    data = request.get_json(force=True) or {}
    symbol = data.get("symbol")
    if engine and symbol:
        engine.broker.close_position(symbol)
        engine._mgmt.pop(symbol, None)
    return jsonify({"ok": True})


# ------------------------------------------------------------
#  Backtest
# ------------------------------------------------------------
def _purge_old_charts(keep=24):
    """Keep the chart folder from growing unbounded (deploy servers)."""
    try:
        files = sorted(CHART_DIR.glob("backtest_*.png"), key=lambda p: p.stat().st_mtime)
        for p in files[:-keep] if len(files) > keep else []:
            p.unlink(missing_ok=True)
    except Exception:
        pass


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.get_json(force=True) or {}
    symbol = (data.get("symbol") or "AAPL").strip().upper()
    try:
        limit = int(data.get("limit") or 500)
    except (TypeError, ValueError):
        limit = 500
    limit = max(100, min(limit, 2000))
    if not re.fullmatch(r"[A-Z0-9/._-]{1,20}", symbol):
        return jsonify({"error": "invalid symbol"}), 400

    try:
        df, source = fetch_bars(symbol, cfg, limit)
    except Exception as e:
        return jsonify({"error": f"data fetch failed: {e}"}), 502
    if df is None or df.empty:
        return jsonify({"error": f"no data for {symbol}"}), 404

    try:
        result = run_backtest(symbol, cfg, df)
    except Exception as e:
        return jsonify({"error": f"backtest failed: {e}"}), 500

    result["stats"]["source"] = source
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", symbol)
    fname = f"backtest_{safe}_{int(time.time())}.png"
    try:
        render_chart(symbol, source, df, result, CHART_DIR / fname)
    except Exception as e:
        return jsonify({"error": f"chart failed: {e}"}), 500
    _purge_old_charts()

    return jsonify({
        "ok": True,
        "symbol": symbol,
        "source": source,
        "limit": len(df),
        "stats": result["stats"],
        "trades": result["trades"],
        "chart": f"/charts/{fname}",
    })


@app.route("/charts/<path:fname>")
def charts(fname):
    return send_from_directory(CHART_DIR, fname)


if __name__ == "__main__":
    start_engine()
    host = cfg["dashboard"]["host"]
    # honor PORT env var (set by Render/Railway/Heroku etc.)
    port = int(os.environ.get("PORT", cfg["dashboard"]["port"]))
    print(f"714 Method dashboard on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
