"""
dental_functions.py
-------------------
Function handlers for the Deepgram Agent telephony pipeline.
Called by server.py when Deepgram sends a FunctionCallRequest.

IDENTITY SESSION:
  verify_patient() stores the verified customer_id in PATIENT_SESSION.
  All subsequent calls (book/reschedule/cancel) check PATIENT_SESSION first.
  If a valid customer_id is present, name+phone are resolved from the DB —
  the LLM never needs to re-collect or re-pass them.
"""
import sys
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────
#  Per-call identity session  (reset at start of every Twilio call)
#  server.py calls reset_patient_session() on each new /media-stream
# ─────────────────────────────────────────────────────────────
PATIENT_SESSION: dict = {}
_session_lock = threading.Lock()

def reset_patient_session():
    """Clear the identity session. Called at the start of every new call."""
    global PATIENT_SESSION
    with _session_lock:
        PATIENT_SESSION.clear()
    print("[SESSION] Patient identity session reset.")

def get_session_customer_id() -> str | None:
    """Return the verified customer_id for this call, or None."""
    return PATIENT_SESSION.get("customer_id")

# Ensure parent directory is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────────────────────
#  Singleton DentalVoiceAgent (heavy — Calendar + Sheets auth)
#  Shared across all calls; thread-safe via a lock.
# ─────────────────────────────────────────────────────────────
_agent = None
_agent_lock = threading.Lock()

def _get_agent():
    """Return a shared DentalVoiceAgent instance, initialised on first call."""
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                from app import DentalVoiceAgent
                _agent = DentalVoiceAgent(use_voice=False, streaming=False)
                print("[DENTAL-FN] DentalVoiceAgent singleton initialized.")
    return _agent


# ─────────────────────────────────────────────────────────────
#  BOOK APPOINTMENT
# ─────────────────────────────────────────────────────────────
def book_appointment(date: str, time: str, reason: str,
                     customer_id: str = None,
                     name: str = None, phone: str = None) -> dict:
    """
    Book a new dental appointment.

    For VERIFIED (existing) patients — call with customer_id only:
      book_appointment(customer_id="CUST009", date="2026-04-02", time="10:00 AM", reason="checkup")

    For NEW patients — call with name + phone:
      book_appointment(name="John", phone="9876543210", date="2026-04-02", time="10:00 AM", reason="checkup")
    """
    # Prefer session customer_id over argument if available
    cid = customer_id or get_session_customer_id()

    print(f"[TOOL] book_appointment: cid={cid}, name={name}, phone={phone}, date={date}, time={time}, reason={reason}")
    try:
        agent = _get_agent()
        agent.reset_state()

        if cid:
            # Verified patient path — resolve name+phone from DB
            print(f"[TOOL] Using verified customer_id={cid} for booking")
            result_msg = agent._book_custom_by_id(cid, date, time, reason)
        elif name and phone:
            # New patient path
            result_msg = agent._book_custom(name, phone, date, time, reason)
        else:
            return {"error": "Please provide either a verified customer ID or both name and phone number."}

        print(f"[TOOL RESULT] {result_msg}")
        return {"result": result_msg}
    except Exception as e:
        err = f"I'm sorry, I couldn't book that appointment. Error: {str(e)}"
        print(f"[TOOL ERROR] book_appointment: {e}")
        return {"error": err}


# ─────────────────────────────────────────────────────────────
#  RESCHEDULE APPOINTMENT
# ─────────────────────────────────────────────────────────────
def reschedule_appointment(old_date: str, new_date: str, new_time: str,
                           customer_id: str = None,
                           name: str = None, phone: str = None) -> dict:
    """
    Reschedule using verified customer_id (preferred) or name+phone fallback.
    """
    cid = customer_id or get_session_customer_id()
    print(f"[TOOL] reschedule_appointment: cid={cid}, old={old_date} -> new={new_date} {new_time}")
    try:
        agent = _get_agent()
        agent.reset_state()
        if cid:
            result_msg = agent._reschedule_custom_by_id(cid, old_date, new_date, new_time)
        elif name and phone:
            result_msg = agent._reschedule_custom(name, phone, old_date, new_date, new_time)
        else:
            return {"error": "Please provide either a verified customer ID or both name and phone number."}
        print(f"[TOOL RESULT] {result_msg}")
        return {"result": result_msg}
    except Exception as e:
        err = f"I'm sorry, I couldn't reschedule that appointment. Error: {str(e)}"
        print(f"[TOOL ERROR] reschedule_appointment: {e}")
        return {"error": err}


