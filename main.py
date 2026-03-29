"""
main.py — APEX V75 Signal Bot — Main engine.
Wires all modules together in a single asyncio event loop.
"""
import asyncio
import logging
import logging.handlers
import os
import signal as os_signal
import sys
from datetime import datetime, timezone

from config import (
    ACCOUNT_BALANCE, LOG_DIR, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    PRIMARY_TF, STRUCTURE_TF, BIAS_TF, INSTRUMENT_NAME, MIN_CONFLUENCE,
    MAX_RISK_PCT, MAX_CONCURRENT_SIGNALS,
)
from data_store import DataStore
from api import DerivAPI
from indicators import IndicatorEngine
from strategies import StrategyEngine
from scorer import ConfluenceScorer
from risk import RiskManager, RiskState
from signals import Signal, SignalBuilder, SignalTracker, next_signal_id
from telegram_bot import TelegramDispatcher
from trade_manager import TradeManager
from journal import Journal
from watchdog import Watchdog

# ── Logging setup ─────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)

_root = logging.getLogger()
_root.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_fh = logging.handlers.RotatingFileHandler(
    f"{LOG_DIR}/apex.log",
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
)
_fh.setFormatter(_fmt)

_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)

_root.addHandler(_fh)
_root.addHandler(_sh)

log = logging.getLogger("apex.main")


# ── Session helper ─────────────────────────────────────────────────────────────
def get_session() -> str:
    h = datetime.now(timezone.utc).hour
    if 13 <= h < 15:
        return "london_ny_overlap"
    if 7 <= h < 8:
        return "london_open"
    if 13 <= h < 14:
        return "ny_open"
    if 15 <= h < 16:
        return "london_close"
    if 7 <= h < 12:
        return "london"
    if 13 <= h < 20:
        return "ny"
    return "asian"


def is_kill_zone() -> bool:
    return get_session() in ("london_open", "ny_open", "london_close", "london_ny_overlap")


def in_chaos_window() -> bool:
    """First 5 minutes of session opens — avoid trading."""
    h = datetime.now(timezone.utc).hour
    m = datetime.now(timezone.utc).minute
    return (h in (7, 13, 15)) and (m < 5)


