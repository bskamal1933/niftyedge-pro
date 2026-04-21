import requests
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

base_url = "http://localhost:5000"
errors_found = []

print("="*70)
print("COMPREHENSIVE ANALYSIS OF http://localhost:5000/")
print("="*70)

# 1. FETCH HTML & BASIC CHECKS
print("\n[1] FETCHING HTML & BASIC PAGE CHECKS")
print("-" * 70)
try:
    response = requests.get(base_url, timeout=5)
    print(f"Status Code: {response.status_code}")
    if response.status_code != 200:
        errors_found.append(f"CRITICAL: Root endpoint returned {response.status_code}")
    
    html_content = response.text
    print(f"HTML Size: {len(html_content)} bytes")
    
    # Parse HTML
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Check for basic HTML structure
    if not soup.find('html'):
        errors_found.append("CRITICAL: Missing <html> tag")
    if not soup.find('head'):
        errors_found.append("CRITICAL: Missing <head> tag")
    if not soup.find('body'):
        errors_found.append("CRITICAL: Missing <body> tag")
    
    print("HTML Structure: Valid basic tags found")
    
except Exception as e:
    errors_found.append(f"CRITICAL: Cannot fetch HTML - {str(e)}")
    print(f"Error: {e}")

# 2. CHECK SCRIPTS
print("\n[2] CHECKING JAVASCRIPT & EXTERNAL ASSETS")
print("-" * 70)

try:
    scripts = soup.find_all('script')
    print(f"Found {len(scripts)} script tags")
    
    external_scripts = [s.get('src') for s in scripts if s.get('src')]
    print(f"External scripts: {len(external_scripts)}")
    
    for src in external_scripts[:5]:
        try:
            url = urljoin(base_url, src)
            r = requests.head(url, timeout=3, allow_redirects=True)
            print(f"  {src}: {r.status_code}")
        except Exception as e:
            print(f"  {src}: ERROR")
    
    css_links = soup.find_all('link', {'rel': 'stylesheet'})
    print(f"Found {len(css_links)} CSS files")
    
    images = soup.find_all('img')
    print(f"Found {len(images)} images")
    
except Exception as e:
    print(f"Asset check error: {e}")

# 3. CHECK API ENDPOINTS
print("\n[3] TESTING API ENDPOINTS")
print("-" * 70)

endpoints = [
    "/api/tips", "/api/data", "/live-data", 
    "/api/signal", "/api/trades", "/tips", "/dashboard"
]

for endpoint in endpoints:
    try:
        url = base_url + endpoint
        r = requests.get(url, timeout=3)
        status = r.status_code
        icon = "✓" if status == 200 else "✗"
        print(f"{icon} {endpoint}: {status}")
        if status != 200:
            errors_found.append(f"WARNING: {endpoint} returns {status}")
    except Exception as e:
        print(f"✗ {endpoint}: ERROR")
        errors_found.append(f"ERROR: {endpoint} failed")

print(f"\n[RESULTS] Total errors found: {len(errors_found)}")
print("\n[ALL ERRORS]")
for i, err in enumerate(errors_found, 1):
    print(f"{i}. {err}")
