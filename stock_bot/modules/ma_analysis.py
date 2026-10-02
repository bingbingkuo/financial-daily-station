"""
ma_analysis.py — 族群 x 大盤 均線強弱背離分析

判斷族群個股是否站上 5/10/20/60/120/240 日均線，並與加權指數、櫃買指數的
均線位置比較，找出強於大盤的族群（背離訊號）。

資料來源：
  - 個股 / 加權指數 (TAIEX)：yfinance（台股 .TW 上市 → fallback .TWO 上櫃）
  - 櫃買指數 (TPEx)：yfinance 沒有可靠代碼，改用 FinMind 免費 API
"""

import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

MA_PERIODS = [5, 10, 20, 60, 120, 240]
_HISTORY_PERIOD = "500d"     # 足夠算出 MA240 的日曆天數
_FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

# ══════════════════════════════════════════════════════════
#  監控清單
# ══════════════════════════════════════════════════════════

STOCK_GROUPS = [
    {"name": "記憶體", "stocks": [
        ("2408", "南亞科"), ("2344", "華邦電"), ("2337", "旺宏"),
        ("3260", "威剛"), ("8299", "群聯"), ("3006", "晶豪科"),
    ]},
    {"name": "CCL銅箔基板", "stocks": [
        ("2383", "台光電"), ("6274", "台燿"), ("6213", "聯茂"), ("1303", "南亞"),
    ]},
    {"name": "被動元件", "stocks": [
        ("2327", "國巨"), ("2492", "華新科"), ("3026", "禾伸堂"),
        ("6173", "信昌電"), ("2472", "立隆電"), ("3624", "光頡"), ("6449", "鈺邦"),
    ]},
    {"name": "ABF載板", "stocks": [
        ("3037", "欣興"), ("8046", "南電"), ("3189", "景碩"), ("4958", "臻鼎-KY"),
    ]},
    {"name": "電源供應", "stocks": [
        ("2308", "台達電"), ("2301", "光寶科"),
    ]},
    {"name": "BBU", "stocks": [
        ("3211", "順達"), ("4931", "新盛力"), ("6781", "AES-KY"),
    ]},
    {"name": "ODM", "stocks": [
        ("2382", "廣達"), ("3231", "緯創"), ("6669", "緯穎"),
        ("2317", "鴻海"), ("2324", "仁寶"), ("2356", "英業達"),
    ]},
    {"name": "光通訊", "stocks": [
        ("3081", "聯亞"), ("2455", "全新"), ("4991", "環宇-KY"), ("4979", "華星光"),
        ("3234", "光環"), ("3363", "上詮"), ("3450", "聯鈞"), ("6451", "訊芯-KY"),
    ]},
    {"name": "矽晶圓", "stocks": [
        ("6488", "環球晶"), ("3532", "台勝科"), ("6182", "合晶"),
    ]},
    {"name": "功率元件", "stocks": [
        ("8261", "富鼎"), ("2481", "強茂"), ("5425", "台半"),
        ("3675", "德微"), ("6435", "大中"),
    ]},
    {"name": "航運", "stocks": [
        ("2603", "長榮"), ("2609", "陽明"), ("2615", "萬海"),
    ]},
    {"name": "晶圓代工(成熟製程)", "stocks": [
        ("2303", "聯電"), ("5347", "世界"),
    ]},
    {"name": "石英元件", "stocks": [
        ("3042", "晶技"), ("2484", "希華"), ("8182", "加高"),
    ]},
    {"name": "TGV設備", "stocks": [
        ("6207", "雷科"), ("8064", "東捷"), ("8027", "鈦昇"), ("3055", "蔚華科"),
    ]},
    {"name": "散熱", "stocks": [
        ("3017", "奇鋐"), ("3324", "雙鴻"), ("3653", "健策"),
    ]},
    {"name": "半導體設備", "stocks": [
        ("2360", "致茂"), ("2404", "漢唐"), ("2467", "志聖"), ("3131", "弘塑"),
        ("3167", "大量"), ("5536", "聖暉*"), ("6139", "亞翔"), ("6187", "萬潤"), ("6640", "均華"),
    ]},
    {"name": "封測", "stocks": [
        ("2449", "京元電子"), ("3264", "欣銓"), ("3711", "日月光投控"),
        ("6239", "力成"), ("6257", "矽格"), ("8150", "南茂"),
    ]},
    {"name": "探針卡", "stocks": [
        ("6223", "旺矽"), ("6510", "精測"), ("6515", "穎崴"), ("6683", "雍智科技"),
    ]},
    {"name": "光學元件", "stocks": [
        ("3008", "大立光"), ("3019", "亞光"), ("3362", "先進光"), ("3406", "玉晶光"),
        ("3441", "聯一光"), ("3504", "揚明光"), ("6209", "今國光"),
    ]},
    {"name": "ASIC", "stocks": [
        ("2454", "聯發科"), ("3443", "創意"), ("3661", "世芯-KY"),
    ]},
    {"name": "玻纖布", "stocks": [
        ("1802", "台玻"), ("1815", "富喬"), ("5340", "建榮"), ("5475", "德宏"),
    ]},
]

