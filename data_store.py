"""
data_store.py — In-memory candle and tick store with asyncio locks.
Stores rolling 500-candle DataFrames per timeframe.
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional
import pandas as pd

from config import TIMEFRAMES, CANDLE_COUNT

log = logging.getLogger(__name__)


class DataStore:
    def __init__(self):
        self._candles: Dict[str, pd.DataFrame] = {tf: pd.DataFrame() for tf in TIMEFRAMES}
        self._locks:   Dict[str, asyncio.Lock]  = {tf: asyncio.Lock() for tf in TIMEFRAMES}
        self._tick_lock = asyncio.Lock()
        self._current_tick: float = 0.0
        self._tick_history: list  = []   # last 20 ticks for spike detection
        self._daily_open: float   = 0.0
        self._daily_high: float   = 0.0
        self._daily_low:  float   = 0.0
        self._account_balance: float = 0.0
        self._balance_lock = asyncio.Lock()

    # ── Tick ──────────────────────────────────────────────────────────────────
    async def update_tick(self, price: float) -> bool:
        """Returns True if tick is valid (not a spike), False if anomaly."""
        async with self._tick_lock:
            if self._tick_history:
                last = self._tick_history[-1]
                if abs(price - last) > 500:
                    log.warning(f"Spike detected: {last:.2f} → {price:.2f} ({abs(price-last):.0f} pts)")
                    return False
            self._current_tick = price
            self._tick_history.append(price)
            if len(self._tick_history) > 20:
                self._tick_history.pop(0)
            # Daily tracking
            if self._daily_open == 0:
                self._daily_open = price
            self._daily_high = max(self._daily_high, price) if self._daily_high else price
            self._daily_low  = min(self._daily_low,  price) if self._daily_low  else price
            return True

    async def get_tick(self) -> float:
        async with self._tick_lock:
            return self._current_tick

    async def get_daily(self) -> dict:
        async with self._tick_lock:
            return {
                "open": self._daily_open,
                "high": self._daily_high,
                "low":  self._daily_low,
                "current": self._current_tick,
            }

    async def reset_daily(self):
        async with self._tick_lock:
            self._daily_open = self._current_tick
            self._daily_high = self._current_tick
            self._daily_low  = self._current_tick

    # ── Candles ───────────────────────────────────────────────────────────────
    async def init_candles(self, tf: str, candles: list):
        """Load initial historical candles from API response."""
        async with self._locks[tf]:
            rows = []
            for c in candles:
                rows.append({
                    "epoch":  c["epoch"],
                    "open":   float(c["open"]),
                    "high":   float(c["high"]),
                    "low":    float(c["low"]),
                    "close":  float(c["close"]),
                    "volume": float(c.get("volume", 1)),
                })
            df = pd.DataFrame(rows)
            df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
            df.set_index("datetime", inplace=True)
            df.sort_index(inplace=True)
            self._candles[tf] = df.tail(CANDLE_COUNT).copy()
            log.info(f"Initialized {tf} candles: {len(self._candles[tf])} rows")

    async def update_candle(self, tf: str, ohlc: dict) -> bool:
        """Update or append a candle. Returns True if it's a NEW closed candle."""
        async with self._locks[tf]:
            epoch  = ohlc["epoch"]
            o, h, l, c = float(ohlc["open"]), float(ohlc["high"]), float(ohlc["low"]), float(ohlc["close"])
            dt = pd.to_datetime(epoch, unit="s", utc=True)
            df = self._candles[tf]

            # Anomaly: extreme wick check
            body  = abs(c - o)
            wick  = h - l
            if df.empty:
                atr_proxy = 300.0
            else:
                atr_proxy = float(df["high"].tail(14).max() - df["low"].tail(14).min()) / 14 if len(df) >= 14 else 300.0

            if wick > 4 * max(atr_proxy, 50) and body == 0:
                log.warning(f"Anomalous candle filtered on {tf}: wick={wick:.1f}")
                return False

            is_new = dt not in df.index
            row = pd.DataFrame([{
                "epoch": epoch, "open": o, "high": h, "low": l, "close": c,
                "volume": float(ohlc.get("volume", 1)),
            }], index=[dt])

            if is_new:
                self._candles[tf] = pd.concat([df, row]).tail(CANDLE_COUNT)
            else:
                # Update in-progress candle
                for col in ["open","high","low","close","volume"]:
                    self._candles[tf].loc[dt, col] = row[col].values[0]

            return is_new

    async def get_candles(self, tf: str, n: int = 200) -> pd.DataFrame:
        async with self._locks[tf]:
            return self._candles[tf].tail(n).copy()

    async def has_enough_data(self, tf: str, minimum: int = 60) -> bool:
        async with self._locks[tf]:
            return len(self._candles[tf]) >= minimum

    # ── Balance ───────────────────────────────────────────────────────────────
    async def update_balance(self, balance: float):
        async with self._balance_lock:
            self._account_balance = balance

    async def get_balance(self) -> float:
        async with self._balance_lock:
            return self._account_balance
