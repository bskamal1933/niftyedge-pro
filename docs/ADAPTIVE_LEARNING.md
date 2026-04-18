# NiftyEdge Pro — Adaptive Learning System

## Overview

NiftyEdge Pro gets smarter with every trade it observes. The adaptive learning system adjusts the importance (weight) of each signal factor based on whether past tips that relied heavily on that factor resulted in wins or losses.

---

## How Tips Are Logged

Every BUY or SELL signal (non-NEUTRAL) is written to `niftyedge_tips.db` (SQLite) the moment it's generated, with a 60-second deduplication window to avoid spam.

Each logged tip stores:

| Field | Description |
|---|---|
| `instrument` | e.g., `NIFTY 24450 CE` |
| `entry` | Option LTP at time of signal |
| `target` | Entry × target multiplier |
| `sl` | Entry × SL multiplier |
| `confidence` | Confidence % |
| `score` | Weighted signal score (±14) |
| `spot_at_tip` | Underlying spot price |
| `pcr` | PCR at time of signal |
| `iv` | ATM IV at time of signal |
| `rationale` | Full factor breakdown string |
| `created_at` | UTC timestamp |
| `expiry_time` | created_at + timeframe duration |
| `outcome` | PENDING / WIN / LOSS / EXPIRED |

---

## Automatic Outcome Resolution

The server runs an outcome checker **every 30 seconds** against all `PENDING` tips.

```python
if current_option_price >= target:
    outcome = 'WIN'
    pnl_pct = (target - entry) / entry × 100

elif current_option_price <= sl:
    outcome = 'LOSS'
    pnl_pct = (current_price - entry) / entry × 100

elif now > expiry_time:
    outcome = 'EXPIRED'
    pnl_pct = (current_price - entry) / entry × 100
```

Resolution only works during market hours when live option prices are available.

---

## Manual Override

You can also mark outcomes manually from the **Tip Log** tab in the dashboard:
1. Find any PENDING tip in the table
2. Click **Win** or **Loss**
3. Enter the exit price
4. P&L% is calculated automatically

This is useful for:
- Marking trades you actually took
- Correcting auto-resolved outcomes
- Logging trades during low-liquidity periods

---

## Weight Update Algorithm

When a tip is resolved as WIN or LOSS, the adaptive weight for each contributing factor is updated:

```python
learning_rate = 0.05
magnitude     = min(2.0, abs(pnl_pct) / 10)
sign          = +1 if outcome == 'WIN' else -1

for each factor:
    weight += sign × learning_rate × magnitude × factor_multiplier
    weight  = clamp(weight, min=0.1, max=5.0)

# Re-normalize so total weight sum stays stable
scale = sum(default_weights) / sum(current_weights)
for each factor:
    weight *= scale
```

**Factor multipliers** (some factors are adjusted more aggressively than others):

| Factor | Multiplier |
|---|---|
| PCR | 1.2 |
| Bull Probability | 1.0 |
| OI Change | 0.8 |
| Max Pain | 0.6 |
| EMA Trend | 1.0 |
| RSI | 0.7 |
| Momentum | 0.8 |

---

## What "Learning" Looks Like

**Scenario:** Over 50 trades, the RSI factor is correct 75% of the time but PCR is only correct 45% of the time for NIFTY 10-minute signals.

**Result after learning:**
- RSI weight increases from 1.0 → ~1.6
- PCR weight decreases from 2.0 → ~1.2
- Tips now rely less on PCR and more on RSI for 10-minute signals

**Scenario 2:** In a trending market, EMA trend is very reliable.
- EMA weight increases from 2.0 → ~3.5
- Trending signals get higher confidence scores

---

## Persistence

Weights are saved to the `weight_history` table in `niftyedge_tips.db` after every update.

On server startup, the most recent saved weights for each symbol×timeframe combination are loaded automatically:

```python
# From server.py startup:
row = db.query("SELECT weights FROM weight_history 
                WHERE symbol=? AND tf_mins=? 
                ORDER BY id DESC LIMIT 1")
if row:
    adaptive_weights[sym][tf].update(json.loads(row[0]))
```

This means the model **retains its learning across restarts and updates**.

---

## Viewing Current Weights

In the dashboard, go to the **Weights** tab. Each factor is shown as a bar chart with its current weight value.

Via API:
```
GET https://pykt.in/api/weights
```

Response:
```json
{
  "NIFTY": {
    "10": {
      "pcr": 2.14,
      "bull_prob": 1.87,
      "oi_change": 1.52,
      "max_pain": 0.98,
      "trend_ema": 2.31,
      "rsi": 1.12,
      "momentum": 0.94,
      "vwap": 0.48,
      "iv_rank": 0.52
    }
  }
}
```

---

## Accuracy Stats

The **Accuracy** tab shows win rate, loss rate, and average P&L per symbol × timeframe × direction combination.

Via API:
```
GET https://pykt.in/api/accuracy
```

---

## Resetting the Model

To reset all weights and tip history:
```bash
rm niftyedge_tips.db
```

The database will be recreated fresh on next server start with default weights.

---

## Important Notes

- The model needs **at least 20–30 resolved trades per timeframe** before weights become meaningfully different from defaults
- Early trades with few resolved outcomes will use near-default weights
- The system works best with consistent use over multiple sessions
- Do not delete `niftyedge_tips.db` unless you want to start fresh

---

*See `SIGNAL_MODEL.md` for the complete list of factors and scoring rules.*
