"""
risk.py — Risk management engine.
Position sizing, SL/TP calculation, daily/weekly limits.
"""
import logging
import math
from datetime import date, datetime, timezone
from typing import Optional

from config import (
    MAX_RISK_PCT, MAX_CONCURRENT_SIGNALS, MAX_DAILY_LOSS_PCT,
    MAX_WEEKLY_LOSS_PCT, MAX_BALANCE_DROP_PCT, DRAWDOWN_RECOVERY_PCT,
    MIN_LOT, MAX_LOT, POINT_VALUE, ATR_MULT, ATR_LOW_THRESHOLD,
    ATR_HIGH_THRESHOLD, ROUND_NUMBER_BUFFER, MIN_RR
)

log = logging.getLogger(__name__)


class RiskState:
    def __init__(self, starting_balance: float):
        self.starting_balance   = starting_balance
        self.current_balance    = starting_balance
        self.daily_pnl          = 0.0
        self.weekly_pnl         = 0.0
        self.daily_reset_date   = date.today()
        self.weekly_reset_date  = datetime.now(timezone.utc).isocalendar()[1]
        self.recovery_mode      = False
        self.all_signals_paused = False
        self.open_signals_count = 0
        self.compound_enabled   = False

    def reset_daily(self):
        self.daily_pnl       = 0.0
        self.recovery_mode   = False
        self.daily_reset_date = date.today()

    def reset_weekly(self):
        self.weekly_pnl = 0.0
        self.weekly_reset_date = datetime.now(timezone.utc).isocalendar()[1]


