import nsepython
import json
import pandas as pd
from datetime import datetime

print("=" * 80)
print("NIFTY 50 OPTIONS CHAIN DATA - Live Fetch")
print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
print("=" * 80)
print()

# 1. Get NIFTY spot price
try:
    print("1. FETCHING NIFTY 50 SPOT PRICE")
    print("-" * 80)
    nifty_quote = nsepython.nse_get_index_quote("NIFTY 50")
    
    spot_price = nifty_quote.get('lastPrice', 'N/A')
    change = nifty_quote.get('change', 'N/A')
    change_pct = nifty_quote.get('pChange', 'N/A')
    
    print(f"NIFTY 50 Spot Price: {spot_price}")
    print(f"Change: {change} ({change_pct}%)")
    print(f"Full Quote: {json.dumps(nifty_quote, indent=2)[:500]}")
    print()
except Exception as e:
    print(f"Error fetching NIFTY quote: {e}")
    print()

# 2. Get options chain
try:
    print("2. FETCHING OPTIONS CHAIN")
    print("-" * 80)
    
    # Get options chain data
    oc = nsepython.option_chain("NIFTY")
    
    print(f"Options chain data fetched successfully")
    print(f"Type: {type(oc)}")
    print(f"Sample data: {str(oc)[:300]}")
    print()
except Exception as e:
    print(f"Error fetching options chain: {e}")
    print()

# 3. Try alternative option chain function
try:
    print("3. FETCHING OPTIONS CHAIN (LTP Method)")
    print("-" * 80)
    
    oc_ltp = nsepython.nse_optionchain_ltp("NIFTY")
    
    print(f"Options chain (LTP) data fetched")
    if isinstance(oc_ltp, dict):
        print(f"Keys: {list(oc_ltp.keys())[:10]}")
        print(f"Sample: {json.dumps({k: oc_ltp[k] for k in list(oc_ltp.keys())[:2]}, indent=2)}")
    else:
        print(f"Data: {str(oc_ltp)[:300]}")
    print()
except Exception as e:
    print(f"Error with LTP method: {e}")
    print()

# 4. Get PCR
try:
    print("4. FETCHING PUT-CALL RATIO (PCR)")
    print("-" * 80)
    
    pcr_value = nsepython.pcr("NIFTY")
    print(f"PCR for NIFTY: {pcr_value}")
    print()
except Exception as e:
    print(f"Error fetching PCR: {e}")
    print()

# 5. Get VIX (proxy for ATM IV)
try:
    print("5. FETCHING VIX (Volatility Index)")
    print("-" * 80)
    
    vix_data = nsepython.indiavix()
    print(f"VIX Data: {json.dumps(vix_data, indent=2)[:500]}")
    print()
except Exception as e:
    print(f"Error fetching VIX: {e}")
    print()

print("=" * 80)
print("Script execution completed")
print("=" * 80)
