# backend/services/exchange.py
import os, time, logging, requests
from threading import Lock
from typing import Dict, Any, Optional
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("exchange")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(h)
logger.setLevel(logging.INFO)

DEFAULT_BASE = os.getenv("COINDCX_BASE_URL", "https://api.coindcx.com").rstrip("/")

class ExchangeService:
    def __init__(self):
        self.base_url = os.getenv("COINDCX_BASE_URL", DEFAULT_BASE).rstrip("/")
        self._cache = {}
        self._cache_ttl = float(os.getenv("CACHE_TTL_SECONDS", "1.5"))
        self._lock = Lock()
    def _now(self): return time.time()
    def _normalize(self, symbol: str) -> str:
        return (symbol or "").strip().upper()
    def _get_cached(self, key: str) -> Optional[Dict[str, Any]]:
        ent = self._cache.get(key)
        if not ent: return None
        if (self._now() - ent["ts"]) <= self._cache_ttl:
            return ent
        return None
    def _set_cache(self, key: str, price: float, source: str):
        with self._lock:
            self._cache[key] = {"price": price, "ts": self._now(), "source": source}
    def get_price(self, symbol: str) -> Dict[str, Any]:
        if not symbol: raise ValueError("symbol required")
        key = self._normalize(symbol)
        cached = self._get_cached(key)
        if cached: return {"symbol": symbol, "price": cached["price"], "source": cached.get("source","cache"), "ts": int(cached["ts"]*1000)}
        # try bulk
        try:
            url = f"{self.base_url}/exchange/ticker"
            r = requests.get(url, timeout=6)
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    searchkey = key.replace("_","").replace("-","")
                    for item in data:
                        if not isinstance(item, dict): continue
                        for pf in ("market","pair","symbol","s"):
                            pv = item.get(pf) if pf in item else None
                            if isinstance(pv, str):
                                if pv.upper().replace("_","").replace("-","") == searchkey:
                                    for pk in ("last_price","price","last","close"):
                                        if pk in item and item.get(pk) is not None:
                                            try:
                                                price = float(item.get(pk))
                                                self._set_cache(key, price, "exchange/ticker")
                                                return {"symbol": symbol, "price": price, "source": "exchange/ticker", "ts": int(self._now()*1000)}
                                            except: pass
        except Exception:
            pass
        # candidate endpoints
        candidates = [ key, key.replace("USDT","_USDT"), key.replace("_",""), key.replace("_","-") ]
        candidate_paths = []
        for cand in candidates:
            candidate_paths += [
                f"/exchange/ticker/{cand}",
                f"/market_data/ticker/{cand}",
                f"/market_data/trade_history/{cand}",
                f"/public/trades/{cand}",
                f"/exchange/v1/derivatives/futures/market_data/ticker/{cand}",
            ]
        for path in candidate_paths:
            url = f"{self.base_url}{path}"
            try:
                r = requests.get(url, timeout=6)
                if not r.ok: continue
                payload = r.json()
                price = None
                if isinstance(payload, dict):
                    for pk in ("price","last_price","last","close"):
                        if pk in payload and payload[pk] is not None:
                            try: price = float(payload[pk]); break
                            except: pass
                    if price is None and isinstance(payload.get("data"), list) and payload["data"]:
                        first = payload["data"][0]
                        for pk in ("price","last_price","last"):
                            if pk in first and first[pk] is not None:
                                try: price = float(first[pk]); break
                                except: pass
                    if price is None and isinstance(payload.get("ticker"), dict):
                        t = payload.get("ticker")
                        for pk in ("last_price","price","last"):
                            if pk in t and t[pk] is not None:
                                try: price = float(t[pk]); break
                                except: pass
                elif isinstance(payload, list) and payload:
                    first = payload[0]
                    if isinstance(first, dict):
                        for pk in ("price","last_price","last"):
                            if pk in first and first[pk] is not None:
                                try: price = float(first[pk]); break
                                except: pass
                if price is not None:
                    self._set_cache(key, price, url)
                    return {"symbol": symbol, "price": price, "source": url, "ts": int(self._now()*1000)}
            except Exception:
                continue
        base = 60000 if "BTC" in key else 2000
        price = round(base * (0.95 + (time.time() % 1) * 0.1), 2)
        self._set_cache(key, price, "fallback")
        return {"symbol": symbol, "price": price, "source": "fallback", "ts": int(self._now()*1000)}

    def place_order(self, payload: dict) -> dict:
        raise NotImplementedError("place_order not implemented")