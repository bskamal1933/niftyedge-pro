"""
Zerodha Kite Connect wrapper for NiftyEdge Pro.
Handles auth, live quotes, historical OHLCV, and option chain data.
Falls back gracefully when kiteconnect is not installed.
"""

import os, json, sqlite3, logging, threading
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "niftyedge_tips.db"))

try:
    from kiteconnect import KiteConnect
    _HAS_KITE = True
except ImportError:
    _HAS_KITE = False

# Kite instrument tokens for NSE indices (constant)
NIFTY_TOKEN    = 256265
BANKNIFTY_TOKEN = 260105

# Kite historical interval mapping  (tf_mins → kite interval string)
TF_TO_INTERVAL = {
    1:   "minute",
    5:   "5minute",
    10:  "10minute",
    15:  "15minute",
    30:  "30minute",
    60:  "60minute",
    180: "60minute",   # Kite max is 60m; we resample 3×60 ourselves
}


class KiteEngine:
    """Thread-safe Kite Connect wrapper with DB-backed config."""

    def __init__(self):
        self._lock  = threading.Lock()
        self._kite  = None
        self._api_key    = None
        self._api_secret = None
        self._token      = None
        self._profile    = None
        self._load_config()

    # ── Config persistence ─────────────────────────────────────────────

    def _load_config(self):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT api_key, api_secret, access_token "
                    "FROM kite_config ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row and row[0]:
                self._api_key, self._api_secret, self._token = row
                self._init_client()
        except Exception as e:
            log.debug(f"[Kite] Config load: {e}")

    def _init_client(self):
        if not _HAS_KITE or not self._api_key:
            return
        self._kite = KiteConnect(api_key=self._api_key)
        if self._token:
            self._kite.set_access_token(self._token)

    def save_config(self, api_key: str, api_secret: str, access_token: str = ""):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM kite_config")
            conn.execute(
                "INSERT INTO kite_config (api_key, api_secret, access_token, updated_at) "
                "VALUES (?,?,?,?)",
                (api_key, api_secret, access_token, datetime.utcnow().isoformat()),
            )
        with self._lock:
            self._api_key    = api_key
            self._api_secret = api_secret
            self._token      = access_token or None
            self._init_client()

    def save_token(self, token: str):
        self._token = token
        if self._kite:
            self._kite.set_access_token(token)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE kite_config SET access_token=?, updated_at=? WHERE api_key=?",
                (token, datetime.utcnow().isoformat(), self._api_key),
            )

    # ── Auth ───────────────────────────────────────────────────────────

    def login_url(self) -> str | None:
        if not _HAS_KITE or not self._api_key:
            return None
        if not self._kite:
            self._kite = KiteConnect(api_key=self._api_key)
        return self._kite.login_url()

    def generate_session(self, request_token: str) -> str:
        if not _HAS_KITE or not self._kite or not self._api_secret:
            raise RuntimeError("Kite not configured — set API key and secret first.")
        data  = self._kite.generate_session(request_token, api_secret=self._api_secret)
        token = data["access_token"]
        self.save_token(token)
        self._profile = data.get("user_name", "")
        return token

    # ── Status ─────────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    @property
    def authenticated(self) -> bool:
        return bool(self._kite and self._token)

    @property
    def has_library(self) -> bool:
        return _HAS_KITE

    def status(self) -> dict:
        return {
            "library":       _HAS_KITE,
            "configured":    self.configured,
            "authenticated": self.authenticated,
            "api_key":       (self._api_key[:6] + "…") if self._api_key else None,
            "profile":       self._profile,
        }

    # ── Market data ────────────────────────────────────────────────────

    def quote(self, instruments: list) -> dict:
        """instruments: ['NSE:NIFTY 50', 'NFO:NIFTY24APR25000CE', …]"""
        if not self.authenticated:
            return {}
        try:
            return self._kite.quote(instruments)
        except Exception as e:
            log.warning(f"[Kite] quote: {e}")
            return {}

    def ltp(self, instruments: list) -> dict:
        if not self.authenticated:
            return {}
        try:
            return self._kite.ltp(instruments)
        except Exception as e:
            log.warning(f"[Kite] ltp: {e}")
            return {}

    def historical(self, token: int, from_dt: datetime, to_dt: datetime,
                   interval: str, oi: bool = True) -> list:
        """Returns list of {date, open, high, low, close, volume, oi} dicts."""
        if not self.authenticated:
            return []
        try:
            return self._kite.historical_data(token, from_dt, to_dt, interval,
                                              continuous=False, oi=oi)
        except Exception as e:
            log.warning(f"[Kite] historical: {e}")
            return []

    def instruments(self, exchange: str = "NFO") -> list:
        if not self.authenticated:
            return []
        try:
            return self._kite.instruments(exchange)
        except Exception as e:
            log.warning(f"[Kite] instruments: {e}")
            return []

    # ── Convenience: fetch OHLCV candles for a symbol + timeframe ──────

    def fetch_candles(self, token: int, tf_mins: int, n_candles: int = 120) -> list:
        interval = TF_TO_INTERVAL.get(tf_mins, "minute")
        to_dt    = datetime.now()
        from_dt  = to_dt - timedelta(minutes=tf_mins * n_candles * 2)
        raw = self.historical(token, from_dt, to_dt, interval)
        candles = [
            {
                "open":   r["open"],
                "high":   r["high"],
                "low":    r["low"],
                "close":  r["close"],
                "volume": r.get("volume", 0),
                "oi":     r.get("oi", 0),
                "date":   r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"]),
            }
            for r in raw
        ]
        return candles[-n_candles:]

    # ── Options chain helpers ───────────────────────────────────────────

    def get_option_chain_instruments(self, underlying: str, expiry: str | None = None) -> list:
        """
        Returns NFO instruments for the given underlying (NIFTY/BANKNIFTY)
        filtered to nearest expiry.
        """
        insts = self.instruments("NFO")
        filtered = [
            i for i in insts
            if i.get("name") == underlying and i.get("instrument_type") in ("CE", "PE")
        ]
        if not filtered:
            return []
        # Pick nearest expiry
        expiries = sorted({i["expiry"] for i in filtered if i.get("expiry")})
        target   = expiry or (expiries[0].isoformat() if expiries else None)
        if target:
            filtered = [i for i in filtered if str(i.get("expiry", "")) == target]
        return sorted(filtered, key=lambda x: x.get("strike", 0))

    def get_atm_options(self, underlying: str, spot: float) -> dict:
        """Returns ATM CE and PE instrument tokens for the underlying."""
        insts = self.get_option_chain_instruments(underlying)
        if not insts:
            return {}
        strikes = sorted({i["strike"] for i in insts})
        if not strikes:
            return {}
        atm_strike = min(strikes, key=lambda s: abs(s - spot))
        result = {}
        for i in insts:
            if i["strike"] == atm_strike:
                result[i["instrument_type"]] = {
                    "token":    i["instrument_token"],
                    "tradingsymbol": i["tradingsymbol"],
                    "strike":   atm_strike,
                    "expiry":   str(i.get("expiry", "")),
                }
        return result


# ── Singleton ──────────────────────────────────────────────────────────
kite = KiteEngine()
