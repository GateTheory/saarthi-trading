import os
import asyncio
import logging
from typing import Dict, List, Optional, Set, Any, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel, Field
import httpx
import time
import json
import hmac
import hashlib
import math

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("trading")
router = APIRouter()

# ---------------- Config ----------------
COINDCX_API = "https://api.coindcx.com"
TICKER_ENDPOINT = "/exchange/ticker"
TICKER_REFRESH_INTERVAL = float(os.getenv("TICKER_REFRESH_INTERVAL", "10"))
WS_TOKEN = os.getenv("WS_TOKEN", "123456")  # still loaded but NOT enforced anymore

# Futures INR order endpoint
COINDCX_ORDER_URL = "https://api.coindcx.com/exchange/v1/derivatives/futures/orders/create"
COINDCX_API_KEY = os.getenv("COINDCX_API_KEY")
COINDCX_API_SECRET = os.getenv("COINDCX_API_SECRET")

# Futures wallets endpoint (INR + USDT)
COINDCX_WALLETS_URL = "https://api.coindcx.com/exchange/v1/derivatives/futures/wallets"

# Futures active instruments endpoint (public)
COINDCX_ACTIVE_INSTRUMENTS_URL = (
    "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
)

# Futures instrument details endpoint (public)
COINDCX_INSTRUMENT_URL = (
    "https://api.coindcx.com/exchange/v1/derivatives/futures/data/instrument"
)

print("DEBUG: COINDCX_ORDER_URL =", COINDCX_ORDER_URL)
print("DEBUG: COINDCX_API_KEY =", "SET" if COINDCX_API_KEY else None)
print("DEBUG: COINDCX_API_SECRET =", "SET" if COINDCX_API_SECRET else None)

BASE_PRECISION: Dict[str, int] = {
    "BTC": 8,
    "ETH": 8,
    "ADA": 2,
    "XRP": 1,
    "DOGE": 0,
    "SOL": 3,
}

# Stores
_ticker_cache: Dict[str, float] = {}
_ticker_cache_updated_at = 0.0
_orders: List[Dict[str, Any]] = []
_order_next_id = 1

# Futures wallets cache
_futures_wallets_cache: Optional[List[Dict[str, Any]]] = None
_futures_wallets_cached_at: float = 0.0

# Active futures instruments cache keyed by margin currency (INR / USDT)
_active_instruments_cache: Dict[str, Set[str]] = {}
_active_instruments_cached_at: Dict[str, float] = {}

# Instrument details cache: key = (pair, margin_currency)
_instrument_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_instrument_cached_at: Dict[Tuple[str, str], float] = {}

router.WS_CONNECTIONS: Set[WebSocket] = set()
router.WS_SUBSCRIPTIONS: Dict[WebSocket, Set[str]] = {}
router._poll_task: Optional[asyncio.Task] = None
router._poll_lock = asyncio.Lock()

# ---------------- Models ----------------
class OrderIn(BaseModel):
    symbol: str
    qty: float  # INR trade notional (computed on frontend as Risk * 100 / SL)
    leverage: int = Field(1, ge=1, le=100)
    side: str = Field(..., pattern="^(BUY|SELL)$")
    order_type: str = Field(..., pattern="^(market|limit)$")
    limit_price: Optional[float] = None
    # keep margin in backend so edits don't lose it
    margin: Optional[float] = None

class OrderOut(OrderIn):
    id: int

class ExecuteIn(BaseModel):
    ids: List[int] = Field(..., min_items=1)

class BulkOrdersIn(BaseModel):
    orders: List[OrderIn] = Field(..., min_items=1)

# ---------------- Ticker logic ----------------
async def _fetch_all_tickers() -> List[Dict[str, Any]]:
    url = f"{COINDCX_API}{TICKER_ENDPOINT}"
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

def _guess_symbol_from_item(item: Dict[str, Any]) -> Optional[str]:
    for k in ("market", "symbol", "pair", "market_symbol"):
        if k in item:
            val = item[k]
            if isinstance(val, str):
                return val.upper()
    if "ticker" in item:
        t = item["ticker"]
        if isinstance(t, dict):
            for k in ("symbol", "pair"):
                if k in t:
                    return str(t[k]).upper()
    return None

