"""
watchdog.py — Heartbeat, uptime monitor, and scheduled tasks.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

log = logging.getLogger(__name__)

_start_time = datetime.now(timezone.utc)


class Watchdog:
    def __init__(self, send_fn: Callable, get_stats_fn: Callable = None):
        self.send      = send_fn
        self.get_stats = get_stats_fn
        self._last_heartbeat = datetime.now(timezone.utc).timestamp()
        self._alive   = True

    def pulse(self):
        self._last_heartbeat = datetime.now(timezone.utc).timestamp()

    async def run(self):
        """Check heartbeat every 30s. Alert if stale for 90s."""
        while self._alive:
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc).timestamp()
            age = now - self._last_heartbeat
            if age > 90:
                log.error(f"Heartbeat stale for {age:.0f}s — system may be frozen!")
                try:
                    await self.send(
                        f"🔴 APEX WATCHDOG ALERT\n"
                        f"No heartbeat for {age:.0f}s.\n"
                        f"System may be frozen — please check server."
                    )
                except Exception as e:
                    log.error(f"Watchdog alert send failed: {e}")

    async def run_daily_heartbeat(self):
        """Send daily heartbeat at 00:00 UTC every day."""
        while self._alive:
            now = datetime.now(timezone.utc)
            # Calculate seconds to next midnight
            next_midnight = now.replace(hour=0, minute=0, second=5, microsecond=0)
            if next_midnight <= now:
                from datetime import timedelta
                next_midnight += timedelta(days=1)
            wait = (next_midnight - now).total_seconds()
            await asyncio.sleep(wait)
            await self._send_heartbeat()

    async def _send_heartbeat(self):
        uptime_hours = (datetime.now(timezone.utc) - _start_time).total_seconds() / 3600
        stats = {}
        if self.get_stats:
            try:
                stats = self.get_stats(days=1)
            except Exception:
                pass
        await self.send(
            f"💚 APEX HEARTBEAT — {datetime.now(timezone.utc).strftime('%d/%m/%Y')}\n"
            f"Uptime:    {uptime_hours:.1f} hrs\n"
            f"Yesterday: {stats.get('total',0)} signals | "
            f"{stats.get('wins',0)} wins | "
            f"${stats.get('net_pnl',0):+.2f}\n"
            f"All systems: ✅ Nominal\n"
            f"Scanning continues. 🔍"
        )
        log.info("Daily heartbeat sent.")

    async def run_daily_brief(self, brief_fn: Callable):
        """Send daily market brief at 07:00 UTC."""
        while self._alive:
            now = datetime.now(timezone.utc)
            target = now.replace(hour=7, minute=0, second=5, microsecond=0)
            if target <= now:
                from datetime import timedelta
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)
            try:
                brief = await brief_fn()
                await self.send(brief)
            except Exception as e:
                log.error(f"Daily brief error: {e}")

    async def run_weekly_report(self, report_fn: Callable):
        """Send weekly report every Sunday at 23:00 UTC."""
        while self._alive:
            now = datetime.now(timezone.utc)
            # Days until Sunday (weekday 6)
            days_ahead = (6 - now.weekday()) % 7
            from datetime import timedelta
            target = now.replace(hour=23, minute=0, second=0, microsecond=0)
            if days_ahead == 0 and now.hour >= 23:
                days_ahead = 7
            target += timedelta(days=days_ahead)
            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)
            try:
                report = await report_fn()
                await self.send(report)
            except Exception as e:
                log.error(f"Weekly report error: {e}")

    def stop(self):
        self._alive = False
