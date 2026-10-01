"""
Telegram 推播模組（功率元件情報雷達）
"""
import requests
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def format_signal(signal: dict) -> str:
    conf = signal.get("confidence", 0.0)
    if conf >= 0.75:
        badge = "🔴"
    elif conf >= 0.5:
        badge = "🟡"
    else:
        badge = "⚪"

    company = signal.get("company") or "不明廠商"
    product = signal.get("product") or "功率元件"
    pct = signal.get("price_change_pct")
    pct_str = f"+{pct}%" if pct else "幅度未揭露"
    eff = signal.get("effective_date") or "未揭露"
    downstream = signal.get("downstream") or "未揭露"
    reason = signal.get("reason") or "—"
    source = signal.get("source_title", "")
    url = signal.get("source_url", "")

    lines = [
        f"{badge} <b>功率元件漲價情報</b>",
        "",
        f"🏭 廠商：{company}",
        f"📦 產品：{product}",
        f"📈 漲幅：{pct_str}",
        f"📅 生效：{eff}",
        f"🔗 下游：{downstream}",
        f"📝 原因：{reason}",
        f"📊 可信度：{conf:.0%}",
        "",
        f'📰 <a href="{url}">{source[:50]}</a>',
    ]
    return "\n".join(lines)


def format_summary(signals: list[dict]) -> str:
    """每日彙整推播"""
    lines = [
        "⚡ <b>功率元件漲價雷達 — 今日彙整</b>",
        f"共發現 <b>{len(signals)}</b> 則相關情報",
        "",
    ]
    for i, s in enumerate(signals, 1):
        conf = s.get("confidence", 0.0)
        badge = "🔴" if conf >= 0.75 else "🟡" if conf >= 0.5 else "⚪"
        company = s.get("company") or "不明"
        product = s.get("product") or "功率元件"
        pct = s.get("price_change_pct")
        pct_str = f"+{pct}%" if pct else "幅度未揭露"
        url = s.get("source_url", "")
        lines.append(f"{i}. {badge} <a href=\"{url}\">{company} · {product} · {pct_str}</a>")

    return "\n".join(lines)


def send(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[notifier] Token 未設定，印出訊息：")
        print(message)
        return False

    try:
        resp = requests.post(API_URL, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notifier] 推播失敗：{e}")
        return False