BENCHMARKS = [
    {"key": "twii", "name": "加權指數 (TAIEX)", "source": "yfinance", "ticker": "^TWII",
     "chart_url": "https://www.wantgoo.com/index/0000"},
    {"key": "otc", "name": "櫃買指數 (TPEx)", "source": "finmind", "ticker": "TPEx",
     "chart_url": "https://www.wantgoo.com/index/two"},
]


# ══════════════════════════════════════════════════════════
#  資料抓取
# ══════════════════════════════════════════════════════════

def _fetch_tw_stock_history(code: str):
    """回傳 (history_df, suffix)：先試 .TW 上市，無資料再試 .TWO 上櫃"""
    for suffix in (".TW", ".TWO"):
        try:
            df = yf.Ticker(f"{code}{suffix}").history(period=_HISTORY_PERIOD, auto_adjust=False)
            if df is not None and not df.empty and len(df) >= 10:
                return df, suffix
        except Exception:
            continue
    return None, ""


def _fetch_twii_history():
    try:
        df = yf.Ticker("^TWII").history(period=_HISTORY_PERIOD, auto_adjust=False)
        return df if df is not None and not df.empty else None
    except Exception:
        return None


def _fetch_tpex_history():
    """櫃買指數歷史資料（FinMind，免金鑰）"""
    start = (datetime.now() - timedelta(days=500)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(_FINMIND_URL, params={
            "dataset": "TaiwanStockPrice",
            "data_id": "TPEx",
            "start_date": start,
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.rename(columns={"close": "Close", "max": "High", "min": "Low", "open": "Open"})
        return df
    except Exception:
        return None


# ══════════════════════════════════════════════════════════
#  計算：收盤 / 當日最高 / 均線 / 52週高
# ══════════════════════════════════════════════════════════

def _empty_stats():
    return {"close": None, "high": None, "ma": {p: None for p in MA_PERIODS},
            "high52w": None, "dist_pct": None, "date": "—", "daily_history": [],
            "chg": None, "chg_pct": None}


def _compute_stats(df: pd.DataFrame | None) -> dict:
    if df is None or df.empty or "Close" not in df:
        return _empty_stats()

    closes = df["Close"].dropna()
    if closes.empty:
        return _empty_stats()

    close = float(closes.iloc[-1])
    high_series = df["High"].dropna() if "High" in df else closes
    high_today = float(high_series.iloc[-1]) if not high_series.empty else close

    chg, chg_pct = None, None
    if len(closes) >= 2:
        prev_close = float(closes.iloc[-2])
        if prev_close:
            chg = round(close - prev_close, 2)
            chg_pct = round((close - prev_close) / prev_close * 100, 2)

    ma = {}
    for p in MA_PERIODS:
        ma[p] = round(float(closes.iloc[-p:].mean()), 2) if len(closes) >= p else None

    window = high_series.iloc[-252:] if not high_series.empty else closes.iloc[-252:]
    high52w = round(float(window.max()), 2) if not window.empty else None
    dist_pct = round((close / high52w * 100 - 100), 1) if high52w else None

    return {
        "close": round(close, 2),
        "high": round(high_today, 2),
        "ma": ma,
        "high52w": high52w,
        "dist_pct": dist_pct,
        "date": closes.index[-1].strftime("%Y/%m/%d"),
        "daily_history": _compute_daily_history(closes),
        "chg": chg,
        "chg_pct": chg_pct,
    }


def _compute_daily_history(closes: pd.Series, n_days: int = 7) -> list:
    """回傳最近 n_days 個交易日，每日各均線站上與否，用於回溯畫族群走勢圖
    （不用等 7 天累積，用歷史收盤直接算出當天的均線站上狀態）"""
    n = len(closes)
    result = []
    for idx in range(max(0, n - n_days), n):
        date_str = closes.index[idx].strftime("%m-%d")
        close_val = closes.iloc[idx]
        day_map = {}
        for p in MA_PERIODS:
            if idx + 1 >= p:
                ma_val = closes.iloc[idx - p + 1: idx + 1].mean()
                day_map[p] = bool(close_val > ma_val)
            else:
                day_map[p] = None
        result.append({"date": date_str, "above": day_map})
    return result


def is_above(close, ma):
    if close is None or ma is None:
        return None
    return close > ma


def count_above(close, ma_map):
    n, total = 0, 0
    for p in MA_PERIODS:
        r = is_above(close, ma_map.get(p))
        if r is not None:
            total += 1
            if r:
                n += 1
    return n, total


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def run_analysis() -> dict:
    """執行完整分析，回傳 {benchmarks, groups, report_date}"""
    bench_results = []
    for b in BENCHMARKS:
        df = _fetch_twii_history() if b["source"] == "yfinance" else _fetch_tpex_history()
        stats = _compute_stats(df)
        bench_results.append({**b, **stats})

    group_results = []
    for g in STOCK_GROUPS:
        stock_rows = []
        for code, name in g["stocks"]:
            df, suffix = _fetch_tw_stock_history(code)
            stats = _compute_stats(df)
            stock_rows.append({"code": code, "name": name, "suffix": suffix, **stats})
            time.sleep(0.15)  # 避免對 yfinance 打太快
        group_results.append({"name": g["name"], "stocks": stock_rows})

    all_dates = [s["date"] for g in group_results for s in g["stocks"] if s["date"] != "—"]
    all_dates += [b["date"] for b in bench_results if b["date"] != "—"]
    report_date = max(all_dates) if all_dates else datetime.now().strftime("%Y/%m/%d")

    today_str = datetime.now().strftime("%Y/%m/%d")
    stale = report_date != today_str

    return {"benchmarks": bench_results, "groups": group_results,
            "report_date": report_date, "stale": stale}


# ══════════════════════════════════════════════════════════
#  HTML 報表（純 HTML，無 JS，供 email 顯示）
# ══════════════════════════════════════════════════════════

_CSS = """
body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px}
.wrap{max-width:900px;margin:0 auto}
.hdr{padding:18px 24px 14px;border-bottom:1px solid #eee}
.hdr h2{margin:0;font-size:20px;color:#1a237e}
.hdr p{margin:4px 0 0;font-size:13px;color:#666}
.stale-note{background:#fff3e0;color:#e65100;padding:8px 24px;font-size:12px}
.bench-row{display:flex;gap:12px;padding:16px;background:#fff}
.bench-card{flex:1;border:1px solid #eee;border-radius:8px;padding:12px 16px}
.bench-name{font-size:14px;font-weight:bold;color:#1a237e}
.bench-date{font-size:11px;color:#999;margin-bottom:6px}
.bench-price{font-size:22px;font-weight:bold;margin-bottom:2px}
.bench-price .sub{font-size:12px;color:#666;font-weight:normal;margin-left:8px}
.chg{display:block;font-size:12.5px;font-weight:bold;margin-bottom:8px}
.ladder{display:flex;gap:4px}
.dot{flex:1;text-align:center;border-radius:6px;padding:4px 2px;font-size:10px}
.dot.above{background:#ffebee;color:#c62828;border:1px solid #ffcdd2}
.dot.below{background:#e8f5e9;color:#2e7d32;border:1px solid #c8e6c9}
.dot .lbl{font-size:9px;opacity:.75}
.dot .val{font-weight:bold;white-space:nowrap}
.overview-card{background:#fff;margin:0 0 16px;border-radius:8px;padding:16px 18px;
              box-shadow:0 1px 4px rgba(0,0,0,.08)}
.overview-title{font-size:14px;font-weight:bold;color:#1a237e;margin-bottom:2px}
.overview-sub{font-size:11px;color:#888;margin-bottom:12px}
table.overview-table{table-layout:fixed}
.overview-table td{padding:7px 4px;border-bottom:1px solid #f5f5f5;white-space:normal}
.overview-table tr:last-child td{border-bottom:none}
.overview-rank{font-size:12px;font-weight:bold;color:#999;text-align:center}
.overview-name{font-size:12.5px;font-weight:600;text-align:left;padding-left:8px !important}
.overview-name a{color:#333;text-decoration:none;border-bottom:1px dashed #ccc}
.overview-barwrap{background:#f0f0f0;border-radius:6px;height:14px;overflow:hidden}
.overview-bar{height:100%;border-radius:6px}
.overview-pct{font-size:12px;font-weight:bold;text-align:right}
.overview-diverge{font-size:9px;color:#e65100;background:#fff3e0;padding:2px 7px;
                  border-radius:20px;white-space:nowrap;display:inline-block}
.group-card{background:#fff;margin:0 0 16px;border-radius:8px;overflow:hidden;
            box-shadow:0 1px 4px rgba(0,0,0,.08)}
.group-hdr{background:#eceff1;padding:10px 16px;font-size:14px;font-weight:bold;color:#37474f}
table{border-collapse:collapse;width:100%;font-size:11px}
th{background:#fafafa;color:#777;font-size:9.5px;padding:6px 2px;border-bottom:1px solid #eee;
   text-align:center;white-space:nowrap}
th.left{text-align:left;padding-left:10px}
td{padding:5px 2px;text-align:center;border-bottom:1px solid #f5f5f5;white-space:nowrap}
td.left{text-align:left;padding-left:10px;font-weight:600}
td.code{color:#999}
td.above{background:#ffebee;color:#c62828;font-weight:bold}
td.below{background:#e8f5e9;color:#2e7d32;font-weight:bold}
.count-badge{font-weight:bold;font-size:11px}
.summary{padding:10px 16px 14px;background:#fafafa;border-top:1px solid #eee}
.summary-title{font-size:11px;color:#888;margin-bottom:8px}
.summary-grid{display:flex;gap:6px;flex-wrap:wrap}
.summary-cell{flex:1;min-width:70px;background:#fff;border:1px solid #eee;border-radius:6px;
              padding:6px 4px;text-align:center}
.summary-cell.diverge{background:#fff8e1;border-color:#ffe082}
.summary-cell .period{font-size:9.5px;color:#999}
.summary-cell .pct{font-size:15px;font-weight:bold;margin:2px 0}
.summary-cell .flag{font-size:9px;color:#e65100;min-height:11px}
.group-trend{padding:14px 18px 16px;border-top:1px solid #eee}
.trend-chart{width:100%;height:150px;display:block}
.trend-empty{font-size:11px;color:#999;text-align:center;padding:20px 0}
.legend{padding:14px 20px;font-size:11.5px;color:#666;background:#fff;border-radius:0 0 8px 8px}
.legend span{margin-right:16px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.footer{text-align:center;font-size:11px;color:#aaa;padding:14px}
.table-scroll{overflow-x:auto}
@media (max-width:600px) {
  .ladder{flex-wrap:wrap}
  .dot{flex:0 0 31%}
}
@media (max-width:480px) {
  .hdr{padding-left:10px;padding-right:10px}
  .bench-row{padding-left:6px;padding-right:6px;gap:8px}
  .bench-card{padding-left:10px;padding-right:10px}
  .overview-card{padding-left:10px;padding-right:10px}
  .group-hdr{padding-left:10px;padding-right:10px}
  .summary{padding-left:10px;padding-right:10px}
  .group-trend{padding-left:10px;padding-right:10px}
  .legend{padding-left:10px;padding-right:10px}
}
"""


def _pct_str(pct):
    if pct is None:
        return "–"
    return f"{pct:,.2f}"


def _dist_str(pct):
    if pct is None:
        return "–"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct}%"


def _group_trend_history(g: dict, n_days: int = 7) -> list:
    """把族群內每檔股票的每日均線站上狀態，彙整成 [{date, pct}]（跨個股+週期平均）"""
    by_date = {}
    for s in g["stocks"]:
        for day in s.get("daily_history", []):
            flags = [v for v in day["above"].values() if v is not None]
            if flags:
                by_date.setdefault(day["date"], []).extend(flags)
    dates = sorted(by_date.keys())[-n_days:]
    return [{"date": d, "pct": round(sum(by_date[d]) / len(by_date[d]) * 100, 1)} for d in dates]


def _group_trend_svg(history: list) -> str:
    if not history or len(history) < 2:
        return '<div class="trend-empty">趨勢圖累積中 — 需要至少 2 天的每日紀錄才會顯示</div>'

    w, h = 600, 160
    pad_l, pad_r, pad_t, pad_b = 40, 14, 14, 26
    chart_w, chart_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(history)

    def y_for(pct):
        return pad_t + chart_h - (pct / 100) * chart_h

    def x_for(i):
        return pad_l + (0 if n == 1 else (i / (n - 1)) * chart_w)

    grid = ""
    for v in (0, 25, 50, 75, 100):
        y = y_for(v)
        grid += (f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" y2="{y:.1f}" stroke="#eee" stroke-width="1"/>'
                 f'<text x="{pad_l - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9" fill="#999" '
                 f'font-family="\'IBM Plex Mono\',monospace">{v}%</text>')

    points = [(x_for(i), y_for(d["pct"]), d["date"], d["pct"]) for i, d in enumerate(history)]
    points_str = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in points)

    trend_up = history[-1]["pct"] >= history[0]["pct"]
    color = "#c62828" if trend_up else "#2e7d32"

    x_labels = "".join(
        f'<text x="{x:.1f}" y="{h - 6}" text-anchor="middle" font-size="9" fill="#999" '
        f'font-family="\'IBM Plex Mono\',monospace">{date}</text>'
        for x, _, date, _ in points
    )
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"><title>{date}: {pct}%</title></circle>'
        for x, y, date, pct in points
    )
    value_labels = "".join(
        f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-size="9" font-weight="600" fill="{color}" '
        f'font-family="\'IBM Plex Mono\',monospace">{pct}%</text>'
        for x, y, date, pct in points
    )

    return f"""<svg class="trend-chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none">
      {grid}
      <polyline points="{points_str}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
      {dots}
      {value_labels}
      {x_labels}
    </svg>"""


