"""
future_appointments.py
──────────────────────
Manages the Future_Appointments sheet tab in the existing
Dental_Customer_Database Google Spreadsheet.

Sheet columns:
  A: Customer ID
  B: Name
  C: Phone Number
  D: Appointment Date       (first/current visit date)
  E: Appointment Time
  F: Appointment Reason
  G: Future_Appointment1
  H: Future_Appointment2
  I: Future_Appointment3
  ...  (dynamically expanded based on total sittings)

Rules:
  - Do NOT overwrite a confirmed future appointment date.
  - A confirmed date is one that has been moved to the Customers sheet
    (it will be absent from its column when confirmed).
  - When deleting a cancelled appointment, remove the entire row.
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

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
FUTURE_SHEET_NAME = "Future_Appointments"
SHEETS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "sheets_config.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "token.pickle")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "..", "credentials.json")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# Base headers (columns A–F fixed, G+ are dynamic future slots)
BASE_HEADERS = [
    "Customer ID", "Name", "Phone Number",
    "Appointment Date", "Appointment Time", "Appointment Reason"
]
MAX_FUTURE_COLS = 10   # Support up to 10 future appointments


class FutureAppointmentsManager:

    def __init__(self):
        self.service = self._authenticate()
        self.spreadsheet_id = self._load_spreadsheet_id()
        self._ensure_future_sheet()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self):
        """Re-use existing token.pickle for authentication."""
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
                raise RuntimeError(
                    "Google Sheets credentials not found or expired. "
                    "Please run the main app first to generate token.pickle."
                )
        return build("sheets", "v4", credentials=creds)

    def _load_spreadsheet_id(self) -> str:
        """Load spreadsheet ID from sheets_config.json."""
        if not os.path.exists(SHEETS_CONFIG_PATH):
            raise FileNotFoundError(f"sheets_config.json not found at {SHEETS_CONFIG_PATH}")
        with open(SHEETS_CONFIG_PATH, "r") as f:
            config = json.load(f)
        sid = config.get("spreadsheet_id")
        if not sid:
            raise ValueError("spreadsheet_id missing in sheets_config.json")
        logger.info(f"[FA] Using spreadsheet: {sid}")
        return sid

    # ── Sheet Init ────────────────────────────────────────────────────────────

    def _ensure_future_sheet(self):
        """Create Future_Appointments sheet if it doesn't already exist."""
        try:
            meta = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()
            existing = [s["properties"]["title"] for s in meta.get("sheets", [])]

            if FUTURE_SHEET_NAME not in existing:
                body = {"requests": [{"addSheet": {"properties": {"title": FUTURE_SHEET_NAME}}}]}
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id, body=body
                ).execute()
                self._write_headers()
                logger.info(f"[FA] Created sheet: {FUTURE_SHEET_NAME}")
            else:
                logger.info(f"[FA] Sheet '{FUTURE_SHEET_NAME}' already exists.")
        except Exception as e:
            logger.error(f"[FA] Error ensuring future sheet: {e}")
            raise

    def _write_headers(self):
        """Write base + dynamic future appointment headers."""
        future_headers = [f"Future_Appointment{i}" for i in range(1, MAX_FUTURE_COLS + 1)]
        all_headers = BASE_HEADERS + future_headers
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{FUTURE_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [all_headers]}
        ).execute()

    # ── Sheet ID helper ───────────────────────────────────────────────────────

    def _get_sheet_gid(self) -> int:
        """Return the GID (sheetId) of the Future_Appointments sheet."""
        meta = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        for s in meta.get("sheets", []):
            if s["properties"]["title"] == FUTURE_SHEET_NAME:
                return s["properties"]["sheetId"]
        return 0

    # ── Read helpers ──────────────────────────────────────────────────────────

    def _read_all_rows(self) -> list[list]:
        """Read all rows (including header) from Future_Appointments."""
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{FUTURE_SHEET_NAME}!A:Z"
        ).execute()
        return result.get("values", [])

    def _find_row_index(self, customer_id: str, rows: list[list]) -> Optional[int]:
        """
        Return 1-based row index for customer_id, or None.
        Row 1 = header, data starts at row 2.
        """
        cid = str(customer_id).strip().upper()
        for i, row in enumerate(rows[1:], start=2):
            if row and str(row[0]).strip().upper() == cid:
                return i
        return None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_future_row(self, customer_id: str) -> Optional[dict]:
        """
        Return the Future_Appointments row for customer_id as a dict, or None.
        """
        rows = self._read_all_rows()
        idx = self._find_row_index(customer_id, rows)
        if idx is None:
            return None
        row = rows[idx - 1]  # 0-indexed
        result = {
            "customer_id":        row[0] if len(row) > 0 else "",
            "name":               row[1] if len(row) > 1 else "",
            "phone":              row[2] if len(row) > 2 else "",
            "appointment_date":   row[3] if len(row) > 3 else "",
            "appointment_time":   row[4] if len(row) > 4 else "",
            "appointment_reason": row[5] if len(row) > 5 else "",
            "future_dates":       [row[i] for i in range(6, len(row)) if len(row) > i and row[i]],
            "_row_index":         idx
        }
        return result

    def upsert_future_row(
        self,
        customer_id: str,
        name: str,
        phone: str,
        appt_date: str,
        appt_time: str,
        reason: str,
        future_dates: list[str]
    ) -> bool:
        """
        Insert or update row for customer_id.
        - If existing row exists: only fill EMPTY future columns (never overwrite confirmed dates).
        - If no existing row: insert a new one.
        """
        try:
            rows = self._read_all_rows()
            idx = self._find_row_index(customer_id, rows)

            # Build base + future columns
            base_values = [customer_id, name, phone, appt_date, appt_time, reason]
            future_cols = [""] * MAX_FUTURE_COLS

            for i, fd in enumerate(future_dates[:MAX_FUTURE_COLS]):
                future_cols[i] = fd

            full_row = base_values + future_cols

            if idx is None:
                # ── INSERT new row ─────────────────────────────────────────
                self.service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{FUTURE_SHEET_NAME}!A:Z",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [full_row]}
                ).execute()
                logger.info(f"[FA] ✅ Inserted future row for {customer_id}")
            else:
                # ── UPDATE existing row — only fill empty future slots ──────
                existing = rows[idx - 1]
                # Extend existing row to full width
                while len(existing) < len(full_row):
                    existing.append("")

                for i in range(6, 6 + MAX_FUTURE_COLS):
                    col_i = i - 6  # 0-based future col index
                    if col_i >= len(future_cols):
                        break
                    # Only update if cell is currently empty
                    if not existing[i]:
                        existing[i] = future_cols[col_i]

                # Also update base columns (date/time/reason may have changed)
                existing[3] = appt_date
                existing[4] = appt_time
                existing[5] = reason

                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{FUTURE_SHEET_NAME}!A{idx}",
                    valueInputOption="RAW",
                    body={"values": [existing]}
                ).execute()
                logger.info(f"[FA] ✅ Updated future row for {customer_id} at row {idx}")

            return True
        except Exception as e:
            logger.error(f"[FA] ❌ Error upserting future row: {e}")
            return False

    def delete_future_row(self, customer_id: str) -> bool:
        """Delete the entire Future_Appointments row for customer_id."""
        try:
            rows = self._read_all_rows()
            idx = self._find_row_index(customer_id, rows)
            if idx is None:
                logger.warning(f"[FA] No future row found for {customer_id} — nothing to delete.")
                return True   # Idempotent

            sheet_gid = self._get_sheet_gid()
            requests = [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_gid,
                        "dimension": "ROWS",
                        "startIndex": idx - 1,
                        "endIndex": idx
                    }
                }
            }]
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests}
            ).execute()
            logger.info(f"[FA] ✅ Deleted future row for {customer_id}")
            return True
        except Exception as e:
            logger.error(f"[FA] ❌ Error deleting future row: {e}")
            return False

    def clear_confirmed_future_date(self, customer_id: str, confirmed_date: str) -> bool:
        """
        Remove a specific future date from the row (after patient replies YES).
        Clears the cell that matches confirmed_date.
        """
        try:
            rows = self._read_all_rows()
            idx = self._find_row_index(customer_id, rows)
            if idx is None:
                return False

            row = rows[idx - 1]
            updated = False
            for i in range(6, len(row)):
                if str(row[i]).strip() == str(confirmed_date).strip():
                    row[i] = ""
                    updated = True
                    break

            if not updated:
                logger.warning(f"[FA] Date {confirmed_date} not found in row for {customer_id}")
                return False

            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{FUTURE_SHEET_NAME}!A{idx}",
                valueInputOption="RAW",
                body={"values": [row]}
            ).execute()
            logger.info(f"[FA] ✅ Cleared confirmed date {confirmed_date} for {customer_id}")
            return True
        except Exception as e:
            logger.error(f"[FA] ❌ Error clearing confirmed date: {e}")
            return False

    def get_all_pending_future_appointments(self) -> list[dict]:
        """
        Return all non-empty future appointment slots across all customers.
        Used by scheduler to check for 1.5-day reminders.

        Returns list of:
          {
            "customer_id": str,
            "name": str,
            "phone": str,
            "reason": str,
            "future_date": str,
            "future_col_index": int   # 0-based (0 = Appointment1)
          }
        """
        try:
            rows = self._read_all_rows()
            pending = []
            for row in rows[1:]:   # skip header
                if not row or len(row) < 7:
                    continue
                cid    = row[0] if len(row) > 0 else ""
                name   = row[1] if len(row) > 1 else ""
                phone  = row[2] if len(row) > 2 else ""
                reason = row[5] if len(row) > 5 else ""

                for col_i in range(6, min(len(row), 6 + MAX_FUTURE_COLS)):
                    val = str(row[col_i]).strip()
                    if val:
                        pending.append({
                            "customer_id":     cid,
                            "name":            name,
                            "phone":           phone,
                            "reason":          reason,
                            "future_date":     val,
                            "future_col_index": col_i - 6
                        })
            return pending
        except Exception as e:
            logger.error(f"[FA] ❌ Error reading pending future appointments: {e}")
            return []
