import requests
import json
from datetime import datetime
import pandas as pd

print("=" * 80)
print("NIFTY 50 OPTIONS CHAIN DATA FETCHER")
print("=" * 80)
print()

# Try method 1: Using nsepython
try:
    import nsepython
    print("✓ nsepython library loaded successfully")
    
    # Get NIFTY spot price
    print("\nAttempting to fetch NIFTY spot price...")
    nifty_spot = nsepython.nse_get_quote("NIFTY50")
    print(json.dumps(nifty_spot, indent=2))
except ImportError:
    print("✗ nsepython not available, trying alternative methods...")
except Exception as e:
    print(f"✗ Error with nsepython: {e}")

print()
print("=" * 80)

# Try method 2: Direct NSE API
try:
    print("\nMethod 2: Fetching from NSE API...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    # Get NIFTY spot price
    url = "https://www.nseindia.com/api/quote-equity?symbol=NIFTY50"
    response = requests.get(url, headers=headers, timeout=10)
    print(f"NSE API Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(json.dumps(data, indent=2)[:500])
except Exception as e:
    print(f"✗ NSE API Error: {e}")

print()
print("=" * 80)

# Try method 3: Alternative NSE endpoint
try:
    print("\nMethod 3: Fetching from NSE options API...")
    
    # NIFTY options data endpoint
    url = "https://www.nseindia.com/api/optionchain?symbol=NIFTY"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    response = requests.get(url, headers=headers, timeout=10)
    print(f"NSE Options API Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print("Successfully fetched options chain data")
        print(f"Keys available: {list(data.keys())}")
except Exception as e:
    print(f"✗ Options API Error: {e}")