def _bench_html(b: dict) -> str:
    cnt_n, cnt_total = count_above(b["close"], b["ma"])
    dots = ""
    for p in MA_PERIODS:
        ma_val = b["ma"].get(p)
        above = is_above(b["close"], ma_val)
        cls = "above" if above else ("below" if above is False else "")
        dots += (f'<div class="dot {cls}"><div class="lbl">MA{p}</div>'
                 f'<div class="val">{_pct_str(ma_val)}</div></div>')
    price = _pct_str(b["close"])
    sub = f"站上 {cnt_n}/{cnt_total} 條均線" if cnt_total else "資料不足"

    chg, chg_pct = b.get("chg"), b.get("chg_pct")
    if chg is not None:
        up = chg >= 0
        price_color = "#c62828" if up else "#2e7d32"  # 台股慣例：紅漲綠跌
        arrow = "▲" if up else "▼"
        sign = "+" if up else ""
        chg_html = (f'<span class="chg" style="color:{price_color}">'
                    f'{arrow} {sign}{chg:,.2f}（{sign}{chg_pct:.2f}%）</span>')
    else:
        price_color = "#333"
        chg_html = ""

    return f"""
    <div class="bench-card">
      <div class="bench-name">{b['name']}</div>
      <div class="bench-date">{b['date']} 收盤　<a href="{b['chart_url']}">K線圖 ↗</a></div>
      <div class="bench-price" style="color:{price_color}">{price}<span class="sub">{sub}</span></div>
      {chg_html}
      <div class="ladder">{dots}</div>
    </div>"""


