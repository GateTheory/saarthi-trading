import os
import asyncio
import logging
from typing import Dict, Set
import httpx
from fastapi import WebSocket

logger = logging.getLogger("trading")

COINDCX_PUBLIC_URL = os.getenv("COINDCX_PUBLIC_URL", "https://api.coindcx.com")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "2.0"))  # seconds

class PriceBroadcaster:
    def __init__(self):
        self.tickers: Dict[str, float] = {}          # symbol -> last price
        self.symbols_cache = []                      # list of available symbols (strings)
        self._task = None
        self._running = False

        # subscriptions: websocket -> set(symbols)
        self._subscriptions = {}
        self._lock = asyncio.Lock()

    async def start_broadcaster(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("price_broadcaster started")

    async def stop_broadcaster(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("price_broadcaster stopped")

    async def _poll_loop(self):
        """
        Polls CoinDCX ticker endpoint and updates self.tickers & self.symbols_cache.
        Broadcasts updates to connected websockets for their subscribed symbols.
        """
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    r = await client.get(f"{COINDCX_PUBLIC_URL}/exchange/ticker", timeout=10.0)
                    r.raise_for_status()
                    data = r.json()
                    # data is a list of objects with keys like 'market' and 'last_price'
                    # populate the cache and tickers
                    new_tickers = {}
                    symbols = []
                    for item in data:
                        m = item.get("market")
                        lp = item.get("last_price")
                        if m and lp is not None:
                            sym = m.upper()
                            symbols.append(sym)
                            # try convert to float
                            try:
                                price_val = float(lp)
                            except Exception:
                                # some endpoints return string with commas — try replace
                                price_val = float(str(lp).replace(",", ""))
                            new_tickers[sym] = price_val

                    # Update caches
                    self.tickers = new_tickers
                    self.symbols_cache = symbols

                    # push updates to connected clients (only those subscribed)
                    await self._push_updates()

                except Exception as e:
                    logger.exception("price_broadcaster poll error: %s", e)

                try:
                    await asyncio.sleep(POLL_INTERVAL)
                except asyncio.CancelledError:
                    break

    async def _push_updates(self):
        """
        For all websockets and their subscribed symbols, send the latest price for those symbols.
        """
        to_remove = []
        async with self._lock:
            for ws, subs in list(self._subscriptions.items()):
                try:
                    for sym in list(subs):
                        price = self.tickers.get(sym)
                        if price is not None:
                            # send a JSON message with symbol and price
                            await ws.send_json({"symbol": sym, "price": price})
                except Exception as e:
                    logger.info("WebSocket connection closed (clients will be removed): %s", e)
                    to_remove.append(ws)

            # cleanup disconnected websockets
            for ws in to_remove:
                self._subscriptions.pop(ws, None)

    # websocket registry helpers
    async def register(self, websocket: WebSocket):
        async with self._lock:
            self._subscriptions.setdefault(websocket, set())
            logger.info("WebSocket registered (clients=%d)", len(self._subscriptions))

    async def unregister(self, websocket: WebSocket):
        async with self._lock:
            self._subscriptions.pop(websocket, None)
            logger.info("WebSocket connection closed (clients=%d)", len(self._subscriptions))

    def subscribe(self, websocket: WebSocket, symbol: str):
        # keep uppercase
        s = symbol.upper()
        self._subscriptions.setdefault(websocket, set()).add(s)
        logger.info("Subscribed websocket to %s", s)

    def unsubscribe(self, websocket: WebSocket, symbol: str):
        s = symbol.upper()
        if websocket in self._subscriptions:
            self._subscriptions[websocket].discard(s)
            logger.info("Unsubscribed websocket from %s", s)

# single global instance
price_broadcaster = PriceBroadcaster()