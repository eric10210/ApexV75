"""
api.py — Deriv WebSocket API handler.
Handles auth, subscriptions, reconnection with exponential backoff.
"""
import asyncio
import json
import logging
import time
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import DERIV_WS_URL, DERIV_API_KEY, INSTRUMENT, TIMEFRAMES, CANDLE_COUNT
from data_store import DataStore

log = logging.getLogger(__name__)

RECONNECT_DELAYS = [5, 15, 30, 60]   # exponential backoff seconds


class DerivAPI:
    def __init__(self, store: DataStore):
        self.store           = store
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.authenticated   = False
        self.connected       = False
        self._req_id         = 0
        self._on_candle_close: Optional[Callable] = None   # callback(tf)
        self._on_tick:         Optional[Callable] = None   # callback(price)
        self._candle_last_epoch = {tf: 0 for tf in TIMEFRAMES}
        self._tf_sub_ids     = {}   # subscription ids
        self._retry_count    = 0
        self._send_lock      = asyncio.Lock()

    def on_candle_close(self, fn: Callable):
        self._on_candle_close = fn

    def on_tick(self, fn: Callable):
        self._on_tick = fn

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, payload: dict):
        async with self._send_lock:
            if self.ws and self.ws.open:
                await self.ws.send(json.dumps(payload))

    # ── Connection ────────────────────────────────────────────────────────────
    async def connect_and_run(self, alert_fn: Callable = None):
        """Main loop — connect, authenticate, subscribe, receive messages."""
        while True:
            try:
                log.info("Connecting to Deriv WebSocket...")
                async with websockets.connect(
                    DERIV_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    self.ws          = ws
                    self.connected   = True
                    self.authenticated = False
                    self._retry_count = 0
                    log.info("WebSocket connected.")

                    await self._authenticate()
                    await self._subscribe_all()
                    await self._receive_loop()

            except (ConnectionClosed, WebSocketException, OSError) as e:
                self.connected     = False
                self.authenticated = False
                delay = RECONNECT_DELAYS[min(self._retry_count, len(RECONNECT_DELAYS)-1)]
                self._retry_count += 1
                log.warning(f"WS disconnected ({e}). Retry {self._retry_count} in {delay}s...")

                if alert_fn:
                    await alert_fn(
                        f"⚠️ APEX reconnecting... stream interrupted.\n"
                        f"Attempt {self._retry_count} — waiting {delay}s"
                    )

                if self._retry_count > 5 and alert_fn:
                    await alert_fn(
                        "🔴 APEX OFFLINE — 5+ reconnect failures.\n"
                        "Check your server and API key."
                    )

                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                log.info("API task cancelled — shutting down.")
                break
            except Exception as e:
                log.error(f"Unexpected API error: {e}", exc_info=True)
                await asyncio.sleep(10)

    # ── Auth ──────────────────────────────────────────────────────────────────
    async def _authenticate(self):
        log.info("Authenticating...")
        await self._send({"authorize": DERIV_API_KEY, "req_id": self._next_id()})
        # Wait for auth response (handled in receive loop)
        for _ in range(30):   # 15 second timeout
            if self.authenticated:
                return
            await asyncio.sleep(0.5)
        raise ConnectionError("Authentication timed out.")

    # ── Subscriptions ─────────────────────────────────────────────────────────
    async def _subscribe_all(self):
        # Subscribe to live ticks
        await self._send({
            "ticks": INSTRUMENT,
            "subscribe": 1,
            "req_id": self._next_id(),
        })

        # Subscribe to balance
        await self._send({
            "balance": 1,
            "subscribe": 1,
            "req_id": self._next_id(),
        })

        # Subscribe to candles for each timeframe
        for tf, gran in TIMEFRAMES.items():
            await self._send({
                "ticks_history": INSTRUMENT,
                "adjust_start_time": 1,
                "count": CANDLE_COUNT,
                "end": "latest",
                "granularity": gran,
                "start": 1,
                "style": "candles",
                "subscribe": 1,
                "req_id": self._next_id(),
            })
            await asyncio.sleep(0.2)  # avoid rate limiting

        log.info("All subscriptions sent.")

    # ── Message Receiver ──────────────────────────────────────────────────────
    async def _receive_loop(self):
        async for raw in self.ws:
            try:
                msg = json.loads(raw)
                await self._dispatch(msg)
            except json.JSONDecodeError:
                log.warning("Invalid JSON from API.")
            except Exception as e:
                log.error(f"Dispatch error: {e}", exc_info=True)

    async def _dispatch(self, msg: dict):
        msg_type = msg.get("msg_type")

        if "error" in msg:
            code = msg["error"].get("code", "")
            text = msg["error"].get("message", "")
            log.error(f"API error [{code}]: {text}")
            if code in ("InvalidToken", "AuthorizationRequired"):
                raise ConnectionError(f"Auth error: {text}")
            return

        if msg_type == "authorize":
            self.authenticated = True
            log.info("Deriv API authenticated ✅")

        elif msg_type == "balance":
            bal = msg.get("balance", {}).get("balance", 0)
            await self.store.update_balance(float(bal))
            log.debug(f"Balance updated: {bal}")

        elif msg_type == "tick":
            tick = msg.get("tick", {})
            price = float(tick.get("quote", 0))
            if price > 0:
                valid = await self.store.update_tick(price)
                if valid and self._on_tick:
                    await self._on_tick(price)

        elif msg_type == "candles":
            # Initial history load
            candles = msg.get("candles", [])
            gran    = msg.get("echo_req", {}).get("granularity", 300)
            tf      = self._gran_to_tf(gran)
            if tf and candles:
                await self.store.init_candles(tf, candles)
                if candles:
                    self._candle_last_epoch[tf] = candles[-1]["epoch"]

        elif msg_type == "ohlc":
            # Live candle update
            ohlc = msg.get("ohlc", {})
            gran = int(ohlc.get("granularity", 300))
            tf   = self._gran_to_tf(gran)
            if tf and ohlc:
                is_new = await self.store.update_candle(tf, ohlc)
                # Fire candle-close callback only on M5 new closed candle
                if is_new and tf == "M5" and self._on_candle_close:
                    await self._on_candle_close(tf)
                # Also fire for M15 and H1 new candles (for strategy checks)
                elif is_new and tf in ("M15", "H1", "H4") and self._on_candle_close:
                    await self._on_candle_close(tf)

    def _gran_to_tf(self, gran: int) -> Optional[str]:
        mapping = {v: k for k, v in TIMEFRAMES.items()}
        return mapping.get(gran)

    # ── Manual fetch ──────────────────────────────────────────────────────────
    async def fetch_history(self, tf: str, count: int = 100):
        gran = TIMEFRAMES.get(tf, 300)
        await self._send({
            "ticks_history": INSTRUMENT,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "granularity": gran,
            "start": 1,
            "style": "candles",
            "req_id": self._next_id(),
        })