def _stock_row_html(s: dict) -> str:
    ma_map = s["ma"]
    cnt_n, cnt_total = count_above(s["close"], ma_map)
    if cnt_total == 0:
        cnt_color = "#999"
    elif cnt_n >= 4:
        cnt_color = "#c62828"
    elif cnt_n <= 2:
        cnt_color = "#2e7d32"
    else:
        cnt_color = "#e65100"

    ma_cells = ""
    for p in MA_PERIODS:
        v = ma_map.get(p)
        above = is_above(s["close"], v)
        cls = "above" if above else ("below" if above is False else "")
        ma_cells += f'<td class="{cls}">{_pct_str(v)}</td>'

    chart_url = f"https://www.wantgoo.com/stock/{s['code']}/technical-chart"
    cnt_str = f"{cnt_n}/{cnt_total}" if cnt_total else "–"
    return f"""<tr>
      <td class="left code">{s['code']}</td>
      <td class="left"><a href="{chart_url}">{s['name']}</a></td>
      <td>{_pct_str(s['close'])}</td>
      <td>{_pct_str(s['high'])}</td>
      <td>{_pct_str(s['high52w'])}</td>
      <td>{_dist_str(s['dist_pct'])}</td>
      {ma_cells}
      <td><span class="count-badge" style="color:{cnt_color}">{cnt_str}</span></td>
    </tr>"""


