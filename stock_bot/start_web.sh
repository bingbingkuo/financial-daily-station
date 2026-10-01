#!/bin/bash
# 啟動股票機器人儀表板
# 用法：./start_web.sh
# 訪問：http://localhost:5001

cd "$(dirname "$0")"
echo "🚀 啟動儀表板... http://localhost:5001"
open "http://localhost:5001"
/opt/anaconda3/bin/python3 web/app.py
