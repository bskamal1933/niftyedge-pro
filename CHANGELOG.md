# Changelog

All notable changes to NiftyEdge Pro are documented here.

---

## [3.0.0] — 2025

### Added
- **Tip Logger** — Every BUY/SELL signal written to SQLite (`niftyedge_tips.db`)
- **Accuracy Tracker** — Auto-resolves WIN/LOSS every 30s; per-timeframe accuracy stats
- **Adaptive Learning** — Factor weights update after each outcome (Bayesian-inspired)
- **9-Factor Signal Model** — Added VWAP, IV Rank, Bollinger Band breakout, Stochastic K
- **Manual Outcome Override** — Mark WIN/LOSS from Tip Log tab with exit price
- **Weight History** — All weight changes persisted to DB and reloaded on restart
- **Windows Desktop App** (`app.py`) — Native window, system tray, toast notifications
- **New Dashboard UI** — Full redesign: Bebas Neue + Outfit + DM Mono fonts, generous spacing
- **5-Tab Navigation** — Live Tips · OI Chain · Tip Log · Accuracy · Weights
- **API: `/api/accuracy`** — Full tip log + accuracy breakdown
- **API: `/api/weights`** — Current adaptive weights per symbol×timeframe
- **API: `/api/tip/{id}/outcome`** — Manual outcome endpoint
- **Comprehensive docs** — SETUP.md, SIGNAL_MODEL.md, ADAPTIVE_LEARNING.md

### Changed
- Candle indicator panel now shows ATR, Bollinger Bands, Stochastic K
- Confidence formula improved to incorporate factor alignment %
- R:R ratios now timeframe-scaled (longer = better R:R)
- Dashboard performance: canvas charts resize properly on window resize

---

## [2.0.0] — 2025

### Added
- Real-time SSE (Server-Sent Events) streaming — zero delay browser push
- Multi-timeframe candle engine (10M/20M/30M/1H/3H)
- All-timeframe comparison panel
- EMA9/EMA21 candlestick charts with VWAP line
- OI flow chart (call vs put OI over time)
- Direction badge (▲/▼) on each timeframe button
- RSI, momentum, EMA cross in candle stats

### Changed
- Replaced 60-second polling with SSE push architecture
- Signal model expanded from 5 to 7 factors

---

## [1.0.0] — 2025

### Added
- Initial release
- NSE option chain proxy (Flask backend)
- PCR, Bull Probability, OI Change, Max Pain, Probability signals
- Black-Scholes Greeks (Delta, Gamma, Theta, Vega)
- Option chain OI table with visual bars
- 5-signal tip generation
- Simulation fallback mode
