"""
automation_engine.py
────────────────────
Core business logic router for the Smile Dental scheduling automation.

Spec Compliance (v2):
  ─────────────────────────────────────────────────────────────────────────
  ABSOLUTE RULE: Never process any appointment whose datetime <= now (IST).
  ─────────────────────────────────────────────────────────────────────────

  Appointment Types:
    A) CURRENT   — booked/modified by clinic staff. Confirmed. No YES/NO.
    B) PREDICTED — auto-generated from treatment plan. Requires YES/NO.

  Workflows:
    1. New appointment   → TYPE-A confirmation + silent prediction
    2. Modified          → TYPE-A modification notice + recalculate predictions
    3. Cancelled         → remove from Future_Appointments + cancellation notice
    4. 36h current check → TYPE-B reminder (informational, no YES/NO)
    5. Prediction notify → TYPE-C YES/NO request (predicted only)
    6. 8AM same-day      → TYPE-B same-day reminder (informational)
    7. YES reply         → confirm + move to Customers sheet
    8. NO reply          → decline + notify team
    9. Emergency         → immediate alert
   10. Fallback          → generic response

  Duplicate Protection:
    All messages guarded by StateStore flags:
      - confirmation_sent
      - reminder_sent
      - prediction_message_sent
"""

import os
import json
import pickle
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from googleapiclient.discovery import build
from google.auth.transport.requests import Request

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scheduling_automation.whatsapp_service import (
    send_confirmation,
    send_future_visits_info,
    send_modification_notice,
    send_cancellation_notice,
    send_current_appointment_reminder,
    send_appointment_today_reminder,
    send_predicted_appointment_confirmation_request,
    send_emergency_reply,
    send_yes_confirmation,
    send_no_reply,
    send_fallback,
)
from scheduling_automation.services_parser import get_future_dates_for_reason
from scheduling_automation.future_appointments import FutureAppointmentsManager
from scheduling_automation.state_store import StateStore

logger = logging.getLogger(__name__)

TIMEZONE        = "Asia/Kolkata"
CUSTOMERS_SHEET = "Customers"
TOKEN_PATH      = os.path.join(os.path.dirname(__file__), "..", "token.pickle")
SHEETS_CONFIG   = os.path.join(os.path.dirname(__file__), "..", "sheets_config.json")
SCOPES          = ["https://www.googleapis.com/auth/spreadsheets"]

EMERGENCY_KEYWORDS = ["pain", "swelling", "bleeding", "emergency", "urgent"]

# Pending YES/NO context store (phone → context dict)
PENDING_STORE_PATH = os.path.join(os.path.dirname(__file__), "pending_replies.json")


