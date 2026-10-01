"""
notifier.py — 推送模組（Telegram + Gmail）
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
import os
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_RECIPIENTS

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LENGTH = 4096  # Telegram 單則訊息上限


def send_telegram(message: str) -> bool:
    """
    發送 Telegram 訊息
    若訊息超過上限則自動拆分多則發送
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未設定，改為印出訊息：")
        print(message)
        return False

    url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    chunks = _split_message(message)
    success = True

    for chunk in chunks:
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"❌ Telegram 發送失敗：{resp.status_code} {resp.text}")
                success = False
        except Exception as e:
            print(f"❌ Telegram 發送例外：{e}")
            success = False

    return success


def send_email(subject: str, body: str, to_addr: str = "",
               html: bool = False) -> bool:
    """
    用 Gmail SMTP 寄送郵件。
    - html=True 時 body 視為 HTML；預設純文字。
    - to_addr 指定單一收件人時，只寄給該人。
    - to_addr 為空時，寄給 .env 的 GMAIL_RECIPIENTS 所有人（逗號分隔清單）。
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("⚠️  GMAIL_ADDRESS 或 GMAIL_APP_PASSWORD 未設定，改為印出內容：")
        print(body)
        return False

    # 決定收件人清單（to_addr 支援單一地址，也支援逗號分隔多個地址）
    if to_addr:
        recipients = [a.strip() for a in to_addr.split(",") if a.strip()]
    else:
        recipients = GMAIL_RECIPIENTS if GMAIL_RECIPIENTS else [GMAIL_ADDRESS]

    mime_type = "html" if html else "plain"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(recipients)   # 顯示在信件 To 欄
    msg.attach(MIMEText(body, mime_type, "utf-8"))

    import time as _time
    last_err = None
    for attempt in range(1, 4):
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=60) as server:
                server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
                server.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())
            print(f"✅ 郵件已寄至 {', '.join(recipients)}")
            return True
        except Exception as e:
            last_err = e
            print(f"⚠️  Gmail 第 {attempt} 次寄送失敗：{e}，"
                  f"{'重試中...' if attempt < 3 else '已達重試上限'}")
            if attempt < 3:
                _time.sleep(10 * attempt)
    print(f"❌ Gmail 最終寄送失敗：{last_err}")
    return False


def _split_message(message: str) -> list[str]:
    """將長訊息切割成多個段落"""
    if len(message) <= MAX_LENGTH:
        return [message]

    chunks = []
    lines = message.split("\n")
    current = ""
    for line in lines:
        candidate = current + "\n" + line if current else line
        if len(candidate) > MAX_LENGTH:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
