from flask import Flask, render_template, jsonify, request, send_file
from pathlib import Path
import json
import yfinance as yf
from datetime import datetime, timedelta

app = Flask(__name__)
DATA_DIR = Path(__file__).parent.parent / "data"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
INDUSTRIES_PATH = DATA_DIR / "industries.json"


def load_data(date_str: str) -> list[dict]:
    f = DATA_DIR / f"{date_str}.json"
    if f.exists():
        with open(f, encoding="utf-8") as fp:
            return json.load(fp)
    return []


CLIPS_DIR = DATA_DIR / "clips"
ALL_EPISODES_PATH = DATA_DIR / "all_episodes.json"


def load_all_episodes() -> list[dict]:
    if ALL_EPISODES_PATH.exists():
        with open(ALL_EPISODES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def fetch_price_history(ticker: str, start_date: str) -> dict:
    """用 yfinance 抓從 start_date 至今的價格，回傳圖表所需資料"""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=1)
        hist = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"))
        close_series = hist["Close"].dropna()
        if close_series.empty:
            return {}

        closes = close_series.round(2).tolist()
        dates = [d.strftime("%Y-%m-%d") for d in close_series.index]
        base = closes[0]
        pct_change = round((closes[-1] - base) / base * 100, 2) if base else 0
        current_price = closes[-1]

        return {
            "dates": dates,
            "closes": closes,
            "base_price": round(base, 2),
            "current_price": round(current_price, 2),
            "pct_change": pct_change,
        }
    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def index():
    episodes = load_all_episodes()
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("index.html", episodes=episodes, current_date=today)


@app.route("/watchlist")
def watchlist():
    wl = load_watchlist()
    return render_template("watchlist.html", watchlist=wl)


@app.route("/api/watchlist")
def api_watchlist():
    return jsonify(load_watchlist())


@app.route("/api/watchlist/remove", methods=["POST"])
def api_watchlist_remove():
    key = request.json.get("key", "")
    wl = load_watchlist()
    if key in wl:
        del wl[key]
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(wl, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "not found"}), 404


@app.route("/api/price/<market>/<symbol>")
def api_price(market, symbol):
    wl = load_watchlist()
    key = f"{market.upper()}:{symbol.upper()}"
    if key not in wl:
        return jsonify({"error": "not found"}), 404
    entry = wl[key]
    # 台股 fallback: 先試 .TW 再試 .TWO
    ticker = entry["ticker"]
    data = fetch_price_history(ticker, entry["first_date"])
    if not data and market.upper() == "TW":
        ticker = symbol + ".TWO"
        data = fetch_price_history(ticker, entry["first_date"])
    return jsonify({**data, "ticker": ticker})


@app.route("/trends")
def trends():
    data = {}
    if INDUSTRIES_PATH.exists():
        with open(INDUSTRIES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    # 依最新提及日期排序（新→舊）
    def latest_date(item):
        mentions = item[1].get("mentions", [])
        return max((m.get("date", "") for m in mentions), default="")
    data = dict(sorted(data.items(), key=latest_date, reverse=True))
    return render_template("trends.html", industries=data)


@app.route("/api/industries")
def api_industries():
    if INDUSTRIES_PATH.exists():
        with open(INDUSTRIES_PATH, encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({})


@app.route("/api/industries/merge", methods=["POST"])
def api_industries_merge():
    source = request.json.get("source", "")
    target = request.json.get("target", "")
    if not INDUSTRIES_PATH.exists():
        return jsonify({"ok": False, "error": "not found"}), 404
    with open(INDUSTRIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if source not in data or target not in data:
        return jsonify({"ok": False, "error": "industry not found"}), 404
    # 合併 mentions，依日期排序去重
    merged_mentions = data[target].get("mentions", []) + data[source].get("mentions", [])
    seen = set()
    unique_mentions = []
    for m in merged_mentions:
        key = (m.get("date"), m.get("podcast"), m.get("episode"))
        if key not in seen:
            seen.add(key)
            unique_mentions.append(m)
    unique_mentions.sort(key=lambda m: m.get("date", ""))
    data[target]["mentions"] = unique_mentions
    data[target]["first_date"] = min(data[target].get("first_date", "9999"), data[source].get("first_date", "9999"))
    del data[source]
    with open(INDUSTRIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "merged_count": len(unique_mentions)})


@app.route("/api/industries/remove", methods=["POST"])
def api_industries_remove():
    name = request.json.get("name", "")
    if not INDUSTRIES_PATH.exists():
        return jsonify({"ok": False, "error": "not found"}), 404
    with open(INDUSTRIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if name not in data:
        return jsonify({"ok": False, "error": "not found"}), 404
    del data[name]
    with open(INDUSTRIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


@app.route("/clips/<filename>")
def serve_clip(filename):
    path = CLIPS_DIR / filename
    if not path.exists():
        return "not found", 404
    return send_file(path, mimetype="audio/mpeg")


@app.route("/api/today")
def api_today():
    return jsonify(load_all_episodes())


if __name__ == "__main__":
    app.run(port=5002, debug=True)