def _load_pending() -> dict:
    if os.path.exists(PENDING_STORE_PATH):
        try:
            with open(PENDING_STORE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_pending(data: dict):
    with open(PENDING_STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


class AutomationEngine:

    def __init__(self):
        self.fa      = FutureAppointmentsManager()
        self.state   = StateStore()
        self.service = self._authenticate()
        self.spreadsheet_id = self._load_spreadsheet_id()
        self._pending: dict = _load_pending()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self):
        creds = None
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH, "rb") as f:
                creds = pickle.load(f)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_PATH, "wb") as f:
                    pickle.dump(creds, f)
            else:
                raise RuntimeError("Google Sheets not authenticated. Run main app first.")
        return build("sheets", "v4", credentials=creds)

    def _load_spreadsheet_id(self) -> str:
        with open(SHEETS_CONFIG, "r") as f:
            return json.load(f)["spreadsheet_id"]

    # ── ABSOLUTE TIME SAFETY RULE ─────────────────────────────────────────────

    def _is_past_datetime(self, date_str: str, time_str: str = "") -> bool:
        """
        Returns True if the appointment datetime is in the past.

        Rules:
          IF appointment_date  < today         → PAST (skip)
          IF appointment_date == today AND time <= now → PAST (skip)
          ELSE                                 → VALID

        If time_str is empty/unparseable, falls back to date-only check.
        """
        if not date_str:
            return True
        try:
            tz    = ZoneInfo(TIMEZONE)
            now   = datetime.now(tz)
            today = now.date()

            appt_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()

            # Date is clearly in the past
            if appt_date < today:
                return True

            # Date is in the future — always valid
            if appt_date > today:
                return False

            # Date is TODAY — must also check time
            if time_str:
                for fmt in ["%I:%M %p", "%H:%M", "%I:%M%p"]:
                    try:
                        t = datetime.strptime(time_str.strip(), fmt).time()
                        appt_dt = datetime.combine(appt_date, t, tzinfo=tz)
                        return appt_dt <= now
                    except ValueError:
                        continue

            # If time couldn't be parsed, treat today's appt as valid (conservative)
            return False

        except Exception as e:
            logger.warning(f"[ENGINE] Could not parse datetime '{date_str} {time_str}': {e}")
            return False

    def _parse_appt_datetime(self, date_str: str, time_str: str):
        """
        Parse appointment date + time into a timezone-aware datetime.
        Returns None if parsing fails.
        """
        try:
            appt_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
            for fmt in ["%I:%M %p", "%H:%M", "%I:%M%p"]:
                try:
                    t = datetime.strptime(time_str.strip(), fmt).time()
                    return datetime.combine(appt_date, t, tzinfo=ZoneInfo(TIMEZONE))
                except ValueError:
                    continue
            # Time unparseable — return date at midnight (conservative)
            from datetime import time as dtime
            return datetime.combine(appt_date, dtime(0, 0), tzinfo=ZoneInfo(TIMEZONE))
        except Exception:
            return None

    def _is_short_notice(self, date_str: str, time_str: str) -> bool:
        """
        Returns True if the appointment is within 36 hours of now.
        SHORT-NOTICE rule: booking made less than 36h before appointment time.
        When True, reminder_sent is immediately set so the scheduler skips forever.
        """
        appt_dt = self._parse_appt_datetime(date_str, time_str)
        if appt_dt is None:
            return False
        now = datetime.now(ZoneInfo(TIMEZONE))
        hours_until = (appt_dt - now).total_seconds() / 3600
        return 0 < hours_until <= 36

    # ── Workflow 1: New Appointment ───────────────────────────────────────────

    def on_new_appointment(self, row: dict):
        """
        Triggered when a new row detected in Customers sheet.
        TYPE-A: Send informational confirmation (no YES/NO).
        Silent prediction of future dates.
        """
        cid    = row.get("customer_id", "")
        name   = row.get("name", "")
        phone  = row.get("phone", "")
        date_  = row.get("appointment_date", "")
        time_  = row.get("appointment_time", "")
        reason = row.get("appointment_reason", "")

        # ── ABSOLUTE SAFETY CHECK ───────────────────────────────────────────
        if self._is_past_datetime(date_, time_):
            logger.info(f"[ENGINE] ⏳ Skipping past appointment: {cid} | {date_} {time_}")
            return

        state_key = self.state.make_key(cid, date_, time_)

        # ── DUPLICATE PROTECTION ────────────────────────────────────────────
        if self.state.is_confirmation_sent(state_key):
            logger.info(f"[ENGINE] 🔁 Confirmation already sent for {state_key}. Skipping.")
            return

        logger.info(f"[ENGINE] 🆕 New appointment: {cid} | {name} | {date_} {time_} | {reason}")

        # Step 1 — Send TYPE-A confirmation (informational, no YES/NO)
        if send_confirmation(phone, name, date_, time_, reason):
            self.state.set_confirmation_sent(state_key)

        # ── SHORT-NOTICE DETECTION ──────────────────────────────────────────
        # If booked within 36h of the appointment, the 36h reminder window
        # has already passed or is too close. Mark reminder_sent immediately
        # so the scheduler NEVER sends a reminder for this appointment.
        if self._is_short_notice(date_, time_):
            logger.info(
                f"[ENGINE] ⚡ Short-notice booking: {cid} | {date_} {time_} "
                f"— marking reminder as sent (SHORT_NOTICE)"
            )
            self.state.set_reminder_sent(state_key, mode="SHORT_NOTICE")

        # Step 2 — Resolve service logic for future sittings
        info         = get_future_dates_for_reason(reason, date_)
        future_dates = info.get("future_dates", [])
        total_sittings = info.get("total_sittings", 1)

        logger.info(
            f"[ENGINE] 🔮 Service: '{info['service']}' | "
            f"Sittings: {total_sittings} | Gap: {info['gap_days']}d | "
            f"Futures: {future_dates}"
        )

        # Step 3 — Silently store predicted dates in Future_Appointments sheet
        if future_dates:
            self.fa.upsert_future_row(
                customer_id=cid,
                name=name,
                phone=phone,
                appt_date=date_,
                appt_time=time_,
                reason=reason,
                future_dates=future_dates
            )
            # Initialize state for each predicted date
            for fd in future_dates:
                pred_key = self.state.make_key(cid, fd, "predicted")
                self.state.init_prediction(pred_key)

            # Step 4 — Inform patient about multi-sitting (still informational)
            send_future_visits_info(phone, name, reason, total_sittings)

    # ── Workflow 2: Appointment Modified ─────────────────────────────────────

    def on_appointment_modified(self, old_row: dict, new_row: dict):
        """
        Sent when an existing row's date/time changes.
        TYPE-A: Send informational reschedule notice (no YES/NO).
        """
        cid    = new_row.get("customer_id", "")
        name   = new_row.get("name", "")
        phone  = new_row.get("phone", "")
        date_  = new_row.get("appointment_date", "")
        time_  = new_row.get("appointment_time", "")
        reason = new_row.get("appointment_reason", "")
        old_date = old_row.get("appointment_date", "")
        old_time = old_row.get("appointment_time", "")

        # ── ABSOLUTE SAFETY CHECK ───────────────────────────────────────────
        if self._is_past_datetime(date_, time_):
            logger.info(f"[ENGINE] ⏳ Skipping modification to past datetime: {cid} | {date_} {time_}")
            return

        state_key = self.state.make_key(cid, date_, time_)

        # ── DUPLICATE PROTECTION ────────────────────────────────────────────
        if self.state.is_confirmation_sent(state_key):
            logger.info(f"[ENGINE] 🔁 Modification notice already sent for {state_key}. Skipping.")
            return

        logger.info(f"[ENGINE] ✏️ Modified: {cid} | {old_date} {old_time} → {date_} {time_}")

        # Step 1 — Send TYPE-A modification notice
        if send_modification_notice(phone, name, date_, time_, reason):
            self.state.set_confirmation_sent(state_key)

        # ── SHORT-NOTICE DETECTION ──────────────────────────────────────────
        if self._is_short_notice(date_, time_):
            logger.info(
                f"[ENGINE] ⚡ Short-notice modification: {cid} | {date_} {time_} "
                f"— marking reminder as sent (SHORT_NOTICE)"
            )
            self.state.set_reminder_sent(state_key, mode="SHORT_NOTICE")

        # Step 2 — Recalculate future dates from new base date
        info         = get_future_dates_for_reason(reason, date_)
        future_dates = info.get("future_dates", [])

        # Step 3 — Update Future_Appointments with recalculated dates
        self.fa.upsert_future_row(
            customer_id=cid,
            name=name,
            phone=phone,
            appt_date=date_,
            appt_time=time_,
            reason=reason,
            future_dates=future_dates
        )

    # ── Workflow 3: Appointment Cancelled ─────────────────────────────────────

    def on_appointment_cancelled(self, row: dict):
        """Row deleted from Customers sheet."""
        cid   = row.get("customer_id", "")
        name  = row.get("name", "")
        phone = row.get("phone", "")
        date_ = row.get("appointment_date", "")
        time_ = row.get("appointment_time", "")

        # Skip if already past
        if self._is_past_datetime(date_, time_):
            logger.info(f"[ENGINE] ⏳ Skipping cancellation notice for past appointment: {cid}")
            self.fa.delete_future_row(cid)
            return

        logger.info(f"[ENGINE] 🗑️ Cancelled: {cid} | {date_}")
        self.fa.delete_future_row(cid)
        send_cancellation_notice(phone, name, date_)

    # ── Workflow 4: 36h Reminder — CURRENT appointments ──────────────────────

    def check_and_send_current_reminders(self):
        """
        Called every hour by scheduler.
        Sends TYPE-B informational reminder if any CURRENT appointment is ~36h away.
        NO YES/NO — current appointments are already confirmed.
        """
        now = datetime.now(ZoneInfo(TIMEZONE))
        window_start = now + timedelta(hours=30)
        window_end   = now + timedelta(hours=42)

        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CUSTOMERS_SHEET}!A:F"
            ).execute()
            rows = result.get("values", [])[1:]  # skip header
        except Exception as e:
            logger.error(f"[ENGINE] Failed to read Customers for 36h check: {e}")
            return

        for row in rows:
            if len(row) < 5:
                continue
            cid     = str(row[0]).strip()
            name    = str(row[1]).strip()
            phone   = str(row[2]).strip()
            date_   = str(row[3]).strip()
            time_   = str(row[4]).strip()
            reason  = str(row[5]).strip() if len(row) > 5 else ""

            # Safety check
            if self._is_past_datetime(date_, time_):
                continue

            # Build state key
            state_key = self.state.make_key(cid, date_, time_)

            # Duplicate protection
            if self.state.is_reminder_sent(state_key):
                continue

            # Parse appointment datetime
            for fmt in ["%I:%M %p", "%H:%M", "%I:%M%p"]:
                try:
                    t = datetime.strptime(time_.strip(), fmt).time()
                    appt_date = datetime.strptime(date_.strip(), "%Y-%m-%d").date()
                    appt_dt   = datetime.combine(appt_date, t, tzinfo=ZoneInfo(TIMEZONE))

                    hours_away = (appt_dt - now).total_seconds() / 3600
                    if 18 <= hours_away <= 42:
                        logger.info(f"[ENGINE] ⏰ 36h reminder → {cid} | {name} | {date_} {time_}")
                        # TYPE-B: Informational only, no YES/NO
                        if send_current_appointment_reminder(phone, name, date_, time_, reason):
                            self.state.set_reminder_sent(state_key)
                    break
                except ValueError:
                    continue

    # ── Workflow 5: Prediction Notification (YES/NO) ──────────────────────────

    def check_and_send_prediction_messages(self):
        """
        Called every hour by scheduler.
        Sends TYPE-C YES/NO request for PREDICTED future dates ~36h away.
        Only predicted appointments receive YES/NO.
        """
        now          = datetime.now(ZoneInfo(TIMEZONE))
        window_start = now + timedelta(hours=30)
        window_end   = now + timedelta(hours=42)

        pending = self.fa.get_all_pending_future_appointments()
        logger.debug(f"[ENGINE] Checking {len(pending)} predicted slots for notification...")

        for item in pending:
            fd_str = item.get("future_date", "")
            if not fd_str:
                continue

            try:
                fd_date = datetime.strptime(fd_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TIMEZONE))
            except ValueError:
                continue

            # Must be in the future
            if fd_date.date() < now.date():
                continue

            # Build prediction state key
            cid      = item["customer_id"]
            pred_key = self.state.make_key(cid, fd_str, "predicted")

            # Duplicate protection
            if self.state.is_prediction_message_sent(pred_key):
                continue

            # Check if prediction is already DECLINED
            if self.state.get_prediction_status(pred_key) == "DECLINED":
                continue

            # Check if within the 36h notification window (day-level)
            hours_away = (fd_date - now).total_seconds() / 3600
            if 18 <= hours_away <= 42:
                logger.info(f"[ENGINE] 🔮 Prediction notify → {cid} | {fd_str}")
                # TYPE-C: Ask YES/NO
                if send_predicted_appointment_confirmation_request(
                    phone=item["phone"],
                    name=item["name"],
                    treatment=item["reason"],
                    predicted_date=fd_str
                ):
                    self.state.set_prediction_message_sent(pred_key)
                    self.state.set_prediction_status(pred_key, "PENDING")
                    # Store context for reply handling
                    normalized = str(item["phone"]).strip().replace("+91", "").replace("+", "")
                    self._pending[normalized] = {
                        "customer_id": cid,
                        "name":        item["name"],
                        "future_date": fd_str,
                        "reason":      item["reason"],
                        "pred_key":    pred_key,
                    }
                    _save_pending(self._pending)

    # ── Workflow 6: Same-Day 8 AM Reminders ──────────────────────────────────

    def send_today_reminders(self):
        """Called at 08:00 AM daily. TYPE-B reminder for all of today's appointments."""
        tz        = ZoneInfo(TIMEZONE)
        now       = datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")

        logger.info(f"[ENGINE] 🌅 Sending same-day reminders for {today_str}")

        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CUSTOMERS_SHEET}!A:F"
            ).execute()
            rows = result.get("values", [])[1:]
        except Exception as e:
            logger.error(f"[ENGINE] Failed to read Customers for today: {e}")
            return

        for row in rows:
            if len(row) < 5:
                continue
            cid    = str(row[0]).strip()
            name   = str(row[1]).strip()
            phone  = str(row[2]).strip()
            date_  = str(row[3]).strip()
            time_  = str(row[4]).strip()
            reason = str(row[5]).strip() if len(row) > 5 else ""

            if date_ != today_str:
                continue

            # Safety: skip past times even on today
            if self._is_past_datetime(date_, time_):
                continue

            state_key = self.state.make_key(cid, date_, time_)
            if self.state.is_reminder_sent(state_key):
                continue

            logger.info(f"[ENGINE] 🌅 Same-day reminder → {name} ({phone}) at {time_}")
            if send_appointment_today_reminder(phone, name, time_, reason):
                self.state.set_reminder_sent(state_key)

    # ── Workflow 7/8: WhatsApp Reply Handler ─────────────────────────────────

    def handle_reply(self, phone: str, message: str):
        """Main entry for incoming WhatsApp messages. Routes to YES / NO / emergency / fallback."""
        clean_phone = str(phone).strip().replace("+91", "").replace("+", "")
        msg_lower   = message.strip().lower()

        logger.info(f"[ENGINE] 📩 Reply from {clean_phone}: '{message}'")

        # Emergency detection (Workflow 9)
        for kw in EMERGENCY_KEYWORDS:
            if kw in msg_lower:
                logger.warning(f"[ENGINE] 🚨 Emergency keyword '{kw}' from {phone}")
                send_emergency_reply(phone)
                return

        # YES (Workflow 7)
        if msg_lower in ("yes", "yes.", "y", "yeah", "ok", "okay", "confirm"):
            ctx = self._pending.get(clean_phone)
            if ctx:
                self._handle_yes(phone, clean_phone, ctx)
            else:
                send_fallback(phone)
            return

        # NO (Workflow 8)
        if msg_lower in ("no", "no.", "n", "nope", "decline"):
            ctx  = self._pending.get(clean_phone)
            name = ctx["name"] if ctx else ""
            if ctx:
                # Mark prediction DECLINED
                pred_key = ctx.get("pred_key", "")
                if pred_key:
                    self.state.set_prediction_status(pred_key, "DECLINED")
                # Clean up pending
                del self._pending[clean_phone]
                _save_pending(self._pending)
            send_no_reply(phone, name)
            return

        # Fallback (Workflow 10)
        send_fallback(phone)

    def _handle_yes(self, phone: str, clean_phone: str, ctx: dict):
        """
        Patient confirmed YES for a predicted appointment.
        1. Validate predicted date is still in the future
        2. Move to Customers sheet
        3. Clear from Future_Appointments
        4. Update state
        5. Send TYPE-A YES confirmation
        """
        cid         = ctx.get("customer_id", "")
        name        = ctx.get("name", "")
        future_date = ctx.get("future_date", "")
        reason      = ctx.get("reason", "")
        pred_key    = ctx.get("pred_key", "")

        # Safety: ensure predicted date is still in future
        if self._is_past_datetime(future_date):
            logger.warning(f"[ENGINE] ⏳ YES received but predicted date {future_date} is past. Ignoring.")
            send_fallback(phone)
            return

        # Fetch time from Future_Appointments row
        fa_row    = self.fa.get_future_row(cid)
        appt_time = fa_row.get("appointment_time", "TBD") if fa_row else "TBD"

        logger.info(f"[ENGINE] ✅ YES confirmed: {cid} | {future_date} | {reason}")

        # Move to Customers sheet
        try:
            values = [[cid, name, phone, future_date, appt_time, reason]]
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CUSTOMERS_SHEET}!A:F",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": values}
            ).execute()
            logger.info(f"[ENGINE] ✅ Moved {future_date} to Customers for {cid}")
        except Exception as e:
            logger.error(f"[ENGINE] Failed to write confirmed appointment: {e}")
            return

        # Clear from Future_Appointments
        self.fa.clear_confirmed_future_date(cid, future_date)

        # Update state
        if pred_key:
            self.state.set_prediction_status(pred_key, "CONFIRMED")

        # Remove from pending
        if clean_phone in self._pending:
            del self._pending[clean_phone]
            _save_pending(self._pending)

        # Send TYPE-A confirmation with actual date/time
        send_yes_confirmation(phone, name, future_date, appt_time)
