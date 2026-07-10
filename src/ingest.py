import ssl
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

import requests
import pandas as pd
import time
import logging
import os
import json
import re
from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
engine = create_engine(os.getenv("DATABASE_URL"))

FUND_REGISTRY = {
    "119598": ("SBI Large Cap Fund - Direct Growth",                  "Large Cap Fund"),
    "118825": ("Mirae Asset Large Cap Fund - Direct Growth",          "Large Cap Fund"),
    "122639": ("Parag Parikh Flexi Cap Fund - Direct Growth",         "Flexi Cap Fund"),
    "125354": ("Axis Small Cap Fund - Direct Growth",                 "Small Cap Fund"),
    "118778": ("Nippon India Small Cap Fund - Direct Growth",         "Small Cap Fund"),
    "118834": ("Mirae Asset Large & Midcap Fund - Direct Growth",     "Large & Mid Cap Fund"),
    "120503": ("Axis ELSS Tax Saver Fund - Direct Growth",            "ELSS Fund"),
    "120847": ("Quant ELSS Tax Saver Fund - Direct Growth",           "ELSS Fund"),
}

# Screener.in fund slug mapping — matches scheme_code to screener URL slug
SCREENER_SLUGS = {
    "119598": "sbi-large-cap-fund-direct-plan",
    "118825": "mirae-asset-large-cap-fund-direct-plan",
    "122639": "parag-parikh-flexi-cap-fund-direct-plan",
    "125354": "axis-small-cap-fund-direct-plan",
    "118778": "nippon-india-small-cap-fund-direct-plan",
    "118834": "mirae-asset-large-midcap-fund-direct-plan",
    "120503": "axis-long-term-equity-fund-direct-plan",
    "120847": "quant-tax-plan-direct-plan",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.screener.in/",
}

def load_cap_tiers():
    path = "data/raw/nse_cap_list.xlsx"
    if not os.path.exists(path):
        log.warning("NSE cap list not found")
        return {}, {}
    df = pd.read_excel(path, skiprows=1, header=None)
    df.columns = ["Rank", "Symbol", "Company_Name", "Avg_MCAP"]
    df["Rank"] = pd.to_numeric(df["Rank"], errors="coerce")
    df = df[df["Rank"].notna()].copy()
    df["Rank"] = df["Rank"].astype(int)
    df["Symbol"] = df["Symbol"].fillna("").str.strip().str.upper()
    df["Company_Name"] = df["Company_Name"].fillna("").str.strip().str.upper()
    cap_by_symbol, cap_by_name = {}, {}
    for _, row in df.iterrows():
        tier = "large" if row["Rank"] <= 100 else "mid" if row["Rank"] <= 250 else "small"
        if row["Symbol"]:       cap_by_symbol[row["Symbol"]] = tier
        if row["Company_Name"]: cap_by_name[row["Company_Name"]] = tier
    log.info(f"Cap tiers — large:{sum(1 for v in cap_by_symbol.values() if v=='large')} "
             f"mid:{sum(1 for v in cap_by_symbol.values() if v=='mid')} "
             f"small:{sum(1 for v in cap_by_symbol.values() if v=='small')}")
    return cap_by_symbol, cap_by_name

def fetch_holdings_screener(scheme_code: str) -> list:
    """
    Scrapes stock-level portfolio holdings from Screener.in.
    """
    slug = SCREENER_SLUGS.get(scheme_code)
    if not slug:
        log.warning(f"No Screener slug for {scheme_code}")
        return []

    url = f"https://www.screener.in/company/{slug}/consolidated/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        if resp.status_code == 404:
            # Try without /consolidated/
            url = f"https://www.screener.in/company/{slug}/"
            resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Screener fetch failed for {scheme_code}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    holdings = []

    # Screener shows MF holdings in a table with class 'data-table'
    tables = soup.find_all("table", class_=re.compile("data-table", re.I))
    for table in tables:
        headers_row = table.find("thead")
        if not headers_row:
            continue
        header_texts = [th.get_text(strip=True).lower()
                        for th in headers_row.find_all("th")]
        # Look for the holdings table which has stock/company column
        if not any(h in header_texts for h in ["stock", "company", "name"]):
            continue
        tbody = table.find("tbody")
        if not tbody:
            continue
        for row in tbody.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) >= 2:
                try:
                    stock_name = cells[0].upper().strip()
                    # Last numeric cell is usually % of AUM
                    pct = 0.0
                    for cell in reversed(cells):
                        cleaned = cell.replace("%", "").replace(",", "").strip()
                        try:
                            pct = float(cleaned)
                            break
                        except ValueError:
                            continue
                    if stock_name and pct > 0:
                        holdings.append({
                            "stock_name": stock_name,
                            "sector": "Unknown",
                            "pct_of_nav": pct,
                            "market_value_cr": 0.0,
                            "isin": "",
                        })
                except Exception:
                    continue

    if holdings:
        log.info(f"  Screener: {len(holdings)} holdings scraped")
        return holdings

    # Fallback — try mfapi NAV history to at least confirm fund exists
    log.warning(f"  Screener returned no holdings for {scheme_code}. Check slug.")
    return []

