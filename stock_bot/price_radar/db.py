"""
SQLite 操作模組
"""
import sqlite3
import os
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "price_radar.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT,
                product TEXT,
                price_change_pct REAL,
                effective_date TEXT,
                reason TEXT,
                downstream TEXT,
                source_url TEXT UNIQUE,
                source_title TEXT,
                published_at TEXT,
                confidence REAL DEFAULT 0.0,
                notified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_urls (
                url TEXT PRIMARY KEY,
                processed_at TEXT DEFAULT (datetime('now'))
            )
        """)


def is_processed(url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM processed_urls WHERE url=?", (url,)).fetchone()
        return row is not None


def mark_processed(url: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_urls (url) VALUES (?)", (url,)
        )


def save_signal(signal: dict) -> bool:
    """回傳 True 表示新增成功，False 表示 URL 已存在"""
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO price_signals
                    (company, product, price_change_pct, effective_date, reason,
                     downstream, source_url, source_title, published_at, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("company"),
                signal.get("product"),
                signal.get("price_change_pct"),
                signal.get("effective_date"),
                signal.get("reason"),
                signal.get("downstream"),
                signal.get("source_url"),
                signal.get("source_title"),
                signal.get("published_at"),
                signal.get("confidence", 0.0),
            ))
            return conn.total_changes > 0
    except sqlite3.IntegrityError:
        return False


def get_recent_signals(days: int = 14) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM price_signals
            WHERE created_at >= ?
            ORDER BY created_at DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def mark_notified(signal_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE price_signals SET notified=1 WHERE id=?", (signal_id,))
