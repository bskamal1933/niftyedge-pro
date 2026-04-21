import nsepython
import requests
import json

print("Available nsepython functions:")
print([x for x in dir(nsepython) if not x.startswith('_')])

print("\n" + "=" * 80)
print("Trying alternative NSE endpoints...")
print("=" * 80)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json'
}

# Try different endpoints
endpoints = [
    "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY50",
    "https://www.nseindia.com/api/option-chain-indices?index=NIFTY",
    "https://www.nseindia.com/api/historical-option-chain",
]

for url in endpoints:
    try:
        print(f"\nTrying: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Keys: {list(data.keys())[:5]}")
            print(f"Sample: {json.dumps(data, indent=2)[:300]}")
    except Exception as e:
        print(f"Error: {e}")
