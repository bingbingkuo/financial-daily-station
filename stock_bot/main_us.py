"""
main_us.py — 美股盤前分析報告
執行時間：美東時間 週一至週五 08:30（= 台灣時間 20:30 EDT / 21:30 EST）
內容：美國總經 / 美股前三族群+領頭股 / 美股持倉動態
"""

import sys
import traceback
from datetime import datetime

from modules.macro     import get_us_macro, format_us_macro
from modules.sectors   import get_us_top_sectors, format_us_sectors
from modules.portfolio import (get_all_us_portfolio, format_us_portfolio,
                               get_portfolio_schedule, format_schedule)
from config import US_STOCKS
from modules.notifier  import send_telegram


def build_us_report() -> str:
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [f"\n🇺🇸 美股盤前分析  {now}", "=" * 30]

    # 1. 美國總經
    print("📊 抓取美國總經數據...")
    try:
        lines.append(format_us_macro(get_us_macro()))
    except Exception as e:
        lines.append(f"📊 【美國總經環境】\n  ⚠️ 失敗：{e}")

    lines.append("")

    # 2. 美股前三族群 + 領頭股
    print("🏆 分析美股族群強弱...")
    try:
        us_sectors = get_us_top_sectors(3)
        lines.append(format_us_sectors(us_sectors))
    except Exception as e:
        lines.append(f"🏆 【美股前三大強勢族群】\n  ⚠️ 失敗：{e}")

    lines.append("")

    # 3. 持倉近期行程
    print("📅 取得持倉財報/法說會行程...")
    try:
        schedule = get_portfolio_schedule({}, US_STOCKS)
        lines.append(format_schedule(schedule))
    except Exception as e:
        lines.append(f"📅 【持倉近期行程】\n  ⚠️ 失敗：{e}")

    lines.append("")

    # 4. 美股持倉新聞
    print("📁 取得美股持倉新聞...")
    try:
        us_data = get_all_us_portfolio()
        lines.append(format_us_portfolio(us_data))
    except Exception as e:
        lines.append(f"📁 【美股持倉動態】\n  ⚠️ 失敗：{e}")

    return "\n".join(lines)


def main():
    print("🚀 美股盤前報告啟動...")
    try:
        report = build_us_report()
        print("\n" + report)
        print("\n📤 發送 Telegram...")
        ok = send_telegram(report)
        print("✅ 美股報告推送成功！" if ok else "⚠️  Telegram 未設定")
    except Exception:
        traceback.print_exc()
        sys.exit(0)  # 保持 exit 0，避免 launchd throttle


if __name__ == "__main__":
    main()
