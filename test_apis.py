# test_apis.py
import requests

tests = [
    ("mfapi scheme list",   "https://api.mfapi.in/mf"),
    ("mfapi single scheme", "https://api.mfapi.in/mf/119533"),
    ("mfdata holdings",     "https://api.mfdata.in/v1/schemes/119533/holdings"),
    ("mfdata scheme info",  "https://api.mfdata.in/v1/schemes/119533"),
    ("AMFI NAV file",       "https://portal.amfiindia.com/spages/NAVopen.txt"),
]

for name, url in tests:
    try:
        r = requests.get(url, timeout=10)
        print(f"OK  {name}: status={r.status_code}, chars={len(r.text)}")
    except Exception as e:
        print(f"ERR {name}: {e}")