class RiskManager:
    def __init__(self, state: RiskState):
        self.state = state

    def get_regime(self, atr14: float) -> str:
        if atr14 < ATR_LOW_THRESHOLD:
            return "LOW"
        if atr14 > ATR_HIGH_THRESHOLD:
            return "HIGH"
        return "MEDIUM"

    def get_atr_mult(self, regime: str) -> dict:
        return ATR_MULT.get(regime, ATR_MULT["MEDIUM"])

    def calculate_lot(
        self,
        balance:    float,
        sl_points:  float,
        risk_pct:   Optional[float] = None,
    ) -> float:
        if risk_pct is None:
            risk_pct = MAX_RISK_PCT / 2 if self.state.recovery_mode else MAX_RISK_PCT
        if sl_points <= 0:
            return MIN_LOT
        risk_usd = balance * risk_pct
        lot = risk_usd / (sl_points * POINT_VALUE)
        lot = max(MIN_LOT, min(MAX_LOT, math.floor(lot * 100) / 100))
        # Verify
        verify = lot * sl_points * POINT_VALUE
        if verify > balance * (risk_pct + 0.005):
            lot = max(MIN_LOT, lot - 0.01)
        return round(lot, 2)

    def calculate_sl_tp(
        self,
        direction: str,
        entry:     float,
        atr14:     float,
        regime:    str,
    ) -> dict:
        mult = self.get_atr_mult(regime)
        sl_dist  = atr14 * mult["sl"]
        tp1_dist = atr14 * mult["tp1"]
        tp2_dist = atr14 * mult["tp2"]
        tp3_dist = atr14 * mult["tp3"]

        if direction == "BUY":
            sl  = entry - sl_dist
            tp1 = entry + tp1_dist
            tp2 = entry + tp2_dist
            tp3 = entry + tp3_dist
        else:
            sl  = entry + sl_dist
            tp1 = entry - tp1_dist
            tp2 = entry - tp2_dist
            tp3 = entry - tp3_dist

        # Round-number magnet avoidance
        sl  = self._adjust_round(sl,  direction, "sl")
        tp1 = self._adjust_round(tp1, direction, "tp")
        tp2 = self._adjust_round(tp2, direction, "tp")
        tp3 = self._adjust_round(tp3, direction, "tp")

        sl_pts  = abs(entry - sl)
        tp1_pts = abs(tp1 - entry)
        tp2_pts = abs(tp2 - entry)
        tp3_pts = abs(tp3 - entry)

        rr_tp2 = tp2_pts / sl_pts if sl_pts > 0 else 0
        rr_tp3 = tp3_pts / sl_pts if sl_pts > 0 else 0

        return {
            "sl":   round(sl,  2),
            "tp1":  round(tp1, 2),
            "tp2":  round(tp2, 2),
            "tp3":  round(tp3, 2),
            "sl_pts":  round(sl_pts,  1),
            "tp1_pts": round(tp1_pts, 1),
            "tp2_pts": round(tp2_pts, 1),
            "tp3_pts": round(tp3_pts, 1),
            "rr_tp2":  round(rr_tp2, 2),
            "rr_tp3":  round(rr_tp3, 2),
        }

    def _adjust_round(self, price: float, direction: str, tp_or_sl: str) -> float:
        """Move price away from round-number magnets."""
        nearest_k = round(price / 1000) * 1000
        nearest_500 = round(price / 500) * 500
        for mag in [nearest_k, nearest_500]:
            if abs(price - mag) < ROUND_NUMBER_BUFFER:
                if direction == "BUY":
                    if tp_or_sl == "tp":
                        price = mag + ROUND_NUMBER_BUFFER
                    else:
                        price = mag - ROUND_NUMBER_BUFFER
                else:
                    if tp_or_sl == "tp":
                        price = mag - ROUND_NUMBER_BUFFER
                    else:
                        price = mag + ROUND_NUMBER_BUFFER
        return price

    def can_fire_signal(self) -> tuple:
        """Returns (bool, reason_string)."""
        s = self.state

        if s.all_signals_paused:
            return False, "🔴 All signals paused by user command or hard stop."

        daily_loss_pct = abs(s.daily_pnl) / s.current_balance if s.current_balance > 0 and s.daily_pnl < 0 else 0
        if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
            s.all_signals_paused = True
            return False, f"🛑 Daily loss limit reached ({daily_loss_pct:.1%}). No more signals today."

        weekly_loss_pct = abs(s.weekly_pnl) / s.current_balance if s.current_balance > 0 and s.weekly_pnl < 0 else 0
        if weekly_loss_pct >= MAX_WEEKLY_LOSS_PCT:
            return False, f"🛑 Weekly loss limit reached ({weekly_loss_pct:.1%}). Pausing 48 hours."

        balance_drop = (s.starting_balance - s.current_balance) / s.starting_balance if s.starting_balance > 0 else 0
        if balance_drop >= MAX_BALANCE_DROP_PCT:
            s.all_signals_paused = True
            return False, f"🚨 20% balance drawdown reached. All signals stopped."

        if s.open_signals_count >= MAX_CONCURRENT_SIGNALS:
            return False, f"⏸️ Max {MAX_CONCURRENT_SIGNALS} concurrent signals open. Queuing."

        return True, ""

    def check_recovery_mode(self) -> bool:
        s = self.state
        daily_loss_pct = abs(s.daily_pnl) / s.current_balance if s.current_balance > 0 and s.daily_pnl < 0 else 0
        if daily_loss_pct >= DRAWDOWN_RECOVERY_PCT and not s.recovery_mode:
            s.recovery_mode = True
            log.warning(f"Recovery mode activated at {daily_loss_pct:.1%} daily loss.")
            return True
        return False

    def validate_rr(self, sl_pts: float, tp2_pts: float) -> bool:
        return (tp2_pts / sl_pts) >= MIN_RR if sl_pts > 0 else False

    def get_risk_usd(self, balance: float, lot: float, sl_pts: float) -> float:
        return round(lot * sl_pts * POINT_VALUE, 2)

    def record_pnl(self, pnl: float):
        self.state.daily_pnl  += pnl
        self.state.weekly_pnl += pnl
        self.state.current_balance += pnl
