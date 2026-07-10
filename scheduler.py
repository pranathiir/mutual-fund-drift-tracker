import logging
import sys
import os
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/scheduler.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


def run_pipeline(month: int = None, year: int = None):
    from src.ingest import ingest_all_funds
    from src.features import compute_scores
    from src.model import train_model

    now = datetime.now()
    month = month or now.month
    year  = year  or now.year
    date_str = f"{year}-{month:02d}-01"

    log.info("=" * 55)
    log.info(f"Pipeline starting for {date_str}")
    log.info("=" * 55)

    try:
        ingest_all_funds(date_str)
    except Exception as e:
        log.error(f"Ingestion failed: {e}", exc_info=True)
        return

    try:
        compute_scores(date_str)
    except Exception as e:
        log.error(f"Feature computation failed: {e}", exc_info=True)
        return

    try:
        train_model()
    except Exception as e:
        log.warning(f"Model training skipped: {e}")

    try:
        send_alert(date_str)
    except Exception as e:
        log.warning(f"Alert skipped: {e}")

    log.info("Pipeline complete.")


def send_alert(date_str: str):
    import smtplib
    import pandas as pd
    from email.mime.text import MIMEText
    from sqlalchemy import create_engine

    engine = create_engine(os.getenv("DATABASE_URL"))

    try:
        df = pd.read_sql(
            f"SELECT fund_name, sebi_category, style_purity_score "
            f"FROM drift_scores WHERE as_of_date='{date_str}' "
            f"AND drift_flag=true ORDER BY style_purity_score ASC",
            engine
        )
    except Exception as e:
        log.warning(f"Could not query drift_scores: {e}")
        return

    if df.empty:
        log.info("No violations this month — no alert sent.")
        return

    body = (
        f"Mandate Violation Report — {date_str}\n"
        f"{len(df)} fund(s) are drifting from their stated mandate.\n\n"
        f"{df.to_string(index=False)}\n\n"
        f"Open the dashboard for SHAP-level stock attribution.\n"
        f"http://localhost:8050"
    )

    sender    = os.getenv("EMAIL_FROM")
    recipient = os.getenv("EMAIL_TO")
    password  = os.getenv("EMAIL_PASSWORD")

    if not all([sender, recipient, password]):
        log.warning("Email credentials missing in .env — skipping alert.")
        return

    msg = MIMEText(body)
    msg["Subject"] = f"[MF Drift Alert] {len(df)} violation(s) — {date_str}"
    msg["From"]    = sender
    msg["To"]      = recipient

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.send_message(msg)
        log.info(f"Alert sent — {len(df)} violation(s).")
    except Exception as e:
        log.error(f"Email send failed: {e}")


def start_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    scheduler.add_listener(
        lambda e: log.info(f"Job '{e.job_id}' completed."),
        EVENT_JOB_EXECUTED
    )
    scheduler.add_listener(
        lambda e: log.error(f"Job '{e.job_id}' failed: {e.exception}"),
        EVENT_JOB_ERROR
    )

    scheduler.add_job(
        run_pipeline,
        trigger="cron",
        day=15,
        hour=9,
        minute=0,
        id="monthly_pipeline",
        misfire_grace_time=3600,
        max_instances=1
    )

    log.info("Scheduler started — fires on the 15th of each month at 09:00 IST.")
    log.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    # python scheduler.py           → starts scheduled mode
    # python scheduler.py now       → runs pipeline immediately
    # python scheduler.py 2025 12   → runs for specific month

    if len(sys.argv) == 1:
        start_scheduler()

    elif sys.argv[1] == "now":
        run_pipeline()

    elif len(sys.argv) == 3:
        try:
            y = int(sys.argv[1])
            m = int(sys.argv[2])
            run_pipeline(month=m, year=y)
        except ValueError:
            print("Usage: python scheduler.py 2025 12")

    else:
        print("Usage:")
        print("  python scheduler.py            # scheduled mode")
        print("  python scheduler.py now        # run immediately")
        print("  python scheduler.py 2025 12    # specific month")