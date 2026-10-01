"""
main_us_scout.py — 美股選股雷達執行入口

快取機制：
  同一天若 us_scout_cache_YYYYMMDD.html 已存在，
  直接讀取快取重送 email，不重跑 Gemini，節省 API 配額。

yfinance SQLite 隔離：
  在 import yfinance 之前把 cache 路徑改到臨時目錄，
  避免多執行緒同時寫入同一個 .db 造成 disk I/O error。
"""

import sys
import os
import json
import shutil
import tempfile

# ── yfinance SQLite 隔離（必須在 import yfinance 之前設定）────────────
# yfinance 用 SQLite 快取時區資訊（tkr-tz.db）。
# 多執行緒下批次下載時，多個 thread 競爭同一個 .db → disk I/O error。
# 解法：每次啟動用獨立的 tempdir，完全隔離，程式結束後 OS 自動回收。
_YF_TMP_CACHE = tempfile.mkdtemp(prefix="yf_cache_")
os.environ["YF_CACHE_DIR"] = _YF_TMP_CACHE          # 部分版本讀這個
try:
    import yfinance as _yf_pre
    if hasattr(_yf_pre, "set_tz_cache_location"):
        _yf_pre.set_tz_cache_location(_YF_TMP_CACHE)
        print(f"  yfinance cache → {_YF_TMP_CACHE}")
except Exception:
    pass
# ────────────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime

from modules.us_scout import run_us_scout, _fetch_us_market_overview
from modules.notifier import send_email
from modules.dark_theme import apply_dark_theme, US_EXTRA_DARK_COLOR_MAP
from modules.site_shell import render_report_page
from modules.git_publish import publish_docs

_CACHE_DIR  = os.path.dirname(os.path.abspath(__file__))
_CACHE_DAYS = 3
_DOCS_ROOT  = os.path.join(os.path.dirname(_CACHE_DIR), "docs")
_DOCS_DIR   = os.path.join(_DOCS_ROOT, "us_scout")


def _cache_path(date_str: str) -> str:
    return os.path.join(_CACHE_DIR, f"us_scout_cache_{date_str}.html")


def _cleanup_old_caches() -> None:
    for fname in os.listdir(_CACHE_DIR):
        if not (fname.startswith("us_scout_cache_") and fname.endswith(".html")):
            continue
        date_str = fname[len("us_scout_cache_"):-len(".html")]
        try:
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if (datetime.now() - file_date).days > _CACHE_DAYS:
                os.remove(os.path.join(_CACHE_DIR, fname))
                print(f"🗑  已刪除舊快取：{fname}")
        except Exception:
            pass


def _publish_to_docs(date_str: str, html: str) -> None:
    """把報告複製到 docs/us_scout/ 並更新 manifest.json（比照 main_scout.py）。"""
    try:
        os.makedirs(_DOCS_DIR, exist_ok=True)
        formatted   = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        dark_html   = apply_dark_theme(html, extra_map=US_EXTRA_DARK_COLOR_MAP)
        active_file = f"{formatted}.html"

        manifest_path = os.path.join(_DOCS_DIR, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        else:
            manifest = []

        entry = {"date": formatted, "file": active_file}
        if not any(e["date"] == formatted for e in manifest):
            manifest.insert(0, entry)
            manifest.sort(key=lambda e: e["date"], reverse=True)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

        manifest_js_path = os.path.join(_DOCS_DIR, "manifest.js")
        with open(manifest_js_path, "w", encoding="utf-8") as f:
            f.write("window.US_SCOUT_MANIFEST = ")
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write(";\n")

        # 包上跟 docs/index.html 一致的側欄／頂部列外殼（比照 main_scout.py，
        # 避免 file:// 下 iframe 嵌入被瀏覽器擋掉變空白）。
        page_html = render_report_page(
            docs_root=_DOCS_ROOT, kind="us", date=formatted,
            active_file=active_file, report_html=dark_html,
        )
        dest = os.path.join(_DOCS_DIR, active_file)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(page_html)
        shutil.copy(dest, os.path.join(_DOCS_DIR, "latest.html"))

        try:
            overview = _fetch_us_market_overview()
        except Exception:
            overview = {}
        market_json_path = os.path.join(_DOCS_DIR, "market.json")
        with open(market_json_path, "w", encoding="utf-8") as f:
            json.dump(overview, f, ensure_ascii=False, indent=2)
        market_js_path = os.path.join(_DOCS_DIR, "market.js")
        with open(market_js_path, "w", encoding="utf-8") as f:
            f.write("window.US_MARKET_OVERVIEW = ")
            json.dump(overview, f, ensure_ascii=False, indent=2)
            f.write(";\n")

        print(f"🌐 已發布到 docs/us_scout/{formatted}.html")
    except Exception as e:
        print(f"⚠️  docs 發布失敗：{e}")


def main():
    print("=" * 50)
    print("美股選股雷達 (US Scout) 啟動")
    print("=" * 50)

    today      = datetime.now().strftime("%Y%m%d")
    cache_file = _cache_path(today)

    if os.path.exists(cache_file):
        print(f"📦 發現今日快取，直接重送 email（跳過 Gemini）…")
        with open(cache_file, encoding="utf-8") as f:
            report = f.read()
    else:
        report = run_us_scout()
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"💾 今日快取已儲存：{os.path.basename(cache_file)}")
        except Exception as e:
            print(f"⚠️  快取寫入失敗：{e}")

    _cleanup_old_caches()
    _publish_to_docs(today, report)
    publish_docs(f"美股選股雷達 {today}")

    today_display = datetime.now().strftime("%Y/%m/%d")
    subject = f"🔭 美股選股雷達 {today_display}"
    send_email(subject, report, html=True)

    print("\n" + "=" * 50)
    print("完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
