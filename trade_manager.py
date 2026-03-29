"""
trade_manager.py — Trade lifecycle manager.
Monitors open trades, fires TP/SL alerts, manages trailing stops.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from signals import Signal, SignalTracker
from config import SIGNAL_EXPIRY_MINS, ENTRY_EXTENSION_BUFFER

log = logging.getLogger(__name__)


class TradeManager:
    def __init__(
        self,
        tracker:   SignalTracker,
        send_fn:   Callable,       # async fn(text) → bool
        risk_fn:   Callable,       # records pnl
        journal_fn: Callable,      # records trade result
        atr_fn:    Callable = None, # fn() → float
    ):
        self.tracker   = tracker
        self.send      = send_fn
        self.record_pnl = risk_fn
        self.journal   = journal_fn
        self.get_atr   = atr_fn
        self._update_interval = 15 * 60   # 15 min live updates
        self._last_update: dict = {}

    async def on_price_update(self, price: float):
        """Called on every significant price move. Check all open trades."""
        actives = await self.tracker.get_all_active()
        for sig in actives:
            await self._check_signal(sig, price)

    async def _check_signal(self, sig: Signal, price: float):
        try:
            if sig.status == "PENDING":
                await self._check_entry(sig, price)
            elif sig.status in ("LIVE", "TP1", "TP2"):
                await self._check_levels(sig, price)
                await self._maybe_send_update(sig, price)
        except Exception as e:
            log.error(f"Trade check error {sig.signal_id}: {e}", exc_info=True)

    # ── Entry check ───────────────────────────────────────────────────────────
    async def _check_entry(self, sig: Signal, price: float):
        if sig.is_expired:
            await self._on_expired(sig, price)
            return

        # Extension: within 10 pts but not touched — extend once
        dist = abs(price - sig.entry)
        if dist <= ENTRY_EXTENSION_BUFFER and not sig.extended:
            new_expiry = sig.expiry_at + timedelta(minutes=15)
            await self.tracker.update(sig.signal_id, extended=True, expiry_at=new_expiry)
            await self.send(
                f"⏱️ SIGNAL EXTENDED — #{sig.signal_id}\n"
                f"Price is within {dist:.0f} pts of entry {sig.entry:.2f}\n"
                f"Extended by 15 mins. New expiry: {new_expiry.strftime('%H:%M UTC')}"
            )

        # Entry hit
        if sig.direction == "BUY" and price <= sig.entry:
            await self._on_entry_hit(sig, price)
        elif sig.direction == "SELL" and price >= sig.entry:
            await self._on_entry_hit(sig, price)

    async def _on_entry_hit(self, sig: Signal, price: float):
        await self.tracker.update(sig.signal_id,
                                   status="LIVE",
                                   entry_price_actual=price)
        await self.send(
            f"✅ ENTRY LIVE — #{sig.signal_id}\n"
            f"Entry filled at:  {price:.2f}\n"
            f"Stop Loss:        {sig.sl:.2f}  | -${sig.risk_usd:.2f}\n"
            f"TP1 target:       {sig.tp1:.2f}  | +{sig.tp1_pts:.0f} pts\n"
            f"TP2 target:       {sig.tp2:.2f}  | +{sig.tp2_pts:.0f} pts\n"
            f"TP3 target:       {sig.tp3:.2f}  | +{sig.tp3_pts:.0f} pts\n"
            f"Status: MONITORING 🟡"
        )
        log.info(f"Signal {sig.signal_id} entry hit at {price:.2f}")

    # ── Level checks ─────────────────────────────────────────────────────────
    async def _check_levels(self, sig: Signal, price: float):
        # SL check first
        sl = sig.sl_current or sig.sl
        if sig.direction == "BUY" and price <= sl:
            if sig.tp1_hit:
                await self._on_trail_stop(sig, price)
            else:
                await self._on_sl_hit(sig, price)
            return
        elif sig.direction == "SELL" and price >= sl:
            if sig.tp1_hit:
                await self._on_trail_stop(sig, price)
            else:
                await self._on_sl_hit(sig, price)
            return

        # TP checks
        if not sig.tp1_hit:
            tp1_hit = (sig.direction == "BUY" and price >= sig.tp1) or \
                      (sig.direction == "SELL" and price <= sig.tp1)
            if tp1_hit:
                await self._on_tp1(sig, price)

        elif not sig.tp2_hit:
            tp2_hit = (sig.direction == "BUY" and price >= sig.tp2) or \
                      (sig.direction == "SELL" and price <= sig.tp2)
            if tp2_hit:
                await self._on_tp2(sig, price)
            else:
                # Update trailing SL after TP1 — move to BE after 0.5 ATR
                await self._update_be_stop(sig, price)

        else:
            # After TP2 — trail at 1 ATR
            await self._update_trail_stop(sig, price)
            tp3_hit = (sig.direction == "BUY" and price >= sig.tp3) or \
                      (sig.direction == "SELL" and price <= sig.tp3)
            if tp3_hit:
                await self._on_tp3(sig, price)

    async def _update_be_stop(self, sig: Signal, price: float):
        """Move SL to breakeven after price travels 0.5 ATR past entry."""
        atr = 250.0
        if self.get_atr:
            try:
                atr = self.get_atr() or 250.0
            except Exception:
                pass
        be_trigger = sig.entry + (0.5 * atr * (1 if sig.direction == "BUY" else -1))
        entry = sig.entry_price_actual or sig.entry
        sl = sig.sl_current or sig.sl

        if sig.direction == "BUY" and price > be_trigger and sl < entry:
            await self.tracker.update(sig.signal_id, sl_current=entry)
            await self.send(
                f"🔒 SL UPDATED — #{sig.signal_id}\n"
                f"Old SL: {sl:.2f}\n"
                f"New SL: {entry:.2f} (BREAKEVEN)\n"
                f"Trade is now RISK-FREE ✅"
            )
        elif sig.direction == "SELL" and price < be_trigger and sl > entry:
            await self.tracker.update(sig.signal_id, sl_current=entry)
            await self.send(
                f"🔒 SL UPDATED — #{sig.signal_id}\n"
                f"Old SL: {sl:.2f}\n"
                f"New SL: {entry:.2f} (BREAKEVEN)\n"
                f"Trade is now RISK-FREE ✅"
            )

    async def _update_trail_stop(self, sig: Signal, price: float):
        """Trail SL at 1 ATR behind current price after TP2."""
        atr = 250.0
        if self.get_atr:
            try:
                atr = self.get_atr() or 250.0
            except Exception:
                pass

        sl = sig.sl_current or sig.sl
        if sig.direction == "BUY":
            new_sl = price - atr
            if new_sl > sl + 10:
                await self.tracker.update(sig.signal_id, sl_current=new_sl)
        else:
            new_sl = price + atr
            if new_sl < sl - 10:
                await self.tracker.update(sig.signal_id, sl_current=new_sl)

    # ── TP/SL events ─────────────────────────────────────────────────────────
    async def _on_tp1(self, sig: Signal, price: float):
        pnl_pts = sig.tp1_pts
        pnl_usd = round(sig.lot * pnl_pts * 1.0 * 0.35, 2)   # 35% of lot
        await self.tracker.update(sig.signal_id, tp1_hit=True, status="TP1")
        await self.send(
            f"🎯 TP1 HIT — #{sig.signal_id}\n"
            f"TP1 reached at: {price:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Close 35% of position NOW\n"
            f"Profit (35%): +{pnl_pts:.0f} pts | +${pnl_usd:.2f} | +1.0R\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ SL moves to BE after +0.5 ATR past entry\n"
            f"Holding 65% for TP2/TP3\n"
            f"Trade becomes RISK-FREE ✅"
        )

    async def _on_tp2(self, sig: Signal, price: float):
        pnl_pts = sig.tp2_pts
        pnl_usd = round(sig.lot * pnl_pts * 1.0 * 0.35, 2)
        await self.tracker.update(sig.signal_id, tp2_hit=True, status="TP2")
        await self.send(
            f"🎯🎯 TP2 HIT — #{sig.signal_id}\n"
            f"TP2 reached at: {price:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Close another 35% NOW\n"
            f"Profit (35%): +{pnl_pts:.0f} pts | +${pnl_usd:.2f} | +{sig.rr_tp2:.1f}R\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 Trailing SL activated @ 1 ATR\n"
            f"Running last 30% to TP3"
        )

    async def _on_tp3(self, sig: Signal, price: float):
        pnl_pts = sig.tp3_pts
        pnl_usd = round(sig.lot * pnl_pts * 1.0, 2)
        duration = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60
        await self.tracker.update(sig.signal_id, status="CLOSED",
                                   pnl_pts=pnl_pts, pnl_usd=pnl_usd)
        self.record_pnl(pnl_usd)
        self.journal(sig.signal_id, "win", pnl_pts, pnl_usd,
                     sig.rr_tp3, duration, "Full TP3 hit")
        await self.send(
            f"🏆 FULL WIN — #{sig.signal_id}\n"
            f"TP3 hit at: {price:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 TRADE SUMMARY\n"
            f"Entry: {sig.entry:.2f} → Exit: {price:.2f}\n"
            f"P&L:   +{pnl_pts:.0f} pts | +${pnl_usd:.2f} | +{sig.rr_tp3:.1f}R\n"
            f"Duration: {duration:.0f} mins\n"
            f"Strategy: {sig.strategy}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏅 TP1 ✅  TP2 ✅  TP3 ✅"
        )

    async def _on_trail_stop(self, sig: Signal, price: float):
        pnl_pts = abs(price - (sig.entry_price_actual or sig.entry))
        pnl_usd = round(sig.lot * pnl_pts * 1.0, 2)
        duration = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60
        r_achieved = pnl_pts / sig.sl_pts if sig.sl_pts > 0 else 0
        result = "partial"
        await self.tracker.update(sig.signal_id, status="CLOSED",
                                   pnl_pts=pnl_pts, pnl_usd=pnl_usd)
        self.record_pnl(pnl_usd)
        self.journal(sig.signal_id, result, pnl_pts, pnl_usd,
                     r_achieved, duration, "Trailing stop hit after TP2")
        await self.send(
            f"🏆 TRADE CLOSED — TRAIL HIT — #{sig.signal_id}\n"
            f"Trailing SL hit at: {price:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Entry: {sig.entry:.2f} → Exit: {price:.2f}\n"
            f"P&L:   +{pnl_pts:.0f} pts | +${pnl_usd:.2f} | +{r_achieved:.1f}R\n"
            f"TP1 ✅  TP2 ✅  TP3 (trail hit: +{r_achieved:.1f}R)\n"
            f"Duration: {duration:.0f} mins\n"
            f"Note: Trailing stop locked in profit. Good trade. 💪"
        )

    async def _on_sl_hit(self, sig: Signal, price: float):
        pnl_pts = -sig.sl_pts
        pnl_usd = -sig.risk_usd
        duration = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60
        await self.tracker.update(sig.signal_id, status="CLOSED",
                                   pnl_pts=pnl_pts, pnl_usd=pnl_usd)
        self.record_pnl(pnl_usd)
        self.journal(sig.signal_id, "loss", pnl_pts, pnl_usd,
                     -1.0, duration, "SL hit")
        await self.send(
            f"❌ STOP LOSS HIT — #{sig.signal_id}\n"
            f"Stopped at: {price:.2f}\n"
            f"Loss: -{sig.sl_pts:.0f} pts | -${sig.risk_usd:.2f} (-2%)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 ANALYSIS\n"
            f"Strategy:  {sig.strategy}\n"
            f"Score was: {sig.score:.0f}% — setup was valid\n"
            f"Duration:  {duration:.0f} mins\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Every loss is part of the process.\n"
            f"Assess, adjust, continue. 📈"
        )

    async def _on_expired(self, sig: Signal, price: float):
        await self.tracker.update(sig.signal_id, status="EXPIRED")
        self.journal(sig.signal_id, "expired", 0, 0, 0,
                     SIGNAL_EXPIRY_MINS, "Limit not triggered")
        await self.send(
            f"⏰ SIGNAL EXPIRED — #{sig.signal_id}\n"
            f"Entry {sig.entry:.2f} was not triggered.\n"
            f"Price at expiry: {price:.2f}\n"
            f"Signal cancelled. Scanning for next setup."
        )

    # ── Periodic live updates ─────────────────────────────────────────────────
    async def _maybe_send_update(self, sig: Signal, price: float):
        last = self._last_update.get(sig.signal_id, 0)
        now  = datetime.now(timezone.utc).timestamp()
        if now - last < self._update_interval:
            return
        self._last_update[sig.signal_id] = now

        entry = sig.entry_price_actual or sig.entry
        float_pnl_pts = (price - entry) if sig.direction == "BUY" else (entry - price)
        float_pnl_usd = round(sig.lot * float_pnl_pts, 2)
        r_so_far = float_pnl_pts / sig.sl_pts if sig.sl_pts > 0 else 0
        dist_tp = abs(sig.tp1 - price) if not sig.tp1_hit else abs(sig.tp2 - price)
        dist_sl = abs(price - (sig.sl_current or sig.sl))

        status_str = "ON TRACK" if float_pnl_pts > 0 else "PULLBACK — watch SL"

        open_mins = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60

        await self.send(
            f"📡 LIVE UPDATE — #{sig.signal_id}\n"
            f"Price now:        {price:.2f}\n"
            f"Floating P&L:     {float_pnl_pts:+.0f} pts | ${float_pnl_usd:+.2f} | {r_so_far:+.1f}R\n"
            f"Next TP dist:     {dist_tp:.0f} pts\n"
            f"Distance to SL:   {dist_sl:.0f} pts\n"
            f"Trade open:       {open_mins:.0f} mins\n"
            f"Status:           {status_str}"
        )
