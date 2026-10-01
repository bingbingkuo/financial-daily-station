"""
main_ma_analysis.py — 族群 x 大盤 均線強弱分析　執行入口
執行時間：台灣時間 週一至週五 14:30（收盤後約 1 小時，等資料源更新完整）

快取機制：
  同一天若 ma_cache_YYYYMMDD.html 已存在（代表當天跑過），
  直接讀取快取重送 email，不重新抓資料。
"""

import json
import os
import shutil
import sys
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from modules.ma_analysis import run_analysis, render_html
from modules.notifier import send_email
from modules.dark_theme import apply_dark_theme, MA_EXTRA_DARK_COLOR_MAP
from modules.site_shell import render_report_page
from modules.git_publish import publish_docs
from config import MA_ANALYSIS_RECIPIENTS

_CACHE_DIR  = os.path.dirname(os.path.abspath(__file__))
_CACHE_DAYS = 5   # 保留最近 N 天的快取
_DOCS_ROOT  = os.path.join(os.path.dirname(_CACHE_DIR), "docs")
_DOCS_DIR   = os.path.join(_DOCS_ROOT, "tw_ma")


def _cache_path(date_str: str) -> str:
    return os.path.join(_CACHE_DIR, f"ma_cache_{date_str}.html")


def _publish_to_docs(date_str: str, html: str) -> None:
    """把報告發布到 docs/tw_ma/（比照 main_scout.py）。"""
    try:
        os.makedirs(_DOCS_DIR, exist_ok=True)
        formatted   = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        dark_html   = apply_dark_theme(html, extra_map=MA_EXTRA_DARK_COLOR_MAP)
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
            f.write("window.TW_MA_MANIFEST = ")
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write(";\n")

        page_html = render_report_page(
            docs_root=_DOCS_ROOT, kind="twma", date=formatted,
            active_file=active_file, report_html=dark_html,
        )
        dest = os.path.join(_DOCS_DIR, active_file)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(page_html)
        shutil.copy(dest, os.path.join(_DOCS_DIR, "latest.html"))

        print(f"🌐 已發布到 docs/tw_ma/{formatted}.html")
    except Exception as e:
        print(f"⚠️  docs 發布失敗：{e}")


def _cleanup_old_caches() -> None:
    for fname in os.listdir(_CACHE_DIR):
        if not (fname.startswith("ma_cache_") and fname.endswith(".html")):
            continue
        date_str = fname[len("ma_cache_"):-len(".html")]
        try:
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if (datetime.now() - file_date).days > _CACHE_DAYS:
                os.remove(os.path.join(_CACHE_DIR, fname))
                print(f"🗑  已刪除舊快取：{fname}")
        except Exception:
            pass


def main():
    print("=" * 50)
    print("族群 x 大盤 均線強弱分析 啟動")
    print("=" * 50)

    today      = datetime.now().strftime("%Y%m%d")
    cache_file = _cache_path(today)

    try:
        if os.path.exists(cache_file):
            print(f"📦 發現今日快取 {os.path.basename(cache_file)}，直接重送 email...")
            with open(cache_file, encoding="utf-8") as f:
                report_html = f.read()
        else:
            print("📊 抓取個股 / 大盤均線資料...")
            result = run_analysis()
            if result.get("stale"):
                print(f"⚠️  資料日期為 {result['report_date']}，非最新交易日")
            report_html = render_html(result)
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    f.write(report_html)
                print(f"💾 已儲存今日快取：{os.path.basename(cache_file)}")
            except Exception as e:
                print(f"⚠️  快取寫入失敗：{e}")

        _cleanup_old_caches()
        _publish_to_docs(today, report_html)
        publish_docs(f"台股族群強弱 {today}")

        today_display = datetime.now().strftime("%Y/%m/%d")
        subject = f"🇹🇼 台股族群強弱均線分析 {today_display}"
        to_addr = ",".join(MA_ANALYSIS_RECIPIENTS)
        ok = send_email(subject, report_html, to_addr=to_addr, html=True)
        print("✅ 郵件發送成功！" if ok else "⚠️  郵件發送失敗（見上方訊息）")

    except Exception:
        traceback.print_exc()

    print("\n" + "=" * 50)
    print("完成")
    print("=" * 50)
    sys.exit(0)  # 保持 exit 0，避免 launchd throttle


if __name__ == "__main__":
    main()
