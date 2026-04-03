"""
state_store.py
──────────────
Persistent per-appointment state flags for Smile Dental automation.

Prevents duplicate WhatsApp messages across restarts by tracking:
  - confirmation_sent         (bool) → booked/modified confirmation
  - reminder_sent             (bool) → 36h informational reminder (current appointments)
  - prediction_message_sent   (bool) → YES/NO request (predicted appointments only)
  - prediction_status         (str)  → PENDING | CONFIRMED | DECLINED

State is keyed by:
  "{customer_id}_{appointment_date}_{appointment_time}"

Example:
  "CUST001_2026-04-05_10:00 AM"
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.path.dirname(__file__), "appointment_state.json")


class StateStore:
    """
    Thread-safe (read-on-every-check) state store.
    Persists to appointment_state.json on every write.
    """

    def __init__(self):
        self._data: dict = self._load()

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                logger.warning("[STATE] Failed to load state store. Starting fresh.")
        return {}

    def _save(self):
        try:
            with open(STATE_PATH, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.error(f"[STATE] Failed to save state: {e}")

    # ── Key builder ───────────────────────────────────────────────────────────

    @staticmethod
    def make_key(customer_id: str, date: str, time: str) -> str:
        return f"{customer_id}_{date}_{time}"

    # ── Get helpers ───────────────────────────────────────────────────────────

    def _get_entry(self, key: str) -> dict:
        return self._data.get(key, {})

    def is_confirmation_sent(self, key: str) -> bool:
        return self._get_entry(key).get("confirmation_sent", False)

    def is_reminder_sent(self, key: str) -> bool:
        return self._get_entry(key).get("reminder_sent", False)

    def get_reminder_mode(self, key: str) -> str:
        """Returns 'SHORT_NOTICE', 'NORMAL', or '' if not set."""
        return self._get_entry(key).get("reminder_mode", "")

    def is_prediction_message_sent(self, key: str) -> bool:
        return self._get_entry(key).get("prediction_message_sent", False)

    def get_prediction_status(self, key: str) -> str:
        """Returns PENDING, CONFIRMED, DECLINED, or '' if unknown."""
        return self._get_entry(key).get("prediction_status", "")

    # ── Set helpers ───────────────────────────────────────────────────────────

    def set_confirmation_sent(self, key: str):
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["confirmation_sent"] = True
        self._save()
        logger.debug(f"[STATE] ✅ confirmation_sent = True for {key}")

    def set_reminder_sent(self, key: str, mode: str = "NORMAL"):
        """
        Mark reminder as sent.
        mode: 'NORMAL' (36h job) | 'SHORT_NOTICE' (immediate, no 36h job ever).
        """
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["reminder_sent"] = True
        self._data[key]["reminder_mode"] = mode
        self._save()
        logger.debug(f"[STATE] ✅ reminder_sent = True (mode={mode}) for {key}")

    def set_prediction_message_sent(self, key: str):
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["prediction_message_sent"] = True
        self._save()
        logger.debug(f"[STATE] ✅ prediction_message_sent = True for {key}")

    def set_prediction_status(self, key: str, status: str):
        """status: PENDING | CONFIRMED | DECLINED"""
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["prediction_status"] = status
        self._save()
        logger.debug(f"[STATE] Prediction status = {status} for {key}")

    def init_prediction(self, key: str):
        """Initialize a new predicted appointment with PENDING status."""
        if key not in self._data:
            self._data[key] = {}
        if "prediction_status" not in self._data[key]:
            self._data[key]["prediction_status"] = "PENDING"
            self._data[key]["prediction_message_sent"] = False
            self._save()
            logger.debug(f"[STATE] 🔮 Prediction initialized (PENDING) for {key}")

    def purge_past_states(self, current_keys: set):
        """
        Remove state entries for appointments that no longer exist in the sheet.
        Keeps the store clean over time.
        """
        stale = [k for k in self._data if k not in current_keys]
        for k in stale:
            del self._data[k]
        if stale:
            self._save()
            logger.info(f"[STATE] 🧹 Purged {len(stale)} stale state entries.")
