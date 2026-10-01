"""
web/app.py — 股票機器人視覺化儀表板
功能：
  - 即時查看台股／美股持倉報價
  - 新增／移除持股
  - 手動觸發 Telegram 報告
"""

import os
import sys
import json
import subprocess
from datetime import date

import yfinance as yf
from flask import Flask, render_template, jsonify, request

# 讓 Flask 能 import 上層的 config / modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

app = Flask(__name__)

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "..", "portfolio.json")
BOT_DIR        = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PYTHON         = sys.executable

# ── 預設持倉（與 config.py 保持一致）────────────────────────
DEFAULT_PORTFOLIO = {
    "tw": {
        "2330": "台積電", "1802": "台玻",   "2449": "京元電子",
        "2337": "旺宏",   "2455": "全新",   "3105": "穩懋",
        "6257": "矽格",   "6213": "聯茂",
    },
    "us": {
        "NVDA": "輝達",     "PLTR": "Palantir", "MRVL": "邁威爾",
        "TSLA": "特斯拉",   "PL":   "Planet Labs", "AVGO": "博通",
    },
}


# ── 讀寫 portfolio.json ──────────────────────────────────────
def load_portfolio() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # 首次啟動：寫入預設值
    save_portfolio(DEFAULT_PORTFOLIO)
    return DEFAULT_PORTFOLIO.copy()


def save_portfolio(portfolio: dict):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)


# ── 報價抓取 ─────────────────────────────────────────────────
def _fetch_price(ticker_str: str) -> dict:
    """回傳 {price, change, pct}，失敗時回傳 {}
    台股自動 fallback：先試 .TW（上市），無資料再試 .TWO（上櫃）
    """
    candidates = [ticker_str]
    if ticker_str.endswith(".TW"):
        code = ticker_str[:-3]
        candidates = [f"{code}.TW", f"{code}.TWO"]

    for t in candidates:
        try:
            hist   = yf.Ticker(t).history(period="5d")
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                continue
            latest = float(closes.iloc[-1])
            prev   = float(closes.iloc[-2])
            change = latest - prev
            pct    = change / prev * 100
            return {
                "price":  round(latest, 2),
                "change": round(change, 2),
                "pct":    round(pct, 2),
            }
        except Exception:
            continue
    return {}


# ══════════════════════════════════════════════════════════════
#  HTML
# ══════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")


# ══════════════════════════════════════════════════════════════
#  API — 取得持倉報價
# ══════════════════════════════════════════════════════════════
@app.route("/api/portfolio")
def api_portfolio():
    portfolio = load_portfolio()
    result = {"tw": [], "us": []}

    for code, name in portfolio.get("tw", {}).items():
        info = _fetch_price(f"{code}.TW")
        result["tw"].append({"code": code, "name": name, **info})

    for symbol, name in portfolio.get("us", {}).items():
        info = _fetch_price(symbol)
        result["us"].append({"symbol": symbol, "name": name, **info})

    return jsonify(result)


# ══════════════════════════════════════════════════════════════
#  API — 台股持倉管理
# ══════════════════════════════════════════════════════════════
@app.route("/api/tw/add", methods=["POST"])
def add_tw():
    data   = request.json or {}
    code   = data.get("code", "").strip()
    name   = data.get("name", "").strip()
    if not code or not name:
        return jsonify({"ok": False, "error": "代號和名稱不能為空"}), 400
    portfolio = load_portfolio()
    portfolio.setdefault("tw", {})[code] = name
    save_portfolio(portfolio)
    return jsonify({"ok": True})


@app.route("/api/tw/<code>", methods=["DELETE"])
def del_tw(code: str):
    portfolio = load_portfolio()
    portfolio.get("tw", {}).pop(code, None)
    save_portfolio(portfolio)
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════
#  API — 美股持倉管理
# ══════════════════════════════════════════════════════════════
@app.route("/api/us/add", methods=["POST"])
def add_us():
    data   = request.json or {}
    symbol = data.get("symbol", "").strip().upper()
    name   = data.get("name", "").strip()
    if not symbol or not name:
        return jsonify({"ok": False, "error": "代號和名稱不能為空"}), 400
    portfolio = load_portfolio()
    portfolio.setdefault("us", {})[symbol] = name
    save_portfolio(portfolio)
    return jsonify({"ok": True})


@app.route("/api/us/<symbol>", methods=["DELETE"])
def del_us(symbol: str):
    portfolio = load_portfolio()
    portfolio.get("us", {}).pop(symbol.upper(), None)
    save_portfolio(portfolio)
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════
#  API — 手動觸發 Telegram 報告
# ══════════════════════════════════════════════════════════════
@app.route("/api/schedule")
def api_schedule():
    """回傳持倉近期財報／法說會行程（供日曆使用）"""
    try:
        portfolio = load_portfolio()
        tw = portfolio.get("tw", {})
        us = portfolio.get("us", {})

        from modules.portfolio import get_portfolio_schedule
        events = get_portfolio_schedule(tw, us)

        result = []
        for e in events:
            d = e["date"]
            result.append({
                "date":   d.isoformat(),
                "name":   e["name"],
                "code":   e["code"],
                "label":  e["label"],
                "market": e["market"],
            })
        return jsonify(result)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ══════════════════════════════════════════════════════════════
