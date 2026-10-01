"""
main_scout.py — 台股選股雷達執行入口

快取機制：
  同一天若 scout_cache_YYYYMMDD.html 已存在（代表管線跑過），
  直接讀取快取重送 email，不重跑 Gemini，節省 API 配額。
"""

import sys
import os
import json
import shutil

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime

from modules.scout import run_scout, _fetch_market_overview
from modules.notifier import send_email
from modules.dark_theme import apply_dark_theme
from modules.site_shell import render_report_page
from modules.git_publish import publish_docs

_CACHE_DIR  = os.path.dirname(os.path.abspath(__file__))
_CACHE_DAYS = 3   # 保留最近 N 天的快取
_DOCS_ROOT  = os.path.join(os.path.dirname(_CACHE_DIR), "docs")
_DOCS_DIR   = os.path.join(_DOCS_ROOT, "tw_scout")


def _cache_path(date_str: str) -> str:
    return os.path.join(_CACHE_DIR, f"scout_cache_{date_str}.html")


def _cleanup_old_caches() -> None:
    """刪除超過 _CACHE_DAYS 天的快取檔案。"""
    for fname in os.listdir(_CACHE_DIR):
        if not (fname.startswith("scout_cache_") and fname.endswith(".html")):
            continue
        date_str = fname[len("scout_cache_"):-len(".html")]
        try:
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if (datetime.now() - file_date).days > _CACHE_DAYS:
                os.remove(os.path.join(_CACHE_DIR, fname))
                print(f"🗑  已刪除舊快取：{fname}")
        except Exception:
            pass


def _publish_to_docs(date_str: str, html: str) -> None:
    """把報告複製到 docs/tw_scout/ 並更新 manifest.json。"""
    try:
        os.makedirs(_DOCS_DIR, exist_ok=True)
        formatted   = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        dark_html   = apply_dark_theme(html)
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

        # 額外輸出 manifest.js：<script src> 不受 file:// 的 CORS 限制，
        # 讓 index.html 雙擊本機檔案打開時也能正常讀到清單（fetch 對 file:// 會被擋）。
        manifest_js_path = os.path.join(_DOCS_DIR, "manifest.js")
        with open(manifest_js_path, "w", encoding="utf-8") as f:
            f.write("window.TW_SCOUT_MANIFEST = ")
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write(";\n")

        # 包上跟 docs/index.html 一致的側欄／頂部列外殼再寫檔：純 <a> 連結導覽，
        # 雙擊本機檔案打開（file://）也能正常運作（iframe 嵌入在 file:// 下會被
        # 瀏覽器擋掉、整片空白，所以改用這種「烤進頁面」的做法）。
        page_html = render_report_page(
            docs_root=_DOCS_ROOT, kind="tw", date=formatted,
            active_file=active_file, report_html=dark_html,
        )
        dest = os.path.join(_DOCS_DIR, active_file)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(page_html)
        shutil.copy(dest, os.path.join(_DOCS_DIR, "latest.html"))

        # 大盤概況也另外抓一份給首頁總覽用（market.js 給 <script src> 避開 file://
        # CORS；market.json 保留給跑伺服器 / GitHub Pages 時的 fetch fallback）。
        try:
            overview = _fetch_market_overview()
        except Exception:
            overview = {}
        market_json_path = os.path.join(_DOCS_DIR, "market.json")
        with open(market_json_path, "w", encoding="utf-8") as f:
            json.dump(overview, f, ensure_ascii=False, indent=2)
        market_js_path = os.path.join(_DOCS_DIR, "market.js")
        with open(market_js_path, "w", encoding="utf-8") as f:
            f.write("window.TW_MARKET_OVERVIEW = ")
            json.dump(overview, f, ensure_ascii=False, indent=2)
            f.write(";\n")

        print(f"🌐 已發布到 docs/tw_scout/{formatted}.html")
    except Exception as e:
        print(f"⚠️  docs 發布失敗：{e}")


def main():
    print("=" * 50)
    print("台股選股雷達 (Market Scout) 啟動")
    print("=" * 50)

    today      = datetime.now().strftime("%Y%m%d")
    cache_file = _cache_path(today)

    if os.path.exists(cache_file):
        print(f"📦 發現今日快取 {os.path.basename(cache_file)}，直接重送 email（跳過 Gemini）...")
        with open(cache_file, encoding="utf-8") as f:
            report = f.read()
    else:
        report = run_scout()
        # 寫入快取（供同日重送使用）
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"💾 已儲存今日快取：{os.path.basename(cache_file)}")
        except Exception as e:
            print(f"⚠️  快取寫入失敗：{e}")

    _cleanup_old_caches()
    _publish_to_docs(today, report)
    publish_docs(f"台股選股雷達 {today}")

    today_display = datetime.now().strftime("%Y/%m/%d")
    subject = f"📡 台股選股雷達 {today_display}"
    send_email(subject, report, html=True)

    print("\n" + "=" * 50)
    print("完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