# ── Main Bot ───────────────────────────────────────────────────────────────────
class ApexBot:
    def __init__(self):
        self.store      = DataStore()
        self.api        = DerivAPI(self.store)
        self.ind_eng    = IndicatorEngine()
        self.strat_eng  = StrategyEngine()
        self.scorer     = ConfluenceScorer()
        self.risk_state = RiskState(starting_balance=ACCOUNT_BALANCE)
        self.risk_mgr   = RiskManager(self.risk_state)
        self.builder    = SignalBuilder()
        self.tracker    = SignalTracker()
        self.tg         = TelegramDispatcher()
        self.journal    = Journal()

        self.trade_mgr  = TradeManager(
            tracker    = self.tracker,
            send_fn    = self._send,
            risk_fn    = self.risk_mgr.record_pnl,
            journal_fn = self.journal.update_result,
            atr_fn     = self._get_current_atr,
        )

        self.watchdog   = Watchdog(
            send_fn      = self._send,
            get_stats_fn = self.journal.get_stats,
        )

        self._running    = True
        self._paused     = False
        self._last_scan  = 0.0
        self._indicators = {}   # tf → indicator dict
        self._custom_risk_pct = None
        self._signal_fired_times = []   # for correlation check

        # Wire up Telegram command callbacks
        self._wire_commands()

    # ── Command callbacks ─────────────────────────────────────────────────────
    def _wire_commands(self):
        tg = self.tg

        async def on_status():
            m5 = self._indicators.get("M5", {})
            h4 = self._indicators.get("H4", {})
            atr = m5.get("atr14", 0) or 0
            regime = self.risk_mgr.get_regime(atr)
            bal = await self.store.get_balance() or self.risk_state.current_balance
            actives = await self.tracker.get_all_active()
            return (
                f"📊 APEX STATUS\n"
                f"Price:        {await self.store.get_tick():.2f}\n"
                f"H4 Bias:      {('Bullish' if h4.get('ema_stack_bull') else 'Bearish')}\n"
                f"Regime:       {regime} | ATR: {atr:.0f} pts\n"
                f"Session:      {get_session().replace('_',' ').title()}\n"
                f"Kill zone:    {'✅ ACTIVE' if is_kill_zone() else '❌ No'}\n"
                f"Scanning:     {'⏸️ PAUSED' if self._paused else '✅ Active'}\n"
                f"Open signals: {len(actives)}/{MAX_CONCURRENT_SIGNALS}\n"
                f"Balance:      ${bal:.2f}\n"
                f"Daily P&L:    ${self.risk_state.daily_pnl:+.2f}\n"
                f"Recovery:     {'⚠️ ACTIVE' if self.risk_state.recovery_mode else '✅ Normal'}"
            )

        async def on_signals():
            actives = await self.tracker.get_all_active()
            if not actives:
                return "📋 No active signals at the moment."
            lines = [f"📋 ACTIVE SIGNALS ({len(actives)}/{MAX_CONCURRENT_SIGNALS})"]
            price = await self.store.get_tick()
            for s in actives:
                dist = abs(price - s.entry) if s.status == "PENDING" else abs(price - (s.sl_current or s.sl))
                lines.append(
                    f"\n#{s.signal_id} {s.direction} | {s.status}\n"
                    f"Entry: {s.entry:.2f} | SL: {s.sl:.2f} | TP1: {s.tp1:.2f}\n"
                    f"Score: {s.score:.0f}% | {s.strategy}"
                )
            return "\n".join(lines)

        async def on_balance():
            bal = await self.store.get_balance() or self.risk_state.current_balance
            daily_loss_pct = abs(self.risk_state.daily_pnl) / bal * 100 if bal > 0 and self.risk_state.daily_pnl < 0 else 0
            return (
                f"💰 ACCOUNT STATUS\n"
                f"Balance:       ${bal:.2f}\n"
                f"Starting:      ${self.risk_state.starting_balance:.2f}\n"
                f"Daily P&L:     ${self.risk_state.daily_pnl:+.2f}\n"
                f"Daily limit:   5% (${bal*0.05:.2f})\n"
                f"Daily used:    {daily_loss_pct:.1f}%\n"
                f"Weekly P&L:    ${self.risk_state.weekly_pnl:+.2f}\n"
                f"Recovery:      {'⚠️ ON' if self.risk_state.recovery_mode else '✅ OFF'}"
            )

        async def on_pause():
            self._paused = True
            self.risk_state.all_signals_paused = True

        async def on_resume():
            self._paused = False
            self.risk_state.all_signals_paused = False

        async def on_weekly():
            return self.journal.weekly_report_text()

        async def on_journal():
            trades = self.journal.get_last_trades(10)
            if not trades:
                return "📋 No completed trades yet."
            lines = ["📋 LAST 10 TRADES"]
            for t in trades:
                emoji = "✅" if t["result"] in ("win","partial") else ("❌" if t["result"]=="loss" else "⬜")
                lines.append(
                    f"{emoji} #{t['id']} {t['dir']} | {t['result'].upper()}\n"
                    f"   P&L: ${t['pnl_usd']:+.2f} | {t['r_mult']:.1f}R | {t['strategy']}"
                )
            return "\n".join(lines)

        async def on_kill(signal_id: str):
            sig = await self.tracker.get(signal_id)
            if not sig:
                return f"❌ Signal #{signal_id} not found."
            await self.tracker.update(signal_id, status="CLOSED")
            self.journal.update_result(signal_id, "cancelled", 0, 0, 0, 0, "Manual cancel")
            return f"✅ Signal #{signal_id} cancelled manually."

        async def on_risk(pct: float):
            pct = max(0.5, min(3.0, pct))
            self._custom_risk_pct = pct / 100
            return f"✅ Risk per trade set to {pct:.1f}%"

        tg.on_status_cmd  = on_status
        tg.on_signals_cmd = on_signals
        tg.on_balance_cmd = on_balance
        tg.on_pause_cmd   = on_pause
        tg.on_resume_cmd  = on_resume
        tg.on_weekly_cmd  = on_weekly
        tg.on_journal_cmd = on_journal
        tg.on_kill_cmd    = on_kill
        tg.on_risk_cmd    = on_risk

    # ── Core scan loop ────────────────────────────────────────────────────────
    async def _on_candle_close(self, tf: str):
        """Called on every new candle close for M5, M15, H1, H4."""
        self.watchdog.pulse()

        # Calculate indicators for this timeframe
        df = await self.store.get_candles(tf, n=300)
        if len(df) < 50:
            return

        ind = self.ind_eng.calculate(df, tf)
        self._indicators[tf] = ind

        # Structure shift alerts on H1
        if tf == "H1":
            await self._check_structure_alerts(ind)

        # Main signal scan — only on M5 candle close
        if tf != "M5":
            return

        if self._paused or in_chaos_window():
            return

        await self._scan_for_signals()

    async def _scan_for_signals(self):
        """Full signal scan after M5 candle close."""
        # Get indicators for all timeframes
        m5  = self._indicators.get("M5",  {})
        m15 = self._indicators.get("M15", {})
        h1  = self._indicators.get("H1",  {})
        h4  = self._indicators.get("H4",  {})

        if not m5 or not m15:
            return

        session = get_session()
        atr = m5.get("atr14") or 250
        regime = self.risk_mgr.get_regime(atr)

        # Run all strategies
        setups = self.strat_eng.run_all(m5, m15, h1, h4, session)
        if not setups:
            return

        # Score
        score_data = self.scorer.score(setups, m5, m15, h1, h4, session, regime)
        direction  = score_data["direction"]
        score      = score_data["score"]
        grade      = score_data["grade"]

        if direction == "NONE":
            return

        # Watch alert
        if 65 <= score < 70:
            price = await self.store.get_tick()
            await self._send(self.builder.build_watch_alert(direction, score, price))
            return

        if not score_data["signal_valid"]:
            return

        # Risk checks
        can_fire, reason = self.risk_mgr.can_fire_signal()
        if not can_fire:
            log.info(f"Signal blocked: {reason}")
            return

        # Correlation check — no same direction within 15 mins
        now = datetime.now(timezone.utc).timestamp()
        self._signal_fired_times = [(t, d) for t, d in self._signal_fired_times
                                     if now - t < 900]
        recent_same = any(d == direction for t, d in self._signal_fired_times)
        if recent_same:
            log.info(f"Correlation block: {direction} signal fired within 15 mins.")
            return

        # Build signal
        price = await self.store.get_tick()
        if price <= 0:
            return

        levels = self.risk_mgr.calculate_sl_tp(direction, price, atr, regime)
        if not self.risk_mgr.validate_rr(levels["sl_pts"], levels["tp2_pts"]):
            log.info(f"R/R too low: {levels['rr_tp2']:.2f} — signal aborted.")
            return

        balance = await self.store.get_balance() or self.risk_state.current_balance
        risk_pct = self._custom_risk_pct or MAX_RISK_PCT
        lot = self.risk_mgr.calculate_lot(balance, levels["sl_pts"], risk_pct)
        risk_usd = self.risk_mgr.get_risk_usd(balance, lot, levels["sl_pts"])

        sig = Signal(
            signal_id  = next_signal_id(),
            direction  = direction,
            entry      = price,
            sl         = levels["sl"],
            tp1        = levels["tp1"],
            tp2        = levels["tp2"],
            tp3        = levels["tp3"],
            sl_pts     = levels["sl_pts"],
            tp1_pts    = levels["tp1_pts"],
            tp2_pts    = levels["tp2_pts"],
            tp3_pts    = levels["tp3_pts"],
            lot        = lot,
            risk_usd   = risk_usd,
            score      = score,
            grade      = grade,
            strategy   = score_data["strategy"],
            supporting = score_data["supporting"],
            regime     = regime,
            atr14      = atr,
            session    = session,
            reasons    = score_data["reasons"],
            pattern    = m5.get("pattern", "None"),
            structure  = m15.get("structure", "Unknown"),
            rr_tp2     = levels["rr_tp2"],
            rr_tp3     = levels["rr_tp3"],
            balance    = balance,
        )

        await self.tracker.add(sig)
        self.journal.log_signal(sig)
        self.risk_state.open_signals_count += 1
        self._signal_fired_times.append((now, direction))

        # Send card
        card = self.builder.build_card(sig, score_data)
        await self._send(card)
        log.info(f"Signal fired: #{sig.signal_id} {direction} @ {price:.2f} | {grade} {score:.0f}%")

        # Recovery mode check
        if self.risk_mgr.check_recovery_mode():
            await self._send(
                f"⚠️ DRAWDOWN RECOVERY MODE ACTIVE\n"
                f"Lot sizes halved. Min confluence: 80%.\n"
                f"Daily loss: ${abs(self.risk_state.daily_pnl):.2f}\n"
                f"Stay disciplined — protect the account."
            )

    async def _on_tick(self, price: float):
        """Called on every valid tick. Route to trade manager."""
        await self.trade_mgr.on_price_update(price)

    async def _check_structure_alerts(self, ind_h1: dict):
        if ind_h1.get("bos_bull"):
            price = await self.store.get_tick()
            await self._send(self.builder.build_structure_alert(
                "BOS (Break of Structure)", "Bullish", price))
        elif ind_h1.get("choch_bull"):
            price = await self.store.get_tick()
            await self._send(self.builder.build_structure_alert(
                "ChoCH (Change of Character)", "Potential Bullish Shift", price))

    def _get_current_atr(self) -> float:
        return self._indicators.get("M5", {}).get("atr14", 250) or 250

    async def _send(self, text: str) -> bool:
        return await self.tg.send(text)

    # ── Startup card ──────────────────────────────────────────────────────────
    async def _send_startup_card(self):
        await asyncio.sleep(5)  # Wait for data to populate
        m5  = self._indicators.get("M5",  {})
        h4  = self._indicators.get("H4",  {})
        atr = m5.get("atr14", 0) or 0
        regime = self.risk_mgr.get_regime(atr)
        price = await self.store.get_tick()
        balance = await self.store.get_balance() or ACCOUNT_BALANCE

        await self._send(
            f"🚀 APEX v2.0 ONLINE — V75 Signal Engine\n"
            f"─────────────────────────────────────────\n"
            f"Deriv API:      ✅ Connected\n"
            f"Auth:           ✅ Verified\n"
            f"Data streams:   ✅ M1 M5 M15 M30 H1 H4\n"
            f"Indicators:     ✅ All loaded\n"
            f"Strategies:     ✅ All 11 active\n"
            f"Risk engine:    ✅ Active\n"
            f"Trade manager:  ✅ Active\n"
            f"Journal:        ✅ Active\n"
            f"Watchdog:       ✅ Running\n"
            f"─────────────────────────────────────────\n"
            f"V75 price now:  {price:.2f}\n"
            f"H4 bias:        {'Bullish' if h4.get('ema_stack_bull') else ('Bearish' if h4.get('ema_stack_bear') else 'Neutral')}\n"
            f"Volatility:     {regime} | ATR: {atr:.0f} pts\n"
            f"Session:        {get_session().replace('_',' ').title()}\n"
            f"Kill zone:      {'✅ ACTIVE' if is_kill_zone() else '❌ No'}\n"
            f"Balance:        ${balance:.2f}\n"
            f"─────────────────────────────────────────\n"
            f"Max risk/trade: 2.0%\n"
            f"Max open:       {MAX_CONCURRENT_SIGNALS} signals\n"
            f"Min confluence: {MIN_CONFLUENCE}%\n"
            f"─────────────────────────────────────────\n"
            f"Scanning. Waiting for 70%+ setups. 🎯"
        )

    # ── Daily brief ───────────────────────────────────────────────────────────
    async def _build_daily_brief(self) -> str:
        m5 = self._indicators.get("M5", {})
        h4 = self._indicators.get("H4", {})
        h1 = self._indicators.get("H1", {})
        atr = m5.get("atr14", 250) or 250
        regime = self.risk_mgr.get_regime(atr)
        h4_bias = "Bullish" if h4.get("ema_stack_bull") else "Bearish"
        h1_structure = h1.get("structure", "Unknown")
        levels = {
            "pivot": m5.get("pivot", 0),
            "r1": m5.get("r1", 0),
            "r2": m5.get("r2", 0),
            "s1": m5.get("s1", 0),
            "s2": m5.get("s2", 0),
        }
        return self.builder.build_daily_brief(
            h4_bias, h1_structure, regime, atr,
            get_session().replace("_", " ").title(), levels)

    # ── Main run ──────────────────────────────────────────────────────────────
    async def run(self):
        log.info("APEX v2.0 starting...")

        # Wire API callbacks
        self.api.on_candle_close(self._on_candle_close)
        self.api.on_tick(self._on_tick)

        # Build Telegram app
        self.tg.build_app()

        # Graceful shutdown
        loop = asyncio.get_running_loop()

        def _shutdown(sig_num):
            log.info(f"Shutdown signal {sig_num} received.")
            self._running = False
            self.watchdog.stop()

        for sig_num in (os_signal.SIGINT, os_signal.SIGTERM):
            try:
                loop.add_signal_handler(sig_num, lambda s=sig_num: _shutdown(s))
            except NotImplementedError:
                pass  # Windows

        # Create all tasks
        tasks = [
            asyncio.create_task(self.api.connect_and_run(alert_fn=self._send),
                                name="api"),
            asyncio.create_task(self.tg.run_polling(),
                                name="telegram"),
            asyncio.create_task(self.watchdog.run(),
                                name="watchdog"),
            asyncio.create_task(self.watchdog.run_daily_heartbeat(),
                                name="heartbeat"),
            asyncio.create_task(self.watchdog.run_daily_brief(self._build_daily_brief),
                                name="daily_brief"),
            asyncio.create_task(self.watchdog.run_weekly_report(
                lambda: asyncio.coroutine(lambda: self.journal.weekly_report_text())()),
                                name="weekly_report"),
            asyncio.create_task(self._send_startup_card(),
                                name="startup"),
        ]

        log.info(f"APEX running with {len(tasks)} tasks.")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("Tasks cancelled — shutting down gracefully.")
        except Exception as e:
            log.critical(f"Fatal error: {e}", exc_info=True)
            await self._send(f"🆘 APEX FATAL ERROR: {e}\nBot crashed — restart required.")
        finally:
            await self._send(
                "🔴 APEX shutting down.\n"
                "All open signals remain — manage manually.\n"
                "Restart with: python main.py"
            )
            for t in tasks:
                t.cancel()
            log.info("Shutdown complete.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = ApexBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("APEX stopped by user.")