#  API — 台股注意 / 處置
# ══════════════════════════════════════════════════════════════
@app.route("/api/tw/flags")
def api_tw_flags():
    """從 TWSE OpenAPI 取得注意股票與處置股票清單"""
    import requests as _req
    from datetime import date as _date

    headers = {"User-Agent": "Mozilla/5.0", "accept": "application/json"}
    base    = "https://openapi.twse.com.tw/v1"
    today   = _date.today()

    attention   = set()   # 注意股票代號
    disposition = {}      # code → 處置次數/原因

    # ── 注意股票（當日名單）
    try:
        r = _req.get(f"{base}/announcement/notice", headers=headers, timeout=8)
        for item in r.json():
            code = item.get("Code", "").strip()
            if code and code != "0":
                attention.add(code)
    except Exception:
        pass

    # ── 近期達注意交易資訊標準（notetrans）
    try:
        r = _req.get(f"{base}/announcement/notetrans", headers=headers, timeout=8)
        for item in r.json():
            code = item.get("Code", "").strip()
            if code:
                attention.add(code)
    except Exception:
        pass

    # ── 處置股票（過濾仍在有效期間內）
    try:
        r = _req.get(f"{base}/announcement/punish", headers=headers, timeout=8)
        for item in r.json():
            code   = item.get("Code", "").strip()
            period = item.get("DispositionPeriod", "")
            if not code or not period:
                continue
            try:
                end_str = period.split("～")[-1].strip()   # e.g. "115/04/30"
                y, m, d = end_str.split("/")
                end_date = _date(int(y) + 1911, int(m), int(d))
                if end_date >= today:
                    measures = item.get("DispositionMeasures", "處置中")
                    # 保留最嚴重的（次數最多）
                    if code not in disposition:
                        disposition[code] = measures
            except Exception:
                pass
    except Exception:
        pass

    return jsonify({
        "attention":   list(attention),
        "disposition": disposition,
    })


# ══════════════════════════════════════════════════════════════
#  API — 總體經濟
# ══════════════════════════════════════════════════════════════
@app.route("/api/macro")
def api_macro():
    try:
        from modules.macro import get_tw_macro, get_us_macro
        return jsonify({"tw": get_tw_macro(), "us": get_us_macro()})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ══════════════════════════════════════════════════════════════
#  API — 個股新聞
# ══════════════════════════════════════════════════════════════
@app.route("/api/news/tw/<code>")
def api_news_tw(code: str):
    try:
        portfolio = load_portfolio()
        name = portfolio.get("tw", {}).get(code, code)
        from modules.portfolio import _fetch_tw_stock_rss, fetch_cnyes_batch, cnyes_news_for_codes, _format_cnyes_item
        # 主要：Yahoo Finance 個股 RSS（精準）
        items = _fetch_tw_stock_rss(code, name, max_items=5)
        # 備用：cnyes batch（嚴格過濾標題含股名）
        if not items:
            batch    = fetch_cnyes_batch(pages=3)
            filtered = cnyes_news_for_codes([code], batch, max_per_code=5)
            raw      = [it for it in filtered.get(code, []) if name in it.get("title", "")]
            items    = [_format_cnyes_item(i) for i in raw]
        return jsonify(items)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/news/us/<symbol>")
def api_news_us(symbol: str):
    try:
        from modules.portfolio import _fetch_yahoo_rss
        items = _fetch_yahoo_rss(symbol.upper(), max_items=5)
        return jsonify(items)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/trigger/tw", methods=["POST"])
def trigger_tw():
    try:
        res = subprocess.run(
            [PYTHON, "main_tw.py"],
            cwd=BOT_DIR, capture_output=True, text=True, timeout=180
        )
        if res.returncode == 0:
            return jsonify({"ok": True, "msg": "✅ 台股報告已發送至 Telegram！"})
        return jsonify({"ok": False, "error": res.stderr[-300:] or res.stdout[-300:]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/trigger/us", methods=["POST"])
def trigger_us():
    try:
        res = subprocess.run(
            [PYTHON, "main_us.py"],
            cwd=BOT_DIR, capture_output=True, text=True, timeout=180
        )
        if res.returncode == 0:
            return jsonify({"ok": True, "msg": "✅ 美股報告已發送至 Telegram！"})
        return jsonify({"ok": False, "error": res.stderr[-300:] or res.stdout[-300:]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════
#  API — 功率元件漲價雷達
# ══════════════════════════════════════════════════════════════
@app.route("/api/radar/signals")
def api_radar_signals():
    try:
        sys.path.insert(0, BOT_DIR)
        from price_radar.db import init_db, get_recent_signals
        init_db()
        signals = get_recent_signals(days=14)
        return jsonify(signals)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/radar/run", methods=["POST"])
def api_radar_run():
    try:
        res = subprocess.run(
            [PYTHON, "-m", "price_radar.main"],
            cwd=BOT_DIR, capture_output=True, text=True, timeout=300
        )
        if res.returncode == 0:
            return jsonify({"ok": True, "msg": "✅ 雷達掃描完成！"})
        return jsonify({"ok": False, "error": res.stderr[-500:] or res.stdout[-500:]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
