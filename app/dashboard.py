from flask import Flask, jsonify, request

from app.config import Config

app = Flask(__name__)
_bot = None  # injected by main.py


def init_dashboard(bot):
    global _bot
    _bot = bot
    return app


@app.route("/")
def home():
    return jsonify({
        "service": "okx-grid-bot",
        "mode": "demo" if Config.DEMO_MODE else "live",
        "endpoints": ["/status", "/coins (GET/POST/DELETE)", "/healthz"],
    })


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/status")
def status():
    s = _bot.get_status()
    grids = {
        base: {
            "symbol": mgr.symbol,
            "capital": round(mgr.state.capital, 2),
            "range": [round(mgr.state.lower, 6), round(mgr.state.upper, 6)],
            "levels": len(mgr.state.levels),
            "open_orders": sum(1 for l in mgr.state.levels if l.status == "open"),
        }
        for base, mgr in _bot.managers.items()
    }
    s["grids"] = grids
    s["coin_scores"] = _bot.coin_scores
    s["manual_coins"] = list(_bot.manual_coins)
    return jsonify(s)


@app.route("/coins", methods=["GET"])
def list_coins():
    return jsonify({"manual": list(_bot.manual_coins), "active": _bot.status.get("active_coins", [])})


@app.route("/coins", methods=["POST"])
def add_coin():
    data = request.get_json(force=True, silent=True) or {}
    base = data.get("coin")
    if not base:
        return jsonify({"error": "missing 'coin'"}), 400
    _bot.add_coin(base)
    return jsonify({"ok": True, "manual_coins": list(_bot.manual_coins)})


@app.route("/coins/<coin>", methods=["DELETE"])
def remove_coin(coin):
    _bot.remove_coin(coin)
    return jsonify({"ok": True, "manual_coins": list(_bot.manual_coins)})
