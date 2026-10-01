# 功率元件情報雷達設定

KEYWORDS_TW = [
    "功率元件 漲價",
    "MOSFET 調漲",
    "IGBT 漲價",
    "SiC 碳化矽 報價",
    "GaN 氮化鎵 漲",
    "功率半導體 供貨",
    "強茂 漲價",
    "富鼎 報價",
    "台半 調漲",
]

KEYWORDS_EN = [
    "power MOSFET price increase",
    "IGBT price hike",
    "SiC MOSFET supply",
    "power semiconductor ASP",
    "GaN price increase",
    "Infineon price adjustment",
    "onsemi price hike",
    "STMicro power discrete",
]

# 台灣功率元件廠商（用於判斷文章相關性）
TW_POWER_COMPANIES = [
    "強茂", "富鼎", "台半", "朋程", "杰力", "偉詮", "茂達",
    "尼克森", "大中", "致新", "安國",
]

# 國際功率元件廠商
INTL_POWER_COMPANIES = [
    "Infineon", "onsemi", "ON Semiconductor", "STMicro", "STMicroelectronics",
    "Vishay", "ROHM", "Renesas", "Toshiba", "Mitsubishi Electric",
    "Wolfspeed", "Coherent",
]

# 供應鏈漲價邏輯鏈
SUPPLY_CHAIN = {
    "SiC 基板": ["SiC MOSFET", "碳化矽"],
    "SiC MOSFET": ["電動車", "充電樁", "太陽能變流器", "儲能"],
    "IGBT": ["工業變頻器", "電動車", "UPS", "軌道交通"],
    "MOSFET": ["電源管理", "伺服器電源", "充電器"],
    "GaN": ["快充", "資料中心電源", "5G基站"],
}

# 新聞抓取設定
MAX_ARTICLES_PER_KEYWORD = 10
ARTICLE_LOOKBACK_DAYS = 3       # 只處理 3 天內的新聞
MIN_CONFIDENCE = 0.45           # 低於此值不推播