def _guess_last_from_item(item: Dict[str, Any]) -> Optional[float]:
    for k in ("last", "last_price", "price", "close"):
        if k in item:
            try:
                return float(item[k])
            except:
                pass
    t = item.get("ticker")
    if isinstance(t, dict):
        for k in ("last", "last_price", "close", "price"):
            if k in t:
                try:
                    return float(t[k])
                except:
                    pass
    return None

async def refresh_ticker_cache():
    global _ticker_cache, _ticker_cache_updated_at
    try:
        data = await _fetch_all_tickers()
        new_cache: Dict[str, float] = {}
        for item in (data if isinstance(data, list) else []):
            if isinstance(item, dict):
                sym = _guess_symbol_from_item(item)
                last = _guess_last_from_item(item)
                if sym and last is not None:
                    new_cache[sym] = last
        if new_cache:
            _ticker_cache = new_cache
            _ticker_cache_updated_at = time.time()
            logger.info(f"Ticker cache refreshed ({len(_ticker_cache)} symbols)")
    except Exception as e:
        logger.exception("Ticker cache update failed: %s", e)

async def ticker_background_loop():
    try:
        while True:
            await refresh_ticker_cache()
            await asyncio.sleep(TICKER_REFRESH_INTERVAL)
    except asyncio.CancelledError:
        logger.info("Ticker background loop cancelled")

# ---------- Futures wallets helper + startup logging ----------
async def _fetch_futures_wallets() -> Any:
    """
    Call CoinDCX futures wallets endpoint and return parsed JSON.
    Also logs INR + USDT wallet balances.
    """
    global _futures_wallets_cache, _futures_wallets_cached_at

    if not (COINDCX_API_KEY and COINDCX_API_SECRET):
        logger.warning("CoinDCX API credentials missing; skipping wallet fetch")
        return None

    timestamp_ms = int(round(time.time() * 1000))
    body = {"timestamp": timestamp_ms}

    json_body = json.dumps(body, separators=(",", ":"))
    signature = hmac.new(
        COINDCX_API_SECRET.encode("utf-8"),
        json_body.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": COINDCX_API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(
            "GET",
            COINDCX_WALLETS_URL,
            content=json_body,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, list):
        _futures_wallets_cache = data
    else:
        _futures_wallets_cache = [data]
    _futures_wallets_cached_at = time.time()

    logger.info("Futures wallets raw response: %s", json.dumps(data, indent=2))

    try:
        if isinstance(data, list):
            for w in data:
                cur = (w.get("currency_short_name") or "").upper()
                if cur in ("INR", "USDT"):
                    logger.info(
                        "Futures %s wallet -> id=%s balance=%s locked_balance=%s",
                        cur,
                        w.get("id"),
                        w.get("balance"),
                        w.get("locked_balance"),
                    )
    except Exception as e:
        logger.exception("Error while logging wallet summary: %s", e)

    return data

async def _get_inr_futures_wallet() -> Optional[Dict[str, Any]]:
    """
    Returns the Futures INR wallet (id, balance, etc.) from /derivatives/futures/wallets.
    Used only for logging / safety checks before sending an order.
    """
    global _futures_wallets_cache, _futures_wallets_cached_at

    if (not _futures_wallets_cache) or (time.time() - _futures_wallets_cached_at > 30):
        await _fetch_futures_wallets()

    wallets = _futures_wallets_cache or []
    for w in wallets:
        if (w.get("currency_short_name") or "").upper() == "INR":
            return w
    return None

