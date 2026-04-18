# NiftyEdge Pro — Setup Guide

## Prerequisites

| Requirement    | Version | Download                                        |
| -------------- | ------- | ----------------------------------------------- |
| Python         | 3.8+    | [python.org](https://www.python.org/downloads/) |
| Chrome or Edge | Latest  | For the dashboard                               |

> **Windows users:** During Python installation, tick **"Add Python to PATH"**

---

## Option A — Browser Mode (Recommended for beginners)

This is the simplest way to run NiftyEdge Pro.

### Step 1 — Install Python packages

Open **Command Prompt** (Windows) or **Terminal** (Mac/Linux):

```bash
pip install -r requirements.txt
```

Expected output:

```
Successfully installed flask-2.x.x requests-2.x.x flask-cors-4.x.x
```

### Step 2 — Start the server

```bash
python server.py
```

You should see:

```
══════════════════════════════════════════════════════
   NiftyEdge Pro v3 — Real-Time NSE Streaming Server
══════════════════════════════════════════════════════
  [DB] Database ready: niftyedge_tips.db
  [09:14:55] ✓ NSE cookies refreshed
  [09:14:56] ▶ Background poll started (every 3s)
  ✓ Stream  : https://pykt.in/stream
  ✓ Accuracy: https://pykt.in/api/accuracy
  Open dashboard.html in Chrome/Edge
══════════════════════════════════════════════════════
```

**Keep this window open** for the entire trading session.

### Step 3 — Open the dashboard

Double-click `dashboard.html` and open it in **Chrome** or **Edge**.

You should see **⬤ LIVE** in the top-right corner within a few seconds.

---

## Option B — Windows Desktop App

Runs as a native Windows application with system tray support and desktop notifications.

### Step 1 — Install all packages

```bash
pip install -r requirements.txt
pip install -r requirements-desktop.txt
```

### Step 2 — Launch the app

```bash
python app.py
```

This automatically:

- Starts `server.py` in the background
- Opens a native window with the embedded dashboard
- Adds a system tray icon (right-click for options)
- Sends desktop notifications for strong signals (confidence ≥ 78%)

---

## One-Click Windows Install

Double-click `scripts/install.bat` — it will:

1. Check if Python is installed
2. Install all required packages
3. Create a desktop shortcut

---

## Verifying the Connection

### Server health check

Open your browser and go to:

```
https://pykt.in/api/status
```

You should see:

```json
{
  "status": "ok",
  "version": "3.0",
  "market_open": true,
  "time_ist": "09:18:42",
  "subscribers": 1
}
```

### Data source indicator

The dashboard top-right shows:

- **⬤ LIVE** (green) — Connected to Python server, real NSE data flowing
- **⬤ SIM** (red) — Server not running, using simulation mode

---

## Market Hours

| Session         | Time (IST)        |
| --------------- | ----------------- |
| Pre-Market      | 09:00 – 09:15     |
| **Market Open** | **09:15 – 15:30** |
| After Hours     | 15:30+            |

The server polls NSE every 3 seconds. NSE itself updates its option chain every ~15–30 seconds.

---

## Troubleshooting

### `pip` not found

Reinstall Python and check "Add Python to PATH" during installation.

### `ModuleNotFoundError: No module named 'flask'`

```bash
pip install flask requests flask-cors
```

### Dashboard shows ⬤ SIM even after starting server

- Confirm `server.py` is running and shows no errors
- Confirm you opened `dashboard.html` on the **same computer** as the server
- Check that port 5000 is not blocked by a firewall

### Port 5000 already in use

Edit `server.py` — change `port=5000` to `port=5001`.  
Then edit `dashboard.html` — find `localhost:5000` and replace with `localhost:5001`.

### NSE data not loading / server shows errors

- NSE best works between **09:15 and 15:30 IST on weekdays**
- Outside market hours, NSE may reject requests — simulation mode activates automatically

### `pywebview` install fails on Windows

```bash
pip install pywebview --pre
```

Or use Browser Mode instead (Option A).

### No system tray icon

```bash
pip install pystray Pillow
```

### No toast notifications

```bash
pip install win10toast
```

---

## File Locations

| File                | Purpose                               |
| ------------------- | ------------------------------------- |
| `server.py`         | Backend server — keep running         |
| `dashboard.html`    | Open in browser                       |
| `app.py`            | Windows desktop launcher              |
| `niftyedge_tips.db` | Auto-created — stores all tip history |

> `niftyedge_tips.db` is in `.gitignore` — it won't be committed. It contains your personal trade log and adaptive weights.

---

## Updating

```bash
git pull origin main
pip install -r requirements.txt
```

Your `niftyedge_tips.db` database (trade history and weights) is preserved across updates.

---

_For questions, open an issue on GitHub._
