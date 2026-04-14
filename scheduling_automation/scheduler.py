"""
scheduler.py

APScheduler jobs for the Smile Dental scheduling automation.

Jobs:
  1. sheet_watcher_job         — every 30s  → detect Customers sheet changes
  2. current_reminder_job      — every 1h   → TYPE-B 36h informational reminder
                                              for CURRENT (confirmed) appointments
  3. prediction_notifier_job   — every 1h   → TYPE-C YES/NO for PREDICTED appointments
                                              (separate from current reminders)
  4. morning_reminder_job      — cron 08:00 → TYPE-B same-day reminder (IST)
"""

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

logger = structlog.get_logger(__name__)

TIMEZONE = "Asia/Kolkata"


def build_scheduler(engine, watcher) -> BackgroundScheduler:
    """
    Build and return a configured BackgroundScheduler.

    Parameters:
      engine  — AutomationEngine instance
      watcher — SheetWatcher instance
    """
    scheduler = BackgroundScheduler(timezone=ZoneInfo(TIMEZONE))

    #  Job 1: Sheet Watcher (every 30 seconds) ─
    scheduler.add_job(
        func=_safe_run(watcher.check_for_changes, "SheetWatcher"),
        trigger=IntervalTrigger(seconds=30),
        id="sheet_watcher_job",
        name="Customers Sheet Watcher",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(ZoneInfo(TIMEZONE)) + timedelta(seconds=5)
    )
    logger.info("[SCHEDULER] Job 1: Sheet Watcher (30s)")

    #  Job 2: 36h Reminder — CURRENT appointments (every 1 hour) 
    # TYPE-B: Informational reminder only. No YES/NO.
    scheduler.add_job(
        func=_safe_run(engine.check_and_send_current_reminders, "CurrentReminder"),
        trigger=IntervalTrigger(hours=1),
        id="current_reminder_job",
        name="36h Reminder for Confirmed Appointments",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(ZoneInfo(TIMEZONE)) + timedelta(seconds=5)
    )
    logger.info("[SCHEDULER] Job 2: 36h Current Reminder (1h interval)")

    #  Job 3: Prediction Notifier — PREDICTED appointments (every 1 hour) ─
    # TYPE-C: YES/NO confirmation request only.
    scheduler.add_job(
        func=_safe_run(engine.check_and_send_prediction_messages, "PredictionNotifier"),
        trigger=IntervalTrigger(hours=1),
        id="prediction_notifier_job",
        name="YES/NO Request for Predicted Appointments",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(ZoneInfo(TIMEZONE)) + timedelta(seconds=5)
    )
    logger.info("[SCHEDULER] Job 3: Prediction Notifier (1h interval)")

    #  Job 4: 8 AM Same-Day Reminder (daily cron) ─
    # TYPE-B: Informational only. No YES/NO.
    scheduler.add_job(
        func=_safe_run(engine.send_today_reminders, "MorningReminder"),
        trigger=CronTrigger(hour=8, minute=0, timezone=ZoneInfo(TIMEZONE)),
        id="morning_reminder_job",
        name="8 AM Same-Day Reminder",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(ZoneInfo(TIMEZONE)) + timedelta(seconds=5)
    )
    logger.info("[SCHEDULER] Job 4: 8 AM Morning Reminder (daily cron)")
    
    #  Job 5: Status Cleanup — Mark COMPLETED/EXPIRED (every 1 hour) 
    scheduler.add_job(
        func=_safe_run(engine.mark_past_status_updates, "StatusCleanup"),
        trigger=IntervalTrigger(hours=1),
        id="status_cleanup_job",
        name="Mark Past Appointments as Completed/Expired",
        replace_existing=True,
        max_instances=1,
        next_run_time=datetime.now(ZoneInfo(TIMEZONE)) + timedelta(seconds=5)
    )
    logger.info("[SCHEDULER] Job 5: Status Cleanup (1h interval)")

    return scheduler


def _safe_run(func, name: str):
    """Wrap a job function to catch and log exceptions without crashing the scheduler."""
    def wrapper():
        try:
            func()
        except Exception as e:
            logger.error(f"[SCHEDULER] Job '{name}' error: {e}", exc_info=True)
    wrapper.__name__ = name
    return wrapper