# ─────────────────────────────────────────────────────────────
#  CANCEL APPOINTMENT
# ─────────────────────────────────────────────────────────────
def cancel_appointment(date: str,
                       customer_id: str = None,
                       name: str = None, phone: str = None) -> dict:
    """
    Cancel using verified customer_id (preferred) or name+phone fallback.
    """
    cid = customer_id or get_session_customer_id()
    print(f"[TOOL] cancel_appointment: cid={cid}, date={date}")
    try:
        agent = _get_agent()
        agent.reset_state()
        if cid:
            result_msg = agent._cancel_custom_by_id(cid, date)
        elif name and phone:
            result_msg = agent._cancel_custom(name, phone, date)
        else:
            return {"error": "Please provide either a verified customer ID or both name and phone number."}
        print(f"[TOOL RESULT] {result_msg}")
        return {"result": result_msg}
    except Exception as e:
        err = f"I'm sorry, I couldn't cancel that appointment. Error: {str(e)}"
        print(f"[TOOL ERROR] cancel_appointment: {e}")
        return {"error": err}


# ─────────────────────────────────────────────────────────────
#  VERIFY PATIENT — stores customer_id in PATIENT_SESSION
# ─────────────────────────────────────────────────────────────
def verify_patient(phone: str) -> dict:
    """
    Verify a patient's identity by phone number.
    Returns name and customer_id for confirmation only.
    Does NOT return appointment details — use lookup_appointments for that.
    """
    print(f"[TOOL] verify_patient: phone={phone}")
    try:
        agent = _get_agent()
        customer = agent.sheets.get_customer_by_phone(phone)
        if not customer:
            return {
                "found": False,
                "result": (
                    "No patient record found for this phone number. "
                    "This appears to be a new patient."
                )
            }
        name        = customer.get("name", "")
        customer_id = customer.get("customer_id", "")
        # Store in session so all subsequent tools can use it without re-asking
        with _session_lock:
            PATIENT_SESSION["customer_id"] = customer_id
            PATIENT_SESSION["name"]        = name
            PATIENT_SESSION["phone"]       = phone
        print(f"[SESSION] Identity verified and stored: {name} ({customer_id})")
        return {
            "found":       True,
            "name":        name,
            "customer_id": customer_id,
            "result":      f"Patient verified: {name} (ID: {customer_id}). Do not ask for name or phone again."
        }
    except Exception as e:
        err = f"I'm sorry, I couldn't verify patient details. Error: {str(e)}"
        print(f"[TOOL ERROR] verify_patient: {e}")
        return {"error": err}


# ─────────────────────────────────────────────────────────────
#  LOOKUP APPOINTMENTS
# ─────────────────────────────────────────────────────────────
def lookup_appointments(phone: str) -> dict:
    """
    Look up upcoming appointments for a patient by phone number.
    """
    print(f"[TOOL] lookup_appointments: phone={phone}")
    try:
        agent = _get_agent()
        # Look up customer by phone
        customer = agent.sheets.get_customer_by_phone(phone)
        if not customer:
            return {
                "result": (
                    "I couldn't find any record for that phone number. "
                    "Would you like me to book a new appointment for you?"
                )
            }

        customer_id = customer.get("customer_id")
        patient_name = customer.get("name", "you")
        appointments = agent.sheets.get_appointments_by_id(customer_id)

        if not appointments:
            return {"result": f"I don't see any appointments on file for {patient_name}. Would you like to book one?"}

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        upcoming = []
        for a in appointments:
            try:
                appt_date = datetime.strptime(a["appointment_date"], "%Y-%m-%d").date()
                if appt_date >= today:
                    upcoming.append(
                        f"{a['appointment_date']} at {a['appointment_time']}"
                        + (f" for {a['appointment_reason']}" if a.get("appointment_reason") else "")
                    )
            except Exception:
                continue

        if not upcoming:
            return {
                "result": (
                    f"I don't see any upcoming appointments for {patient_name}. "
                    "Would you like to book a new one?"
                )
            }

        count = len(upcoming)
        listing = ", and ".join(upcoming)
        return {
            "result": (
                f"I found {count} upcoming appointment{'s' if count > 1 else ''} for {patient_name}: "
                f"{listing}. Is there anything else I can help you with?"
            )
        }
    except Exception as e:
        err = f"I'm sorry, I couldn't look up that information right now. Error: {str(e)}"
        print(f"[TOOL ERROR] lookup_appointments: {e}")
        return {"error": err}


# ─────────────────────────────────────────────────────────────
#  FUNCTION MAP  (mirrors pharmacy FUNCTION_MAP)
# ─────────────────────────────────────────────────────────────
FUNCTION_MAP = {
    "verify_patient":         verify_patient,
    "book_appointment":       book_appointment,
    "reschedule_appointment": reschedule_appointment,
    "cancel_appointment":     cancel_appointment,
    "lookup_appointments":    lookup_appointments,
}
