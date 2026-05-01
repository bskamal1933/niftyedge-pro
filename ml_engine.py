"""
ML engine for NiftyEdge Pro.

Feature engineering from OHLCV + OI + market microstructure data.
Ensemble model: Random Forest + XGBoost (or GradientBoosting fallback).
Rule-based scoring is used until enough outcome data accumulates (≥50 samples).
Models are persisted per symbol×timeframe and retrained online as outcomes arrive.
"""

import os, math, json, logging
import numpy as np
from datetime import datetime

log = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# ── Optional ML libraries ──────────────────────────────────────────────
try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    import joblib
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

try:
    import xgboost as xgb
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False

# Label encoding
SELL = 0; NEUTRAL = 1; BUY = 2
_LABEL = {SELL: "SELL", NEUTRAL: "NEUTRAL", BUY: "BUY"}

MIN_TRAIN_SAMPLES = 50


# ── Technical indicator helpers ────────────────────────────────────────

def _ema(arr: list, period: int) -> list:
    if len(arr) < period:
        return [None] * len(arr)
    k = 2.0 / (period + 1)
    val = sum(arr[:period]) / period
    out = [None] * (period - 1) + [val]
    for x in arr[period:]:
        val = x * k + val * (1 - k)
        out.append(val)
    return out


def _rsi(closes: list, period: int = 14) -> float | None:
    if len(closes) < period + 2:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(abs(min(d, 0)))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def _macd_hist(closes: list) -> float:
    if len(closes) < 35:
        return 0.0
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    line = [a - b for a, b in zip(e12, e26) if a is not None and b is not None]
    if len(line) < 9:
        return 0.0
    sig = _ema(line, 9)
    if sig[-1] is None:
        return 0.0
    return (line[-1] - sig[-1]) / (closes[-1] + 1e-9) * 100


def _bollinger_pos(closes: list, period: int = 20) -> float:
    if len(closes) < period:
        return 0.5
    sl  = closes[-period:]
    mid = sum(sl) / period
    std = math.sqrt(sum((x - mid) ** 2 for x in sl) / period) or 1e-9
    bw  = 4 * std
    return max(0.0, min(1.0, (closes[-1] - (mid - 2 * std)) / bw))


