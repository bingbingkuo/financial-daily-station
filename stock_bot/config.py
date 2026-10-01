import os
import json
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
FRED_API_KEY       = os.getenv("FRED_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
# 多位收件人：讀取 GMAIL_RECIPIENTS（逗號分隔），未設定則預設寄給自己
_raw_recipients    = os.getenv("GMAIL_RECIPIENTS", "")
GMAIL_RECIPIENTS: list[str] = (
    [r.strip() for r in _raw_recipients.split(",") if r.strip()]
    if _raw_recipients else [GMAIL_ADDRESS]
)
# 均線強弱分析 email 專用收件人：未設定則沿用 GMAIL_RECIPIENTS
_raw_ma_recipients = os.getenv("MA_ANALYSIS_RECIPIENTS", "")
MA_ANALYSIS_RECIPIENTS: list[str] = (
    [r.strip() for r in _raw_ma_recipients.split(",") if r.strip()]
    if _raw_ma_recipients else GMAIL_RECIPIENTS
)

# ── 預設持倉（portfolio.json 不存在時使用）────────────────
_DEFAULT_TW = {
    "2330": "台積電", "1802": "台玻",   "2449": "京元電子",
    "2337": "旺宏",   "2455": "全新",   "3105": "穩懋",
    "6257": "矽格",   "6213": "聯茂",
}
_DEFAULT_US = {
    "NVDA": "輝達",   "PLTR": "Palantir", "MRVL": "邁威爾",
    "TSLA": "特斯拉", "PL":   "Planet Labs", "AVGO": "博通",
}

# ── 從 portfolio.json 動態載入（由 Web UI 維護）──────────
_PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")
if os.path.exists(_PORTFOLIO_FILE):
    with open(_PORTFOLIO_FILE, "r", encoding="utf-8") as _f:
        _p     = json.load(_f)
    TW_STOCKS = _p.get("tw", _DEFAULT_TW)
    US_STOCKS = _p.get("us", _DEFAULT_US)
else:
    TW_STOCKS = _DEFAULT_TW
    US_STOCKS = _DEFAULT_US

# ── 美股族群 ETF ──────────────────────────────────────────
US_SECTOR_ETFS = {
    "XLK":  "科技",
    "XLE":  "能源",
    "XLF":  "金融",
    "XLV":  "醫療保健",
    "XLI":  "工業",
    "XLY":  "非必需消費",
    "XLP":  "必需消費",
    "XLB":  "原物料",
    "XLRE": "房地產",
    "XLU":  "公用事業",
    "XLC":  "通訊服務",
}

# ── 美股族群主題 ───────────────────────────────────────────
US_SECTOR_THEMES = {
    "XLK":  "AI / 半導體 / 雲端運算",
    "XLE":  "原油 / 天然氣 / 能源轉型",
    "XLF":  "升息週期 / 銀行獲利",
    "XLV":  "防禦性資產 / 生技新藥",
    "XLI":  "製造業回流 / 基礎建設",
    "XLY":  "消費信心 / 電商 / 汽車",
    "XLP":  "防禦性消費 / 抗通膨",
    "XLB":  "原物料 / 綠能轉型需求",
    "XLRE": "REITs / 降息預期",
    "XLU":  "公用事業 / AI 耗電需求",
    "XLC":  "串流媒體 / 廣告 / 社群",
}

# ── 美股族群領頭股票（每族群 5 支，取當日漲最多前 3）──────
US_SECTOR_LEADERS = {
    "XLK":  [("AAPL","蘋果"),  ("MSFT","微軟"),     ("NVDA","輝達"),     ("AVGO","博通"),    ("AMD","超微")],
    "XLE":  [("XOM","艾克森"), ("CVX","雪佛龍"),     ("COP","康菲石油"),  ("EOG","EOG資源"),  ("SLB","斯倫貝謝")],
    "XLF":  [("JPM","摩根大通"),("BAC","美銀"),      ("WFC","富國銀行"),  ("GS","高盛"),      ("MS","摩根士丹利")],
    "XLV":  [("LLY","禮來"),   ("UNH","聯合健康"),  ("JNJ","嬌生"),      ("ABBV","艾伯維"),  ("MRK","默克")],
    "XLI":  [("GE","奇異"),    ("RTX","雷神"),       ("CAT","卡特彼勒"),  ("HON","霍尼韋爾"), ("UPS","聯合包裹")],
    "XLY":  [("AMZN","亞馬遜"),("TSLA","特斯拉"),   ("HD","家得寶"),     ("MCD","麥當勞"),   ("BKNG","Booking")],
    "XLP":  [("PG","寶僑"),    ("KO","可口可樂"),    ("PEP","百事"),      ("COST","好市多"),  ("WMT","沃爾瑪")],
    "XLB":  [("LIN","林德"),   ("APD","空氣化工"),   ("SHW","宣偉"),      ("FCX","自由港"),   ("NEM","紐蒙特")],
    "XLRE": [("PLD","普洛斯"), ("AMT","美國鐵塔"),   ("EQIX","Equinix"),  ("CCI","皇冠城堡"), ("PSA","公共儲存")],
    "XLU":  [("NEE","NextEra"),("DUK","杜克能源"),   ("SO","南方電力"),   ("AEP","美國電力"), ("EXC","Exelon")],
    "XLC":  [("META","Meta"),  ("GOOGL","Alphabet"), ("NFLX","Netflix"),  ("DIS","迪士尼"),   ("CMCSA","康卡斯特")],
}

# ── 台股族群代表股票 ──────────────────────────────────────
TW_SECTOR_STOCKS = {
    "半導體":    ["2330", "2303", "2454", "3711", "2379"],
    "IC設計":    ["2454", "3034", "6415", "2344", "3661"],
    "被動元件":  ["2317", "2327", "2328", "2379", "2330"],
    "電子代工":  ["2317", "2354", "2382", "2356", "3231"],
    "網通":      ["4904", "2345", "3515", "6153", "3561"],
    "電動車/儲能":["1590", "6213", "6669", "3443", "3008"],
    "面板":      ["2408", "3481", "2409", "3673"],
    "銀行金融":  ["2882", "2881", "2891", "2886", "2884"],
    "航運":      ["2603", "2609", "2615", "2618", "2610"],
    "傳產/玻璃": ["1802", "1301", "1303", "1326", "1102"],
}

# ── Scout：台股產業 → 全球代理指標 ───────────────────────
SCOUT_SECTOR_PROXIES: dict[str, list[tuple[str, str]]] = {
    "半導體":      [("NVDA", "輝達"),     ("SOXX", "費城半導體ETF")],
    "IC設計":      [("NVDA", "輝達"),     ("AMD",  "超微")],
    "ABF載板/PCB": [("4062.T", "Ibiden"), ("NVDA", "輝達")],
    "電子代工":    [("AAPL", "蘋果"),     ("HON",  "霍尼韋爾")],
    "被動元件":    [("AVGO", "博通"),     ("TXN",  "德州儀器")],
    "網通":        [("CSCO", "思科"),     ("ANET", "Arista")],
    "電動車/儲能": [("TSLA", "特斯拉"),   ("RIVN", "Rivian")],
    "面板":        [("AAPL", "蘋果"),     ("MSFT", "微軟")],
    "銀行金融":    [("JPM",  "摩根大通"), ("XLF",  "金融ETF")],
    "航運":        [("ZIM",  "以星航運"), ("FDX",  "聯邦快遞")],
    "傳產/玻璃":   [("SPY",  "S&P500"),   ("XLB",  "原物料ETF")],
    "_default":    [("QQQ",  "那斯達克ETF"), ("SPY", "S&P500")],
}

# ── 台股股票名稱對照表 ────────────────────────────────────
TW_STOCK_NAMES = {
    "2330": "台積電",  "2303": "聯電",    "2454": "聯發科",  "3711": "日月光",
    "2379": "瑞昱",    "3034": "聯詠",    "6415": "矽力-KY", "2344": "華邦電",
    "3661": "世芯-KY", "2317": "鴻海",    "2327": "國巨",    "2328": "廣宇",
    "2354": "鴻準",    "2382": "廣達",    "2356": "英業達",  "3231": "緯創",
    "4904": "遠傳",    "2345": "智邦",    "3515": "奇鋐",    "6153": "嘉聯益",
    "3561": "昇銳",    "1590": "亞德客",  "6213": "聯茂",    "6669": "緯穎",
    "3443": "創意",    "3008": "大立光",  "2408": "南亞科",  "3481": "群創",
    "2409": "友達",    "3673": "TPK宸鴻", "2882": "國泰金",  "2881": "富邦金",
    "2891": "中信金",  "2886": "兆豐金",  "2884": "玉山金",  "2603": "長榮",
    "2609": "陽明",    "2615": "萬海",    "2618": "長榮航",  "2610": "華航",
    "1802": "台玻",    "1301": "台塑",    "1303": "南亞",    "1326": "台化",
    "1102": "亞泥",    "2449": "京元電子","2337": "旺宏",    "2455": "全新",
    "3105": "穩懋",    "6257": "矽格",
}
