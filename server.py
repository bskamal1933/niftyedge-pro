"""
NiftyEdge Pro v3 — Real-Time NSE Server
=========================================
• Real-time SSE streaming (~3s poll)
• Multi-timeframe candle engine
• Advanced 7-factor signal model (PCR + OI + Greeks + Statistics + ML-like scoring)
• TIP LOGGER: logs every tip with entry price, target, SL
• ACCURACY TRACKER: monitors outcomes, marks WIN/LOSS/PENDING
• Adaptive weight system: learns from past outcomes to improve accuracy
• SQLite database for persistent tip history

SETUP:
  pip install flask requests flask-cors

RUN:
  python server.py
"""

from flask import Flask, jsonify, Response, request, send_file
from flask_cors import CORS
import requests, json, time, threading, math, logging, sqlite3, os, shutil
from datetime import datetime, timezone, timedelta, date as _date
from collections import deque

logging.getLogger('werkzeug').setLevel(logging.WARNING)

try:
    from nsepython import nse_optionchain_scrapper as _nse_scrapper
    _HAS_NSEPY = True
except ImportError:
    _HAS_NSEPY = False

try:
    from playwright.sync_api import sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

try:
    from curl_cffi import requests as cf_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

app  = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL = 3
CANDLE_MINS   = [1, 10, 20, 30, 60, 180]
SYMBOLS       = ['NIFTY', 'BANKNIFTY']
MAX_CANDLES   = 300
DB_PATH       = os.environ.get("DB_PATH", os.path.join(BASE_DIR, 'niftyedge_tips.db'))
CACHE_DIR     = os.environ.get("CACHE_DIR", os.path.join(BASE_DIR, 'cache'))

