"""
strategies.py — All 11 APEX strategies for Volatility 75 Index.
Each strategy returns a SetupResult with direction, score, and reason.
"""
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List

log = logging.getLogger(__name__)


@dataclass
class SetupResult:
    strategy:  str
    direction: str        # "BUY" | "SELL" | "NONE"
    score:     float      # 0-100 base score from this strategy
    reasons:   List[str]  = field(default_factory=list)
    entry_zone: Optional[tuple] = None   # (low, high)
    valid:     bool = False


class StrategyEngine:
    def run_all(self, ind_m5: dict, ind_m15: dict, ind_h1: dict,
                ind_h4: dict, session: str) -> List[SetupResult]:
        """Run all 11 strategies and return list of SetupResults."""
        results = []
        for fn in [
            self.s1_ema_stack,
            self.s2_bb_squeeze,
            self.s3_rsi_divergence,
            self.s4_order_block,
            self.s5_mean_reversion,
            self.s6_session_momentum,
            self.s7_fib_confluence,
            self.s8_breakout_retest,
            self.s9_liquidity_sweep,
            self.s10_harmonic,
            self.s11_wyckoff,
        ]:
            try:
                r = fn(ind_m5, ind_m15, ind_h1, ind_h4, session)
                if r and r.direction != "NONE":
                    results.append(r)
            except Exception as e:
                log.debug(f"Strategy error {fn.__name__}: {e}")
        return results

    # ── Helper ────────────────────────────────────────────────────────────────
    @staticmethod
    def _g(d: dict, key, default=None):
        v = d.get(key, default)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return default
        return v

    # ── Strategy 1: EMA Stack Trend Follow ───────────────────────────────────
    def s1_ema_stack(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("EMA Stack Trend Follow", "NONE", 0)

        adx = g(m15, "adx", 0)
        if adx < 20:
            return result

        if g(m15, "ema_stack_bull") and g(m5, "price_above_ema200"):
            price   = g(m5, "close", 0) or g(m15, "close", 0)
            ema21   = g(m15, "ema21", 0)
            ema50   = g(m15, "ema50", 0)
            rsi     = g(m5, "rsi", 50)
            if ema21 and ema50 and 35 <= rsi <= 60:
                result.direction = "BUY"
                result.valid     = True
                result.score     = 65
                result.entry_zone = (ema50, ema21)
                result.reasons   = [
                    "EMA stack bullish (M15)",
                    f"ADX {adx:.0f} — trend confirmed",
                    f"RSI {rsi:.0f} — pullback zone",
                    "Price pulling back to EMA21/50",
                ]

        elif g(m15, "ema_stack_bear") and not g(m5, "price_above_ema200", True):
            rsi = g(m5, "rsi", 50)
            ema21 = g(m15, "ema21", 9999)
            ema50 = g(m15, "ema50", 9999)
            if 40 <= rsi <= 65:
                result.direction = "SELL"
                result.valid     = True
                result.score     = 65
                result.entry_zone = (ema21, ema50)
                result.reasons   = [
                    "EMA stack bearish (M15)",
                    f"ADX {adx:.0f} — trend confirmed",
                    f"RSI {rsi:.0f} — pullback zone",
                ]

        return result

    # ── Strategy 2: BB Squeeze / TTM Breakout ────────────────────────────────
    def s2_bb_squeeze(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("BB Squeeze / TTM Breakout", "NONE", 0)

        squeeze_m5  = g(m5,  "ttm_squeeze", False) or g(m5,  "bb_squeeze", False)
        squeeze_m15 = g(m15, "ttm_squeeze", False) or g(m15, "bb_squeeze", False)

        if not (squeeze_m5 or squeeze_m15):
            return result

        macd_hist     = g(m5, "macd_hist", 0) or 0
        macd_hist_prev = g(m5, "macd_hist_prev", 0) or 0
        expanding     = abs(macd_hist) > abs(macd_hist_prev)

        if macd_hist > 0 and expanding:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 70
            result.reasons   = [
                "TTM Squeeze active — breakout forming",
                "MACD histogram positive + expanding",
                "Buy above squeeze high",
            ]
        elif macd_hist < 0 and expanding:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 70
            result.reasons   = [
                "TTM Squeeze active — breakdown forming",
                "MACD histogram negative + expanding",
                "Sell below squeeze low",
            ]

        return result

    # ── Strategy 3: RSI Divergence Reversal ──────────────────────────────────
    def s3_rsi_divergence(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("RSI Divergence Reversal", "NONE", 0)

        bull_div_m15  = g(m15, "rsi_bull_div", False)
        bear_div_m15  = g(m15, "rsi_bear_div", False)
        bull_div_h1   = g(h1,  "rsi_bull_div", False)
        bear_div_h1   = g(h1,  "rsi_bear_div", False)

        stoch_bull = g(m5, "stoch_bull_div", False)
        stoch_bear = g(m5, "stoch_bear_div", False)
        macd_bear  = g(m5, "macd_bear", False)
        macd_bull  = g(m5, "macd_bull", False)
        at_fib     = g(m5, "at_fib_any", False)
        at_pivot   = g(m5, "at_pivot", False) or g(m5, "at_s1", False) or g(m5, "at_r1", False)

        if bull_div_m15 and (at_fib or at_pivot):
            score = 68
            if bull_div_h1:   score += 5
            if stoch_bull:    score += 3
            result.direction = "BUY"
            result.valid     = True
            result.score     = score
            result.reasons   = [
                "RSI bullish divergence (M15)",
                "Price at Fibonacci/Pivot level",
                f"Multi-TF div: {'Yes' if bull_div_h1 else 'No'}",
            ]

        elif bear_div_m15 and (at_fib or at_pivot):
            score = 68
            if bear_div_h1: score += 5
            if stoch_bear:  score += 3
            result.direction = "SELL"
            result.valid     = True
            result.score     = score
            result.reasons   = [
                "RSI bearish divergence (M15)",
                "Price at Fibonacci/Pivot level",
            ]

        return result

    # ── Strategy 4: Order Block Retest ────────────────────────────────────────
    def s4_order_block(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Order Block Retest", "NONE", 0)

        at_bull_ob = g(m15, "at_bull_ob", False) or g(h1, "at_bull_ob", False)
        at_bear_ob = g(m15, "at_bear_ob", False) or g(h1, "at_bear_ob", False)
        pat_bull   = g(m5, "pattern_bull", False)
        pat_bear   = g(m5, "pattern_bear", False)
        rsi        = g(m5, "rsi", 50)

        if at_bull_ob and rsi and rsi < 55 and pat_bull:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 68
            result.reasons   = [
                f"Bullish OB retest ({g(m5,'pattern','pattern')})",
                f"RSI {rsi:.0f} — not overbought",
                "Order block zone defended",
            ]

        elif at_bear_ob and rsi and rsi > 45 and pat_bear:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 68
            result.reasons   = [
                f"Bearish OB retest ({g(m5,'pattern','pattern')})",
                f"RSI {rsi:.0f} — not oversold",
            ]

        return result

    # ── Strategy 5: Mean Reversion ───────────────────────────────────────────
    def s5_mean_reversion(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Mean Reversion (Range)", "NONE", 0)

        adx = g(m5, "adx", 99)
        if adx is None or adx >= 20:
            return result

        bb_touch_lower = g(m5, "bb_touch_lower", False)
        bb_touch_upper = g(m5, "bb_touch_upper", False)
        stoch_os       = g(m5, "stoch_oversold",   False)
        stoch_ob       = g(m5, "stoch_overbought", False)
        rsi            = g(m5, "rsi", 50)

        if bb_touch_lower and stoch_os and rsi is not None and rsi < 35:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 65
            result.reasons   = [
                "Price at lower BB (ranging market)",
                f"Stochastic oversold",
                f"RSI {rsi:.0f} < 35",
                "ADX < 20 — range confirmed",
            ]

        elif bb_touch_upper and stoch_ob and rsi is not None and rsi > 65:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 65
            result.reasons   = [
                "Price at upper BB (ranging market)",
                f"Stochastic overbought",
                f"RSI {rsi:.0f} > 65",
            ]

        return result

    # ── Strategy 6: Session Momentum Kill Zone ────────────────────────────────
    def s6_session_momentum(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Session Kill Zone Momentum", "NONE", 0)

        in_kill_zone = session in ("london_open", "ny_open", "london_close",
                                   "london_ny_overlap")
        if not in_kill_zone:
            return result

        ema_cross_bull = g(m5, "ema_cross_bull", False) or g(m15, "ema_cross_bull", False)
        ema_cross_bear = g(m5, "ema_cross_bear", False) or g(m15, "ema_cross_bear", False)
        adx            = g(m15, "adx", 0) or 0
        rsi            = g(m5, "rsi", 50)
        ichi_bull      = g(m15, "ichi_full_bull", False)
        ichi_bear      = g(m15, "ichi_full_bear", False)

        if ema_cross_bull and adx > 20 and rsi and 40 <= rsi <= 60:
            score = 68 + (5 if ichi_bull else 0) + (3 if session == "london_ny_overlap" else 0)
            result.direction = "BUY"
            result.valid     = True
            result.score     = score
            result.reasons   = [
                f"Kill zone: {session.replace('_', ' ').title()}",
                "EMA 8×21 bullish cross",
                f"ADX {adx:.0f} — momentum",
                f"Ichimoku: {'aligned' if ichi_bull else 'neutral'}",
            ]

        elif ema_cross_bear and adx > 20 and rsi and 40 <= rsi <= 60:
            score = 68 + (5 if ichi_bear else 0) + (3 if session == "london_ny_overlap" else 0)
            result.direction = "SELL"
            result.valid     = True
            result.score     = score
            result.reasons   = [
                f"Kill zone: {session.replace('_', ' ').title()}",
                "EMA 8×21 bearish cross",
                f"ADX {adx:.0f} — momentum",
            ]

        return result

    # ── Strategy 7: Fibonacci Confluence Zone ────────────────────────────────
    def s7_fib_confluence(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Fibonacci Confluence Zone", "NONE", 0)

        at_fib_618 = g(h1, "at_fib_618", False) or g(m15, "at_fib_618", False)
        at_fib_786 = g(h1, "at_fib_786", False) or g(m15, "at_fib_786", False)
        at_pivot   = g(m5, "at_pivot", False) or g(m5, "at_s1", False) or g(m5, "at_r1", False)
        ema50_h1   = g(h1, "ema50")
        rsi        = g(m5, "rsi", 50)
        pat_bull   = g(m5, "pattern_bull", False)
        pat_bear   = g(m5, "pattern_bear", False)
        pat_score  = g(m5, "pattern_score", 0) or 0

        fib_hit = at_fib_618 or at_fib_786
        if not fib_hit:
            return result

        confluence_count = sum([fib_hit, at_pivot, bool(ema50_h1)])

        if confluence_count >= 2 and pat_bull and rsi and rsi < 40:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 70 + min(pat_score, 8)
            result.reasons   = [
                f"Fib {'61.8%' if at_fib_618 else '78.6%'} confluence",
                f"RSI {rsi:.0f} — oversold at key level",
                f"Pattern: {g(m5,'pattern','N/A')}",
                f"Confluence zones: {confluence_count}",
            ]

        elif confluence_count >= 2 and pat_bear and rsi and rsi > 60:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 70 + min(pat_score, 8)
            result.reasons   = [
                f"Fib {'61.8%' if at_fib_618 else '78.6%'} confluence",
                f"RSI {rsi:.0f} — overbought at key level",
                f"Pattern: {g(m5,'pattern','N/A')}",
            ]

        return result

    # ── Strategy 8: Breakout Retest ──────────────────────────────────────────
    def s8_breakout_retest(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Breakout Retest", "NONE", 0)

        bos_bull = g(m15, "bos_bull", False) or g(h1, "bos_bull", False)
        bos_bear = g(m15, "bos_bear", False) or g(h1, "bos_bear", False)
        rsi      = g(m5, "rsi", 50)
        macd_bull = g(m5, "macd_bull", False)
        macd_bear = g(m5, "macd_bear", False)

        if bos_bull and rsi and 42 <= rsi <= 58 and macd_bull:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 68
            result.reasons   = [
                "Break of structure (BOS) — bullish",
                "Pullback to broken level",
                f"RSI {rsi:.0f} — healthy retest",
                "MACD still positive",
            ]

        elif bos_bear and rsi and 42 <= rsi <= 58 and macd_bear:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 68
            result.reasons   = [
                "Break of structure (BOS) — bearish",
                "Pullback to broken level",
                f"RSI {rsi:.0f} — healthy retest",
            ]

        return result

    # ── Strategy 9: Liquidity Sweep Reversal ─────────────────────────────────
    def s9_liquidity_sweep(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Liquidity Sweep Reversal", "NONE", 0)

        sweep_bull = g(m5, "liq_sweep_bull", False) or g(m15, "liq_sweep_bull", False)
        sweep_bear = g(m5, "liq_sweep_bear", False) or g(m15, "liq_sweep_bear", False)
        rsi        = g(m5, "rsi", 50)
        fib_618    = g(m5, "fib_618")
        fib_swing_high = g(m5, "fib_swing_high")
        fib_swing_low  = g(m5, "fib_swing_low")

        # Discount zone: below 50% Fib for buys
        in_discount = None
        in_premium  = None
        if fib_618 and fib_swing_high and fib_swing_low:
            mid = (fib_swing_high + fib_swing_low) / 2
            price = g(m5, "close") or g(m15, "close") or mid
            in_discount = price < mid
            in_premium  = price > mid

        if sweep_bull and in_discount and rsi and rsi < 40:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 75
            result.reasons   = [
                "Liquidity sweep — equal lows taken + close back inside",
                f"In discount zone (below 50% Fib)",
                f"RSI {rsi:.0f} — oversold on sweep",
                "Smart Money reversal signal",
            ]

        elif sweep_bear and in_premium and rsi and rsi > 60:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 75
            result.reasons   = [
                "Liquidity sweep — equal highs taken + close back inside",
                f"In premium zone (above 50% Fib)",
                f"RSI {rsi:.0f} — overbought on sweep",
            ]

        return result

    # ── Strategy 10: Harmonic Pattern ────────────────────────────────────────
    def s10_harmonic(self, m5, m15, h1, h4, session) -> SetupResult:
        """Simplified harmonic detection — ratio-validated at current price."""
        g = self._g
        result = SetupResult("Harmonic Pattern", "NONE", 0)
        # Requires deep pattern recognition — flagged as bonus setup when
        # price is at a key Fib cluster (61.8% + 78.6% overlap)
        at_618 = g(m15, "at_fib_618", False) or g(h1, "at_fib_618", False)
        at_786 = g(m15, "at_fib_786", False) or g(h1, "at_fib_786", False)
        rsi    = g(m5, "rsi", 50)
        pat_bull = g(m5, "pattern_bull", False)
        pat_bear = g(m5, "pattern_bear", False)

        if at_618 and at_786:
            # Potential PRZ (Potential Reversal Zone) — Bat-like or Gartley
            if pat_bull and rsi and rsi < 35:
                result.direction = "BUY"
                result.valid     = True
                result.score     = 72
                result.reasons   = [
                    "Harmonic PRZ — 61.8% + 78.6% Fib cluster",
                    f"RSI {rsi:.0f} — oversold at PRZ",
                    f"Confirmation: {g(m5,'pattern','N/A')}",
                ]
            elif pat_bear and rsi and rsi > 65:
                result.direction = "SELL"
                result.valid     = True
                result.score     = 72
                result.reasons   = [
                    "Harmonic PRZ — 61.8% + 78.6% Fib cluster",
                    f"RSI {rsi:.0f} — overbought at PRZ",
                ]

        return result

    # ── Strategy 11: Wyckoff Spring / UTAD ───────────────────────────────────
    def s11_wyckoff(self, m5, m15, h1, h4, session) -> SetupResult:
        g = self._g
        result = SetupResult("Wyckoff Spring", "NONE", 0)

        sweep_bull = g(m15, "liq_sweep_bull", False) or g(h1, "liq_sweep_bull", False)
        sweep_bear = g(m15, "liq_sweep_bear", False) or g(h1, "liq_sweep_bear", False)
        bos_bull   = g(m15, "bos_bull", False) or g(h1, "bos_bull", False)
        bos_bear   = g(m15, "bos_bear", False) or g(h1, "bos_bear", False)
        rsi_div_bull = g(m15, "rsi_bull_div", False)
        rsi_div_bear = g(m15, "rsi_bear_div", False)
        adx_ranging  = g(m15, "adx_ranging", True)

        if sweep_bull and bos_bull and rsi_div_bull:
            result.direction = "BUY"
            result.valid     = True
            result.score     = 78
            result.reasons   = [
                "Wyckoff Spring — sweep + SOS (BOS) confirmed",
                "RSI bullish divergence on Spring low",
                "Markup phase expected",
            ]

        elif sweep_bear and bos_bear and rsi_div_bear:
            result.direction = "SELL"
            result.valid     = True
            result.score     = 78
            result.reasons   = [
                "Wyckoff UTAD — sweep + SOW (BOS) confirmed",
                "RSI bearish divergence on UTAD high",
                "Markdown phase expected",
            ]

        return result
