"""
whatsapp_service.py
───────────────────
WhatsApp Cloud API sender for Smile Dental scheduling automation.

Message Types (per spec):
  A) CONFIRMATION   — Booking/modification confirmed. Informational only. No YES/NO.
  B) REMINDER       — 36h before current appointment. Informational only. No YES/NO.
  C) YES/NO REQUEST — Predicted future appointments only. Requires patient reply.
  D) SYSTEM REPLIES — YES confirmation, NO ack, emergency, fallback.
"""

import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
ACCESS_TOKEN    = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
CLINIC_NAME     = os.getenv("CLINIC_NAME", "Smile Dental")
CLINIC_NUMBER   = os.getenv("CLINIC_NUMBER", "+91XXXXXXXXXX")
GRAPH_API_URL   = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"


def _normalize_phone(phone: str) -> str:
    """Ensure phone is in E.164 format without '+' (e.g. 918610080257)."""
    phone = str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")
    if len(phone) == 10:
        phone = "91" + phone   # assume India
    return phone


def send_whatsapp_message(phone: str, message: str) -> bool:
    """
    Core sender — POST text message via WhatsApp Cloud API.
    Returns True on success, False on failure.
    """
    if not PHONE_NUMBER_ID or not ACCESS_TOKEN:
        logger.error("[WA] WHATSAPP_PHONE_NUMBER_ID or WHATSAPP_ACCESS_TOKEN not set in .env")
        return False

    to = _normalize_phone(phone)
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type":    "individual",
        "to":                to,
        "type":              "text",
        "text": {
            "preview_url": False,
            "body":        message
        }
    }
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type":  "application/json"
    }

    try:
        resp = requests.post(GRAPH_API_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            logger.info(f"[WA] ✅ Sent to {to}")
            return True
        else:
            logger.error(f"[WA] ❌ Failed ({resp.status_code}): {resp.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"[WA] ❌ Request error: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPE A — CONFIRMATION (Booking / Modification)
# Informational only. NO YES/NO request.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_confirmation(phone: str, name: str, date: str, time: str, reason: str) -> bool:
    """
    Sent immediately after a NEW appointment is booked.
    Informational only — does NOT ask patient to reply YES/NO.
    """
    msg = (
        f"Hello {name}, your appointment at {CLINIC_NAME} has been confirmed!\n\n"
        f"📅 Date    : {date}\n"
        f"🕐 Time    : {time}\n"
        f"🦷 Reason  : {reason}\n\n"
        f"Please arrive 5 minutes early. See you soon!\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-A] Sending booking confirmation to {phone}")
    return send_whatsapp_message(phone, msg)


def send_modification_notice(phone: str, name: str, date: str, time: str, reason: str) -> bool:
    """
    Sent when an existing appointment is rescheduled.
    Informational only — does NOT ask patient to reply YES/NO.
    """
    msg = (
        f"Hello {name}, your appointment at {CLINIC_NAME} has been updated.\n\n"
        f"📅 New Date : {date}\n"
        f"🕐 New Time : {time}\n"
        f"🦷 Reason   : {reason}\n\n"
        f"If you have any questions, please call us at {CLINIC_NUMBER}.\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-A] Sending modification notice to {phone}")
    return send_whatsapp_message(phone, msg)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPE B — REMINDER (36h before CURRENT appointment)
# Informational only. NO YES/NO request.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_current_appointment_reminder(phone: str, name: str, date: str, time: str, reason: str) -> bool:
    """
    Sent exactly 36 hours before a CONFIRMED (current) appointment.
    Informational only — no response needed from patient.
    """
    msg = (
        f"Hello {name}, just a friendly reminder from {CLINIC_NAME}!\n\n"
        f"Your appointment is coming up:\n"
        f"📅 Date   : {date}\n"
        f"🕐 Time   : {time}\n"
        f"🦷 Reason : {reason}\n\n"
        f"No action needed. We look forward to seeing you!\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-B] Sending 36h reminder to {phone} for {date} {time}")
    return send_whatsapp_message(phone, msg)


def send_appointment_today_reminder(phone: str, name: str, time: str, reason: str) -> bool:
    """
    Sent at 8:00 AM on the day of the appointment.
    Informational only — no response needed.
    """
    msg = (
        f"Good morning {name}! 🌟\n\n"
        f"You have a dental appointment today at {CLINIC_NAME}.\n"
        f"🕐 Time   : {time}\n"
        f"🦷 Reason : {reason}\n\n"
        f"Please arrive 5 minutes early. See you soon!\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-B] Sending same-day reminder to {phone}")
    return send_whatsapp_message(phone, msg)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPE C — YES/NO REQUEST (PREDICTED appointments ONLY)
# Requires patient reply to confirm or decline.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_predicted_appointment_confirmation_request(
    phone: str, name: str, treatment: str, predicted_date: str
) -> bool:
    """
    Sent 36h before a PREDICTED future appointment date.
    Asks patient to reply YES (confirm) or NO (decline).
    This is the ONLY message type that uses YES/NO.
    """
    msg = (
        f"Hello {name}, based on your treatment plan at {CLINIC_NAME},\n"
        f"we have scheduled your next sitting:\n\n"
        f"🦷 Treatment : {treatment}\n"
        f"📅 Date      : {predicted_date}\n\n"
        f"Please reply:\n"
        f"✅ *YES* to confirm this appointment\n"
        f"❌ *NO* to decline (our team will contact you)\n\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-C] Sending YES/NO prediction request to {phone} for {predicted_date}")
    return send_whatsapp_message(phone, msg)


def send_future_visits_info(phone: str, name: str, treatment: str, total_sittings: int) -> bool:
    """
    Sent silently after booking when a multi-sitting treatment is detected.
    Informs patient that future visits will be predicted and confirmed via WhatsApp.
    """
    msg = (
        f"Hello {name}, your treatment *{treatment}* may require "
        f"up to *{total_sittings} sittings*.\n\n"
        f"We will send you a WhatsApp message before each predicted visit "
        f"for you to confirm. No action needed right now.\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-C-INFO] Sending multi-sitting info to {phone}")
    return send_whatsapp_message(phone, msg)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TYPE D — SYSTEM REPLIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_yes_confirmation(phone: str, name: str, date: str, time: str) -> bool:
    """Sent when patient replies YES to a predicted appointment request."""
    msg = (
        f"✅ Confirmed! Your appointment at {CLINIC_NAME} has been booked.\n\n"
        f"📅 Date : {date}\n"
        f"🕐 Time : {time}\n\n"
        f"We will send you a reminder closer to the date. See you then!\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-D] Sending YES confirmation to {phone}")
    return send_whatsapp_message(phone, msg)


def send_no_reply(phone: str, name: str) -> bool:
    """Sent when patient replies NO to a predicted appointment request."""
    msg = (
        f"Thank you for letting us know, {name}. "
        f"The appointment has been cancelled.\n\n"
        f"Our team will contact you to reschedule at a suitable time.\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-D] Sending NO acknowledgement to {phone}")
    return send_whatsapp_message(phone, msg)


def send_cancellation_notice(phone: str, name: str, date: str) -> bool:
    """Sent when clinic staff deletes an appointment from the sheet."""
    msg = (
        f"Hello {name}, your appointment scheduled on *{date}* "
        f"at {CLINIC_NAME} has been cancelled.\n\n"
        f"Please call us at {CLINIC_NUMBER} to reschedule.\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-D] Sending cancellation notice to {phone}")
    return send_whatsapp_message(phone, msg)


def send_emergency_reply(phone: str) -> bool:
    """Immediate response when emergency keywords detected."""
    msg = (
        f"🚨 We received your message. "
        f"Please visit {CLINIC_NAME} immediately "
        f"or call us directly at {CLINIC_NUMBER}.\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-D] Sending emergency reply to {phone}")
    return send_whatsapp_message(phone, msg)


def send_fallback(phone: str) -> bool:
    """Catch-all for unrecognized messages."""
    msg = (
        f"Thank you for reaching out to {CLINIC_NAME}. "
        f"Our team will get back to you shortly.\n"
        f"For urgent concerns, please call {CLINIC_NUMBER}.\n"
        f"— {CLINIC_NAME}"
    )
    logger.info(f"[WA] [TYPE-D] Sending fallback to {phone}")
    return send_whatsapp_message(phone, msg)
