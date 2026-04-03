"""
sheet_watcher.py
────────────────
Polls the Customers sheet every 30 seconds.
Detects new rows, modified rows (date/time changed), and deleted rows.
Fires events to the automation engine.

Snapshot format (in-memory + persisted to watcher_snapshot.json):
  {
    "CUST001_2026-04-01_10:00 AM": {
        "customer_id": "CUST001",
        "name": "...",
        "phone": "...",
        "appointment_date": "2026-04-01",
        "appointment_time": "10:00 AM",
        "appointment_reason": "..."
    },
    ...
  }
"""

import os
import json
import pickle
import logging
from typing import Callable, Optional
from datetime import datetime

from googleapiclient.discovery import build
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

CUSTOMERS_SHEET   = "Customers"
SNAPSHOT_PATH     = os.path.join(os.path.dirname(__file__), "watcher_snapshot.json")
TOKEN_PATH        = os.path.join(os.path.dirname(__file__), "..", "token.pickle")
SHEETS_CONFIG_PATH= os.path.join(os.path.dirname(__file__), "..", "sheets_config.json")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _make_key(row: dict) -> str:
    """Unique key per appointment row — Customer ID + Date + Time."""
    return f"{row['customer_id']}_{row['appointment_date']}_{row['appointment_time']}".strip()


class SheetWatcher:
    """
    Watches the Customers sheet for row-level changes and fires callbacks.

    Callbacks:
      on_new(row)             → new appointment detected
      on_modified(old, new)   → date or time changed for existing customer
      on_deleted(row)         → row was removed
    """

    def __init__(
        self,
        on_new: Callable[[dict], None],
        on_modified: Callable[[dict, dict], None],
        on_deleted: Callable[[dict], None]
    ):
        self.on_new      = on_new
        self.on_modified = on_modified
        self.on_deleted  = on_deleted
        self.service = self._authenticate()
        self.spreadsheet_id = self._load_spreadsheet_id()
        self._snapshot: dict[str, dict] = self._load_snapshot()

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
        with open(SHEETS_CONFIG_PATH, "r") as f:
            return json.load(f)["spreadsheet_id"]

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def _load_snapshot(self) -> dict:
        if os.path.exists(SNAPSHOT_PATH):
            try:
                with open(SNAPSHOT_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_snapshot(self):
        with open(SNAPSHOT_PATH, "w") as f:
            json.dump(self._snapshot, f, indent=2)

    # ── Sheet reader ─────────────────────────────────────────────────────────

    def _fetch_current_rows(self) -> dict[str, dict]:
        """
        Read Customers sheet and return dict keyed by appointment key.
        Also indexed by customer_id for change detection.
        """
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CUSTOMERS_SHEET}!A:F"
            ).execute()
            raw = result.get("values", [])
        except Exception as e:
            logger.error(f"[WATCHER] Failed to read Customers sheet: {e}")
            return {}

        if len(raw) <= 1:
            return {}

        current: dict[str, dict] = {}
        for row in raw[1:]:
            if not row or len(row) < 5:
                continue
            r = {
                "customer_id":        str(row[0]).strip() if len(row) > 0 else "",
                "name":               str(row[1]).strip() if len(row) > 1 else "",
                "phone":              str(row[2]).strip() if len(row) > 2 else "",
                "appointment_date":   str(row[3]).strip() if len(row) > 3 else "",
                "appointment_time":   str(row[4]).strip() if len(row) > 4 else "",
                "appointment_reason": str(row[5]).strip() if len(row) > 5 else "",
            }
            if r["customer_id"]:
                key = _make_key(r)
                current[key] = r
        return current

    # ── Main poll method ──────────────────────────────────────────────────────

    def check_for_changes(self):
        """
        Compare current sheet state with last snapshot.
        Fire callbacks for new/modified/deleted rows.
        """
        logger.debug("[WATCHER] Polling Customers sheet...")
        current = self._fetch_current_rows()

        # ── Silent Startup Logic ──────────────────────────────────────────────
        # If the snapshot is completely empty (first run), we baseline the 
        # current rows without firing 'on_new' events. This avoids 
        # spamming notifications for old historical records.
        if not self._snapshot and current:
            logger.info(f"[WATCHER] 🤫 First run detected. Baselining {len(current)} existing rows silently.")
            self._snapshot = current
            self._save_snapshot()
            return

        prev_keys = set(self._snapshot.keys())
        curr_keys = set(current.keys())

        # ── New rows ─────────────────────────────────────────────────────────
        new_keys = curr_keys - prev_keys
        for key in new_keys:
            row = current[key]
            logger.info(f"[WATCHER] 🆕 New appointment: {key}")
            try:
                self.on_new(row)
            except Exception as e:
                logger.error(f"[WATCHER] on_new callback error: {e}")

        # ── Deleted rows ──────────────────────────────────────────────────────
        deleted_keys = prev_keys - curr_keys
        for key in deleted_keys:
            row = self._snapshot[key]
            # Check if it was really deleted (not just moved with a new time/date)
            # Detect modification: same customer_id with a different key in current
            cid = row["customer_id"]
            # Find if this customer_id exists with a different key
            curr_for_cid = [r for r in current.values() if r["customer_id"] == cid]
            prev_for_cid = [r for r in self._snapshot.values() if r["customer_id"] == cid]

            if curr_for_cid and len(curr_for_cid) == len(prev_for_cid):
                # Same customer, different key → it's a modification
                old_row = prev_for_cid[0]
                new_row = curr_for_cid[0]
                mod_key = _make_key(new_row)

                # Only fire if this modified key wasn't already handled as "new"
                if mod_key not in new_keys:
                    continue

                logger.info(f"[WATCHER] ✏️ Modified appointment: {cid}")
                try:
                    self.on_modified(old_row, new_row)
                except Exception as e:
                    logger.error(f"[WATCHER] on_modified callback error: {e}")
            else:
                # Truly deleted
                logger.info(f"[WATCHER] 🗑️ Deleted appointment: {key}")
                try:
                    self.on_deleted(row)
                except Exception as e:
                    logger.error(f"[WATCHER] on_deleted callback error: {e}")

        # ── Save new snapshot ─────────────────────────────────────────────────
        self._snapshot = current
        self._save_snapshot()
        logger.debug(f"[WATCHER] Snapshot saved ({len(current)} rows).")
