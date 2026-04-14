"""
app_scheduler.py

Main entry point for the Smile Dental Scheduling Automation Service.

Starts:
  1. AutomationEngine  (sheet watcher callbacks + business logic)
  2. SheetWatcher      (polls Customers sheet every 30s)
  3. APScheduler       (background jobs)
  4. Flask Webhook Server on port 5001 (foreground, blocks)

Usage:
  cd d:\\04_Others\\Dental_Care
  python -m scheduling_automation.app_scheduler

  OR with ngrok for WhatsApp webhook testing:
  ngrok http 5001
  (Copy the https URL → set in Meta Business Manager webhook settings)
"""

import os
import sys
import structlog
import threading
from zoneinfo import ZoneInfo

#  Logging setup ─
import logger_setup
logger = structlog.get_logger("app_scheduler")

#  Path setup — allow imports from root 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(ROOT, ".env"))

from flask import Flask
from scheduling_automation.automation_engine import AutomationEngine
from scheduling_automation.sheet_watcher     import SheetWatcher
from scheduling_automation.scheduler         import build_scheduler
from scheduling_automation.webhook_server    import register_automation_routes

WEBHOOK_PORT = int(os.getenv("SCHEDULER_PORT", "5001"))
TIMEZONE     = "Asia/Kolkata"


def main():
    logger.info("=" * 60)
    logger.info("  Smile Dental Scheduling Automation — Starting Up")
    logger.info("=" * 60)

    #  Step 1: Initialize Automation Engine 
    logger.info("[STARTUP] Initializing Automation Engine...")
    engine = AutomationEngine()
    logger.info("[STARTUP] ✅ Automation Engine ready")

    #  Step 2: Initialize Sheet Watcher ─
    logger.info("[STARTUP] Initializing Sheet Watcher...")
    watcher = SheetWatcher(
        on_new=engine.on_new_appointment,
        on_modified=engine.on_appointment_modified,
        on_deleted=engine.on_appointment_cancelled
    )
    logger.info("[STARTUP] ✅ Sheet Watcher ready")

    #  Step 3: Build and Start APScheduler 
    logger.info("[STARTUP] Starting APScheduler jobs...")
    scheduler = build_scheduler(engine, watcher)
    scheduler.start()
    logger.info("[STARTUP] ✅ Scheduler running")

    #  Step 4: Initialize mini Flask server for webhooks
    flask_app = Flask(__name__)
    register_automation_routes(flask_app, engine)

    #  Step 5: Run an initial check immediately 
    logger.info("[STARTUP] Running initial sheet scan and status catch-up...")
    try:
        engine.mark_past_status_updates()  # Catch-up completed/expired
        watcher.check_for_changes()       # Catch-up new/modified rows
    except Exception as e:
        logger.warning(f"[STARTUP] Initial scan warning (non-fatal): {e}")

    #  Step 6: Start Flask webhook server (main thread, blocking) 
    logger.info(f"[STARTUP] Starting webhook server on port {WEBHOOK_PORT}")
    logger.info(f"[STARTUP] ")
    logger.info(f"[STARTUP] Webhook URL (local): http://localhost:{WEBHOOK_PORT}/webhook")
    logger.info(f"[STARTUP] For WhatsApp replies, expose with: ngrok http {WEBHOOK_PORT}")
    logger.info(f"[STARTUP] Manual triggers available at:")
    logger.info(f"[STARTUP]   POST /trigger/today-reminders")
    logger.info(f"[STARTUP]   POST /trigger/future-check")
    logger.info(f"[STARTUP]   POST /trigger/simulate-reply")
    logger.info(f"[STARTUP] ")

    try:
        flask_app.run(
            host="0.0.0.0",
            port=WEBHOOK_PORT,
            debug=False,
            use_reloader=False    # MUST be False when running with APScheduler
        )
    except KeyboardInterrupt:
        logger.info("[SHUTDOWN] Stopping scheduler...")
        scheduler.shutdown()
        logger.info("[SHUTDOWN] ✅ Goodbye.")


if __name__ == "__main__":
    main()
