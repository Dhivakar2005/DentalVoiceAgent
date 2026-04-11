"""
sheet_watcher.py

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
        "appointment_reason": "...",
        "appointment_reason": "..."
    },
    ...
  }
"""

import os
import json
import pickle
import structlog
from typing import Callable, Optional
from datetime import datetime

from googleapiclient.discovery import build
from google.auth.transport.requests import Request

logger = structlog.get_logger(__name__)

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

    #  Auth 

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

    #  Snapshot 

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

    #  Sheet reader ─

    def _fetch_current_rows(self) -> Optional[dict[str, dict]]:
        """
        Read Customers sheet and return dict keyed by appointment key.
        Also indexed by customer_id for change detection.
        Returns None if an API error occurs to prevent mass-wiping snapshot.
        """
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CUSTOMERS_SHEET}!A:K"
            ).execute()
            raw = result.get("values", [])
        except Exception as e:
            logger.error(f"[WATCHER] Failed to read Customers sheet: {e}")
            return None

        if len(raw) <= 1:
            return {}

        current: dict[str, dict] = {}
        for row in raw[1:]:
            if not row or len(row) < 5:
                continue
            
            # Skip PREDICTED rows — unless they are PENDING (manually confirmed or overridden)
            # Type is Column I (index 8), WhatsApp is Column K (index 10)
            type_val      = str(row[8]).strip() if len(row) > 8 else "BOOKED"
            whatsapp_conf = str(row[10]).strip().upper() if len(row) > 10 else ""
            
            if type_val == "PREDICTED" and whatsapp_conf != "PENDING":
                continue

            r = {
                "customer_id":        str(row[0]).strip() if len(row) > 0 else "",
                "name":               str(row[1]).strip() if len(row) > 1 else "",
                "phone":              str(row[2]).strip() if len(row) > 2 else "",
                "appointment_date":   str(row[3]).strip() if len(row) > 3 else "",
                "appointment_time":   str(row[4]).strip() if len(row) > 4 else "",
                "appointment_reason": str(row[5]).strip() if len(row) > 5 else "",
                "doctor":             str(row[6]).strip() if len(row) > 6 else "Unassigned",
                "type":               str(row[8]).strip() if len(row) > 8 else "BOOKED",
                "status":             str(row[9]).strip().upper() if len(row) > 9 else "BOOKED",
                "whatsapp_conf":      str(row[10]).strip().upper() if len(row) > 10 else "",
            }
            if r["customer_id"]:
                key = _make_key(r)
                current[key] = r
        return current

    #  Main poll method 

    def check_for_changes(self):
        """
        Compare current sheet state with last snapshot.
        Fire callbacks for new/modified/deleted rows.
        """
        logger.debug("[WATCHER] Polling Customers sheet...")
        current = self._fetch_current_rows()

        if current is None:
            logger.warning("[WATCHER] Aborting check_for_changes due to fetch/API error.")
            return

        #  Silent Startup Logic 
        # If the snapshot is completely empty (first run), we baseline the 
        # current rows without firing 'on_new' events. This avoids 
        # spamming notifications for old historical records.
        if not self._snapshot and current:
            logger.info(f"[WATCHER] 🤫 First run detected. Baselining existing rows silently.")
            # We baseline everything EXCEPT the pending ones, so they trigger below.
            self._snapshot = {k:v for k,v in current.items() if v.get("whatsapp_conf") != "PENDING"}
            self._save_snapshot()
            # Continue so pending logic below can run
            pass

        prev_keys = set(self._snapshot.keys())
        curr_keys = set(current.keys())

        #  Detect modifications first 
        new_keys_set = curr_keys - prev_keys
        del_keys_set = prev_keys - curr_keys
        
        modifications = []
        for old_key in list(del_keys_set):
            row = self._snapshot[old_key]
            cid = row["customer_id"]
            
            # Find the corresponding new key for this CID that was just created
            potential_new_keys = [k for k in new_keys_set if current[k]["customer_id"] == cid]
            
            # If there's exactly one deleted row and one new row for this customer, it's a modification
            potential_old_keys = [k for k in del_keys_set if self._snapshot[k]["customer_id"] == cid]
            
            if len(potential_new_keys) == 1 and len(potential_old_keys) == 1:
                new_key = potential_new_keys[0]
                new_row = current[new_key]
                modifications.append((row, new_row))
                
                new_keys_set.remove(new_key)
                del_keys_set.remove(old_key)

        #  Detect fresh PENDING rows (Primary Trigger) ─
        pending_rows = [r for r in current.values() if r.get("whatsapp_conf") == "PENDING"]
        
        for row in pending_rows:
            key = _make_key(row)
            logger.info(f"[WATCHER] 🔔 Found PENDING row: {key}")
            try:
                self.on_new(row)
                # Remove from new_keys_set so we don't double-fire
                if key in new_keys_set:
                    new_keys_set.remove(key)
            except Exception as e:
                logger.error(f"[WATCHER] on_new (PENDING) error: {e}")

        #  Trigger Remaining Callbacks (Shadow/Diff Logic) ─
        
        for old_row, new_row in modifications:
            logger.info(f"[WATCHER] ✏️ Modified appointment: {old_row['customer_id']}")
            try:
                self.on_modified(old_row, new_row)
            except Exception as e:
                logger.error(f"[WATCHER] on_modified error: {e}")

        for key in new_keys_set:
            row = current[key]
            logger.info(f"[WATCHER] 🆕 New appointment (New Key): {key}")
            try:
                self.on_new(row)
            except Exception as e:
                logger.error(f"[WATCHER] on_new error: {e}")

        # Bulk-deletion protection: If more than 2 rows are deleted at once, it's likely a sheet cleanup.
        if len(del_keys_set) > 2:
            logger.warning(f"[WATCHER] 🛑 Mass deletion detected ({len(del_keys_set)} rows). Skipping WhatsApp cancellation notices to prevent spam.")
        else:
            for key in del_keys_set:
                row = self._snapshot[key]
                logger.info(f"[WATCHER] 🗑️ Deleted appointment: {key}")
                try:
                    self.on_deleted(row)
                except Exception as e:
                    logger.error(f"[WATCHER] on_deleted callback error: {e}")

        #  Save new snapshot ─
        self._snapshot = current
        self._save_snapshot()
        logger.debug(f"[WATCHER] Snapshot saved ({len(current)} rows).")