def _group_period_stats(g: dict, benchmarks: list) -> list:
    """每個 MA 週期算：族群站上比例(%)、是否背離大盤、大盤中哪些跌破"""
    stats = []
    for p in MA_PERIODS:
        above, total = 0, 0
        for s in g["stocks"]:
            r = is_above(s["close"], s["ma"].get(p))
            if r is not None:
                total += 1
                if r:
                    above += 1
        pct = round(above / total * 100) if total else None
        bench_below = [b["name"].split(" ")[0] for b in benchmarks
                        if is_above(b["close"], b["ma"].get(p)) is False]
        diverge = pct is not None and pct >= 70 and bool(bench_below)
        stats.append({"period": p, "pct": pct, "diverge": diverge, "bench_below": bench_below})
    return stats


def _group_summary_html(g: dict, benchmarks: list) -> str:
    cells = ""
    for st in _group_period_stats(g, benchmarks):
        pct, diverge = st["pct"], st["diverge"]
        pct_color = "#e65100" if diverge else ("#c62828" if (pct or 0) >= 70 else
                                                 "#2e7d32" if (pct or 0) <= 30 else "#333")
        pct_str = "–" if pct is None else f"{pct}%"
        flag = f"強於 {'/'.join(st['bench_below'])}" if diverge else ""
        cells += (f'<div class="summary-cell {"diverge" if diverge else ""}">'
                  f'<div class="period">MA{st["period"]}</div>'
                  f'<div class="pct" style="color:{pct_color}">{pct_str}</div>'
                  f'<div class="flag">{flag}</div></div>')
    return f"""<div class="summary">
      <div class="summary-title">族群站上各均線比例（%）— 與大盤同期均線位置比對</div>
      <div class="summary-grid">{cells}</div>
    </div>"""


