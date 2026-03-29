import os
from dotenv import load_dotenv

load_dotenv()

# ── Deriv API ──────────────────────────────────────────────────────────────────
DERIV_APP_ID    = os.getenv("DERIV_APP_ID", "1089")
DERIV_API_KEY   = os.getenv("DERIV_API_KEY", "")
DERIV_WS_URL    = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Instrument ────────────────────────────────────────────────────────────────
INSTRUMENT      = "R_75"
INSTRUMENT_NAME = "Volatility 75 Index"
POINT_VALUE     = 1.0   # $1 per point per 1.0 lot

# ── Risk defaults ─────────────────────────────────────────────────────────────
ACCOUNT_BALANCE          = float(os.getenv("ACCOUNT_BALANCE", "1000"))
MAX_RISK_PCT             = 0.02
MAX_CONCURRENT_SIGNALS   = 3
MIN_CONFLUENCE           = 70
MAX_DAILY_LOSS_PCT       = 0.05
MAX_WEEKLY_LOSS_PCT      = 0.10
MAX_BALANCE_DROP_PCT     = 0.20
DRAWDOWN_RECOVERY_PCT    = 0.03
MIN_LOT                  = 0.01
MAX_LOT                  = 10.0

# ── Signal expiry ─────────────────────────────────────────────────────────────
SIGNAL_EXPIRY_MINS      = 30
SIGNAL_EXPIRY_EXT_MINS  = 15
ENTRY_EXTENSION_BUFFER  = 10   # points within entry to trigger extension

# ── Round-number buffer ───────────────────────────────────────────────────────
ROUND_NUMBER_BUFFER = 25

# ── Timeframes ────────────────────────────────────────────────────────────────
TIMEFRAMES = {
    "M1":  60,
    "M5":  300,
    "M15": 900,
    "M30": 1800,
    "H1":  3600,
    "H4":  14400,
}
CANDLE_COUNT = 500
PRIMARY_TF   = "M5"
STRUCTURE_TF = "M15"
BIAS_TF      = "H4"

# ── ATR regime thresholds (M5 pts) ────────────────────────────────────────────
ATR_LOW_THRESHOLD  = 150
ATR_HIGH_THRESHOLD = 400

# ── ATR multipliers per regime ────────────────────────────────────────────────
ATR_MULT = {
    "LOW":    {"sl": 0.8, "tp1": 0.8, "tp2": 1.8, "tp3": 3.0},
    "MEDIUM": {"sl": 1.0, "tp1": 1.0, "tp2": 2.0, "tp3": 3.5},
    "HIGH":   {"sl": 1.5, "tp1": 1.5, "tp2": 2.5, "tp3": 4.0},
}

# ── Session hours (UTC) ───────────────────────────────────────────────────────
SESSIONS = {
    "asian":          (0,  7),
    "london":         (7,  12),
    "ny":             (13, 20),
    "london_ny_overlap": (13, 15),
}

# ── Minimum R:R ───────────────────────────────────────────────────────────────
MIN_RR = 1.5

# ── Watchdog ──────────────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL   = 60   # seconds between heartbeats
WATCHDOG_TIMEOUT     = 90   # seconds before considered frozen

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR      = "logs"
LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
LOG_BACKUP_COUNT = 10

# ── Journal ───────────────────────────────────────────────────────────────────
DB_PATH  = "apex_journal.db"
CSV_PATH = "apex_trades.csv"