def _stochastic(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(closes) < period:
        return 50.0
    h = max(highs[-period:])
    l = min(lows[-period:])
    return 100.0 * (closes[-1] - l) / (h - l + 1e-9)


def _atr_norm(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(closes) < period + 2:
        return 0.0
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]
    return (sum(trs[-period:]) / period) / (closes[-1] + 1e-9) * 100


def _vwap_dev(closes: list, volumes: list) -> float:
    if not volumes or sum(volumes[-20:]) == 0:
        return 0.0
    cv = list(zip(closes[-20:], volumes[-20:]))
    vwap = sum(c * v for c, v in cv) / sum(v for _, v in cv)
    return (closes[-1] - vwap) / (closes[-1] + 1e-9) * 100


def _ret(closes: list, lag: int) -> float:
    if len(closes) <= lag:
        return 0.0
    return (closes[-1] - closes[-(lag + 1)]) / (closes[-(lag + 1)] + 1e-9) * 100


def _vol_ratio(volumes: list, window: int = 10) -> float:
    if len(volumes) < window + 1:
        return 1.0
    avg = sum(volumes[-window:]) / window
    return min(volumes[-1] / (avg + 1e-9), 5.0)


# ── Feature extraction ─────────────────────────────────────────────────

FEATURE_NAMES = [
    "ema_signal", "rsi", "macd_hist", "bb_pos", "stoch",
    "atr_norm", "ret_1", "ret_5", "ret_10",
    "vol_ratio", "vwap_dev", "pcr", "iv",
]


def extract_features(candles: list, pcr: float = 1.0, iv: float = 20.0) -> dict | None:
    """
    candles: list of {"open", "high", "low", "close", "volume"} dicts (oldest → newest)
    Returns feature dict or None if insufficient data.
    """
    if len(candles) < 35:
        return None

    opens   = [c["open"]          for c in candles]
    highs   = [c["high"]          for c in candles]
    lows    = [c["low"]           for c in candles]
    closes  = [c["close"]         for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    c_last = closes[-1]
    if c_last <= 0:
        return None

    e9  = _ema(closes, 9)
    e21 = _ema(closes, 21)
    ema_s = ((e9[-1] or c_last) - (e21[-1] or c_last)) / c_last * 100

    return {
        "ema_signal": round(ema_s,        4),
        "rsi":        round(_rsi(closes) or 50.0, 4),
        "macd_hist":  round(_macd_hist(closes),   4),
        "bb_pos":     round(_bollinger_pos(closes), 4),
        "stoch":      round(_stochastic(highs, lows, closes), 4),
        "atr_norm":   round(_atr_norm(highs, lows, closes), 4),
        "ret_1":      round(_ret(closes, 1),  4),
        "ret_5":      round(_ret(closes, 5),  4),
        "ret_10":     round(_ret(closes, 10), 4),
        "vol_ratio":  round(_vol_ratio(volumes), 4),
        "vwap_dev":   round(_vwap_dev(closes, volumes), 4),
        "pcr":        round(min(float(pcr or 1.0), 3.0), 4),
        "iv":         round(min(float(iv  or 20.0), 120.0), 4),
    }


def to_vector(f: dict) -> list:
    return [f[k] for k in FEATURE_NAMES]


# ── Ensemble model ─────────────────────────────────────────────────────

class ProMLEngine:
    """
    Per-symbol × per-timeframe ensemble.
    Trains / retrains automatically once MIN_TRAIN_SAMPLES outcomes accumulate.
    """

    def __init__(self, symbol: str, tf_mins: int):
        self.symbol  = symbol
        self.tf_mins = tf_mins
        self._X: list[list] = []
        self._y: list[int]  = []
        self._model   = None
        self._scaler  = None
        self._trained = False
        self._model_path  = os.path.join(MODELS_DIR, f"{symbol}_{tf_mins}_pro_model.pkl")
        self._scaler_path = os.path.join(MODELS_DIR, f"{symbol}_{tf_mins}_pro_scaler.pkl")
        self._load()

    # ── Persistence ───────────────────────────────────────────────────

    def _load(self):
        if not _HAS_SKLEARN:
            return
        try:
            self._model  = joblib.load(self._model_path)
            self._scaler = joblib.load(self._scaler_path)
            self._trained = True
            log.info(f"[ML] Loaded {self.symbol} {self.tf_mins}m")
        except Exception:
            self._model   = self._build()
            self._scaler  = StandardScaler()
            self._trained = False

    def _save(self):
        if not (_HAS_SKLEARN and self._model and self._scaler):
            return
        try:
            joblib.dump(self._model,  self._model_path)
            joblib.dump(self._scaler, self._scaler_path)
        except Exception as e:
            log.warning(f"[ML] Save error: {e}")

    def _build(self):
        if not _HAS_SKLEARN:
            return None
        rf = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_split=4,
            class_weight="balanced", random_state=42, n_jobs=-1,
        )
        if _HAS_XGB:
            gb = xgb.XGBClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                use_label_encoder=False, eval_metric="mlogloss",
                random_state=42, n_jobs=-1,
            )
        else:
            gb = GradientBoostingClassifier(
                n_estimators=150, max_depth=4, learning_rate=0.05, random_state=42,
            )
        # Soft-voting ensemble
        from sklearn.ensemble import VotingClassifier
        return VotingClassifier(
            estimators=[("rf", rf), ("gb", gb)], voting="soft"
        )

    # ── Online learning ───────────────────────────────────────────────

    def record_outcome(self, features: dict, outcome: str):
        """Call this when a tip's outcome (WIN/LOSS/EXPIRED) is known."""
        label_map = {"WIN": BUY, "LOSS": SELL, "EXPIRED": NEUTRAL}
        label = label_map.get(outcome)
        if label is None:
            return
        self._X.append(to_vector(features))
        self._y.append(label)
        if len(self._X) >= MIN_TRAIN_SAMPLES:
            self._retrain()

    def _retrain(self):
        if not _HAS_SKLEARN or len(self._X) < MIN_TRAIN_SAMPLES:
            return
        try:
            X = np.array(self._X)
            y = np.array(self._y)
            if self._scaler is None:
                self._scaler = StandardScaler()
            self._scaler.fit(X)
            Xs = self._scaler.transform(X)
            if self._model is None:
                self._model = self._build()
            self._model.fit(Xs, y)
            self._trained = True
            self._save()
            log.info(f"[ML] Retrained {self.symbol} {self.tf_mins}m on {len(X)} samples")
        except Exception as e:
            log.error(f"[ML] Retrain error: {e}")

    # ── Prediction ────────────────────────────────────────────────────

    def predict(self, features: dict) -> dict:
        if _HAS_SKLEARN and self._trained and self._model and self._scaler:
            return self._ml_predict(features)
        return self._rule_predict(features)

    def _ml_predict(self, features: dict) -> dict:
        try:
            X  = np.array([to_vector(features)])
            Xs = self._scaler.transform(X)
            proba   = self._model.predict_proba(Xs)[0]
            classes = list(self._model.classes_)

            def p(label):
                return proba[classes.index(label)] if label in classes else 1 / 3

            buy_p  = p(BUY)
            sell_p = p(SELL)
            neu_p  = p(NEUTRAL)

            best = max(buy_p, sell_p, neu_p)
            if buy_p  == best: direction, conf = "BUY",     buy_p
            elif sell_p == best: direction, conf = "SELL",   sell_p
            else:                direction, conf = "NEUTRAL", neu_p

            return {
                "direction":  direction,
                "confidence": min(95, max(35, int(conf * 100))),
                "ml_score":   round(buy_p - sell_p, 4),
                "model":      "RF+XGB Ensemble" if _HAS_XGB else "RF+GB Ensemble",
                "trained":    True,
                "proba":      {"buy": round(buy_p, 3), "sell": round(sell_p, 3), "neutral": round(neu_p, 3)},
            }
        except Exception as e:
            log.error(f"[ML] Predict error: {e}")
            return self._rule_predict(features)

    def _rule_predict(self, features: dict) -> dict:
        """Deterministic multi-factor scoring — active before enough training data."""
        score = 0.0

        ema_s = features.get("ema_signal", 0)
        score += max(-2, min(2, ema_s * 8))

        rsi = features.get("rsi", 50)
        if   rsi < 28: score += 2.2
        elif rsi < 42: score += 1.0
        elif rsi > 72: score -= 2.2
        elif rsi > 58: score -= 1.0

        macd = features.get("macd_hist", 0)
        score += max(-1.5, min(1.5, macd * 18))

        bb = features.get("bb_pos", 0.5)
        if   bb < 0.15: score += 1.8
        elif bb > 0.85: score -= 1.8

        st = features.get("stoch", 50)
        if   st < 20: score += 1.2
        elif st > 80: score -= 1.2

        r1  = features.get("ret_1",  0)
        r5  = features.get("ret_5",  0)
        r10 = features.get("ret_10", 0)
        score += max(-1, min(1, r1 * 4))
        score += max(-0.8, min(0.8, r5 * 1.5))
        score += max(-0.5, min(0.5, r10 * 0.8))

        pcr = features.get("pcr", 1.0)
        if   pcr > 1.35: score += 1.2
        elif pcr < 0.65: score -= 1.2

        vwap = features.get("vwap_dev", 0)
        score += max(-0.8, min(0.8, -vwap * 0.3))

        vr = features.get("vol_ratio", 1.0)
        if vr > 1.8:
            score *= 1.15   # amplify on high volume

        # Sigmoid → probability
        norm = max(-8, min(8, score))
        buy_p = 1 / (1 + math.exp(-norm * 0.38))
        sel_p = 1 - buy_p

        if   norm >  1.8: direction, conf = "BUY",     buy_p
        elif norm < -1.8: direction, conf = "SELL",    sel_p
        else:             direction, conf = "NEUTRAL", 0.5

        return {
            "direction":  direction,
            "confidence": min(93, max(38, int(conf * 100))),
            "ml_score":   round(norm / 8, 4),
            "model":      "Multi-Factor Rules (Pre-Training)",
            "trained":    False,
            "proba":      {"buy": round(buy_p, 3), "sell": round(sel_p, 3), "neutral": 0.0},
        }


# ── Registry ───────────────────────────────────────────────────────────

_registry: dict[tuple, ProMLEngine] = {}


def get_engine(symbol: str, tf_mins: int) -> ProMLEngine:
    key = (symbol, tf_mins)
    if key not in _registry:
        _registry[key] = ProMLEngine(symbol, tf_mins)
    return _registry[key]