def _group_overview_html(groups: list, benchmarks: list) -> str:
    """族群總覽：依「站上均線平均比例」排序，用長條圖快速看出相對大盤的強勢族群"""
    rows = []
    for g in groups:
        stats = _group_period_stats(g, benchmarks)
        pcts = [st["pct"] for st in stats if st["pct"] is not None]
        avg_pct = round(sum(pcts) / len(pcts)) if pcts else None
        diverge_periods = [f"MA{st['period']}" for st in stats if st["diverge"]]
        rows.append({"name": g["name"], "count": len(g["stocks"]), "anchor": f"grp-{g['name']}",
                      "avg_pct": avg_pct, "diverge_periods": diverge_periods})

    rows.sort(key=lambda r: (r["avg_pct"] is None, -(r["avg_pct"] or 0)))

    row_html = ""
    for i, r in enumerate(rows, 1):
        pct = r["avg_pct"]
        bar_pct = pct or 0
        if pct is None:
            color = "#999"
        elif pct >= 70:
            color = "#c62828"
        elif pct <= 30:
            color = "#2e7d32"
        else:
            color = "#e65100"
        pct_str = "–" if pct is None else f"{pct}%"
        diverge_badge = "–"
        if r["diverge_periods"]:
            diverge_badge = (f'<span class="overview-diverge">'
                              f'{"、".join(r["diverge_periods"])} 強於大盤</span>')
        row_html += f"""<tr>
          <td class="overview-rank">{i}</td>
          <td class="overview-name"><a href="#{r['anchor']}">{r['name']}（{r['count']} 檔）</a></td>
          <td><div class="overview-barwrap"><div class="overview-bar" style="width:{bar_pct}%;background:{color}"></div></div></td>
          <td class="overview-pct" style="color:{color}">{pct_str}</td>
          <td>{diverge_badge}</td>
        </tr>"""

    return f"""<div class="overview-card">
      <div class="overview-title">🏆 族群總覽 — 依站上均線平均比例排序</div>
      <div class="overview-sub">數值為該族群在 6 條均線（5/10/20/60/120/240 日）上站上比例的平均值，越高代表族群整體多頭排列越完整</div>
      <table class="overview-table">
        <colgroup>
          <col style="width:26px"><col style="width:26%">
          <col><col style="width:44px"><col style="width:26%">
        </colgroup>
        <tbody>{row_html}</tbody>
      </table>
    </div>"""


