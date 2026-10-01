import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import yfinance as yf

load_dotenv(dotenv_path=str(__file__).replace("mailer.py", "") + "../stock_bot/.env")

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GMAIL_RECIPIENTS = os.getenv("GMAIL_RECIPIENTS", GMAIL_ADDRESS)

DATA_DIR = Path(__file__).parent / "data"

SENTIMENT_COLOR = {"bullish": "#16a34a", "strong_bullish": "#16a34a", "bearish": "#dc2626", "strong_bearish": "#dc2626", "neutral": "#6b7280"}
SENTIMENT_LABEL = {"bullish": "📈 看漲", "strong_bullish": "📈 看漲", "bearish": "📉 看跌", "strong_bearish": "📉 看跌", "neutral": "📊 中性"}
RATING_LABEL = {"strong_buy": "⭐ 強烈看好", "buy": "👍 偏多", "neutral": "🤝 觀望", "sell": "👎 偏空", "strong_sell": "🚨 強烈看空"}

def _fetch_price(ticker: str, start_date: str, market: str) -> dict:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=1)
        hist = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"))
        if hist.empty and market == "TW":
            ticker2 = ticker.replace(".TW", ".TWO")
            hist = yf.Ticker(ticker2).history(start=start.strftime("%Y-%m-%d"))
        if hist.empty:
            return {}
        closes = hist["Close"].round(2).tolist()
        base = closes[0]
        pct = round((closes[-1] - base) / base * 100, 2) if base else 0
        return {"base": round(base, 2), "current": round(closes[-1], 2), "pct": pct}
    except Exception:
        return {}


def _section_title(title: str) -> str:
    return f'<div style="font-size:11px;font-weight:700;letter-spacing:0.08em;color:#94a3b8;text-transform:uppercase;margin:20px 0 10px;padding-bottom:6px;border-bottom:1px solid #e2e8f0">{title}</div>'


# ── 第一欄：最新集數完整內容 ──────────────────────────────────────
def _build_episode_section(ep: dict) -> str:
    sentiment = ep.get("sentiment", "neutral")
    color = SENTIMENT_COLOR.get(sentiment, "#6b7280")
    label = SENTIMENT_LABEL.get(sentiment, "📊 中性")
    pub = (ep.get("pub_date") or "")[:10]

    # 重點
    kp_html = ""
    key_points = ep.get("key_points", [])
    if key_points:
        items = "".join(
            f'<li style="margin:6px 0;color:#475569;font-size:13px;line-height:1.7">{(p["text"] if isinstance(p, dict) else p)}</li>'
            for p in key_points
        )
        kp_html = _section_title("重點分析") + f'<ul style="padding-left:18px;margin:0">{items}</ul>'

    # 操作心法
    ti_html = ""
    trading_insights = ep.get("trading_insights", [])
    if trading_insights:
        items = ""
        for i, ins in enumerate(trading_insights, 1):
            stocks = "".join(
                f'<span style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:4px;padding:1px 6px;font-size:11px;font-family:monospace;margin-right:4px">{s}</span>'
                for s in (ins.get("related_stocks") or [])
            )
            items += f'''<div style="margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #f1f5f9">
              <div style="font-size:13px;font-weight:600;color:#1e293b;margin-bottom:4px">{i}. {ins.get("title","")}</div>
              <div style="font-size:13px;color:#64748b;line-height:1.7">{ins.get("detail","")}</div>
              {f'<div style="margin-top:6px">{stocks}</div>' if stocks else ""}
            </div>'''
        ti_html = _section_title("💡 操作心法") + f'<div style="background:#f8fafc;border-left:3px solid #e2e8f0;border-radius:6px;padding:14px 16px">{items}</div>'

    # 金句
    nq = ep.get("notable_quotes", {})
    invest_q = (nq if isinstance(nq, list) else nq.get("investing", []))
    life_q = ([] if isinstance(nq, list) else nq.get("life", []))
    nq_html = ""
    if invest_q or life_q:
        nq_html = _section_title("本集金句")
        for q in invest_q:
            nq_html += f'<p style="font-size:13px;color:#64748b;font-style:italic;border-left:2px solid #cbd5e1;padding-left:10px;margin:6px 0;line-height:1.7">「{q}」</p>'
        for q in life_q:
            nq_html += f'<p style="font-size:13px;color:#64748b;font-style:italic;border-left:2px solid #e2e8f0;padding-left:10px;margin:6px 0;line-height:1.7">「{q}」</p>'

    # 股票 chips
    tw = ep.get("tw_stocks", [])
    us = ep.get("us_stocks", [])
    chips = "".join(
        f'<span style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:4px;padding:2px 8px;font-size:11px;font-family:monospace;margin:2px">{s}</span>'
        for s in tw + us
    )

    return f'''
<div style="background:#fff;border-radius:12px;padding:22px 24px;border:1px solid #e2e8f0">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
    <span style="background:#0f172a;color:#fff;font-size:11px;padding:2px 8px;border-radius:4px">{ep.get("podcast","")}</span>
    <span style="color:{color};font-weight:600;font-size:12px">{label}</span>
    <span style="color:#94a3b8;font-size:12px;margin-left:auto">{pub}</span>
  </div>
  <h2 style="margin:0 0 10px;font-size:17px;color:#0f172a;line-height:1.4">{ep.get("title","")}</h2>
  <p style="color:#475569;font-size:14px;line-height:1.75;margin:0 0 12px">{ep.get("summary","")}</p>
  {f'<div style="margin-bottom:12px">{chips}</div>' if chips else ""}
  {kp_html}
  {ti_html}
  {nq_html}
</div>'''


