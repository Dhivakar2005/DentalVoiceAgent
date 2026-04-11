"""
services_parser.py

Reads services_details.json and resolves:
  - Total sittings (always maximum of range)
  - Gap days between visits (midpoint of range)
  - Future appointment date generation

Sitting rules:
  "1 sitting"     → 1
  "1-2 sittings"  → 2
  "2-3 sittings"  → 3
  "3 sittings"    → 3
  "2-4 sittings"  → 4
  "4-5 sittings"  → 5
  "5-10+ sittings"→ 10

Gap rules (midpoint):
  "2-5 days"    → 3
  "3-5 days"    → 4
  "3-7 days"    → 5
  "2-7 days"    → 4
  "5-7 days"    → 6
  "5-10 days"   → 7
  "7-10 days"   → 8
  "1 week"      → 7
  "None"        → 0  (single sitting — no future dates)
"""

import json
import re
import os
import structlog
from datetime import date, timedelta
from typing import Optional

logger = structlog.get_logger(__name__)

#  Load services JSON 
_SERVICES_PATH = os.path.join(os.path.dirname(__file__), "..", "services_details.json")

def _load_services() -> list[dict]:
    """Return flat list of all services from services_details.json."""
    with open(_SERVICES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    services = []
    for stage in data.get("stages", []):
        for svc in stage.get("services", []):
            services.append(svc)
    return services

_ALL_SERVICES: list[dict] = _load_services()


#  Parsers ─

def parse_sittings(text: str) -> int:
    """
    Extract the MAXIMUM number of sittings from the sittings string.
    Examples:
      "1 sitting"      → 1
      "1-2 sittings"   → 2
      "2-3 sittings"   → 3
      "4-5 sittings"   → 5
      "5-10+ sittings" → 10
      "Each sitting as needed" → 1
    """
    if not text:
        return 1

    text_lower = text.lower().strip()

    # Special cases
    if "each sitting as needed" in text_lower:
        return 1
    if "1 (placement)" in text_lower or "1 (scan + plan)" in text_lower or "1 sitting (tray fit)" in text_lower:
        return 1
    if "1 sitting (hospital)" in text_lower or "1 sitting (fitting)" in text_lower:
        return 1

    # Find all numbers (handles "5-10+" → [5, 10])
    numbers = re.findall(r"\d+", text)
    if numbers:
        return max(int(n) for n in numbers)
    return 1


def parse_gap_days(text: str) -> int:
    """
    Extract the midpoint gap in days.
    Returns 0 if no gap (single sitting or 'None').
    """
    if not text:
        return 0

    text_lower = text.lower().strip()

    # Explicit no-gap patterns
    no_gap_patterns = [
        "none", "single visit", "in-chair", "no gap", "not applicable",
        "as clinically indicated", "as prescribed", "periodic",
        "spread over weeks", "follow-up as needed", "osseointegrate",
        "heal", "daily use", "monthly", "every"
    ]
    for pat in no_gap_patterns:
        if pat in text_lower:
            return 0

    # "1 week" → 7
    if "1 week" in text_lower and not re.search(r"\d+-\d+", text_lower):
        return 7

    # "X-Y days" → midpoint
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*days?", text_lower)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return round((lo + hi) / 2)

    # "X days" → X
    m = re.search(r"(\d+)\s*days?", text_lower)
    if m:
        return int(m.group(1))

    # "X weeks" → X*7
    m = re.search(r"(\d+)\s*weeks?", text_lower)
    if m:
        return int(m.group(1)) * 7

    return 0



#  Canonical alias map ─
# Maps normalised spoken/typed names → exact service name in services_details.json
# Keys are lowercase. Values must match a service name exactly (case-insensitive check).
_ALIASES: dict[str, str] = {
    # Checkup / Preventive
    "regular checkup":                        "Routine Recall Checkup & Cleaning",
    "checkup":                                "Routine Recall Checkup & Cleaning",
    "routine checkup":                        "Routine Recall Checkup & Cleaning",
    "routine check up":                       "Routine Recall Checkup & Cleaning",
    "dental checkup":                         "Routine Recall Checkup & Cleaning",
    "general checkup":                        "Routine Recall Checkup & Cleaning",
    "cleaning":                               "Scaling & Polishing (Routine Clean)",
    "scaling":                                "Scaling & Polishing (Routine Clean)",
    "scaling and polishing":                  "Scaling & Polishing (Routine Clean)",
    # Root Canal  ("Root Canal Treatment" is now the single canonical service name)
    "root canal treatment":                   "Root Canal Treatment",
    "root canal treatement":                  "Root Canal Treatment",  # typo
    "root canal":                             "Root Canal Treatment",
    "rct":                                    "Root Canal Treatment",
    "root canal therapy":                     "Root Canal Treatment",
    "root canal anterior":                    "Root Canal Treatment",
    "root canal posterior":                   "Root Canal Treatment",
    # Smile Design / Cosmetic
    "smile design":                           "Smile Design / Smile Makeover",
    "smile makeover":                         "Smile Design / Smile Makeover",
    "smile designing":                        "Smile Design / Smile Makeover",
    # Whitening
    "teeth whitening":                        "In-Office Teeth Whitening",
    "tooth whitening":                        "In-Office Teeth Whitening",
    "whitening":                              "In-Office Teeth Whitening",
    # Filling
    "filling":                                "Composite / Amalgam Filling",
    "tooth filling":                          "Composite / Amalgam Filling",
    "cavity filling":                         "Composite / Amalgam Filling",
    # Extraction
    "tooth extraction":                       "Simple Tooth Extraction",
    "extraction":                             "Simple Tooth Extraction",
    "tooth removal":                          "Simple Tooth Extraction",
    # Crown
    "crown":                                  "Dental Crown - PFM / Full Ceramic / Zirconia",
    "dental crown":                           "Dental Crown - PFM / Full Ceramic / Zirconia",
    "cap":                                    "Dental Crown - PFM / Full Ceramic / Zirconia",
    # Implant
    "implant":                                "Dental Implant - Surgical Placement",
    "dental implant":                         "Dental Implant - Surgical Placement",
    # Braces
    "braces":                                 "Metal / Ceramic Fixed Braces",
    "orthodontic treatment":                  "Metal / Ceramic Fixed Braces",
    # Wisdom tooth
    "wisdom tooth removal":                   "Simple Wisdom Tooth Removal",
    "wisdom tooth extraction":                "Surgical Wisdom Tooth Removal",
}


def _match_service(reason: str) -> Optional[dict]:
    """
    Fuzzy-match a treatment reason to a service entry.

    Priority:
      1. Exact alias table lookup  (handles common spoken names + typos)
      2. Direct substring match    (reason ⊆ svc_name or svc_name ⊆ reason)
      3. Word-overlap scoring      (≥ 1 shared word)
    """
    if not reason:
        return None

    reason_lower = reason.lower().strip()

    #  Priority 1: Alias table ─
    alias_target = _ALIASES.get(reason_lower)
    if alias_target:
        for svc in _ALL_SERVICES:
            if svc.get("service", "").lower() == alias_target.lower():
                logger.info("[PARSER] Alias matched '%s' -> '%s'", reason, svc['service'])
                return svc

    reason_words = set(reason_lower.split())
    best_score = 0
    best_service = None

    for svc in _ALL_SERVICES:
        svc_name = svc.get("service", "").lower()

        #  Priority 2: Direct substring match 
        if reason_lower in svc_name or svc_name in reason_lower:
            logger.info("[PARSER] Substring matched '%s' -> '%s'", reason, svc['service'])
            return svc

        #  Priority 3: Word overlap scoring 
        svc_words = set(svc_name.split())
        overlap = len(reason_words & svc_words)
        if overlap > best_score:
            best_score = overlap
            best_service = svc

    if best_score >= 1:
        logger.info("[PARSER] Word-overlap matched '%s' -> '%s' (score=%d)", reason, best_service['service'], best_score)
        return best_service

    logger.warning("[PARSER] No match found for reason: '%s' -- using defaults (1 sitting)", reason)
    return None


def get_service_info(reason: str) -> dict:
    """
    Returns dict with:
      {
        "service": str,
        "total_sittings": int,
        "gap_days": int
      }
    Falls back to 1 sitting / 0 gap if no match found.
    """
    svc = _match_service(reason)
    if svc:
        total_sittings = parse_sittings(svc.get("sittings", "1 sitting"))
        gap_days = parse_gap_days(svc.get("gap_between_visits", "None"))
        return {
            "service": svc.get("service", reason),
            "total_sittings": total_sittings,
            "gap_days": gap_days
        }
    return {
        "service": reason,
        "total_sittings": 1,
        "gap_days": 0
    }


def calculate_future_dates(base_date: date, total_sittings: int, gap_days: int) -> list[str]:
    """
    Calculate future appointment dates.

    base_date      = first appointment date (already in Customers sheet)
    total_sittings = max sittings for the treatment
    gap_days       = gap between visits

    Returns list of future dates (YYYY-MM-DD strings), EXCLUDING base_date.
    If only 1 sitting or gap_days == 0 → returns empty list.

    Example:
      base = 2026-04-01, sittings = 3, gap = 5
      → ["2026-04-06", "2026-04-11"]
    """
    if total_sittings <= 1 or gap_days <= 0:
        return []

    future_dates = []
    for n in range(1, total_sittings):
        fd = base_date + timedelta(days=gap_days * n)
        future_dates.append(fd.strftime("%Y-%m-%d"))

    logger.info(f"[PARSER] Future dates for {total_sittings} sittings / {gap_days}d gap: {future_dates}")
    return future_dates


#  Convenience one-shot function ─

def get_future_dates_for_reason(reason: str, base_date_str: str) -> dict:
    """
    Given an appointment reason and base date string (YYYY-MM-DD),
    returns full info + computed future dates.

    Returns:
      {
        "service": str,
        "total_sittings": int,
        "gap_days": int,
        "future_dates": ["YYYY-MM-DD", ...]
      }
    """
    from datetime import datetime
    try:
        base = datetime.strptime(base_date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"[PARSER] Invalid base_date format: {base_date_str}")
        return {"service": reason, "total_sittings": 1, "gap_days": 0, "future_dates": []}

    info = get_service_info(reason)
    info["future_dates"] = calculate_future_dates(base, info["total_sittings"], info["gap_days"])
    return info