# Allow overriding public URL / port via environment for deployments (e.g. pykt.in)
PORT = int(os.environ.get("PORT", "5000"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", f"http://localhost:{PORT}")

# Market session boundaries (IST)
_MKT_OPEN  = (9,  15)
_MKT_CLOSE = (15, 30)
_MKT_EXP   = (15, 35)   # after this → session CLOSED

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
    "DNT": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

NSE_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# ── Adaptive weights (start equal, improve from logged outcomes) ───────────────
DEFAULT_WEIGHTS = {
    'pcr':        2.0,
    'bull_prob':  2.0,
    'oi_change':  1.5,
    'max_pain':   1.0,
    'trend_ema':  2.0,
    'rsi':        1.0,
    'momentum':   1.0,
    'vwap':       0.5,
    'iv_rank':    0.5,
}

adaptive_weights = {sym: {tf: dict(DEFAULT_WEIGHTS) for tf in CANDLE_MINS} for sym in SYMBOLS}

# ── Option expiry helpers ──────────────────────────────────────────────────────
_MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']

def next_expiry_date(sym):
    """Next weekly expiry: NIFTY=Thursday(3), BANKNIFTY=Wednesday(2) in Python weekday()."""
    now = ist_now()
    target = 2 if sym == 'BANKNIFTY' else 3  # Mon=0 … Sun=6
    days = (target - now.weekday()) % 7
    if days == 0:
        days = 7
    return (now + timedelta(days=days)).replace(hour=15, minute=30, second=0, microsecond=0)

def nse_symbol(sym, strike, opt_type):
    """NSE trading symbol, e.g. NIFTY25APR2524350CE"""
    exp = next_expiry_date(sym)
    dd  = str(exp.day).zfill(2)
    mon = _MONTHS[exp.month - 1]
    yy  = str(exp.year)[-2:]
    return f"{sym}{dd}{mon}{yy}{int(strike)}{opt_type}"

def display_instrument(sym, strike, opt_type):
    """Human-readable label, e.g. NIFTY 25APR25 24350 CE"""
    exp = next_expiry_date(sym)
    dd  = str(exp.day).zfill(2)
    mon = _MONTHS[exp.month - 1]
    yy  = str(exp.year)[-2:]
    return f"{sym} {dd}{mon}{yy} {int(strike)} {opt_type}"

# ── State ─────────────────────────────────────────────────────────────────────
state = {
    sym: {
        "analysis": None,
        "candles":  {m: deque(maxlen=MAX_CANDLES) for m in CANDLE_MINS},
        "oi_hist":  deque(maxlen=500),
        "lock":     threading.Lock(),
    }
    for sym in SYMBOLS
}

subscribers = []; subs_lock = threading.Lock()
session = requests.Session()

# Persistent signals: track active direction+expiry per (sym, tf) so countdown doesn't reset every poll
_active_signals = {}  # {(sym, tf_mins): {"direction": str, "expiry_at": str_iso}}

# NSE auto-recovery state
_nse_fail_counts  = {sym: 0 for sym in ['NIFTY','BANKNIFTY']}
_recovery_lock    = threading.Lock()
_recovery_active  = False
_nse_source_label = "requests"   # shown in /api/debug

# Demo / sample-data mode
_demo_mode    = False
_demo_prices  = {'NIFTY': 24200.0, 'BANKNIFTY': 52000.0}
_demo_trend   = {'NIFTY': 1, 'BANKNIFTY': 1}     # +1 up, -1 down
_demo_ticks   = {'NIFTY': 0, 'BANKNIFTY': 0}      # poll counter per symbol
_demo_regime  = {'NIFTY': 'bull', 'BANKNIFTY': 'bull'}  # 'bull' | 'bear'
_REGIME_FLIP_EVERY = 25  # polls (~75 s) before flipping regime

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tips (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol      TEXT,
        timeframe   TEXT,
        tf_mins     INTEGER,
        direction   TEXT,
        instrument  TEXT,
        strike      REAL,
        opt_type    TEXT,
        entry       REAL,
        target      REAL,
        sl          REAL,
        rr          REAL,
        confidence  INTEGER,
        score       REAL,
        bull_score  REAL,
        spot_at_tip REAL,
        pcr         REAL,
        iv          REAL,
        rationale   TEXT,
        created_at  TEXT,
        expiry_time TEXT,
        outcome     TEXT DEFAULT 'PENDING',
        exit_price  REAL,
        exit_time   TEXT,
        pnl_pct     REAL,
        notes       TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS accuracy_stats (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol      TEXT,
        timeframe   TEXT,
        direction   TEXT,
        win_count   INTEGER DEFAULT 0,
        loss_count  INTEGER DEFAULT 0,
        total       INTEGER DEFAULT 0,
        avg_pnl     REAL DEFAULT 0,
        updated_at  TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS weight_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol     TEXT,
        tf_mins    INTEGER,
        weights    TEXT,
        accuracy   REAL,
        updated_at TEXT
    )''')
    conn.commit()
    # Migration: add notes column if missing (for demo/source tagging)
    try: conn.execute("ALTER TABLE tips ADD COLUMN notes TEXT")
    except: pass
    conn.commit(); conn.close()
    print(f"  [DB] Database ready: {DB_PATH}")

def log_tip(tip: dict, notes: str = None):
    """Insert a new tip into the DB."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    sym, tf_mins = tip['symbol'], tip['tf_mins']
    # Dedup: skip if same sym+tf+direction logged in last tf_mins (prevent poll duplicates)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=tf_mins)).isoformat()
    existing = c.execute(
        "SELECT id FROM tips WHERE symbol=? AND tf_mins=? AND direction=? AND created_at>?",
        (sym, tf_mins, tip['direction'], cutoff)
    ).fetchone()
    if existing:
        conn.close(); return None

    expiry = compute_expiry(tf_mins)
    row_id = c.execute('''INSERT INTO tips
        (symbol,timeframe,tf_mins,direction,instrument,strike,opt_type,
         entry,target,sl,rr,confidence,score,bull_score,spot_at_tip,
         pcr,iv,rationale,created_at,expiry_time,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (sym, tip['timeframe'], tf_mins, tip['direction'],
         tip['instrument'], tip['strike'], tip['opt_type'],
         tip['entry'], tip['target'], tip['sl'], tip['rr'],
         tip['confidence'], tip['score'], tip['bull_score_pct'],
         tip.get('spot_at_tip', 0), tip.get('pcr', 0), tip.get('iv', 0),
         tip['rationale'], datetime.now(timezone.utc).isoformat(), expiry, notes)
    ).lastrowid
    conn.commit(); conn.close()
    return row_id

def compute_expiry(tf_mins: int) -> str:
    """Set expiry time = now + timeframe duration."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=tf_mins)
    return exp.isoformat()

def resolve_pending_tips(current_prices: dict):
    """Check pending tips and mark WIN/LOSS based on current spot prices."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    pending = c.execute(
        "SELECT id,symbol,direction,entry,target,sl,opt_type,strike,expiry_time,tf_mins "
        "FROM tips WHERE outcome='PENDING'"
    ).fetchall()

    for row in pending:
        tip_id, sym, direction, entry, target, sl, opt_type, strike, expiry_time, tf_mins = row
        if sym not in current_prices: continue

        spot = current_prices[sym]
        analysis = state[sym]['analysis']
        if not analysis: continue

        # Get current option price from chain
        current_opt_price = get_option_ltp(analysis, strike, opt_type)

        # Check expiry
        try:
            exp_dt = datetime.fromisoformat(expiry_time)
            expired = datetime.now(timezone.utc) > exp_dt
        except:
            expired = False

        outcome = None; pnl_pct = None; exit_price = current_opt_price

        if entry and entry > 0 and current_opt_price > 0:
            # Only check WIN/LOSS when we have a valid current price
            if current_opt_price >= target:
                outcome = 'WIN'; pnl_pct = round((target - entry) / entry * 100, 2)
            elif current_opt_price <= sl:
                outcome = 'LOSS'; pnl_pct = round((current_opt_price - entry) / entry * 100, 2)

        # EXPIRED check is independent of price availability
        if outcome is None and expired:
            outcome = 'EXPIRED'
            pnl_pct = round((current_opt_price - entry) / entry * 100, 2) if entry and current_opt_price > 0 else 0
            exit_price = current_opt_price if current_opt_price > 0 else entry

        if outcome:
            c.execute(
                "UPDATE tips SET outcome=?,exit_price=?,exit_time=?,pnl_pct=? WHERE id=?",
                (outcome, exit_price, now_iso, pnl_pct, tip_id)
            )
            update_accuracy_stats(c, sym, str(tf_mins)+'m', direction, outcome, pnl_pct or 0)
            if outcome in ('WIN', 'LOSS'):
                adapt_weights(sym, tf_mins, outcome, pnl_pct or 0, c)

    conn.commit(); conn.close()

def get_option_ltp(analysis, strike, opt_type):
    for r in analysis.get('records', []):
        if r.get('strikePrice') == strike:
            return r.get(opt_type, {}).get('lastPrice', 0) or 0
    return 0

def update_accuracy_stats(c, symbol, timeframe, direction, outcome, pnl):
    existing = c.execute(
        "SELECT id,win_count,loss_count,total,avg_pnl FROM accuracy_stats "
        "WHERE symbol=? AND timeframe=? AND direction=?",
        (symbol, timeframe, direction)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        rid, wins, losses, total, avg_p = existing
        wins   += 1 if outcome == 'WIN' else 0
        losses += 1 if outcome == 'LOSS' else 0
        total  += 1
        avg_p   = round((avg_p * (total - 1) + pnl) / total, 2)
        c.execute(
            "UPDATE accuracy_stats SET win_count=?,loss_count=?,total=?,avg_pnl=?,updated_at=? WHERE id=?",
            (wins, losses, total, avg_p, now, rid)
        )
    else:
        c.execute(
            "INSERT INTO accuracy_stats (symbol,timeframe,direction,win_count,loss_count,total,avg_pnl,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (symbol, timeframe, direction,
             1 if outcome == 'WIN' else 0,
             1 if outcome == 'LOSS' else 0,
             1, pnl, now)
        )

def adapt_weights(sym, tf_mins, outcome, pnl, cursor):
    """Bayesian-inspired weight adaptation: boost factors that led to wins, reduce for losses."""
    w = adaptive_weights[sym][tf_mins]
    lr = 0.05  # learning rate
    sign = 1 if outcome == 'WIN' else -1
    magnitude = min(2.0, abs(pnl) / 10)  # cap adjustment

    # PCR and trend are most reliable → adjust them more
    adjustments = {
        'pcr':       sign * lr * magnitude * 1.2,
        'bull_prob': sign * lr * magnitude * 1.0,
        'oi_change': sign * lr * magnitude * 0.8,
        'max_pain':  sign * lr * magnitude * 0.6,
        'trend_ema': sign * lr * magnitude * 1.0,
        'rsi':       sign * lr * magnitude * 0.7,
        'momentum':  sign * lr * magnitude * 0.8,
    }
    for k, v in adjustments.items():
        w[k] = max(0.1, min(5.0, w[k] + v))

    # Normalize so total = sum of defaults
    total_default = sum(DEFAULT_WEIGHTS.values())
    total_current = sum(w.values())
    scale = total_default / total_current
    for k in w: w[k] = round(w[k] * scale, 3)

    # Save to DB
    cursor.execute(
        "INSERT INTO weight_history (symbol,tf_mins,weights,accuracy,updated_at) VALUES (?,?,?,?,?)",
        (sym, tf_mins, json.dumps(w), 0, datetime.now(timezone.utc).isoformat())
    )

def get_accuracy_report(mode: str = 'live', days: int = 5):
    """Return tips and accuracy stats filtered by mode (live|demo) and date window."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Date cutoff — last N days
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Mode filter: sample → notes IN ('DEMO','SAMPLE'), live → anything else
    if mode == 'demo':
        note_clause = "notes IN ('DEMO','SAMPLE')"
    else:
        note_clause = "(notes IS NULL OR notes NOT IN ('DEMO','SAMPLE'))"

    rows = c.execute(
        "SELECT symbol,timeframe,direction,win_count,loss_count,total,avg_pnl FROM accuracy_stats ORDER BY total DESC"
    ).fetchall()
    report = []
    for r in rows:
        sym, tf, dir_, wins, losses, total, avg_pnl = r
        acc = round(wins / total * 100, 1) if total else 0
        report.append({
            'symbol': sym, 'timeframe': tf, 'direction': dir_,
            'wins': wins, 'losses': losses, 'total': total,
            'accuracy_pct': acc, 'avg_pnl_pct': round(avg_pnl, 2)
        })

    tips = c.execute(
        f"SELECT id,symbol,timeframe,tf_mins,direction,instrument,entry,target,sl,confidence,score,"
        f"spot_at_tip,outcome,exit_price,pnl_pct,created_at,expiry_time,rationale,notes "
        f"FROM tips WHERE {note_clause} AND created_at >= ? "
        f"ORDER BY created_at DESC LIMIT 500",
        (since,)
    ).fetchall()
    tip_list = [dict(zip(
        ['id','symbol','timeframe','tf_mins','direction','instrument','entry','target','sl',
         'confidence','score','spot_at_tip','outcome','exit_price','pnl_pct',
         'created_at','expiry_time','rationale','notes'], t
    )) for t in tips]
    conn.close()
    return {'accuracy': report, 'tips': tip_list, 'mode': mode, 'days': days}

# ── Math / Indicators ──────────────────────────────────────────────────────────
def norm_cdf(x):
    a=[.254829592,-.284496736,1.421413741,-1.453152027,1.061405429]; p=.3275911
    s=1 if x>=0 else -1; x=abs(x)/math.sqrt(2)
    t=1/(1+p*x)
    y=1-(((((a[4]*t+a[3])*t)+a[2])*t+a[1])*t+a[0])*t*math.exp(-x*x)
    return .5*(1+s*y)

def calc_greeks(S,K,T,r,sigma,opt):
    if T<=0: return {"delta":1 if opt=="call" else -1,"gamma":0,"theta":0,"vega":0}
    d1=(math.log(S/K)+(r+.5*sigma**2)*T)/(sigma*math.sqrt(T)); d2=d1-sigma*math.sqrt(T)
    phi=math.exp(-.5*d1**2)/math.sqrt(2*math.pi)
    delta=norm_cdf(d1) if opt=="call" else norm_cdf(d1)-1
    gamma=phi/(S*sigma*math.sqrt(T))
    theta=(-(S*phi*sigma)/(2*math.sqrt(T))+(-r*K*math.exp(-r*T)*norm_cdf(d2) if opt=="call"
           else r*K*math.exp(-r*T)*norm_cdf(-d2)))/365
    vega=S*phi*math.sqrt(T)/100
    return {"delta":round(delta,4),"gamma":round(gamma,6),"theta":round(theta,4),"vega":round(vega,4)}

def analyse_chain(data, symbol):
    records=(data.get("filtered") or {}).get("data") or data.get("records",{}).get("data",[])
    spot=data.get("records",{}).get("underlyingValue",0)
    step=50 if symbol=="NIFTY" else 100
    atm=round(spot/step)*step
    tcoi=tpoi=tcchg=tpchg=mco=mpo=0
    mcs=mps=atm; atm_iv=15.0; pain={}
    for r in records:
        s=r.get("strikePrice",0); ce=r.get("CE",{}); pe=r.get("PE",{})
        co=ce.get("openInterest",0); po=pe.get("openInterest",0)
        tcoi+=co; tpoi+=po
        tcchg+=ce.get("changeinOpenInterest",0); tpchg+=pe.get("changeinOpenInterest",0)
        if co>mco: mco=co; mcs=s
        if po>mpo: mpo=po; mps=s
        if s==atm and ce.get("impliedVolatility"): atm_iv=ce["impliedVolatility"]
        for r2 in records:
            s2=r2.get("strikePrice",0)
            pain[s]=pain.get(s,0)+(max(0,s-s2)*co)+(max(0,s2-s)*po)
    mp=min(pain,key=pain.get) if pain else atm
    pcr=tpoi/tcoi if tcoi else 1.0
    T=7/365; r_=0.065; sig=atm_iv/100
    std=spot*sig*math.sqrt(T)
    bp=norm_cdf((spot-atm+std*.5)/(std or 1))*100
    return {
        "symbol":symbol,"spot":round(spot,2),"atm":atm,
        "pcr":round(pcr,3),"atm_iv":round(atm_iv,2),
        "total_call_oi":tcoi,"total_put_oi":tpoi,
        "call_chg_pct":round(tcchg/tcoi*100 if tcoi else 0,2),
        "put_chg_pct": round(tpchg/tpoi*100  if tpoi  else 0,2),
        "max_call_strike":mcs,"max_put_strike":mps,"max_pain":mp,
        "greeks_call":calc_greeks(spot,atm,T,r_,sig,"call"),
        "greeks_put": calc_greeks(spot,atm,T,r_,sig,"put"),
        "bull_prob":round(bp,2),"std_move":round(std,2),
        "records":records,"timestamp":datetime.now(timezone.utc).isoformat(),
    }

def candle_stats(dq):
    cl=list(dq)
    n=len(cl)
    if n==0:
        return {"trend":"SIDEWAYS","momentum":0,"volatility":0,
                "ema_fast":0,"ema_slow":0,"rsi":50,"vwap":0,"candle_count":0,
                "atr":0,"bb_upper":0,"bb_lower":0,"stoch_k":50,
                "last_close":0,"prev_close":0,"swing_high":0,"swing_low":0}
    closes=[c["c"] for c in cl]; highs=[c["h"] for c in cl]; lows=[c["l"] for c in cl]

    def ema(d,p):
        k=2/(p+1); e=d[0]
        for v in d[1:]: e=v*k+e*(1-k)
        return e

    ef=ema(closes,min(9,n)); es=ema(closes,min(21,n))

    # Trend: needs ≥2 candles for direction comparison
    if n>=2:
        trend=("BULLISH" if ef>es and closes[-1]>closes[-2]
               else "BEARISH" if ef<es and closes[-1]<closes[-2]
               else "SIDEWAYS")
    else:
        trend="SIDEWAYS"

    # Momentum: % change from first to last close
    mom=((closes[-1]-closes[0])/closes[0]*100) if closes[0] and n>=2 else 0

    # VWAP: typical price average
    vwap=sum((c["h"]+c["l"]+c["c"])/3 for c in cl)/n

    # ATR: needs ≥2 candles; single-candle fallback = H-L
    if n>=2:
        trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,n)]
        atr=sum(trs[-14:])/min(14,len(trs))
    else:
        atr=highs[0]-lows[0]

    # RSI: needs ≥2 candles for gains/losses
    if n>=2:
        gains=[max(0,closes[i]-closes[i-1]) for i in range(1,n)]
        losses=[max(0,-(closes[i]-closes[i-1])) for i in range(1,n)]
        p=min(14,len(gains))
        ag=sum(gains[-p:])/p; al=sum(losses[-p:])/p
        rsi=100-100/(1+(ag/al if al else 100))
    else:
        rsi=50.0

    # Volatility
    if n>=2:
        rets=[(closes[i]-closes[i-1])/closes[i-1] for i in range(1,n) if closes[i-1]]
        avg_r=sum(rets)/len(rets) if rets else 0
        vol=math.sqrt(sum((r-avg_r)**2 for r in rets)/len(rets)) if rets else 0
    else:
        vol=0

    # Bollinger Bands (meaningful from 2+ candles; with 1 upper==lower==close)
    bp=min(20,n)
    bb_mean=sum(closes[-bp:])/bp
    bb_std=math.sqrt(sum((c-bb_mean)**2 for c in closes[-bp:])/bp) if bp>1 else 0
    bb_upper=bb_mean+2*bb_std; bb_lower=bb_mean-2*bb_std

    # Stochastic
    ll=min(lows[-14:]) if n>=14 else min(lows)
    hh=max(highs[-14:]) if n>=14 else max(highs)
    stoch_k=((closes[-1]-ll)/(hh-ll)*100) if hh>ll else 50

    return {
        "trend":trend,"momentum":round(mom,3),"volatility":round(vol*100,4),
        "ema_fast":round(ef,2),"ema_slow":round(es,2),"rsi":round(rsi,2),
        "vwap":round(vwap,2),"candle_count":n,
        "last_close":closes[-1],"prev_close":closes[-2] if n>1 else closes[-1],
        "swing_high":max(highs),"swing_low":min(lows),
        "atr":round(atr,2),"bb_upper":round(bb_upper,2),"bb_lower":round(bb_lower,2),
        "stoch_k":round(stoch_k,2)
    }

def make_tip(sym, tf_mins, analysis):
    tf_label = {1:"1 Min",10:"10 Min",20:"20 Min",30:"30 Min",60:"1 Hour",180:"3 Hour"}.get(tf_mins,"?")
    now_utc = datetime.now(timezone.utc)

    # Yahoo fallback: no real option chain — return a clear no-data tip
    if analysis.get("source") == "yahoo_fallback":
        cst = candle_stats(state[sym]["candles"][tf_mins])
        return {
            "timeframe": tf_label, "tf_mins": tf_mins,
            "direction": "NEUTRAL", "symbol": sym,
            "instrument": None, "nse_symbol": None,
            "entry": None, "target": None, "sl": None, "rr": None,
            "confidence": 0, "score": 0.0, "bull_score_pct": 50.0,
            "spot_at_tip": analysis["spot"], "pcr": None, "iv": None,
            "candle_stats": cst, "greeks": None, "components": {}, "weights": {},
            "rationale": "No option chain data — NSE blocked (Yahoo Finance fallback active)",
            "timestamp": now_utc.isoformat(),
            "expiry_at": (now_utc + timedelta(minutes=tf_mins)).isoformat(),
            "source": "yahoo_fallback",
        }

    a=analysis; step=50 if sym=="NIFTY" else 100
    cst=candle_stats(state[sym]["candles"][tf_mins])
    w=adaptive_weights[sym][tf_mins]

    components = {}  # factor → raw_score for transparency

    # 1. PCR
    pcr=a["pcr"]
    if pcr>1.4:   components['pcr']=+2
    elif pcr>1.2: components['pcr']=+1
    elif pcr>1.0: components['pcr']=+0.5
    elif pcr<0.6: components['pcr']=-2
    elif pcr<0.8: components['pcr']=-1
    else:         components['pcr']=0

    # 2. Bull Probability (Black-Scholes derived)
    bp=a["bull_prob"]
    if bp>65:     components['bull_prob']=+2
    elif bp>58:   components['bull_prob']=+1
    elif bp<35:   components['bull_prob']=-2
    elif bp<42:   components['bull_prob']=-1
    else:         components['bull_prob']=0

    # 3. OI Change (smart money detection)
    ccp=a["call_chg_pct"]; pcp=a["put_chg_pct"]
    if ccp<-3 and pcp<-3:   components['oi_change']=+2   # short covering = bullish
    elif ccp>5 and pcp<0:   components['oi_change']=+1   # call OI adding
    elif pcp>5 and ccp<0:   components['oi_change']=-2   # put OI adding = bearish
    elif ccp<-3 and pcp>3:  components['oi_change']=-1   # call unwinding + put adding
    else:                   components['oi_change']=0

    # 4. Max Pain
    mp_diff=a["max_pain"]-a["spot"]
    if mp_diff>step*2:    components['max_pain']=+1.5
    elif mp_diff>step:    components['max_pain']=+1
    elif mp_diff<-step*2: components['max_pain']=-1.5
    elif mp_diff<-step:   components['max_pain']=-1
    else:                 components['max_pain']=0

    # 5. Trend (EMA cross + candle pattern)
    if cst['trend']=='BULLISH' and cst['ema_fast']>cst['ema_slow']: components['trend_ema']=+2
    elif cst['trend']=='BULLISH':                                    components['trend_ema']=+1
    elif cst['trend']=='BEARISH' and cst['ema_fast']<cst['ema_slow']:components['trend_ema']=-2
    elif cst['trend']=='BEARISH':                                    components['trend_ema']=-1
    else:                                                            components['trend_ema']=0

    # 6. RSI
    rsi=cst['rsi']
    if rsi<30:    components['rsi']=+1.5   # oversold = buy opportunity
    elif rsi<40:  components['rsi']=+0.5
    elif rsi>70:  components['rsi']=-1.5   # overbought = sell opportunity
    elif rsi>60:  components['rsi']=-0.5
    else:         components['rsi']=0

    # 7. Momentum
    mom=cst['momentum']
    if mom>0.5:   components['momentum']=+1
    elif mom>0.2: components['momentum']=+0.5
    elif mom<-0.5:components['momentum']=-1
    elif mom<-0.2:components['momentum']=-0.5
    else:         components['momentum']=0

    # 8. VWAP relation
    spot=a["spot"]; vwap=cst['vwap']
    if vwap>0:
        vwap_diff=(spot-vwap)/vwap*100
        if vwap_diff>0.3:   components['vwap']=+0.5
        elif vwap_diff<-0.3:components['vwap']=-0.5
        else:               components['vwap']=0
    else: components['vwap']=0

    # 9. IV Rank
    iv=a["atm_iv"]
    if iv>25:   components['iv_rank']=-0.5   # high IV → volatility crush risk for buyers
    elif iv<12: components['iv_rank']=+0.5   # low IV → cheap options, buy bias
    else:       components['iv_rank']=0

    # Bonus: Bollinger Band breakout
    bb_bonus=0
    if cst['bb_upper']>0 and spot>cst['bb_upper']:   bb_bonus=+1  # BB breakout up
    elif cst['bb_lower']>0 and spot<cst['bb_lower']: bb_bonus=-1  # BB breakdown

    # Bonus: Stochastic confirmation
    stoch_bonus=0
    sk=cst['stoch_k']
    if sk<20 and components.get('trend_ema',0)>0:    stoch_bonus=+0.5  # stoch oversold + bullish trend
    elif sk>80 and components.get('trend_ema',0)<0:  stoch_bonus=-0.5

    # Weighted score
    weighted_score = sum(components[k]*w.get(k,1) for k in components) + bb_bonus + stoch_bonus
    # Normalize to -14 to +14 range for display
    max_possible = sum(2*w.get(k,1) for k in components) + 1.5
    normalized = (weighted_score/max_possible)*14

    direction="BUY" if normalized>=3 else "SELL" if normalized<=-3 else "NEUTRAL"
    bull_pct=min(99,max(1,(normalized+14)/28*100))

    # ── Strike selection: TF-differentiated, IV + ATR blended ───────────────────
    atm = a["atm"]
    iv  = a["atm_iv"]

    # NEUTRAL direction: break tie with PCR + bull_prob
    if direction == "NEUTRAL":
        opt_type = "CE" if (pcr >= 1.0 and bp >= 50) else "PE"
        direction_for_strike = "BUY" if opt_type == "CE" else "SELL"
    else:
        opt_type = "CE" if direction == "BUY" else "PE"
        direction_for_strike = direction

    # ── Expected move for this TF via Black-Scholes scaling ──────────────────────
    # 375 trading min/day × 252 days = 94,500 min/year
    iv_move_pts = spot * (max(iv, 8.0) / 100) * math.sqrt(tf_mins / 94500)

    # Blend with observed ATR per bar (more reactive to current market volatility)
    atr = cst.get('atr', 0)
    if atr > 0:
        # ATR measures actual per-candle range; expected move ≈ ATR × 0.8 (not sqrt — already 1 bar)
        blended_pts = (iv_move_pts + atr * 0.8) / 2
    else:
        blended_pts = iv_move_pts

    # Convert to step count (strike unit = 50 for NIFTY, 100 for BANKNIFTY)
    raw_steps = blended_pts / step

    # ── TF-specific OTM band guarantees instrument variety across tabs ─────────
    # Each band = (floor, ceiling) in number of steps from ATM
    # Design: short TFs → ATM-ish for high delta; long TFs → deeper OTM for wider R:R
    TF_OTM = {
        1:   (0, 0),   # 1M  — scalp: ATM only (delta ≈ 0.50, max responsiveness)
        10:  (1, 1),   # 10M — quick momentum: always 1-OTM (clean, liquid)
        20:  (1, 2),   # 20M — intraday short: 1-OTM default, 2-OTM if volatile
        30:  (2, 2),   # 30M — intraday mid: always 2-OTM (wider R:R, aligns with ~0.5σ)
        60:  (2, 3),   # 1H  — intraday long: 2-OTM default, 3-OTM if strong expected move
        180: (3, 4),   # 3H  — swing: 3-OTM base, up to 4-OTM for high-vol setups
    }
    lo, hi = TF_OTM.get(tf_mins, (1, 2))
    n_steps = max(lo, min(hi, round(raw_steps)))

    # High IV environment → pull back 1 step (options already expensive; avoid overpaying)
    if iv > 22 and n_steps > lo:
        n_steps -= 1

    # Strong signal confirmation (score ≥ 9) → allow 1 extra step for better R:R
    if abs(normalized) >= 9 and n_steps < hi:
        n_steps += 1

    ideal_strike = (atm + n_steps * step) if direction_for_strike == "BUY" else (atm - n_steps * step)

    # ── Snap to nearest strike that actually exists in the option chain ───────────
    records = a.get('records', [])
    # Only consider OTM-side strikes relative to ATM (correct direction for opt_type)
    if direction_for_strike == "BUY":
        side_recs = [r for r in records if r.get('strikePrice', 0) >= atm]
    else:
        side_recs = [r for r in records if r.get('strikePrice', 0) <= atm]
    # Fall back to all records if no OTM-side ones found
    candidate_pool = side_recs if side_recs else records
    available = sorted(set(r.get('strikePrice', 0) for r in candidate_pool if r.get('strikePrice', 0) > 0))
    if available:
        opt_strike = min(available, key=lambda s: abs(s - ideal_strike))
    else:
        opt_strike = ideal_strike   # no records (synthetic mode): use computed

    # Recalculate n_steps after snap (for rationale label)
    n_steps = abs(round((opt_strike - atm) / step)) if step else n_steps

    # ── LTP from records ──────────────────────────────────────────────────────────
    ltp = 0
    for r in records:
        if r.get('strikePrice') == opt_strike:
            ltp = r.get(opt_type, {}).get('lastPrice', 0) or 0
            break

    # ── Black-Scholes fair-value floor ────────────────────────────────────────────
    # Compute actual DTE (days to expiry) from next weekly expiry
    try:
        exp_dt = next_expiry_date(sym)
        dte = max(0.25, (exp_dt - ist_now()).total_seconds() / 86400)
    except Exception:
        dte = 5.0

    T_bs  = dte / 365
    r_bs  = 0.065
    sig_bs = max(iv, 8.0) / 100
    try:
        d1_bs = (math.log(spot / opt_strike) + (r_bs + 0.5 * sig_bs**2) * T_bs) / (sig_bs * math.sqrt(T_bs))
        d2_bs = d1_bs - sig_bs * math.sqrt(T_bs)
        if opt_type == 'CE':
            bs_price = spot * norm_cdf(d1_bs) - opt_strike * math.exp(-r_bs * T_bs) * norm_cdf(d2_bs)
        else:
            bs_price = opt_strike * math.exp(-r_bs * T_bs) * norm_cdf(-d2_bs) - spot * norm_cdf(-d1_bs)
        bs_price = max(0.05, round(bs_price, 2))
    except Exception:
        bs_price = 0

    # Minimum realistic price per OTM distance (avoid ₹1 deep-OTM mispricing)
    min_floor = max(0.5, bs_price * 0.5)   # at least 50% of BS fair value

    if ltp < min_floor:
        # Recorded price is unrealistically low — use BS fair value
        ltp = round(bs_price, 1) if bs_price > 0 else max(min_floor, 5.0)

    if ltp <= 0:
        ltp = round(bs_price, 1) if bs_price > 0 else 50.0

    # ── R:R map — scalps tight, longer TFs wider ──────────────────────────────────
    rr_map = {1:(1.10,.93), 10:(1.25,.75), 20:(1.40,.70), 30:(1.50,.67), 60:(1.65,.62), 180:(1.85,.58)}
    tm, sm = rr_map.get(tf_mins, (1.4, .70))
    target = round(ltp * tm, 1); sl = round(ltp * sm, 1)
    rr = round((target - ltp) / (ltp - sl), 2) if ltp > sl else 0

    # ── Improved confidence model ─────────────────────────────────────────────
    # Factor alignment: how many of 9 factors agree with direction
    factor_hits = sum(1 for v in components.values()
                      if (v > 0 and direction_for_strike == "BUY") or
                         (v < 0 and direction_for_strike == "SELL"))
    alignment_pct = factor_hits / len(components)
    # Penalize high IV (options expensive → confidence drops for buyers)
    iv_penalty = max(0, (iv - 20) * 0.5)
    # Penalize 1M TF (low predictability)
    tf_penalty = 8 if tf_mins == 1 else 0
    conf = min(93, max(42, int(
        alignment_pct * 55 + abs(normalized) / 14 * 35 + 40 - iv_penalty - tf_penalty
    )))

    tf_label = {1:"1 Min",10:"10 Min",20:"20 Min",30:"30 Min",60:"1 Hour",180:"3 Hour"}.get(tf_mins,"?")
    now_utc = datetime.now(timezone.utc)

    # Expiry logic:
    # - Same direction within window → preserve expiry_at (no reset mid-signal)
    # - Expired → clear the slot so next iteration is a genuinely fresh signal (new expiry_at)
    # - Direction changed / NEUTRAL → always fresh window
    sig_key = (sym, tf_mins)
    prev_sig = _active_signals.get(sig_key, {})
    if direction != "NEUTRAL" and prev_sig.get("direction") == direction and prev_sig.get("expiry_at"):
        try:
            prev_exp = datetime.fromisoformat(prev_sig["expiry_at"])
            if now_utc < prev_exp:
                expiry_at = prev_sig["expiry_at"]   # still within window — preserve
            else:
                # Window elapsed — clear so this becomes a fresh signal on next poll
                _active_signals.pop(sig_key, None)
                expiry_at = (now_utc + timedelta(minutes=tf_mins)).isoformat()
        except Exception:
            expiry_at = (now_utc + timedelta(minutes=tf_mins)).isoformat()
    else:
        expiry_at = (now_utc + timedelta(minutes=tf_mins)).isoformat()

    if direction != "NEUTRAL":
        _active_signals[sig_key] = {"direction": direction, "expiry_at": expiry_at}
    else:
        _active_signals.pop(sig_key, None)

    nse_sym   = nse_symbol(sym, opt_strike, opt_type)
    disp      = display_instrument(sym, opt_strike, opt_type)

    factor_str = " | ".join(f"{k}={v:+.1f}(w={w.get(k,1):.1f})" for k, v in components.items())
    otm_label  = "ATM" if n_steps == 0 else f"{n_steps}-OTM"
    rationale = (
        f"Score={normalized:+.1f}/14 [{direction}] | "
        f"Strike={otm_label} ({n_steps}×{step}pts) | ExpMove=±{blended_pts:.0f}pts | "
        f"PCR={pcr:.3f} | BullProb={bp:.0f}% | "
        f"RSI={rsi:.0f} | Stoch={sk:.0f} | "
        f"EMA9{'>' if cst['ema_fast']>cst['ema_slow'] else '<'}EMA21 | "
        f"ATR={cst['atr']:.1f} | BB={'BREAK' if bb_bonus!=0 else 'IN'} | "
        f"Mom={mom:.2f}% | Trend={cst['trend']} | "
        f"MaxPain={a['max_pain']:,}(Δ={mp_diff:.0f}) | "
        f"IV={iv:.1f}% | 1σ=±{a['std_move']:.0f} | {factor_str}"
    )

    return {
        "timeframe": tf_label, "tf_mins": tf_mins,
        "direction": direction, "symbol": sym,
        "strike": opt_strike, "opt_type": opt_type,
        "instrument": disp,
        "nse_symbol": nse_sym,
        "entry": round(ltp, 1), "target": target, "sl": sl, "rr": rr,
        "confidence": conf, "score": round(normalized, 2), "bull_score_pct": round(bull_pct, 1),
        "rationale": rationale, "candle_stats": cst,
        "greeks": a["greeks_call"] if opt_type == "CE" else a["greeks_put"],
        "components": components, "weights": dict(w),
        "spot_at_tip": spot, "pcr": pcr, "iv": iv,
        "timestamp": now_utc.isoformat(),
        "expiry_at": expiry_at,
        "bb_bonus": bb_bonus, "stoch_bonus": stoch_bonus,
    }

# ── NSE Fetch ─────────────────────────────────────────────────────────────────
_last_nse_error = {}

def _cookies_via_playwright():
    """Open headless Chromium, load NSE pages, return cookies with JS-set values."""
    global session, _nse_source_label
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=NSE_PAGE_HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = ctx.new_page()
        page.goto("https://www.nseindia.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        page.goto("https://www.nseindia.com/option-chain", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        cookies = ctx.cookies()
        browser.close()

    new_sess = requests.Session()
    for c in cookies:
        new_sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    session = new_sess
    _nse_source_label = "playwright"
    print(f"  [{_ts()}] [OK] Playwright: {len(cookies)} cookies → session replaced")

def _cookies_via_requests():
    """Plain HTTP cookie dance — works when Akamai isn't actively blocking."""
    try:
        r1 = session.get("https://www.nseindia.com", headers=NSE_PAGE_HEADERS, timeout=10)
        time.sleep(1.5)
        r2 = session.get("https://www.nseindia.com/option-chain", headers=NSE_PAGE_HEADERS, timeout=10)
        time.sleep(0.5)
        print(f"  [{_ts()}] [OK] Cookies refreshed (home={r1.status_code} oc={r2.status_code} n={len(session.cookies)})")
    except Exception as e:
        print(f"  [{_ts()}] [ERR] Cookie refresh: {e}")

def refresh_cookies():
    if _HAS_PLAYWRIGHT:
        try:
            _cookies_via_playwright()
            return
        except Exception as e:
            print(f"  [{_ts()}] [ERR] Playwright cookie fetch failed: {e} — trying plain requests")
    _cookies_via_requests()

def _auto_recover():
    """Spawn once when consecutive failures hit threshold — resets session & cookies."""
    global session, _recovery_active, _nse_source_label
    with _recovery_lock:
        if _recovery_active:
            return
        _recovery_active = True
    try:
        print(f"  [{_ts()}] ⟳ Auto-recovery: resetting session + fetching fresh cookies")
        session = requests.Session()
        _nse_source_label = "requests"
        refresh_cookies()
        # Reset fail counters so we give it a clean slate
        for k in _nse_fail_counts:
            _nse_fail_counts[k] = 0
        print(f"  [{_ts()}] [OK] Auto-recovery complete")
    except Exception as e:
        print(f"  [{_ts()}] [ERR] Auto-recovery failed: {e}")
    finally:
        _recovery_active = False

def fetch_chain(symbol):
    global _nse_source_label

    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"

    # 1. curl_cffi — Chrome TLS fingerprint bypasses Akamai bot detection
    if _HAS_CURL_CFFI:
        try:
            r = cf_requests.get(url, headers=NSE_HEADERS, impersonate="chrome120", timeout=12)
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                _last_nse_error.pop(symbol, None)
                _nse_fail_counts[symbol] = 0
                _nse_source_label = "curl_cffi"
                return r.json()
        except Exception as e:
            _last_nse_error[symbol] = f"curl_cffi: {e}"

    # 2. nsepython (maintains its own session)
    if _HAS_NSEPY:
        try:
            data = _nse_scrapper(symbol)
            if data and (data.get("records") or data.get("filtered")):
                _last_nse_error.pop(symbol, None)
                _nse_fail_counts[symbol] = 0
                _nse_source_label = "nsepython"
                return data
        except Exception as e:
            _last_nse_error[symbol] = f"nsepython: {e}"

    # 3. Direct session request (playwright-refreshed cookies)
    try:
        r = session.get(url, headers=NSE_HEADERS, timeout=10)
        if r.status_code in (401, 403, 429):
            refresh_cookies()
            time.sleep(2)
            r = session.get(url, headers=NSE_HEADERS, timeout=10)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "json" not in ct:
            _last_nse_error[symbol] = f"Blocked (HTML {r.status_code})"
            _nse_fail_counts[symbol] = _nse_fail_counts.get(symbol, 0) + 1
            if _nse_fail_counts[symbol] >= 3 and not _recovery_active:
                threading.Thread(target=_auto_recover, daemon=True).start()
            return None
        _last_nse_error.pop(symbol, None)
        _nse_fail_counts[symbol] = 0
        return r.json()
    except Exception as e:
        _last_nse_error[symbol] = str(e)
        _nse_fail_counts[symbol] = _nse_fail_counts.get(symbol, 0) + 1
        if _nse_fail_counts[symbol] >= 3 and not _recovery_active:
            threading.Thread(target=_auto_recover, daemon=True).start()
        return None

def _ts(): return datetime.now().strftime('%H:%M:%S')

def ist_now():
    return datetime.now(timezone(timedelta(hours=5,minutes=30)))

def _market_session(now=None):
    """Return session info dict for given (or current) IST datetime."""
    n   = now or ist_now()
    t   = (n.hour, n.minute)
    wd  = n.weekday()           # Mon=0 … Sun=6
    if wd >= 5:                 # weekend
        status = 'CLOSED'
    elif t < _MKT_OPEN:
        status = 'PRE_MARKET'
    elif t > _MKT_EXP:
        status = 'CLOSED'
    else:
        status = 'OPEN'
    return {'status': status, 'ist': n, 'time_tuple': t, 'weekday': wd}

def _fmt_display_date(date_str: str) -> str:
    """'2026-04-17' → '17 Apr'."""
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return f"{d.day} {d.strftime('%b')}"
    except Exception:
        return date_str

# ── Historical snapshot cache (for sample mode) ────────────────────────────────
def save_snapshot(sym: str, analysis: dict):
    """Persist a successful NSE fetch to disk so sample mode can replay it."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        today_str  = ist_now().strftime('%Y-%m-%d')
        today_file = os.path.join(CACHE_DIR, f'{sym}_today.json')
        prev_file  = os.path.join(CACHE_DIR, f'{sym}_prev.json')

        # Rotate: if the stored snapshot is from a previous date, promote → prev
        if os.path.exists(today_file):
            try:
                stored = json.load(open(today_file, 'r', encoding='utf-8'))
                if stored.get('date') != today_str:
                    shutil.copy(today_file, prev_file)
            except Exception:
                pass

        payload = {
            'date':     today_str,
            'saved_at': ist_now().isoformat(),
            'analysis': analysis,
        }
        with open(today_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
    except Exception as e:
        try: print(f"  [WARN] Snapshot save failed for {sym}: {e}")
        except: pass

def load_sample_analysis(sym: str):
    """
    Load real historical data for sample mode.
    - Before 9:15 AM  → previous day snapshot
    - 9:15–15:35      → today's snapshot (live replay)
    - After 15:35 / weekend → today's final snapshot tagged CLOSED
    Returns None if no snapshot exists (caller falls back to synthetic).
    """
    sess    = _market_session()
    use_prev = sess['time_tuple'] < _MKT_OPEN or sess['weekday'] >= 5

    today_file = os.path.join(CACHE_DIR, f'{sym}_today.json')
    prev_file  = os.path.join(CACHE_DIR, f'{sym}_prev.json')
    candidates = [prev_file, today_file] if use_prev else [today_file, prev_file]

    data = None
    for path in candidates:
        if os.path.exists(path):
            try:
                data = json.load(open(path, 'r', encoding='utf-8'))
                break
            except Exception:
                continue

    if not data:
        return None   # no cache — caller falls back to synthetic

    analysis    = dict(data['analysis'])   # shallow copy
    snap_date   = data.get('date', '')
    today_str   = ist_now().strftime('%Y-%m-%d')

    # Determine label
    if snap_date and snap_date < today_str:
        data_label = 'PREV_DAY'
    else:
        data_label = 'TODAY'

    analysis['source']        = 'sample'
    analysis['sample_date']   = snap_date
    analysis['display_date']  = _fmt_display_date(snap_date)
    analysis['data_label']    = data_label
    analysis['market_status'] = sess['status']
    analysis['timestamp']     = datetime.now(timezone.utc).isoformat()
    return analysis

# ── Yahoo Finance fallback (spot price only) ───────────────────────────────────
_YF_SYM = {'NIFTY': '%5ENSEI', 'BANKNIFTY': '%5ENSEBANK'}
_YF_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def fetch_spot_yahoo(symbol):
    try:
        yf = _YF_SYM.get(symbol)
        if not yf: return None
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}?interval=1m&range=1d"
        r = requests.get(url, headers=_YF_HEADERS, timeout=8)
        d = r.json()
        price = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return round(float(price), 2)
    except Exception as e:
        print(f"  [{_ts()}] [ERR] Yahoo {symbol}: {e}")
        return None

def make_synthetic_analysis(symbol: str, spot: float):
    """
    Full synthetic option chain anchored to live Yahoo Finance spot price.
    Used when NSE is blocked so the dashboard stays fully functional.
    BS-priced records, realistic OI/IV — source='live_synthetic' (not 'yahoo_fallback').
    """
    import random as _r
    step   = 50 if symbol == 'NIFTY' else 100
    atm    = round(spot / step) * step
    iv_atm = 15.0 if symbol == 'NIFTY' else 17.0   # typical ATM IV when chain is unavailable

    # Days to expiry for realistic pricing
    try:
        exp_dt = next_expiry_date(symbol)
        dte    = max(0.25, (exp_dt - ist_now()).total_seconds() / 86400)
    except Exception:
        dte = 5.0
    T = dte / 365; r_ = 0.065

    records = []
    base_oi = 500_000 if symbol == 'NIFTY' else 300_000
    strikes = [atm + i * step for i in range(-12, 13)]   # ±12 strikes from ATM

    for s in strikes:
        dist = abs(s - atm) / step           # steps from ATM
        iv_s = iv_atm + dist * 0.35          # volatility smile: OTM IV higher
        sig  = iv_s / 100

        # Black-Scholes fair value
        try:
            d1c = (math.log(spot / s) + (r_ + 0.5*sig**2)*T) / (sig*math.sqrt(T))
            d2c = d1c - sig*math.sqrt(T)
            call_ltp = max(0.05, round(spot*norm_cdf(d1c) - s*math.exp(-r_*T)*norm_cdf(d2c), 1))
            put_ltp  = max(0.05, round(s*math.exp(-r_*T)*norm_cdf(-d2c) - spot*norm_cdf(-d1c), 1))
        except Exception:
            call_ltp = max(0.05, round(spot * 0.005, 1))
            put_ltp  = max(0.05, round(spot * 0.005, 1))

        # OI tapers off far from ATM; neutral bias (PCR ≈ 1.0)
        oi_scale  = max(0.05, 1.0 - dist * 0.08)
        call_oi   = int(base_oi * oi_scale * _r.uniform(0.85, 1.15))
        put_oi    = int(base_oi * oi_scale * _r.uniform(0.85, 1.15))
        call_chg  = int(call_oi * _r.uniform(-0.02, 0.04))
        put_chg   = int(put_oi  * _r.uniform(-0.02, 0.04))

        records.append({
            "strikePrice": s,
            "CE": {"openInterest": call_oi, "changeinOpenInterest": call_chg,
                   "impliedVolatility": round(iv_s, 2), "lastPrice": call_ltp,
                   "totalTradedVolume": int(call_oi * _r.uniform(0.2, 0.5))},
            "PE": {"openInterest": put_oi,  "changeinOpenInterest": put_chg,
                   "impliedVolatility": round(iv_s + _r.uniform(0, 0.4), 2), "lastPrice": put_ltp,
                   "totalTradedVolume": int(put_oi  * _r.uniform(0.2, 0.5))},
        })

    tcoi = sum(r["CE"]["openInterest"] for r in records)
    tpoi = sum(r["PE"]["openInterest"] for r in records)
    pcr  = round(tpoi / tcoi, 3) if tcoi else 1.0
    std_move = round(spot * (iv_atm/100) * math.sqrt(T), 2)
    g_c  = calc_greeks(spot, atm, T, r_, iv_atm/100, "call")
    g_p  = calc_greeks(spot, atm, T, r_, iv_atm/100, "put")
    bp   = round(norm_cdf(0) * 100 + 1.0, 1)   # ≈ 50% (neutral — no chain bias available)

    return {
        "symbol": symbol, "spot": spot, "atm": atm,
        "pcr": pcr, "atm_iv": iv_atm,
        "total_call_oi": tcoi, "total_put_oi": tpoi,
        "call_chg_pct": 0.0, "put_chg_pct": 0.0,
        "max_call_strike": atm + step, "max_put_strike": atm - step, "max_pain": atm,
        "greeks_call": g_c, "greeks_put": g_p,
        "bull_prob": bp, "std_move": std_move,
        "records": records,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "live_synthetic",   # full chain available — NOT blocking
    }

# ── Demo / Sample-data generator ───────────────────────────────────────────────
import random as _rnd

def make_demo_analysis(sym):
    global _demo_prices, _demo_trend, _demo_ticks, _demo_regime
    step = 50 if sym == 'NIFTY' else 100

    # Advance tick counter; flip regime every N polls
    _demo_ticks[sym] += 1
    if _demo_ticks[sym] % _REGIME_FLIP_EVERY == 0:
        _demo_regime[sym] = 'bear' if _demo_regime[sym] == 'bull' else 'bull'
        print(f"  [{_ts()}] [DEMO] {sym} demo regime → {_demo_regime[sym].upper()}")

    regime = _demo_regime[sym]
    bull   = (regime == 'bull')

    # Price random-walk biased by regime
    drift = (_rnd.uniform(0.04, 0.14) if bull else _rnd.uniform(-0.14, -0.04)) * step
    noise = _rnd.uniform(-0.18, 0.18) * step
    spot  = round(_demo_prices[sym] + drift + noise, 2)
    _demo_prices[sym] = max(spot, step * 10)
    atm = round(spot / step) * step

    # Option chain: 7 strikes each side
    strikes = [atm + i * step for i in range(-7, 8)]

    # Regime-biased OI multipliers
    # Bull: put OI >> call OI (support building) → high PCR
    # Bear: call OI >> put OI (resistance building) → low PCR
    call_bias = (0.70, 0.95) if bull else (1.10, 1.40)
    put_bias  = (1.25, 1.60) if bull else (0.65, 0.90)

    records = []
    iv_base = 14.0 + _rnd.uniform(-1, 1)
    for s in strikes:
        dist = abs(s - atm) / step
        oi_base = int(max(500, 9000 - dist * 1100) * _rnd.uniform(0.88, 1.12))
        call_oi = int(oi_base * _rnd.uniform(*call_bias))
        put_oi  = int(oi_base * _rnd.uniform(*put_bias))
        iv = round(iv_base + dist * 1.6 + _rnd.uniform(-0.3, 0.3), 2)
        call_price = round(max(0.5, max(0, spot - s) + iv * 0.07 * _rnd.uniform(0.92, 1.08)), 2)
        put_price  = round(max(0.5, max(0, s - spot) + iv * 0.07 * _rnd.uniform(0.92, 1.08)), 2)
        # OI change: positive (adding) on dominant side
        call_chg = int(call_oi * (_rnd.uniform(0.02, 0.06) if not bull else _rnd.uniform(-0.03, 0.02)))
        put_chg  = int(put_oi  * (_rnd.uniform(0.02, 0.06) if bull     else _rnd.uniform(-0.03, 0.02)))
        records.append({
            "strikePrice": s,
            "CE": {"openInterest": call_oi, "changeinOpenInterest": call_chg,
                   "impliedVolatility": iv, "lastPrice": call_price,
                   "totalTradedVolume": int(call_oi * _rnd.uniform(0.3, 0.7))},
            "PE": {"openInterest": put_oi,  "changeinOpenInterest": put_chg,
                   "impliedVolatility": round(iv + _rnd.uniform(0, 0.6), 2),
                   "lastPrice": put_price,
                   "totalTradedVolume": int(put_oi  * _rnd.uniform(0.3, 0.7))},
        })

    tcoi = sum(r["CE"]["openInterest"] for r in records)
    tpoi = sum(r["PE"]["openInterest"] for r in records)
    pcr  = round(tpoi / tcoi, 3) if tcoi else 1.0
    atm_rec = next((r for r in records if r["strikePrice"] == atm), records[len(records)//2])
    atm_iv  = atm_rec["CE"]["impliedVolatility"]
    std_move = round(spot * (atm_iv / 100) * math.sqrt(7 / 365), 2)

    max_call_strike = max(records, key=lambda r: r["CE"]["openInterest"])["strikePrice"]
    max_put_strike  = max(records, key=lambda r: r["PE"]["openInterest"])["strikePrice"]

    # Bull regime: PCR ~1.4, bull_prob ~72 → scoring gives +6 to +8 → BUY
    # Bear regime: PCR ~0.65, bull_prob ~32 → scoring gives -6 to -8 → SELL
    bull_prob = round(50 + (pcr - 1.0) * 35 + _rnd.uniform(-3, 3), 1)
    bull_prob = max(20.0, min(85.0, bull_prob))

    # OI change pct — MUST align with regime for correct scoring signal:
    #   Bull: calls being added (+6%), puts unwinding (-4%) → score +1
    #   Bear: puts being added (+7%), calls unwinding (-4%) → score -2
    if bull:
        call_chg_pct = round(_rnd.uniform(5.5, 8.0), 2)   # calls adding = bullish
        put_chg_pct  = round(_rnd.uniform(-5.0, -2.5), 2) # puts declining = short covering
    else:
        put_chg_pct  = round(_rnd.uniform(5.5, 8.0), 2)   # puts adding = bearish
        call_chg_pct = round(_rnd.uniform(-5.0, -2.5), 2) # calls declining

    # Max pain: above spot in bull (gravitational pull upward), below in bear
    max_pain = atm + step if bull else atm - step

    return {
        "symbol": sym, "spot": spot, "atm": atm, "pcr": pcr,
        "atm_iv": round(atm_iv, 2), "total_call_oi": tcoi, "total_put_oi": tpoi,
        "call_chg_pct": call_chg_pct, "put_chg_pct": put_chg_pct,
        "max_call_strike": max_call_strike, "max_put_strike": max_put_strike,
        "max_pain": max_pain, "std_move": std_move,
        "greeks_call": {"delta": round(0.50 + _rnd.uniform(-0.03, 0.03), 4),
                        "gamma": round(0.00019 + _rnd.uniform(0, 0.00003), 5),
                        "theta": round(-11.5 + _rnd.uniform(-1.5, 1.5), 3),
                        "vega":  round(0.80 + _rnd.uniform(-0.04, 0.04), 4)},
        "greeks_put":  {"delta": round(-0.50 + _rnd.uniform(-0.03, 0.03), 4),
                        "gamma": round(0.00019 + _rnd.uniform(0, 0.00003), 5),
                        "theta": round(-10.5 + _rnd.uniform(-1.5, 1.5), 3),
                        "vega":  round(0.78 + _rnd.uniform(-0.04, 0.04), 4)},
        "bull_prob": bull_prob, "records": records,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "demo",
    }

# ── Candle builder ─────────────────────────────────────────────────────────────
def candle_bucket(dt,mins):
    tot=dt.hour*60+dt.minute; bs=(tot//mins)*mins
    bh,bm=divmod(bs,60); d=dt.replace(hour=bh,minute=bm,second=0,microsecond=0)
    return d.isoformat()

def update_candles(sym,spot,dt):
    for m in CANDLE_MINS:
        b=candle_bucket(dt,m); c=state[sym]["candles"][m]
        if c and c[-1]["t"]==b:
            x=c[-1]; x["h"]=max(x["h"],spot); x["l"]=min(x["l"],spot); x["c"]=spot; x["n"]+=1
        else:
            c.append({"t":b,"o":spot,"h":spot,"l":spot,"c":spot,"n":1})

# ── Poll loop ──────────────────────────────────────────────────────────────────
def poll_loop():
    print(f"  [{_ts()}] >> Poll loop started")
    cookie_timer=0; resolve_timer=0
    current_prices={}
    _src={}  # track last source per symbol to log transitions only
    while True:
        cookie_timer+=1; resolve_timer+=1
        if cookie_timer>=int(300/POLL_INTERVAL): refresh_cookies(); cookie_timer=0
        if resolve_timer>=int(30/POLL_INTERVAL):
            resolve_pending_tips(current_prices); resolve_timer=0

        for sym in SYMBOLS:
            try:
                if _demo_mode:
                    analysis = load_sample_analysis(sym)
                    if analysis is None:
                        analysis = make_demo_analysis(sym)
                        _sample_src = 'synthetic'
                    else:
                        _sample_src = f"{analysis.get('data_label','?')} {analysis.get('display_date','')}"
                    if _src.get(sym) != 'sample':
                        mst = analysis.get('market_status', '')
                        print(f"  [{_ts()}] [SAMPLE] {sym}: {_sample_src} ({mst})")
                        _src[sym] = 'sample'

                elif (raw := fetch_chain(sym)):
                    analysis = analyse_chain(raw, sym)
                    save_snapshot(sym, analysis)
                    if _src.get(sym) != 'nse':
                        print(f"  [{_ts()}] [OK] {sym}: NSE data restored")
                        _src[sym] = 'nse'
                else:
                    spot = fetch_spot_yahoo(sym)
                    if not spot:
                        if _src.get(sym) != 'fail':
                            print(f"  [{_ts()}] [ERR] {sym}: both NSE and Yahoo failed")
                            _src[sym] = 'fail'
                        continue
                    analysis = make_synthetic_analysis(sym, spot)
                    if _src.get(sym) != 'yahoo':
                        print(f"  [{_ts()}] [WARN] {sym}: NSE blocked — using Yahoo Finance fallback")
                        _src[sym] = 'yahoo'

                now = ist_now()
                current_prices[sym] = analysis["spot"]
                with state[sym]["lock"]:
                    state[sym]["analysis"] = analysis
                    state[sym]["oi_hist"].append((now.isoformat(), analysis["total_call_oi"], analysis["total_put_oi"]))
                    update_candles(sym, analysis["spot"], now)

                tips = {str(tf): make_tip(sym, tf, analysis) for tf in CANDLE_MINS}

                src = analysis.get("source", "nse")
                if src in ("demo", "sample"):
                    for tf, tip in tips.items():
                        if tip["direction"] != "NEUTRAL":
                            log_tip(tip, notes="SAMPLE")
                elif src != "yahoo_fallback":
                    for tf, tip in tips.items():
                        if tip["direction"] != "NEUTRAL" and int(tf) >= 10:
                            log_tip(tip)

                payload = {
                    "type": "update", "symbol": sym, "analysis": analysis, "tips": tips,
                    "candles": {str(tf): list(state[sym]["candles"][tf])[-60:] for tf in CANDLE_MINS},
                    "oi_hist": list(state[sym]["oi_hist"])[-120:],
                }
                msg = f"data: {json.dumps(payload)}\n\n"
                with subs_lock:
                    dead = []
                    for q in subscribers:
                        try: q.append(msg)
                        except: dead.append(q)
                    for q in dead:
                        try: subscribers.remove(q)
                        except: pass

            except Exception as e:
                print(f"  [ERR] {sym} poll error: {e}")

        time.sleep(POLL_INTERVAL)

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    html = os.path.join(BASE_DIR, 'nifty_options_dashboard.html')
    if os.path.exists(html):
        return send_file(html)
    return jsonify({"status":"NiftyEdge Pro v3","db":DB_PATH})

@app.route("/api/status")
def api_status():
    _ensure_poll_running()  # auto-restart if thread died
    return jsonify({"status":"ok","version":"3.0","time_ist":ist_now().strftime("%H:%M:%S"),
                    "subscribers":len(subscribers),"demo_mode":_demo_mode})

@app.route("/api/demo/toggle", methods=["POST"])
def demo_toggle():
    global _demo_mode, _active_signals
    _demo_mode = not _demo_mode
    _active_signals.clear()
    label = "SAMPLE" if _demo_mode else "LIVE"
    try:
        print(f"  [{_ts()}] Demo mode -> {label}")
    except Exception:
        pass
    return jsonify({"demo_mode": _demo_mode, "mode": label})

@app.route("/api/snapshot/<symbol>")
def snapshot(symbol):
    sym=symbol.upper()
    if sym not in SYMBOLS: return jsonify({"error":f"Unknown {sym}"}),400
    with state[sym]["lock"]:
        a=state[sym]["analysis"]
        candles_snap={str(tf):list(state[sym]["candles"][tf])[-60:] for tf in CANDLE_MINS}
    if not a:
        # Server started but hasn't polled NSE yet — tell client to wait for SSE
        return jsonify({"error":"Polling in progress, data arriving via stream shortly"}),503
    tips={str(tf):make_tip(sym,tf,a) for tf in CANDLE_MINS}
    return jsonify({"analysis":a,"tips":tips,"candles":candles_snap})

@app.route("/api/debug")
def debug():
    info = {}
    for sym in SYMBOLS:
        with state[sym]["lock"]:
            a = state[sym]["analysis"]
        info[sym] = {
            "has_data": a is not None,
            "spot": a["spot"] if a else None,
            "source": a.get("source", "nse") if a else None,
            "fail_count": _nse_fail_counts.get(sym, 0),
            "last_error": _last_nse_error.get(sym),
        }
    import threading as _th
    poll_alive = any(t.name == "poll_loop" and t.is_alive() for t in _th.enumerate())
    return jsonify({
        "subscribers": len(subscribers),
        "session_cookies": len(session.cookies),
        "nse_source": _nse_source_label,
        "recovery_active": _recovery_active,
        "playwright": _HAS_PLAYWRIGHT,
        "nsepython": _HAS_NSEPY,
        "curl_cffi": _HAS_CURL_CFFI,
        "poll_running": poll_alive,
        "symbols": info,
    })

@app.route("/api/diagnose")
def diagnose():
    result = {"session_cookies": len(session.cookies), "nse": {}, "yahoo": {}}
    for sym in SYMBOLS:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={sym}"
        try:
            r = session.get(url, headers=NSE_HEADERS, timeout=8)
            ct = r.headers.get("Content-Type", "")
            result["nse"][sym] = {
                "status": r.status_code,
                "content_type": ct,
                "ok": "json" in ct and r.status_code == 200,
                "preview": r.text[:120] if "json" not in ct else "JSON OK",
            }
        except Exception as e:
            result["nse"][sym] = {"status": 0, "ok": False, "error": str(e)}
        spot = fetch_spot_yahoo(sym)
        result["yahoo"][sym] = {"spot": spot, "ok": spot is not None}
    return jsonify(result)

@app.route("/api/accuracy")
def accuracy():
    mode = request.args.get('mode', 'live')          # 'live' or 'demo'
    days = int(request.args.get('days', 5))
    days = max(1, min(days, 90))                     # clamp 1–90 days
    return jsonify(get_accuracy_report(mode, days))

@app.route("/api/weights")
def weights():
    return jsonify({sym:{str(tf):adaptive_weights[sym][tf] for tf in CANDLE_MINS} for sym in SYMBOLS})

@app.route("/api/tip/<int:tip_id>/outcome", methods=['POST'])
def manual_outcome(tip_id):
    """Allow manual outcome override from dashboard."""
    data=request.json
    outcome=data.get('outcome','').upper()
    if outcome not in ('WIN','LOSS','EXPIRED','SKIP'): return jsonify({"error":"Invalid outcome"}),400
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    row=c.execute("SELECT entry,direction,symbol,tf_mins FROM tips WHERE id=?", (tip_id,)).fetchone()
    if not row: conn.close(); return jsonify({"error":"Not found"}),404
    entry,direction,sym,tf_mins=row
    exit_price=data.get('exit_price',0)
    pnl=round((exit_price-entry)/entry*100,2) if entry and exit_price else 0
    c.execute("UPDATE tips SET outcome=?,exit_price=?,exit_time=?,pnl_pct=?,notes=? WHERE id=?",
              (outcome,exit_price,datetime.now(timezone.utc).isoformat(),pnl,data.get('notes',''),tip_id))
    if outcome in('WIN','LOSS'):
        update_accuracy_stats(c,sym,str(tf_mins)+'m',direction,outcome,pnl)
        adapt_weights(sym,tf_mins,outcome,pnl,c)
    conn.commit(); conn.close()
    return jsonify({"status":"updated","pnl_pct":pnl})

@app.route("/stream")
def stream():
    q=deque(maxlen=100)
    with subs_lock: subscribers.append(q)
    def gen():
        for sym in SYMBOLS:
            with state[sym]["lock"]: a=state[sym]["analysis"]
            if a:
                tips={str(tf):make_tip(sym,tf,a) for tf in CANDLE_MINS}
                payload={"type":"update","symbol":sym,"analysis":a,"tips":tips,
                         "candles":{str(tf):list(state[sym]["candles"][tf])[-60:] for tf in CANDLE_MINS},
                         "oi_hist":list(state[sym]["oi_hist"])[-120:]}
                yield f"data: {json.dumps(payload)}\n\n"
        yield 'data: {"type":"connected"}\n\n'
        try:
            while True:
                if q: yield q.popleft()
                else: yield f'data:{{"type":"ping","ts":{int(time.time())}}}\n\n'; time.sleep(1)
        except GeneratorExit:
            with subs_lock:
                try: subscribers.remove(q)
                except: pass
    return Response(gen(),mimetype="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ── Startup ────────────────────────────────────────────────────────────────────
def _startup():
    try:
        init_db()
    except Exception as e:
        print(f"  [WARN] DB init failed ({e}) — running without persistence")
    # Refresh cookies in background — Playwright can hang for 60s+ if NSE blocks
    # the cloud IP, which would prevent the poll thread from ever starting.
    threading.Thread(target=refresh_cookies, daemon=True).start()
    try:
        conn=sqlite3.connect(DB_PATH)
        for sym in SYMBOLS:
            for tf in CANDLE_MINS:
                row=conn.execute("SELECT weights FROM weight_history WHERE symbol=? AND tf_mins=? ORDER BY id DESC LIMIT 1",(sym,tf)).fetchone()
                if row:
                    saved=json.loads(row[0])
                    adaptive_weights[sym][tf].update(saved)
                    print(f"  [DB] Loaded weights for {sym} {tf}m")
        conn.close()
    except: pass
    t = threading.Thread(target=poll_loop, daemon=True, name="poll_loop")
    t.start()
    print(f"  [OK] Poll loop started (thread id={t.ident})")

def _ensure_poll_running():
    """Start poll thread if not already alive (idempotent)."""
    import threading as _th
    if not any(t.name == "poll_loop" and t.is_alive() for t in _th.enumerate()):
        t = threading.Thread(target=poll_loop, daemon=True, name="poll_loop")
        t.start()
        print(f"  [RESTART] Poll loop restarted (thread id={t.ident})")
        return True
    return False

@app.route("/api/restart-poll", methods=["POST"])
def restart_poll():
    restarted = _ensure_poll_running()
    return jsonify({"restarted": restarted, "msg": "Poll loop restarted" if restarted else "Already running"})

# When imported by gunicorn, run startup in the worker process
if __name__ != "__main__":
    _startup()

# ── Main ───────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    print(); print("="*58)
    print("   NiftyEdge Pro v3 — Real-Time + Tip Logger")
    print("="*58)
    _startup()
    print(f"\n  [OK] Stream : {PUBLIC_URL}/stream")
    print(f"  [OK] Accuracy: {PUBLIC_URL}/api/accuracy")
    print(f"  [OK] DB      : {DB_PATH}")
    print(f"  Open dashboard.html in Chrome/Edge")
    print("  Press CTRL+C to stop."); print("="*58); print()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