# ── 第二欄：股票追蹤清單 ────────────────────────────────────────
def _build_watchlist_section(watchlist: dict) -> str:
    if not watchlist:
        return '<p style="color:#94a3b8;font-size:13px">尚無追蹤股票</p>'

    rows = ""
    for key, stock in sorted(watchlist.items()):
        symbol = stock.get("symbol", key.split(":")[-1])
        market = stock.get("market", "")
        market_flag = "🇹🇼" if market == "TW" else "🇺🇸"
        name = stock.get("name", "")
        mentions = stock.get("mentions", [])
        count = len(mentions)

        # 最新評級
        latest = sorted(mentions, key=lambda m: m.get("date", ""), reverse=True)[0] if mentions else {}
        rating = latest.get("rating", "")
        rating_label = RATING_LABEL.get(rating, "")
        rating_color = {"strong_buy": "#16a34a", "buy": "#22c55e", "neutral": "#6b7280", "sell": "#f97316", "strong_sell": "#dc2626"}.get(rating, "#6b7280")

        # 股價
        price_html = ""
        price = _fetch_price(stock.get("ticker", ""), stock.get("first_date", ""), market)
        if price:
            pct = price["pct"]
            pct_color = "#16a34a" if pct > 0 else "#dc2626" if pct < 0 else "#6b7280"
            pct_str = f'+{pct}%' if pct > 0 else f'{pct}%'
            price_html = f'<div style="font-size:11px;color:#94a3b8">{price["base"]} → {price["current"]}</div><div style="font-size:13px;font-weight:700;color:{pct_color}">{pct_str}</div>'
        else:
            price_html = '<div style="font-size:12px;color:#cbd5e1">—</div>'

        # hashtags
        tags = stock.get("tags", [])
        tags_html = ""
        if tags:
            tags_html = "<br>" + "".join(
                f'<span style="font-size:10px;color:#94a3b8;margin-right:4px">#{t}</span>'
                for t in tags
            )

        rows += f'''<tr style="border-top:1px solid #f1f5f9">
          <td style="padding:10px 10px;white-space:nowrap;vertical-align:top">
            <div style="font-family:monospace;font-size:13px;font-weight:700;color:#0f172a">{market_flag} {symbol}</div>
            <div style="font-size:12px;color:#64748b">{name}</div>
            {tags_html}
          </td>
          <td style="padding:10px 10px;text-align:right;vertical-align:top">{price_html}</td>
          <td style="padding:10px 10px;font-size:12px;color:{rating_color};font-weight:600;white-space:nowrap;vertical-align:top">{rating_label}</td>
          <td style="padding:10px 10px;font-size:12px;color:#94a3b8;text-align:center;vertical-align:top">{count}次</td>
        </tr>'''

    return f'''
<div style="background:#fff;border-radius:12px;border:1px solid #e2e8f0;overflow:hidden">
  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="background:#f8fafc">
        <th style="padding:8px 10px;font-size:11px;color:#94a3b8;text-align:left;font-weight:600">代號 / 名稱</th>
        <th style="padding:8px 10px;font-size:11px;color:#94a3b8;text-align:right;font-weight:600">追蹤報酬</th>
        <th style="padding:8px 10px;font-size:11px;color:#94a3b8;text-align:left;font-weight:600">評級</th>
        <th style="padding:8px 10px;font-size:11px;color:#94a3b8;text-align:center;font-weight:600">提及</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>'''