# ---------- Active instruments helper ----------
async def _fetch_active_instruments(margin_currency: str = "INR") -> Set[str]:
    global _active_instruments_cache, _active_instruments_cached_at

    mc = margin_currency.upper()
    params = {"margin_currency_short_name[]": mc}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(COINDCX_ACTIVE_INSTRUMENTS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    instruments = set(str(x) for x in (data or []))
    _active_instruments_cache[mc] = instruments
    _active_instruments_cached_at[mc] = time.time()

    sample = sorted(list(instruments))[:50]
    logger.info(
        "Active futures instruments for %s -> total=%d, sample=%s",
        mc, len(instruments), sample
    )

    return instruments

async def get_active_instruments(margin_currency: str = "INR") -> Set[str]:
    mc = margin_currency.upper()
    now = time.time()
    instruments = _active_instruments_cache.get(mc)
    ts = _active_instruments_cached_at.get(mc, 0)

    if not instruments or (now - ts > 60):
        try:
            instruments = await _fetch_active_instruments(mc)
        except Exception as e:
            logger.exception("Failed to refresh active instruments for %s: %s", mc, e)
            instruments = _active_instruments_cache.get(mc, set())
    return instruments

# ---------- Instrument details helper ----------
async def _fetch_instrument_details(pair: str, margin_currency: str = "INR") -> Dict[str, Any]:
    """
    Fetch full instrument definition for given futures pair.
    We pass margin_currency_short_name to align with INR/USDT margin mode.
    """
    global _instrument_cache, _instrument_cached_at

    mc = margin_currency.upper()
    key = (pair, mc)
    params = {
        "pair": pair,
        "margin_currency_short_name": mc,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(COINDCX_INSTRUMENT_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    inst = data.get("instrument") if isinstance(data, dict) else data
    if not isinstance(inst, dict):
        logger.error("Unexpected instrument response for %s/%s: %s", pair, mc, data)
        inst = {}

    _instrument_cache[key] = inst
    _instrument_cached_at[key] = time.time()

    logger.info(
        "Instrument details for %s (margin=%s): %s",
        pair, mc, json.dumps(inst, indent=2)
    )

    return inst

async def get_instrument_details(pair: str, margin_currency: str = "INR") -> Dict[str, Any]:
    """
    Cached wrapper around _fetch_instrument_details (TTL 60s).
    """
    mc = margin_currency.upper()
    key = (pair, mc)
    now = time.time()
    inst = _instrument_cache.get(key)
    ts = _instrument_cached_at.get(key, 0)

    if not inst or (now - ts > 60):
        try:
            inst = await _fetch_instrument_details(pair, mc)
        except Exception as e:
            logger.exception("Failed to refresh instrument details for %s/%s: %s", pair, mc, e)
            inst = _instrument_cache.get(key, {})
    return inst

# ---------- USDT/INR FX helper ----------
async def _get_usdt_inr_rate() -> float:
    """
    Returns the spot USDT/INR rate from ticker cache.
    Falls back to 90.0 if not available.
    """
    symbol = "USDTINR"
    if (symbol not in _ticker_cache) or (time.time() - _ticker_cache_updated_at > 30):
        await refresh_ticker_cache()
    rate = _ticker_cache.get(symbol)
    if not rate:
        rate = 90.0
        logger.warning("USDTINR ticker not found; falling back to hardcoded rate %s", rate)
    return float(rate)

async def _log_futures_wallets_startup():
    try:
        logger.info("Fetching CoinDCX futures wallets on startup…")
        await _fetch_futures_wallets()
    except Exception as e:
        logger.exception("Startup wallets fetch failed: %s", e)

async def _log_active_instruments_startup():
    try:
        logger.info("Fetching active INR futures instruments on startup…")
        await get_active_instruments("INR")
    except Exception as e:
        logger.exception("Startup active instruments fetch failed: %s", e)

@router.on_event("startup")
async def _router_startup():
    if router._poll_task is None or router._poll_task.done():
        router._poll_task = asyncio.create_task(ticker_background_loop())
    if COINDCX_API_KEY and COINDCX_API_SECRET:
        asyncio.create_task(_log_futures_wallets_startup())
    asyncio.create_task(_log_active_instruments_startup())

@router.on_event("shutdown")
async def _router_shutdown():
    if router._poll_task:
        router._poll_task.cancel()
        try:
            await router._poll_task
        except:
            pass

# ---------------- HTTP endpoints ----------------
@router.get("/securities")
async def list_securities(currency: str = "USD"):
    if not _ticker_cache:
        await refresh_ticker_cache()
    syms = list(_ticker_cache.keys())
    return {"symbols": sorted(syms), "count": len(syms)}

@router.get("/price/{symbol}")
async def get_price_http(symbol: str):
    sym = symbol.upper()
    if sym not in _ticker_cache:
        await refresh_ticker_cache()
        if sym not in _ticker_cache:
            raise HTTPException(status_code=404, detail="Symbol not found")
    return {
        "symbol": sym,
        "price": _ticker_cache[sym],
        "source": "cache",
        "ts": _ticker_cache_updated_at
    }

@router.post("/orders")
async def add_order(order: OrderIn):
    """
    Add a single order to the local queue only if successfully saved.
    Frontend should only add to UI queue when success=True.
    """
    global _order_next_id, _orders

    try:
        if not order.symbol or order.qty <= 0:
            raise HTTPException(status_code=400, detail="Invalid order data")

        o = order.model_dump()
        o["id"] = _order_next_id
        o["local_id"] = _order_next_id  # for client_order_id generation
        _order_next_id += 1
        _orders.append(o)
        logger.info("Order added: %s", o)

        return {
            "success": True,
            "order": o,
            "message": "Order stored successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error while adding order: %s", e)
        raise HTTPException(status_code=500, detail="Failed to store order")

@router.post("/orders/bulk")
async def add_orders_bulk(payload: BulkOrdersIn):
    """
    Accepts multiple orders in a single call and stores them,
    assigning incremental IDs just like /orders.
    """
    global _order_next_id, _orders
    created: List[Dict[str, Any]] = []

    try:
        for order in payload.orders:
            o = order.model_dump()
            o["id"] = _order_next_id
            o["local_id"] = _order_next_id
            _order_next_id += 1
            _orders.append(o)
            created.append(o)

        logger.info("Bulk orders added: %s", created)
        return {"success": True, "orders": created}
    except Exception as e:
        logger.exception("Bulk add failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to store bulk orders")

@router.put("/orders/{order_id}")
async def update_order(order_id: int, order: OrderIn):
    """
    Update an existing queued order by id.
    Used by the frontend when user edits an order in the queue.
    """
    global _orders

    for idx, existing in enumerate(_orders):
        if existing.get("id") == order_id:
            updated = existing.copy()
            data = order.model_dump()
            updated.update(data)
            updated["id"] = existing["id"]
            updated["local_id"] = existing.get("local_id", existing["id"])
            _orders[idx] = updated
            logger.info("Order updated: %s", updated)
            return {"success": True, "order": updated}

    raise HTTPException(status_code=404, detail="Order not found")

@router.get("/orders")
async def list_orders():
    return {"orders": _orders}

# ------- UI balance endpoint -------
@router.get("/balance")
async def get_futures_balance(currency: str = "INR"):
    global _futures_wallets_cache, _futures_wallets_cached_at

    if currency is None:
        currency = "INR"
    cur = currency.upper()
    if cur == "USD":
        cur = "USDT"

    if (not _futures_wallets_cache) or (time.time() - _futures_wallets_cached_at > 30):
        try:
            await _fetch_futures_wallets()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Balance fetch failed (wallets HTTP error): %s %s",
                e.response.status_code,
                e.response.text
            )
            raise HTTPException(status_code=502, detail="Failed to fetch wallets from CoinDCX")
        except Exception as e:
            logger.exception("Balance fetch unexpected error: %s", e)
            raise HTTPException(status_code=500, detail="Internal error while fetching balance")

    wallets = _futures_wallets_cache or []
    for w in wallets:
        w_cur = str(w.get("currency_short_name") or "").upper()
        if w_cur == cur:
            bal = float(w.get("balance") or 0.0)
            locked = float(w.get("locked_balance") or 0.0)
            return {
                "currency": w_cur,
                "balance": bal,
                "locked_balance": locked,
                "wallet_id": w.get("id"),
            }

    raise HTTPException(status_code=404, detail=f"Futures wallet for {cur} not found")

@router.get("/wallets")
async def get_futures_wallets():
    try:
        data = await _fetch_futures_wallets()
        return {"wallets": data}
    except httpx.HTTPStatusError as e:
        logger.error("Wallets fetch failed: status=%s body=%s", e.response.status_code, e.response.text)
        raise HTTPException(status_code=502, detail="Failed to fetch wallets from CoinDCX")
    except Exception as e:
        logger.exception("Wallets fetch unexpected error: %s", e)
        raise HTTPException(status_code=500, detail="Internal error while fetching wallets")

# ---------------- FUTURES INR ORDER EXECUTION ----------------
async def place_order_on_coindcx(order: Dict[str, Any], attempts: int = 3, backoff_sec: float = 0.5):
    # ... UNCHANGED ORDER EXECUTION LOGIC ...
    # (keep the rest of this function exactly as in your original file)
    symbol = order.get("symbol").upper()
    price = order.get("limit_price") or _ticker_cache.get(symbol, None)
    if not price:
        raise HTTPException(status_code=400, detail=f"No price available for {symbol}")

    base = symbol.replace("USDT", "").replace("INR", "").replace("USD", "")
    pair = f"B-{base}_USDT"

    side = "buy" if order.get("side") == "BUY" else "sell"
    api_order_type = "limit_order" if order.get("order_type") == "limit" else "market_order"
    timestamp_ms = int(time.time() * 1000)

    margin_ccy = "INR"

    base_local = order.get("local_id") or order.get("id") or "local"
    client_order_id = f"{base_local}-{timestamp_ms}"
    order["last_client_order_id"] = client_order_id

    inr_wallet = await _get_inr_futures_wallet()
    if not inr_wallet:
        logger.error("No Futures INR wallet found; cannot place order")
        return {
            "sent": False,
            "success": False,
            "error": "no_inr_futures_wallet",
        }

    wallet_balance = float(inr_wallet.get("balance") or 0.0)
    logger.info(
        "Using Futures INR wallet id=%s balance=%s locked_balance=%s for this order",
        inr_wallet.get("id"),
        inr_wallet.get("balance"),
        inr_wallet.get("locked_balance"),
    )

    active_instruments = await get_active_instruments(margin_ccy)
    if pair not in active_instruments:
        sample = sorted(list(active_instruments))[:30]
        logger.error(
            "PAIR NOT ACTIVE for margin=%s -> pair=%s, symbol=%s, sample_active=%s",
            margin_ccy, pair, symbol, sample
        )
        return {
            "sent": False,
            "success": False,
            "error": "instrument_not_active",
            "pair": pair,
            "symbol": symbol,
            "margin_currency": margin_ccy,
            "known_instruments_sample": sample,
        }

    inst = await get_instrument_details(pair, margin_ccy)

    unit_contract_value = float(inst.get("unit_contract_value") or 1.0)
    quantity_increment = float(inst.get("quantity_increment") or 1.0)
    min_quantity = float(inst.get("min_quantity") or quantity_increment)
    max_quantity = float(inst.get("max_quantity") or 1e18)
    max_lev_long = float(inst.get("max_leverage_long") or 0.0)
    max_lev_short = float(inst.get("max_leverage_short") or max_lev_long)

    requested_leverage = int(order.get("leverage") or 1)
    leverage = requested_leverage

    logger.info(
        "Using user-selected leverage=%sx for %s (margin=%s); instrument max_long=%sx max_short=%sx",
        leverage, pair, margin_ccy, max_lev_long, max_lev_short
    )

    trade_size_in_inr = float(order.get("qty"))

    usdt_inr_rate = await _get_usdt_inr_rate()

    inr_per_contract = price * unit_contract_value * usdt_inr_rate

    contracts_raw = trade_size_in_inr / inr_per_contract

    contracts_step = math.floor(contracts_raw / quantity_increment) * quantity_increment

    if contracts_step < min_quantity:
        logger.error(
            "Calculated contracts %.8f is below min_quantity %.8f for %s",
            contracts_step, min_quantity, pair
        )
        return {
            "sent": False,
            "success": False,
            "error": "trade_size_too_small_for_instrument",
            "pair": pair,
            "symbol": symbol,
            "min_quantity": min_quantity,
            "quantity_increment": quantity_increment,
        }

    if contracts_step > max_quantity:
        logger.error(
            "Calculated contracts %.8f exceeds max_quantity %.8f for %s",
            contracts_step, max_quantity, pair
        )
        return {
            "sent": False,
            "success": False,
            "error": "trade_size_too_large_for_instrument",
            "pair": pair,
            "symbol": symbol,
            "max_quantity": max_quantity,
        }

    total_quantity = float(f"{contracts_step:.8f}")

    notional_usdt = price * unit_contract_value * total_quantity
    notional_inr = notional_usdt * usdt_inr_rate

    estimated_margin = notional_inr / leverage if leverage > 0 else notional_inr

    logger.info(
        "Futures calc for %s (margin=%s): trade_inr=%s price=%s usdt_inr_rate=%s "
        "unit_contract_value=%s quantity_increment=%s min_q=%s max_q=%s "
        "contracts_raw=%s contracts_step=%s leverage_used=%s notional_usdt=%s "
        "notional_inr=%s estimated_margin=%s wallet_balance=%s",
        pair,
        margin_ccy,
        trade_size_in_inr,
        price,
        usdt_inr_rate,
        unit_contract_value,
        quantity_increment,
        min_quantity,
        max_quantity,
        contracts_raw,
        contracts_step,
        leverage,
        notional_usdt,
        notional_inr,
        estimated_margin,
        wallet_balance,
    )

    if estimated_margin > wallet_balance:
        logger.error(
            "Estimated margin %.8f exceeds wallet balance %.8f for %s",
            estimated_margin, wallet_balance, pair
        )
        return {
            "sent": False,
            "success": False,
            "error": "estimated_margin_exceeds_balance",
            "estimated_margin": estimated_margin,
            "wallet_balance": wallet_balance,
        }

    payload = {
        "timestamp": timestamp_ms,
        "order": {
            "pair": pair,
            "side": side,
            "order_type": api_order_type,
            "price": float(price),
            "total_quantity": total_quantity,
            "leverage": int(leverage),
            "time_in_force": "good_till_cancel",
            "margin_currency_short_name": margin_ccy,
            "client_order_id": client_order_id,
        }
    }

    json_body = json.dumps(payload, separators=(",", ":"))
    signature = hmac.new(COINDCX_API_SECRET.encode(), json_body.encode(), hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": COINDCX_API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    logger.info(
        "=== FUTURES INR ORDER SEND ===\nURL: %s\nBody: %s\nSignature=%s\n===========================",
        COINDCX_ORDER_URL, json_body, signature,
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(COINDCX_ORDER_URL, data=json_body, headers=headers)
        text = resp.text
        logger.info("Futures response (status=%s) body=%s", resp.status_code, text)
        return {
            "sent": True,
            "success": resp.status_code in (200, 201),
            "payload": payload,
            "response_status": resp.status_code,
            "response_text": text
        }

# ---------------- Order Execution ----------------
@router.post("/orders/execute")
async def execute_orders(payload: ExecuteIn):
    executed, failed, not_found, debug = [], [], [], []
    global _orders

    id_map = {o["id"]: o for o in _orders}
    for oid in payload.ids:
        found = id_map.get(oid)
        if not found:
            not_found.append(oid)
            continue

        result = await place_order_on_coindcx(found)
        debug.append({"local_id": oid, "symbol": found["symbol"], "result": result})

        if result.get("success"):
            executed.append(found)
        else:
            failed.append({"id": oid})

    if executed:
        executed_ids = {o["id"] for o in executed}
        _orders = [o for o in _orders if o["id"] not in executed_ids]

    return {"executed": executed, "failed": failed, "not_found": not_found, "debug": debug}

# ---------------- WebSocket price push ----------------
async def _broadcast_price_updates():
    if not router.WS_CONNECTIONS:
        return
    for ws in list(router.WS_CONNECTIONS):
        for s in list(router.WS_SUBSCRIPTIONS.get(ws, set())):
            price = _ticker_cache.get(s)
            if price is not None:
                await ws.send_text(json.dumps({
                    "type": "price",
                    "data": {"symbol": s, "price": price, "ts": time.time()}
                }))

_original_refresh = refresh_ticker_cache

async def refresh_ticker_cache_and_broadcast():
    await _original_refresh()
    await _broadcast_price_updates()

refresh_ticker_cache = refresh_ticker_cache_and_broadcast

@router.websocket("/ws/price")
async def ws_price_endpoint(ws: WebSocket):
    """
    WebSocket endpoint for price updates.

    - Token is entered by the USER on the frontend.
    - Backend does NOT validate the token; it only logs it.
    """
    user_token = ws.query_params.get("token", "")
    logger.info("WS client connecting with user token=%r", user_token)

    # ✅ Always accept connection – no server-side token check
    await ws.accept()

    router.WS_CONNECTIONS.add(ws)
    router.WS_SUBSCRIPTIONS[ws] = set()

    try:
        while True:
            m = json.loads(await ws.receive_text())
            action, symbol = m.get("action"), (m.get("symbol") or "").upper()
            if action == "subscribe":
                router.WS_SUBSCRIPTIONS[ws].add(symbol)
            elif action == "unsubscribe":
                router.WS_SUBSCRIPTIONS[ws].discard(symbol)
    except Exception:
        pass
    finally:
        router.WS_CONNECTIONS.discard(ws)
        router.WS_SUBSCRIPTIONS.pop(ws, None)
        logger.info("WS client disconnected")
