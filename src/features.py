import pandas as pd
import numpy as np
import logging
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
engine = create_engine(os.getenv("DATABASE_URL"))

# SEBI mandates: minimum % of AUM in each cap tier (as a fraction, not %)
SEBI_MANDATES = {
    "Large Cap Fund":       {"large": 0.80},
    "Mid Cap Fund":         {"mid":   0.65},
    "Small Cap Fund":       {"small": 0.65},
    "Large & Mid Cap Fund": {"large": 0.35, "mid": 0.35},
    "Multi Cap Fund":       {"large": 0.25, "mid": 0.25, "small": 0.25},
    "Flexi Cap Fund":       {},   # no cap tier mandate
    "ELSS Fund":            {},   # no cap tier mandate
    "Focused Fund":         {},
}


def cap_tier_score(df: pd.DataFrame, sebi_category: str) -> float:
    """
    How closely does the fund adhere to its SEBI cap tier mandate?
    Returns 0.0 (fully violating) to 1.0 (fully compliant).
    """
    mandate = SEBI_MANDATES.get(sebi_category, {})
    if not mandate:
        return 1.0  # no mandate for this category

    total = df["pct_of_nav"].sum()
    if total == 0:
        return 0.0

    tier_pct = df.groupby("cap_tier")["pct_of_nav"].sum() / total
    penalty = 0.0
    for tier, required in mandate.items():
        actual = float(tier_pct.get(tier, 0.0))
        shortfall = max(0.0, required - actual)
        penalty += shortfall

    return round(max(0.0, 1.0 - penalty), 4)


def hhi_score(df: pd.DataFrame) -> float:
    """
    Herfindahl-Hirschman Index on sector weights.
    0 = perfectly diversified, 1 = 100% in one sector.
    High HHI in a diversified fund is a red flag.
    """
    total = df["pct_of_nav"].sum()
    if total == 0:
        return 0.0
    sector_wts = df.groupby("sector")["pct_of_nav"].sum() / total
    hhi = float((sector_wts ** 2).sum())
    return round(hhi, 6)


def overlap_score(df_curr: pd.DataFrame, df_prev: pd.DataFrame) -> float:
    """
    Cosine similarity between current and previous month holdings vectors.
    1.0 = identical holdings, 0.0 = completely different.
    Used as a proxy for consistency with historical mandate.
    """
    if df_prev is None or df_prev.empty:
        return 1.0

    stocks = set(df_curr["stock_name"]) | set(df_prev["stock_name"])
    v1 = pd.Series(0.0, index=stocks)
    v2 = pd.Series(0.0, index=stocks)

    for _, r in df_curr.iterrows():
        v1[r["stock_name"]] = r["pct_of_nav"]
    for _, r in df_prev.iterrows():
        v2[r["stock_name"]] = r["pct_of_nav"]

    norm = np.linalg.norm(v1.values) * np.linalg.norm(v2.values)
    if norm == 0:
        return 1.0
    return round(float(np.dot(v1.values, v2.values) / norm), 4)


def churn_rate(df_curr: pd.DataFrame, df_prev: pd.DataFrame) -> float:
    """
    Fraction of top-20 holdings that changed vs previous month.
    0.0 = same top-20, 1.0 = completely different top-20.
    """
    if df_prev is None or df_prev.empty:
        return 0.0

    top_curr = set(df_curr.nlargest(20, "pct_of_nav")["stock_name"])
    top_prev = set(df_prev.nlargest(20, "pct_of_nav")["stock_name"])
    changed = len(top_curr.symmetric_difference(top_prev))
    return round(changed / 20.0, 4)


def style_purity_composite(cap, hhi, overlap, churn) -> float:
    """
    Weighted composite score: 0.0 (fully drifted) to 1.0 (perfectly pure).

    Weights:
      cap tier adherence : 40% (most important — direct SEBI mandate)
      HHI (inverted)     : 20% (concentration risk)
      overlap            : 20% (consistency over time)
      churn (inverted)   : 20% (stability of holdings)
    """
    hhi_s   = round(1.0 - min(hhi,   1.0), 4)
    churn_s = round(1.0 - min(churn, 1.0), 4)
    score = (0.40 * cap +
             0.20 * hhi_s +
             0.20 * overlap +
             0.20 * churn_s)
    return round(score, 4)


def compute_scores(as_of_date: str):
    """
    Reads holdings for as_of_date from DB, computes all scores,
    writes results to drift_scores table.
    """
    log.info(f"Computing scores for {as_of_date}")

    df_all = pd.read_sql(
        f"SELECT * FROM raw_holdings WHERE as_of_date = '{as_of_date}'",
        engine
    )

    if df_all.empty:
        log.error(f"No holdings in DB for {as_of_date}. Run ingestion first.")
        return

    # Load previous month's data for churn and overlap
    prev_date = (
        pd.Timestamp(as_of_date) - pd.DateOffset(months=1)
    ).strftime("%Y-%m-01")

    try:
        df_prev_all = pd.read_sql(
            f"SELECT * FROM raw_holdings WHERE as_of_date = '{prev_date}'",
            engine
        )
    except Exception:
        df_prev_all = pd.DataFrame()

    results = []

    for scheme_code, df in df_all.groupby("scheme_code"):
        fund_name     = df["fund_name"].iloc[0]
        sebi_category = df["sebi_category"].iloc[0]

        df_prev = df_prev_all[
            df_prev_all["scheme_code"] == scheme_code
        ] if not df_prev_all.empty else pd.DataFrame()

        cap     = cap_tier_score(df, sebi_category)
        hhi     = hhi_score(df)
        overlap = overlap_score(df, df_prev)
        churn   = churn_rate(df, df_prev)
        purity  = style_purity_composite(cap, hhi, overlap, churn)

        results.append({
            "scheme_code":        scheme_code,
            "fund_name":          fund_name,
            "sebi_category":      sebi_category,
            "as_of_date":         as_of_date,
            "cap_tier_score":     cap,
            "hhi_concentration":  hhi,
            "benchmark_overlap":  overlap,
            "churn_rate":         churn,
            "style_purity_score": purity,
            "drift_flag":         purity < 0.70,
        })

        flag = "⚠ DRIFTING" if purity < 0.70 else "✓ Clean"
        log.info(f"  {fund_name}: purity={purity} {flag}")

    if not results:
        log.error("No scores computed.")
        return

    out = pd.DataFrame(results)
    out.to_sql("drift_scores", engine, if_exists="append", index=False)

    print(f"\n{'='*55}")
    print(f"SCORES — {as_of_date}")
    print(f"{'='*55}")
    print(out[["fund_name", "style_purity_score", "drift_flag"]].to_string(index=False))
    print(f"{'='*55}\n")
    log.info(f"Scores written to drift_scores table.")