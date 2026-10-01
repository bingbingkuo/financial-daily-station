"""
youtube_gooaye.py — 股癌新片偵測 + Gemini 股市重點摘要 + Telegram 推播
每次執行檢查是否有新集數，有則摘要並推播。
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── 常數 ─────────────────────────────────────────────────
CHANNEL_ID   = "UC23rnlQU_qE3cec9x709peA"
RSS_URL      = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
STATE_FILE   = Path(__file__).parent / "youtube_gooaye_state.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "yt":    "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


# ── RSS ───────────────────────────────────────────────────
def fetch_latest_video() -> dict | None:
    try:
        resp = requests.get(RSS_URL, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entry = root.find("atom:entry", NS)
        if entry is None:
            return None
        vid   = entry.find("yt:videoId", NS).text
        title = entry.find("atom:title", NS).text
        pub   = entry.find("atom:published", NS).text[:10]
        desc_el = entry.find("media:group/media:description", NS)
        desc  = (desc_el.text or "").strip() if desc_el is not None else ""
        return {"id": vid, "title": title, "published": pub, "description": desc}
    except Exception as e:
        print(f"❌ RSS 抓取失敗：{e}")
        return None


# ── 狀態 ─────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_video_id": ""}


def save_state(video_id: str):
    STATE_FILE.write_text(json.dumps({"last_video_id": video_id}, ensure_ascii=False))


# ── Gemini 摘要 ───────────────────────────────────────────
def summarize(title: str, description: str) -> str:
    if not GEMINI_API_KEY:
        return "（未設定 GEMINI_API_KEY，無法摘要）"

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except ImportError:
        return "（google-genai 套件未安裝）"

    prompt = f"""你是一位專業的股市分析師助理。
以下是 YouTube 節目「股癌」新一集的標題與節目描述。
請從中提取所有與股市、投資、總體經濟相關的重點，以繁體中文條列輸出。

規則：
- 只列出與股票、ETF、總經、市場趨勢、投資策略相關的內容
- 忽略廣告、業配、個人閒聊等無關內容
- 每點簡潔有力，50 字以內
- 若描述內容不足以判斷股市重點，請回覆「本集描述未包含明確股市資訊」

標題：{title}

節目描述：
{description[:3000]}
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        return f"（Gemini 摘要失敗：{e}）"


# ── Telegram 推播 ─────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram 未設定，改為印出：\n")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4096
    chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
            }, timeout=10)
            if r.status_code != 200:
                print(f"❌ Telegram 失敗：{r.status_code} {r.text}")
        except Exception as e:
            print(f"❌ Telegram 例外：{e}")


# ── 主流程 ────────────────────────────────────────────────
def main(force: bool = False):
    video = fetch_latest_video()
    if not video:
        print("無法取得影片資訊")
        return

    state = load_state()
    print(f"最新影片：{video['published']} | {video['id']} | {video['title']}")

    if not force and video["id"] == state["last_video_id"]:
        print("✅ 無新集數")
        return

    print("🆕 發現新集數，開始摘要...")
    summary = summarize(video["title"], video["description"])

    video_url = f"https://www.youtube.com/watch?v={video['id']}"
    message = (
        f"🎙️ <b>股癌新集數｜{video['title']}</b>\n"
        f"📅 {video['published']}\n"
        f"🔗 {video_url}\n\n"
        f"📌 <b>股市重點摘要</b>\n"
        f"{summary}"
    )

    send_telegram(message)
    save_state(video["id"])
    print("✅ 推播完成")


if __name__ == "__main__":
    force = "--force" in sys.argv
    main(force=force)