def fetch_holdings_valueresearch(scheme_code: str, fund_name: str) -> list:
    """
    Fallback: scrape from Value Research Online.
    URL pattern: https://www.valueresearchonline.com/funds/{scheme_code}/portfolio/
    """
    url = f"https://www.valueresearchonline.com/funds/{scheme_code}/portfolio/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"VRO fetch failed for {scheme_code}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    holdings = []

    # VRO portfolio table
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows[1:]:  # skip header
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) >= 3:
                try:
                    name = cells[0].upper().strip()
                    pct_str = cells[-1].replace("%", "").replace(",", "").strip()
                    pct = float(pct_str)
                    if name and pct > 0:
                        holdings.append({
                            "stock_name": name,
                            "sector": cells[1].strip() if len(cells) > 2 else "Unknown",
                            "pct_of_nav": pct,
                            "market_value_cr": 0.0,
                            "isin": "",
                        })
                except (ValueError, IndexError):
                    continue

    log.info(f"  VRO: {len(holdings)} holdings for {fund_name}")
    return holdings

def fetch_holdings(scheme_code: str, fund_name: str) -> list:
    """
    Tries Screener.in first, falls back to Value Research Online.
    """
    holdings = fetch_holdings_screener(scheme_code)
    if not holdings:
        log.info(f"  Trying Value Research fallback...")
        holdings = fetch_holdings_valueresearch(scheme_code, fund_name)
    return holdings

def ingest_all_funds(as_of_date: str = None):
    from datetime import date as dt
    as_of_date = as_of_date or dt.today().strftime("%Y-%m-01")
    log.info(f"Ingesting for {as_of_date}")

    cap_by_symbol, cap_by_name = load_cap_tiers()
    all_rows = []

    for i, (scheme_code, (fund_name, sebi_category)) in enumerate(FUND_REGISTRY.items()):
        log.info(f"[{i+1}/{len(FUND_REGISTRY)}] {fund_name}")
        holdings = fetch_holdings(scheme_code, fund_name)

        if not holdings:
            log.warning(f"  No holdings — skipping {fund_name}")
            continue

        for h in holdings:
            name = h["stock_name"]
            words = name.split()
            symbol = words[0] if words else ""
            cap_tier = (
                cap_by_symbol.get(symbol) or
                cap_by_name.get(name) or
                "other"
            )
            all_rows.append({
                "scheme_code":     scheme_code,
                "fund_name":       fund_name,
                "sebi_category":   sebi_category,
                "isin":            h["isin"],
                "stock_name":      name,
                "sector":          h["sector"],
                "pct_of_nav":      h["pct_of_nav"],
                "market_value_cr": h["market_value_cr"],
                "cap_tier":        cap_tier,
                "as_of_date":      as_of_date,
            })
        log.info(f"  Done — {len(holdings)} holdings, cap tiers assigned")
        time.sleep(1)

    if not all_rows:
        log.error("Nothing ingested. All sources failed.")
        return

    df = pd.DataFrame(all_rows)
    print(f"\n{'='*50}\nINGESTION SUMMARY — {as_of_date}")
    print(df.groupby("fund_name")["stock_name"].count().to_string())
    print(f"\nCap tiers:\n{df['cap_tier'].value_counts().to_string()}")
    print("=" * 50)