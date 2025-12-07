# backend/services/exchange_client.py
import os
import logging
from typing import Dict, Any, Optional
import ccxt
import asyncio
from functools import partial

logger = logging.getLogger("exchange_client")

# Environment config
API_KEY = os.getenv("COINDCX_API_KEY")
API_SECRET = os.getenv("COINDCX_API_SECRET")
SANDBOX = os.getenv("COINDCX_SANDBOX", "0") == "1"
SAFETY_ALLOW_PLACE = os.getenv("SAFETY_ALLOW_PLACE", "0") == "1"

if not API_KEY or not API_SECRET:
    logger.warning("CoinDCX API credentials are not set (COINDCX_API_KEY / COINDCX_API_SECRET).")

def _build_exchange() -> ccxt.Exchange:
    """
    Configure a ccxt CoinDCX exchange instance (synchronous).
    """
    ex = ccxt.coindcx({
        "apiKey": API_KEY or "",
        "secret": API_SECRET or "",
        "enableRateLimit": True,
    })

    # optional: switch to sandbox/testnet if supported and requested.
    if SANDBOX:
        # ccxt has different ways to enable sandbox per-exchange; try common property
        try:
            if hasattr(ex, 'set_sandbox_mode'):
                ex.set_sandbox_mode(True)
            else:
                # some ccxt builds accept ex.urls['api'] override — not guaranteed
                logger.info("SANDBOX requested but ccxt doesn't expose set_sandbox_mode for this exchange. Proceeding anyway.")
        except Exception:
            logger.exception("Failed to enable sandbox mode on ccxt instance.")

    return ex

def _place_spot_order_sync(exchange: ccxt.Exchange, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Blocking (sync) call to place a spot order using ccxt.
    """
    symbol = payload["symbol"]
    side = payload["side"].lower()  # 'buy'/'sell'
    otype = payload.get("order_type", "market").lower()  # 'market'/'limit'
    amount = float(payload["qty"])
    limit_price = payload.get("limit_price", None)

    if otype == "limit":
        if limit_price is None:
            raise ValueError("limit_price required for limit order")
        # ccxt.create_limit_order(symbol, side, amount, price, params)
        resp = exchange.create_limit_order(symbol, side, amount, float(limit_price), {})
    else:
        # market
        resp = exchange.create_market_order(symbol, side, amount, {})

    return resp

def _place_futures_order_sync(exchange: ccxt.Exchange, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attempt to place a futures order. CoinDCX futures specifics may vary;
    ccxt exchange may need extra params or a different market type.
    This function makes a best-effort attempt using standard ccxt calls and
    passes 'leverage' and other params in the params dict.
    """
    symbol = payload["symbol"]
    side = payload["side"].lower()
    otype = payload.get("order_type", "market").lower()
    amount = float(payload["qty"])
    limit_price = payload.get("limit_price", None)
    leverage = int(payload.get("leverage", 1))

    params = {}
    # Many futures endpoints expect 'leverage' or 'leverage' param; this is exchange-specific
    params["leverage"] = leverage

    # If coin-specific margin or reduceOnly, pass other params as needed (left blank intentionally)
    if otype == "limit":
        if limit_price is None:
            raise ValueError("limit_price required for limit order")
        resp = exchange.create_limit_order(symbol, side, amount, float(limit_price), params)
    else:
        resp = exchange.create_market_order(symbol, side, amount, params)

    return resp

async def place_order(payload: Dict[str, Any], market_kind: str = "spot") -> Dict[str, Any]:
    """
    Async wrapper to place order. This runs the blocking ccxt calls in an executor.
    - payload: dict with keys: symbol, qty, side, order_type, limit_price, leverage (optional)
    - market_kind: 'spot' or 'futures' (attempts best-effort for futures)
    Returns: exchange response dict (or raises).
    Note: actual placement will only happen if SAFETY_ALLOW_PLACE == True; otherwise returns a dry-run response.
    """
    logger.info("place_order called (dry_run=%s) payload=%s", not SAFETY_ALLOW_PLACE, payload)

    # Dry-run mode: do not call exchange, just return a simulated response
    if not SAFETY_ALLOW_PLACE:
        return {
            "dry_run": True,
            "payload": payload,
            "message": "SAFETY_ALLOW_PLACE not enabled — dry run only. Set SAFETY_ALLOW_PLACE=1 to enable real placements."
        }

    if not API_KEY or not API_SECRET:
        raise RuntimeError("CoinDCX credentials are missing (COINDCX_API_KEY/COINDCX_API_SECRET)")

    # Build exchange (sync object). We run calls in executor to avoid blocking event loop.
    exch = _build_exchange()

    loop = asyncio.get_running_loop()
    try:
        if market_kind == "futures":
            func = partial(_place_futures_order_sync, exch, payload)
        else:
            func = partial(_place_spot_order_sync, exch, payload)
        resp = await loop.run_in_executor(None, func)
    except Exception as e:
        logger.exception("Exchange order placement failed: %s", e)
        raise
    # Resp is usually a dict from ccxt with multiple fields
    return resp