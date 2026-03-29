"""
scorer.py — Confluence scoring engine.
Aggregates strategy results and indicator checks into a final score + grade.
"""
import logging
from typing import List, Dict
from strategies import SetupResult

log = logging.getLogger(__name__)


GRADE_THRESHOLDS = {
    "APEX PRIME": 90,
    "S": 80,
    "A": 70,
    "B": 65,
    "C": 60,
    "D": 50,
    "F": 0,
}


class ConfluenceScorer:

    def score(
        self,
        setups:  List[SetupResult],
        m5:      dict,
        m15:     dict,
        h1:      dict,
        h4:      dict,
        session: str,
        regime:  str,
    ) -> Dict:
        """
        Returns dict with:
          direction, score, grade, reasons, signal_valid, strategy_name
        """
        # Determine dominant direction from strategy votes
        buy_score  = sum(s.score for s in setups if s.direction == "BUY")
        sell_score = sum(s.score for s in setups if s.direction == "SELL")
        buy_count  = sum(1 for s in setups if s.direction == "BUY")
        sell_count = sum(1 for s in setups if s.direction == "SELL")

        if buy_score == 0 and sell_score == 0:
            return self._empty()

        direction = "BUY" if buy_score >= sell_score else "SELL"
        # Require at least 2 strategies aligned for a signal
        aligned_count = buy_count if direction == "BUY" else sell_count
        if aligned_count < 2:
            # Single strategy — only pass as watch
            direction_setups = [s for s in setups if s.direction == direction]
            if not direction_setups or direction_setups[0].score < 75:
                return self._empty()

        # ── Base score from indicators ────────────────────────────────────────
        score = 0.0
        reasons = []
        g = lambda d, k, default=None: d.get(k, default)

        def add(pts, reason, condition):
            nonlocal score
            if condition:
                score += pts
                reasons.append(f"✅ {reason}")
            else:
                reasons.append(f"⬜ {reason}")

        is_bull = direction == "BUY"

        # Trend (+57 max)
        add(10, "EMA stack aligned (M15)",
            g(m15,"ema_stack_bull") if is_bull else g(m15,"ema_stack_bear"))

        add(8, "MACD crossover confirmed",
            g(m5,"macd_cross_bull") if is_bull else g(m5,"macd_cross_bear"))

        rsi = g(m5,"rsi",50) or 50
        add(8, f"RSI {rsi:.0f} — confirming direction",
            (rsi < 40) if is_bull else (rsi > 60))

        adx = g(m15,"adx",0) or 0
        add(8, f"ADX {adx:.0f} — trend confirmed",
            adx > 25)

        add(7, "Market structure aligned",
            g(m15,"structure_bull") if is_bull else g(m15,"structure_bear"))

        add(7, "Ichimoku aligned",
            g(m15,"ichi_full_bull") if is_bull else g(m15,"ichi_full_bear"))

        add(7, "Order block / supply-demand zone",
            g(m5,"at_bull_ob") if is_bull else g(m5,"at_bear_ob"))

        # Volatility (+30 max)
        bb_ok = (g(m5,"bb_touch_lower") if is_bull else g(m5,"bb_touch_upper")) or \
                g(m5,"ttm_squeeze") or g(m5,"bb_squeeze")
        add(8, "Bollinger Band setup active", bb_ok)

        add(8, "Fibonacci key level",
            g(m5,"at_fib_any"))

        add(7, "Stochastic crossover",
            g(m5,"stoch_cross_bull") if is_bull else g(m5,"stoch_cross_bear"))

        pat_score = g(m5,"pattern_score",0) or 0
        add(min(pat_score,7), f"Pattern: {g(m5,'pattern','None')}",
            pat_score >= 5)

        # Key levels (+24 max)
        add(7, "Supply / demand zone",
            g(m5,"at_bull_ob") or g(m5,"fvg_bull"))

        add(6, "Fair Value Gap (FVG)",
            g(m5,"fvg_bull") if is_bull else g(m5,"fvg_bear"))

        add(6, "Pivot level confluence",
            g(m5,"at_pivot") or g(m5,"at_s1") or g(m5,"at_r1"))

        add(5, "VWAP + Volume Profile alignment",
            g(m5,"price_above_vwap") if is_bull else (not g(m5,"price_above_vwap",True)))

        # Session (+16 max)
        session_pts = {
            "london_ny_overlap": 6, "london_open": 6, "ny_open": 6,
            "london_close": 4, "london": 3, "ny": 3, "asian": 2,
        }.get(session, 2)
        add(session_pts, f"Session: {session.replace('_',' ').title()}", True)

        add(5, "Timeframe confluence (M5+M15 agree)",
            len([s for s in setups if s.direction == direction]) >= 2)

        atr = g(m5, "atr14", 250) or 250
        add(5, "No anomalous V75 spike", atr < 600)

        # ── Bonus points ─────────────────────────────────────────────────────
        bonus = []
        div_stack = (g(m5,"rsi_bull_div") and g(m5,"stoch_bull_div") and g(m5,"macd_bull")) if is_bull else \
                    (g(m5,"rsi_bear_div") and g(m5,"stoch_bear_div") and g(m5,"macd_bear"))
        if div_stack:
            score += 5
            bonus.append("✅ Multi-indicator divergence stack +5")

        liq_sweep = g(m5,"liq_sweep_bull") if is_bull else g(m5,"liq_sweep_bear")
        if liq_sweep:
            score += 7
            bonus.append("✅ Liquidity sweep confirmed +7")

        at_618_h1 = g(h1,"at_fib_618",False)
        at_618_h4 = g(h4,"at_fib_618",False)
        if at_618_h1 and at_618_h4:
            score += 5
            bonus.append("✅ H1+H4 Fib confluence +5")

        wyckoff_setup = any(s.strategy == "Wyckoff Spring" for s in setups if s.direction == direction)
        if wyckoff_setup:
            score += 7
            bonus.append("✅ Wyckoff Spring/UTAD confirmed +7")

        harmonic = any(s.strategy == "Harmonic Pattern" for s in setups if s.direction == direction)
        if harmonic:
            score += 10
            bonus.append("✅ Harmonic PRZ confirmed +10")

        reasons.extend(bonus)

        # ── Regime threshold adjustment ───────────────────────────────────────
        min_score = 70
        if regime == "HIGH":    min_score = 75
        elif regime == "LOW":   min_score = 68

        grade       = self._grade(score)
        signal_valid = score >= min_score

        # Strategy names
        primary_strategies = [s.strategy for s in setups if s.direction == direction]
        strategy_name = primary_strategies[0] if primary_strategies else "Multiple"
        supporting    = primary_strategies[1] if len(primary_strategies) > 1 else None

        return {
            "direction":   direction,
            "score":       round(score, 1),
            "grade":       grade,
            "reasons":     reasons,
            "signal_valid": signal_valid,
            "strategy":    strategy_name,
            "supporting":  supporting,
            "aligned_strategies": aligned_count,
            "regime_threshold": min_score,
        }

    def _grade(self, score: float) -> str:
        for grade, threshold in sorted(GRADE_THRESHOLDS.items(),
                                       key=lambda x: -x[1]):
            if score >= threshold:
                return grade
        return "F"

    def _empty(self) -> dict:
        return {
            "direction": "NONE", "score": 0, "grade": "F",
            "reasons": [], "signal_valid": False,
            "strategy": "", "supporting": None,
            "aligned_strategies": 0, "regime_threshold": 70,
        }
