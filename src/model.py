import pandas as pd
import numpy as np
import logging
import os
import pickle
import xgboost as xgb
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)
engine = create_engine(os.getenv("DATABASE_URL"))

MODEL_PATH = "data/processed/drift_model.pkl"
FEATURES   = ["cap_tier_score", "hhi_concentration", "benchmark_overlap", "churn_rate"]


def train_model():
    """
    Trains an XGBoost classifier on historical drift_scores.
    Label: drift_flag (True = drifting, False = compliant).
    Requires at least 2 months of data to train meaningfully.
    """

    df = pd.read_sql("SELECT * FROM drift_scores", engine)

    if len(df) < 8:
        log.warning(
            f"Only {len(df)} rows in drift_scores. "
            f"Backfill more months before training. Skipping."
        )
        return None

    X = df[FEATURES].fillna(0)
    y = df["drift_flag"].astype(int)

    n_pos = (y == 1).sum()
    n_neg = (y == 0).sum()
    scale = n_neg / n_pos if n_pos > 0 else 1.0

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        scale_pos_weight=scale,
        random_state=42,
        eval_metric="logloss",
        verbosity=0,
    )
    model.fit(X, y)

    os.makedirs("data/processed", exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    log.info(f"Model trained on {len(df)} rows ({n_pos} drifting, {n_neg} clean).")
    log.info(f"Saved to {MODEL_PATH}")
    return model


def load_model():
    if not os.path.exists(MODEL_PATH):
        log.warning(f"Model not found at {MODEL_PATH}. Run train_model() first.")
        return None
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def explain_fund_drift(scheme_code: str, as_of_date: str) -> pd.DataFrame:
    """
    Returns top stocks driving mandate drift for a specific fund,
    explained using SHAP values.
    Each row = one stock, with its SHAP contribution to the drift prediction.
    """
    import shap

    model = load_model()
    if model is None:
        return pd.DataFrame()

    # Current month holdings
    df = pd.read_sql(
        f"SELECT * FROM raw_holdings "
        f"WHERE scheme_code='{scheme_code}' AND as_of_date='{as_of_date}'",
        engine
    )
    if df.empty:
        log.warning(f"No holdings for {scheme_code} on {as_of_date}")
        return pd.DataFrame()

    # Previous month for weight change feature
    prev_date = (
        pd.Timestamp(as_of_date) - pd.DateOffset(months=1)
    ).strftime("%Y-%m-01")

    df_prev = pd.read_sql(
        f"SELECT stock_name, pct_of_nav as prev_pct FROM raw_holdings "
        f"WHERE scheme_code='{scheme_code}' AND as_of_date='{prev_date}'",
        engine
    )

    df = df.merge(df_prev, on="stock_name", how="left")
    df["prev_pct"]      = df["prev_pct"].fillna(0)
    df["weight_change"] = df["pct_of_nav"] - df["prev_pct"]
    df["cap_code"]      = df["cap_tier"].map(
        {"large": 0, "mid": 1, "small": 2, "other": 3}
    ).fillna(3).astype(int)
    df["sector_code"]   = pd.Categorical(df["sector"]).codes

    stock_features = ["pct_of_nav", "cap_code", "sector_code", "weight_change"]
    X = df[stock_features].fillna(0)

    explainer  = shap.TreeExplainer(model)
    shap_vals  = explainer.shap_values(X)

    # shap_values shape: (n_samples, n_features) for binary XGBoost
    if isinstance(shap_vals, list):
        # Older shap returns list [neg_class, pos_class]
        sv = shap_vals[1]
    else:
        sv = shap_vals

    # Summarise per stock: sum of absolute SHAP across features
    df["shap_total"]  = np.abs(sv).sum(axis=1)
    df["shap_signed"] = sv.sum(axis=1)   # positive = pushes toward drift

    result = (
        df[["stock_name", "sector", "cap_tier",
            "pct_of_nav", "weight_change", "shap_signed"]]
        .rename(columns={"shap_signed": "shap_contribution"})
        .sort_values("shap_contribution", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )

    return result