# ── 第三欄：產業趨勢 ────────────────────────────────────────────
def _build_industries_section(industries: dict) -> str:
    if not industries:
        return '<p style="color:#94a3b8;font-size:13px">尚無產業資料</p>'

    items = ""
    for name, ind in sorted(industries.items()):
        mentions = ind.get("mentions", [])
        if not mentions:
            continue
        latest = sorted(mentions, key=lambda m: m.get("date", ""), reverse=True)[0]
        outlook = latest.get("outlook", "neutral")
        color = SENTIMENT_COLOR.get(outlook, "#6b7280")
        label = SENTIMENT_LABEL.get(outlook, "📊 中性")
        view = latest.get("view", "")[:120] + ("…" if len(latest.get("view", "")) > 120 else "")
        date = latest.get("date", "")
        count = len(mentions)

        items += f'''
<div style="padding:12px 0;border-bottom:1px solid #f1f5f9">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
    <span style="font-size:13px;font-weight:700;color:#0f172a">{name}</span>
    <span style="font-size:11px;color:{color};font-weight:600">{label}</span>
    <span style="font-size:11px;color:#cbd5e1;margin-left:auto">{date} · {count}次提及</span>
  </div>
  <p style="font-size:12px;color:#64748b;line-height:1.7;margin:0">{view}</p>
</div>'''

    return f'<div style="background:#fff;border-radius:12px;padding:4px 20px;border:1px solid #e2e8f0">{items}</div>'


# ── 主 HTML ─────────────────────────────────────────────────────
def build_html(episodes: list[dict], date_str: str) -> str:
    # 載入完整資料
    wl_path = DATA_DIR / "watchlist.json"
    ind_path = DATA_DIR / "industries.json"
    watchlist = json.loads(wl_path.read_text(encoding="utf-8")) if wl_path.exists() else {}
    industries = json.loads(ind_path.read_text(encoding="utf-8")) if ind_path.exists() else {}

    # 最新一集
    all_eps_path = DATA_DIR / "all_episodes.json"
    if all_eps_path.exists():
        all_eps = json.loads(all_eps_path.read_text(encoding="utf-8"))
        latest_ep = next((e for e in all_eps if e.get("analysis_source") == "audio"), None)
    else:
        latest_ep = episodes[0] if episodes else None

    ep_html = _build_episode_section(latest_ep) if latest_ep else '<p style="color:#94a3b8">無集數資料</p>'
    wl_html = _build_watchlist_section(watchlist)
    ind_html = _build_industries_section(industries)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang TC',sans-serif">
<div style="max-width:700px;margin:0 auto;padding:24px 16px">

  <!-- Header -->
  <div style="text-align:center;margin-bottom:28px">
    <h1 style="font-size:22px;font-weight:700;color:#0f172a;margin:0">🎙️ Podcast 財經摘要</h1>
    <p style="color:#64748b;margin:6px 0 0;font-size:13px">{date_str}</p>
  </div>

  <!-- 第一欄：最新集數 -->
  <div style="font-size:13px;font-weight:700;color:#0f172a;margin-bottom:10px">📻 最新集數</div>
  {ep_html}

  <!-- 第二欄：股票追蹤 -->
  <div style="font-size:13px;font-weight:700;color:#0f172a;margin:24px 0 10px">📈 股票追蹤清單（共 {len(watchlist)} 檔）</div>
  {wl_html}

  <!-- 第三欄：產業趨勢 -->
  <div style="font-size:13px;font-weight:700;color:#0f172a;margin:24px 0 10px">🔭 產業趨勢（共 {len(industries)} 個）</div>
  {ind_html}

  <!-- Footer -->
  <p style="text-align:center;color:#94a3b8;font-size:12px;margin-top:28px">
    由 Podcast Digest 自動生成
  </p>
</div>
</body>
</html>"""


def send_digest(episodes: list[dict] = None):
    if not GMAIL_APP_PASSWORD:
        print("  ✗ 未設定 GMAIL_APP_PASSWORD，跳過寄信")
        return

    date_str = datetime.now().strftime("%Y年%m月%d日")
    html = build_html(episodes or [], date_str)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎙️ Podcast 財經摘要 {date_str}"
    msg["From"] = GMAIL_ADDRESS
    recipients = [r.strip() for r in GMAIL_RECIPIENTS.split(",")]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())

    print(f"  ✓ Email 已寄送至: {', '.join(recipients)}")
