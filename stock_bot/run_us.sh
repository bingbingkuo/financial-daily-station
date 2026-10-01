#!/bin/bash
# 美股晚報 wrapper — 確保永遠 exit 0，防止 launchd throttle
cd /Users/bingbing_kuo/Desktop/Claude_assist/stock_bot
export PATH=/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export HOME=/Users/bingbing_kuo
/opt/anaconda3/bin/python3 main_us.py || echo "[WRAPPER] main_us.py 以非零 exit code 結束，但 wrapper 強制 exit 0"
exit 0
