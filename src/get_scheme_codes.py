import requests
import pandas as pd

resp = requests.get("https://api.mfapi.in/mf")
all_schemes = pd.DataFrame(resp.json())

# Filter to equity funds only by name keywords
equity_keywords = [
    "Large Cap", "Mid Cap", "Small Cap", "Flexi Cap",
    "Multi Cap", "Large & Mid", "Focused", "ELSS",
    "Value Fund", "Contra Fund"
]

pattern = "|".join(equity_keywords)
equity = all_schemes[
    all_schemes["schemeName"].str.contains(pattern, case=False, na=False)
]

# Further filter: keep only Growth / Direct Growth options
# (avoid duplicates from Dividend, IDCW, Bonus variants)
equity = equity[
    equity["schemeName"].str.contains("Growth|growth", na=False)
]
equity = equity[
    ~equity["schemeName"].str.contains("Dividend|IDCW|Bonus|Weekly|Monthly|Quarterly", 
                                        case=False, na=False)
]

equity.to_csv("data/raw/equity_scheme_codes.csv", index=False)
print(f"Found {len(equity)} equity growth schemes")
print(equity.head(20).to_string())