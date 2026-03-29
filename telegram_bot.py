"""
telegram_bot.py — Telegram dispatcher + command handlers.
Uses python-telegram-bot v20+ async API.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Callable

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError, RetryAfter, NetworkError

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)


class TelegramDispatcher:
    def __init__(self):
        self.bot       = Bot(token=TELEGRAM_BOT_TOKEN)
        self.chat_id   = TELEGRAM_CHAT_ID
        self._app      = None
        self._send_lock = asyncio.Lock()
        # Callbacks set by main engine
        self.on_status_cmd:  Optional[Callable] = None
        self.on_signals_cmd: Optional[Callable] = None
        self.on_balance_cmd: Optional[Callable] = None
        self.on_pause_cmd:   Optional[Callable] = None
        self.on_resume_cmd:  Optional[Callable] = None
        self.on_weekly_cmd:  Optional[Callable] = None
        self.on_journal_cmd: Optional[Callable] = None
        self.on_kill_cmd:    Optional[Callable] = None
        self.on_risk_cmd:    Optional[Callable] = None
        self.on_backtest_cmd:Optional[Callable] = None

    async def send(self, text: str, parse_mode: str = None) -> bool:
        """Send a message with retry logic."""
        async with self._send_lock:
            for attempt in range(3):
                try:
                    await self.bot.send_message(
                        chat_id   = self.chat_id,
                        text      = text,
                        parse_mode= parse_mode,
                    )
                    return True
                except RetryAfter as e:
                    log.warning(f"Telegram rate limit — waiting {e.retry_after}s")
                    await asyncio.sleep(e.retry_after + 1)
                except NetworkError as e:
                    log.warning(f"Telegram network error (attempt {attempt+1}): {e}")
                    await asyncio.sleep(10)
                except TelegramError as e:
                    log.error(f"Telegram error: {e}")
                    await asyncio.sleep(5)
            log.error("Failed to send Telegram message after 3 attempts.")
            return False

    # ── Application setup ────────────────────────────────────────────────────
    def build_app(self) -> Application:
        self._app = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .build()
        )
        self._register_handlers()
        return self._app

    def _register_handlers(self):
        app = self._app

        async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            await upd.message.reply_text("🚀 APEX v2.0 is online. Use /status to check.")

        async def cmd_status(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_status_cmd:
                msg = await self.on_status_cmd()
                await upd.message.reply_text(msg)

        async def cmd_signals(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_signals_cmd:
                msg = await self.on_signals_cmd()
                await upd.message.reply_text(msg)

        async def cmd_balance(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_balance_cmd:
                msg = await self.on_balance_cmd()
                await upd.message.reply_text(msg)

        async def cmd_pause(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_pause_cmd:
                await self.on_pause_cmd()
            await upd.message.reply_text("⏸️ APEX paused. No new signals will fire.")

        async def cmd_resume(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_resume_cmd:
                await self.on_resume_cmd()
            await upd.message.reply_text("▶️ APEX resumed. Scanning for signals.")

        async def cmd_weekly(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_weekly_cmd:
                msg = await self.on_weekly_cmd()
                await upd.message.reply_text(msg)

        async def cmd_journal(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if self.on_journal_cmd:
                msg = await self.on_journal_cmd()
                await upd.message.reply_text(msg)

        async def cmd_kill(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            args = ctx.args
            if not args:
                await upd.message.reply_text("Usage: /kill VT001")
                return
            signal_id = args[0].replace("#","").upper()
            if self.on_kill_cmd:
                msg = await self.on_kill_cmd(signal_id)
                await upd.message.reply_text(msg)

        async def cmd_risk(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            args = ctx.args
            if not args:
                await upd.message.reply_text("Usage: /risk 1.5")
                return
            try:
                pct = float(args[0])
                if self.on_risk_cmd:
                    msg = await self.on_risk_cmd(pct)
                    await upd.message.reply_text(msg)
            except ValueError:
                await upd.message.reply_text("Invalid percentage. E.g. /risk 1.5")

        async def cmd_help(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            help_text = (
                "📋 APEX COMMANDS\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "/status    — Market state + active signals\n"
                "/signals   — List open signal cards\n"
                "/balance   — Account balance + daily P&L\n"
                "/pause     — Pause signal generation\n"
                "/resume    — Resume signal generation\n"
                "/weekly    — Weekly performance report\n"
                "/journal   — Last 10 trade results\n"
                "/kill ID   — Cancel signal (e.g. /kill VT001)\n"
                "/risk %    — Change risk % (e.g. /risk 1.5)\n"
                "/help      — This menu"
            )
            await upd.message.reply_text(help_text)

        handlers = [
            ("start",   cmd_start),
            ("status",  cmd_status),
            ("signals", cmd_signals),
            ("balance", cmd_balance),
            ("pause",   cmd_pause),
            ("resume",  cmd_resume),
            ("weekly",  cmd_weekly),
            ("journal", cmd_journal),
            ("kill",    cmd_kill),
            ("risk",    cmd_risk),
            ("help",    cmd_help),
        ]
        for cmd, fn in handlers:
            app.add_handler(CommandHandler(cmd, fn))

    async def run_polling(self):
        """Run the Telegram bot in polling mode (for non-webhook deployments)."""
        if self._app is None:
            self.build_app()
        log.info("Starting Telegram polling...")
        async with self._app:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            # Keep running until cancelled
            while True:
                await asyncio.sleep(1)
