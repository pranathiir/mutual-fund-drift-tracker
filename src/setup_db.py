from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.connect() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS raw_holdings (
            id SERIAL PRIMARY KEY,
            scheme_code VARCHAR(20),
            fund_name VARCHAR(200),
            sebi_category VARCHAR(100),
            isin VARCHAR(20),
            stock_name VARCHAR(200),
            sector VARCHAR(100),
            pct_of_nav DECIMAL(8,4),
            market_value_cr DECIMAL(15,2),
            cap_tier VARCHAR(10),
            as_of_date DATE,
            ingested_at TIMESTAMP DEFAULT NOW()
        );
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS drift_scores (
            id SERIAL PRIMARY KEY,
            scheme_code VARCHAR(20),
            fund_name VARCHAR(200),
            sebi_category VARCHAR(100),
            as_of_date DATE,
            cap_tier_score DECIMAL(6,4),
            hhi_concentration DECIMAL(8,6),
            benchmark_overlap DECIMAL(6,4),
            churn_rate DECIMAL(6,4),
            style_purity_score DECIMAL(6,4),
            drift_flag BOOLEAN,
            computed_at TIMESTAMP DEFAULT NOW()
        );
    """))
    conn.commit()
    print("Tables created successfully.")