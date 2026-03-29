"""
signals.py — Signal card formatter + active signal tracker.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

from config import SIGNAL_EXPIRY_MINS, SIGNAL_EXPIRY_EXT_MINS, INSTRUMENT_NAME

log = logging.getLogger(__name__)

# Grade emoji map
GRADE_EMOJI = {
    "APEX PRIME": "🔥",
    "S":    "⭐",
    "A":    "🟢",  # overridden per direction
    "B":    "👁️",
    "C":    "👁️",
    "D":    "📋",
    "F":    "📋",
}

_signal_counter = 0


def next_signal_id() -> str:
    global _signal_counter
    _signal_counter += 1
    return f"VT{_signal_counter:03d}"


@dataclass
class Signal:
    signal_id:   str
    direction:   str
    entry:       float
    sl:          float
    tp1:         float
    tp2:         float
    tp3:         float
    sl_pts:      float
    tp1_pts:     float
    tp2_pts:     float
    tp3_pts:     float
    lot:         float
    risk_usd:    float
    score:       float
    grade:       str
    strategy:    str
    supporting:  Optional[str]
    regime:      str
    atr14:       float
    session:     str
    reasons:     List[str]
    pattern:     str
    structure:   str
    rr_tp2:      float
    rr_tp3:      float
    balance:     float

    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expiry_at:   Optional[datetime] = None
    extended:    bool = False

    # Lifecycle state
    status:      str = "PENDING"   # PENDING | LIVE | TP1 | TP2 | CLOSED | EXPIRED | INVALID
    entry_price_actual: Optional[float] = None
    sl_current:  Optional[float] = None
    tp1_hit:     bool = False
    tp2_hit:     bool = False
    pnl_pts:     float = 0.0
    pnl_usd:     float = 0.0
    r_multiple:  float = 0.0

    def __post_init__(self):
        self.sl_current = self.sl
        if self.expiry_at is None:
            self.expiry_at = self.created_at + timedelta(minutes=SIGNAL_EXPIRY_MINS)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expiry_at and self.status == "PENDING"

    def minutes_to_expiry(self) -> float:
        delta = self.expiry_at - datetime.now(timezone.utc)
        return max(0, delta.total_seconds() / 60)


class SignalBuilder:
    def build_card(self, sig: Signal, score_data: dict) -> str:
        d = sig.direction
        grade = sig.grade

        # Header emoji
        if grade == "APEX PRIME":
            emoji = "🔥"
        elif grade == "S":
            emoji = "⭐🟢" if d == "BUY" else "⭐🔴"
        elif grade == "A":
            emoji = "🟢" if d == "BUY" else "🔴"
        else:
            emoji = "👁️"

        now  = datetime.now(timezone.utc)
        expiry_str = sig.expiry_at.strftime("%H:%M UTC")
        bal_risk = sig.risk_usd

        # Build reasons string
        reasons_str = "\n".join(f"  {r}" for r in sig.reasons[:12])

        # Strategy display
        strat_line = sig.strategy
        if sig.supporting:
            strat_line += f"\n  Supporting: {sig.supporting}"

        # Regime regime label
        regime_labels = {"LOW": "Low volatility", "MEDIUM": "Normal volatility",
                         "HIGH": "High volatility ⚠️"}

        card = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} {d} SIGNAL — {INSTRUMENT_NAME}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now.strftime('%d/%m/%Y')} | {now.strftime('%H:%M')} UTC | ID: #{sig.signal_id}\n"
            f"Signal Grade: {grade}\n\n"

            f"─── 📊 MARKET ANALYSIS ───\n"
            f"Session:     {sig.session.replace('_',' ').title()}\n"
            f"Regime:      {regime_labels.get(sig.regime,'Normal')} | ATR: {sig.atr14:.0f} pts\n"
            f"Structure:   {sig.structure}\n"
            f"Pattern:     {sig.pattern}\n\n"

            f"─── 🎯 ORDER DETAILS ───\n"
            f"Order Type:  LIMIT ORDER\n"
            f"Entry:       {sig.entry:.2f}\n"
            f"Stop Loss:   {sig.sl:.2f}  ({sig.sl_pts:.0f} pts | -${bal_risk:.2f} worst case)\n"
            f"TP1:         {sig.tp1:.2f}  (+{sig.tp1_pts:.0f} pts | 1:1.0)  ← Close 35%\n"
            f"TP2:         {sig.tp2:.2f}  (+{sig.tp2_pts:.0f} pts | 1:{sig.rr_tp2:.1f})  ← Close 35%\n"
            f"TP3:         {sig.tp3:.2f}  (+{sig.tp3_pts:.0f} pts | 1:{sig.rr_tp3:.1f})  ← Trail 30%\n"
            f"Expiry:      {expiry_str} (30 min)\n\n"

            f"─── 💰 RISK ───\n"
            f"Account risk: 2.0% | -${bal_risk:.2f} if SL hit\n"
            f"Lot size:     {sig.lot:.2f}\n"
            f"R/R at TP2:   1:{sig.rr_tp2:.1f}\n"
            f"R/R at TP3:   1:{sig.rr_tp3:.1f}\n\n"

            f"─── 📈 CONFLUENCE — {sig.score:.0f}% ({grade}) ───\n"
            f"{reasons_str}\n\n"

            f"─── ⚙️ MANAGEMENT ───\n"
            f"At TP1: Close 35% | Move SL → BE (+0.5 ATR)\n"
            f"At TP2: Close 35% | Trail SL @ 1 ATR\n"
            f"Remaining 30%: Trail to TP3\n\n"

            f"─── 📌 STRATEGY ───\n"
            f"  {strat_line}\n\n"

            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ APEX v2.0 | R_75 | #{sig.signal_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return card

    def build_watch_alert(self, direction: str, score: float, price: float) -> str:
        return (
            f"👁️ WATCH ALERT — {INSTRUMENT_NAME}\n"
            f"Direction:  {direction}\n"
            f"Confluence: {score:.0f}% — below minimum threshold\n"
            f"Zone:       {price:.2f}\n"
            f"Status:     NOT a signal. Monitoring only.\n"
            f"Will upgrade if confluence reaches 70%."
        )

    def build_structure_alert(self, bos_type: str, direction: str, price: float) -> str:
        return (
            f"🔔 STRUCTURE SHIFT — H1\n"
            f"{bos_type} detected\n"
            f"Direction: {direction}\n"
            f"Level:     {price:.2f}\n"
            f"Watching for first pullback entry setup."
        )

    def build_daily_brief(self, h4_bias: str, h1_structure: str, regime: str,
                           atr: float, session: str, levels: dict) -> str:
        now = datetime.now(timezone.utc)
        return (
            f"🌅 APEX DAILY BRIEF — {now.strftime('%d/%m/%Y')}\n"
            f"─────────────────────────────────\n"
            f"H4 Trend:     {h4_bias}\n"
            f"H1 Structure: {h1_structure}\n"
            f"Volatility:   {regime} | ATR: {atr:.0f} pts\n"
            f"Session:      {session}\n"
            f"─────────────────────────────────\n"
            f"Key levels:\n"
            f"  Resistance: {levels.get('r1',0):.2f} | {levels.get('r2',0):.2f}\n"
            f"  Support:    {levels.get('s1',0):.2f} | {levels.get('s2',0):.2f}\n"
            f"  Pivot:      {levels.get('pivot',0):.2f}\n"
            f"─────────────────────────────────\n"
            f"Stay patient. Only trade what APEX fires. 🎯"
        )


class SignalTracker:
    def __init__(self):
        self._signals: Dict[str, Signal] = {}
        self._lock = asyncio.Lock()

    async def add(self, sig: Signal):
        async with self._lock:
            self._signals[sig.signal_id] = sig

    async def get(self, signal_id: str) -> Optional[Signal]:
        async with self._lock:
            return self._signals.get(signal_id)

    async def get_all_active(self) -> List[Signal]:
        async with self._lock:
            return [s for s in self._signals.values()
                    if s.status in ("PENDING", "LIVE", "TP1", "TP2")]

    async def update(self, signal_id: str, **kwargs):
        async with self._lock:
            sig = self._signals.get(signal_id)
            if sig:
                for k, v in kwargs.items():
                    setattr(sig, k, v)

    async def remove(self, signal_id: str):
        async with self._lock:
            self._signals.pop(signal_id, None)

    async def count_open(self) -> int:
        async with self._lock:
            return sum(1 for s in self._signals.values()
                       if s.status in ("PENDING", "LIVE", "TP1", "TP2"))