def render_html(result: dict) -> str:
    benchmarks = result["benchmarks"]
    groups = result["groups"]

    bench_html = "".join(_bench_html(b) for b in benchmarks)
    overview_html = _group_overview_html(groups, benchmarks)

    group_blocks = []
    for g in groups:
        rows = "".join(_stock_row_html(s) for s in g["stocks"])
        ma_headers = "".join(f"<th>MA{p}</th>" for p in MA_PERIODS)
        group_blocks.append(f"""
        <div class="group-card" id="grp-{g['name']}">
          <div class="group-hdr">{g['name']}（{len(g['stocks'])} 檔）</div>
          <div class="table-scroll">
          <table>
            <thead><tr>
              <th class="left">代號</th><th class="left">名稱</th>
              <th>收盤</th><th>當日最高</th><th>52週最高</th><th>離高點%</th>{ma_headers}<th>站上均線</th>
            </tr></thead>
            <tbody>{rows}</tbody>
          </table>
          </div>
          {_group_summary_html(g, benchmarks)}
          <div class="group-trend">{_group_trend_svg(_group_trend_history(g))}</div>
        </div>""")

    stale_html = ""
    if result.get("stale"):
        stale_html = (f'<div class="stale-note">⚠️ 部分資料日期為 {result["report_date"]}，'
                      f'尚非最新交易日收盤資料，請留意。</div>')

    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
  <div class="wrap">
    <div class="hdr">
      <h2>📊 族群 x 大盤 均線強弱分析</h2>
      <p>{result['report_date']} 收盤　共 {sum(len(g['stocks']) for g in groups)} 檔個股 / {len(groups)} 個族群</p>
    </div>
    {stale_html}
    <div class="bench-row">{bench_html}</div>
    {overview_html}
    {"".join(group_blocks)}
    <div class="legend">
      <span><i style="background:#c62828"></i>站上均線</span>
      <span><i style="background:#2e7d32"></i>跌破均線</span>
      <span><i style="background:#e65100"></i>族群多數站上，但大盤已跌破 → 背離訊號</span>
    </div>
    <div class="footer">本報告為資訊彙整，非投資建議。資料來源：yfinance / FinMind　產生時間：{now_str}</div>
  </div>
</body></html>"""
