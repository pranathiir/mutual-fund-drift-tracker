import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from src.ingest import fetch_amfi_portfolio, parse_amfi_portfolio, load_cap_tiers

print("TEST 1 — Cap tier loading")
print("-" * 40)
cap_sym, cap_name = load_cap_tiers()
print(f"Symbols loaded: {len(cap_sym)}")

# Check what RELIANCE actually looks like in the file
import pandas as pd
df = pd.read_excel("data/raw/nse_cap_list.xlsx", skiprows=3)
df.columns = ["Rank", "Symbol", "Company_Name", "Avg_MCAP"]
df["Rank"] = pd.to_numeric(df["Rank"], errors="coerce")
df = df[df["Rank"].notna()].copy()
reliance_rows = df[df["Symbol"].str.contains("RELIANCE", na=False)]
print(f"RELIANCE rows in NSE file:\n{reliance_rows[['Rank','Symbol','Company_Name']].to_string()}")

print("\nTEST 2 — AMFI portfolio fetch")
print("-" * 40)
raw = fetch_amfi_portfolio(month=12, year=2025, debug=True)
print(f"Raw text length: {len(raw)}")

if raw:
    print("\nTEST 3 — Parse one fund")
    print("-" * 40)
    records = parse_amfi_portfolio(raw, "119533",
                                    "HDFC Top 100 Fund", "Large Cap Fund",
                                    "2025-12-01")
    print(f"Holdings parsed: {len(records)}")
    if records:
        print(f"First holding: {records[0]}")
    else:
        print("NOTHING PARSED — open data/raw/amfi_portfolio_2025_12.txt")
        print("Search for '119533' in that file to check if the scheme code exists")