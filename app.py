import os
import pickle
import re
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import io

AUDIO_BACKEND = "sounddevice"
TTS_AVAILABLE = True

try:
    import sounddevice as sd
    from scipy.io import wavfile
except ImportError:
    AUDIO_BACKEND = None

try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False

import requests
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google_sheets_manager import GoogleSheetsManager

try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False

# ── CONFIG 
OLLAMA_BASE_URL          = "http://localhost:11434"
OLLAMA_MODEL             = "qwen2.5-coder:3b"
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]
TIMEZONE                 = "Asia/Kolkata"
APPOINTMENT_DURATION_MIN = 10
SAMPLE_RATE              = 16000
DURATION                 = 3
LLM_TIMEOUT_SECONDS      = 8
LLM_NUM_CTX              = 512
LLM_NUM_PREDICT          = 80

# ── FAQ KNOWLEDGE BASE ────────────────────────────────────────────────────────
FAQ_DATABASE = {
    "hours": {
        "keywords": ["hours", "open", "close", "timing", "when open", "what time", "operating hours", "work time", "working hours"],
        "answer": "We are open Monday to Saturday, 9 AM to 5 PM. We are closed on Sundays."
    },
    "booking_methods": {
        "keywords": ["how to book", "how do i book", "book appointment", "make appointment", "schedule appointment", "booking process", "how can i book"],
        "answer": "You can book by calling our voice assistant, using our website chat, or calling us directly."
    },
    "customer_id_info": {
        "keywords": ["what is customer id", "why need id", "is id required", "do i need patient id", "what is patient id", "what is my id"],
        "answer": "Your Customer ID (format CUST001) helps us access your records instantly. New patients receive one after their first appointment."
    },
    "booking_window": {
        "keywords": ["how far", "advance", "how many days", "book ahead", "future appointment", "days in advance"],
        "answer": "You can book appointments up to 3 days in advance from today."
    },
    "services": {
        "keywords": ["service", "services", "what do you offer", "treatments", "procedures", "what can you do", "dental services", "what treatment"],
        "answer": (
            "We offer: General Dental Checkup, Tooth Filling, Root Canal Treatment, "
            "Braces and Teeth Alignment, Gum Treatment and Scaling, Crowns Bridges and Dentures, "
            "Tooth Extraction, Pediatric Dental Care, Dental X-rays, and Oral Disease Evaluation."
        )
    },
    "appointment_duration": {
        "keywords": ["how long", "duration", "appointment length", "how much time", "long is appointment"],
        "answer": "Standard appointments are 10 minutes. Complex procedures may require a longer slot."
    },
    "reschedule_cancel_faq": {
        "keywords": ["how to cancel", "how to reschedule", "cancellation procedure", "rescheduling procedure", "change appointment", "move appointment"],
        "answer": "Just tell me your name, phone number, and current appointment date and time — I will handle the rest."
    },
    "booking_info": {
        "keywords": ["what information", "what do i need", "what details", "required information", "need to provide"],
        "answer": "New patients need name, phone, preferred date and time, and reason for visit. Existing patients need their Customer ID."
    },
    "forgot_id": {
        "keywords": ["forgot id", "don't know id", "lost id", "forgot customer id", "don't have id"],
        "answer": "No problem! Give me your name and phone number and I will look up your records."
    },
    "late_cancellation": {
        "keywords": ["penalty", "late cancel", "cancellation fee", "charge cancel", "cancel late"],
        "answer": "No penalty, but we appreciate at least 24 hours notice so the slot can go to another patient."
    },
    "pediatric": {
        "keywords": ["pediatric", "child", "children", "kids", "baby teeth", "child dentist", "kid dentist", "paediatric"],
        "answer": "Yes! We have a pediatric specialist for child-friendly treatments."
    },
    "xray_records": {
        "keywords": ["x-ray", "xray", "x ray", "records", "dental records", "get my records", "medical records"],
        "answer": "Yes, you can request copies of your X-ray records and dental history. Ask via the assistant or contact us directly."
    },
    "doctors_staff": {
        "keywords": ["doctors", "dentist", "staff", "who works", "specialist", "team", "professionals"],
        "answer": "Our clinic has experienced general dentists, a pediatric specialist, and orthodontic professionals — all licensed."
    },
    "insurance": {
        "keywords": ["insurance", "coverage", "accept insurance", "dental plan", "health plan"],
        "answer": "We accept most major dental insurance plans. Bring your insurance card to verify coverage."
    },
    "location_parking": {
        "keywords": ["location", "address", "where are you", "directions", "parking", "how to get here"],
        "answer": "Please visit our website or call us for our address. Parking is available near the clinic."
    },
    "emergency": {
        "keywords": ["emergency", "urgent", "toothache", "broken tooth", "dental emergency", "severe pain"],
        "answer": "For dental emergencies, call us immediately. We do our best to accommodate urgent cases quickly."
    },
    "cost_fees": {
        "keywords": ["cost", "price", "fee", "how much", "charges", "payment", "expensive"],
        "answer": "Treatment costs vary by procedure. Visit us for a consultation and we will give you a detailed estimate."
    },
    "checkup_frequency": {
        "keywords": ["how often", "frequency", "when should i come", "regular checkup", "routine check"],
        "answer": "Visit every 6 months for a routine check-up and cleaning, even if you have no pain."
    },
    "treatment_pain": {
        "keywords": ["painful", "going to hurt", "hurt", "pain during", "scared of pain", "anesthesia"],
        "answer": "Most modern dental treatments are nearly painless thanks to local anesthesia and advanced techniques."
    },
    "bleeding_gums": {
        "keywords": ["bleeding gums", "blood when brushing", "gingivitis", "gum disease", "gums bleed"],
        "answer": "Bleeding gums may indicate gingivitis. A check-up and cleaning can help significantly."
    },
    "brushing_time": {
        "keywords": ["how long brush", "brushing time", "how to brush", "minutes to brush"],
        "answer": "Brush for at least 2 minutes twice daily with a soft-bristled toothbrush and fluoride toothpaste."
    },
    "tooth_decay": {
        "keywords": ["tooth decay", "cavity", "cavities", "causes decay", "enamel"],
        "answer": "Tooth decay happens when bacteria produce acids from sugar, damaging the tooth enamel."
    },
    "flossing": {
        "keywords": ["floss", "flossing", "clean between teeth"],
        "answer": "Yes — flossing removes food and plaque between teeth where a toothbrush cannot reach."
    },
    "tooth_pain_advice": {
        "keywords": ["tooth pain", "my tooth hurts", "what to do pain", "swollen tooth", "home remedy"],
        "answer": "Rinse with warm water and see a dentist immediately. Avoid self-medication."
    },
    "xray_safety": {
        "keywords": ["xray safe", "x-rays safe", "radiation", "are x-rays dangerous"],
        "answer": "Yes — dental X-rays use very low radiation and are safe when taken only when necessary."
    },
    "bad_breath": {
        "keywords": ["bad breath", "smelly breath", "halitosis", "mouth odor"],
        "answer": "Brush twice daily, floss, clean your tongue, drink water, and get regular dental cleanings."
    },
    "good_foods": {
        "keywords": ["good foods", "what to eat", "healthy food for teeth", "calcium", "diet"],
        "answer": "Calcium-rich foods like milk, cheese, fruits, vegetables, and nuts help maintain strong teeth."
    },
}

# ── FAST KEYWORD EXTRACTORS  (<1ms, no LLM) ──────────────────────────────────

_INTENT_PATTERNS = {
    # More specific patterns first to avoid false matches
    "reschedule": [
        r'\b(reschedule|rescheduling|change|move|shift|update|modify)\b.{0,40}\bappointment\b',
        r'\bappointment\b.{0,40}\b(change|move|reschedule|different\s*time|new\s*time)\b',
    ],
    "cancel": [
        r'\b(cancel|cancell\w*|remove|delete)\b.{0,40}\bappointment\b',
        r'\bappointment\b.{0,40}\b(cancel|remove|delete)\b',
    ],
    "view_appointments": [
        r'\b(view|show|see|check|list)\b.{0,30}\bappointment',
        r'\b(my|upcoming|scheduled)\b.{0,30}\bappointment',
    ],
    "book": [
        r'\b(book|booking|schedule|scheduli\w*|make|set\s*up)\b.{0,40}\bappointment\b',
        r'\bappointment\b.{0,40}\b(book|schedule|make|want|need)\b',
        r'\b(see|visit|come\s*in|consult)\b.{0,30}\b(dentist|doctor|dental|clinic)\b',
        r'\b(need|want|like|wish)\b.{0,30}\b(appointment|visit|checkup|check.up|consultation)\b',
        r'\bi\s+(need|want)\b.{0,30}\b(appointment|dentist|dental|tooth|teeth)\b',
        r'\b(book|schedule)\b',
    ],
}


def fast_extract_intent(text):
    t = text.lower().strip()
    for intent, patterns in _INTENT_PATTERNS.items():
        for p in patterns:
            if re.search(p, t):
                return intent
    return None


def fast_patient_type(text):
    """
    Handles typos (exesting, existng, exisiting) and all natural phrasings.
    Only matches when the text is genuinely about patient type — not when
    the user is answering a different question (name, reason, date, etc.).
    """
    t = text.lower().strip()

    # Short-circuit: if the text is long and looks like a reason / date / name,
    # don't try to extract patient type from it.
    # Patient-type answers are usually short (1-5 words).
    words = t.split()
    if len(words) > 8:
        return None

    new_patterns = [
        r'\bnew\s*patient\b',
        r'\bi\s+am\s+new\b',
        r'\bfirst.?time\b',
        r'\bfirst\s+visit\b',
        r'\bnever\b.{0,20}(been|visit|come)',
        r'\bnot\b.{0,20}(old|exist|register)',
        r'\bdon.t\s+have\b.{0,20}(id|account)',
        r'\bno\s+id\b',
    ]
    for p in new_patterns:
        if re.search(p, t):
            return 'new'

    # Lone "new" only when the whole message is just that word or very short
    if re.match(r'^new$', t) or re.match(r'^new\s+patient$', t):
        return 'new'

    existing_patterns = [
        r'\bex[ise]{0,3}[ts][tin]{0,3}[gi]?\w*\b',  # existing + typos
        r'\bold\s*patient\b',
        r'\bi\s+am\s+old\b',
        r'\balready\s+(registered|a\s+patient|have)\b',
        r'\breturn\w*\s+patient\b',
        r'\bhave\b.{0,20}(customer\s*id|patient\s*id|cust\s*id)',
        r'\bbeen\s+here\s+before\b',
        r'\bregistered\s+patient\b',
        r'\bcame\s+before\b',
        r'\bvisited\s+before\b',
        r'\bmy\s+(customer\s*)?id\s+is\b',
        r'\bcust\s*\d',
        r'\bprevious\s+patient\b',
    ]
    for p in existing_patterns:
        if re.search(p, t):
            return 'old'

    # Lone "old" / "existing" only — never "yes" (that belongs to fast_yes_no)
    if re.match(r'^(old|existing)$', t):
        return 'old'

    return None


def fast_extract_customer_id(text, awaiting=False):
    t = text.strip()
    m = re.search(r'\bCUST[\s\-]?(\d{1,4})\b', t, re.IGNORECASE)
    if m: return f"CUST{m.group(1).zfill(3)}"
    m = re.search(r'\b(?:customer\s*)?(?:id|number|no|num)[\s\-:]*(?:is\s*)?(\d{1,4})\b', t, re.IGNORECASE)
    if m: return f"CUST{m.group(1).zfill(3)}"
    m = re.search(r'\b(?:my|it|the)\s+(?:\w+\s+)?is\s+(\d{1,4})\b', t, re.IGNORECASE)
    if m: return f"CUST{m.group(1).zfill(3)}"
    if awaiting:
        m = re.search(r'\b(\d{1,4})\b', t)
        if m: return f"CUST{m.group(1).zfill(3)}"
    return None


def fast_extract_name(text, awaiting=False):
    t = text.strip()
    m = re.search(
        r'\b(?:my\s+name\s+is|name\s*[:\-]\s*|this\s+is|call\s+me|name\s+is)\s+([A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?)',
        t, re.IGNORECASE
    )
    if m:
        name = m.group(1).strip().title()
        if len(name) >= 2: return name

    if awaiting:
        m = re.search(r'\bi\s*am\s+([A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?)\b', t, re.IGNORECASE)
        if m:
            name = m.group(1).strip().title()
            if len(name) >= 2: return name
        # Bare name (1-2 words, only letters)
        if re.match(r'^[A-Za-z]+(?:\s+[A-Za-z]+)?$', t) and 2 <= len(t) <= 40:
            _EXCLUDE = {
                'yes','no','new','old','book','cancel','reschedule','appointment',
                'patient','existing','hello','hi','hey','okay','ok','sure','thanks',
            }
            words = t.lower().split()
            if not any(w in _EXCLUDE for w in words):
                return t.title()
    return None


def fast_extract_phone(text):
    digits = re.sub(r'[^\d]', '', text)
    if len(digits) == 10: return digits
    if len(digits) == 12 and digits.startswith('91'): return digits[2:]
    if len(digits) == 11 and digits.startswith('0'):  return digits[1:]
    return None


def fast_extract_date(text):
    t = text.lower().strip()
    today = datetime.now(ZoneInfo(TIMEZONE))

    if re.search(r'\btoday\b', t):
        return today.strftime("%Y-%m-%d")
    if re.search(r'\btomorrow\b', t):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r'\bday\s+after\s+tomorrow\b', t):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    m = re.search(r'\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\b', t)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2: y = "20" + y
        try: return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except: pass

    m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
    if m: return m.group()

    _MONTHS = {
        'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
        'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
        'january':1,'february':2,'march':3,'april':4,'june':6,
        'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    }
    _MON_RE = '|'.join(_MONTHS.keys())
    m = re.search(rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON_RE})\b', t)
    if m:
        day = int(m.group(1)); mon = _MONTHS[m.group(2)]
        yr  = today.year
        try:
            base = datetime(yr, mon, day, tzinfo=ZoneInfo(TIMEZONE))
            if base < today: yr += 1
            return datetime(yr, mon, day).strftime("%Y-%m-%d")
        except: pass
    m = re.search(rf'\b({_MON_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b', t)
    if m:
        mon = _MONTHS[m.group(1)]; day = int(m.group(2))
        yr  = today.year
        try:
            base = datetime(yr, mon, day, tzinfo=ZoneInfo(TIMEZONE))
            if base < today: yr += 1
            return datetime(yr, mon, day).strftime("%Y-%m-%d")
        except: pass

    return None


def fast_extract_time(text):
    t = text.strip()
    tu = t.upper()
    m = re.search(r'\b(\d{1,2}):(\d{2})\s*([AP]M)\b', tu)
    if m: return f"{m.group(1)}:{m.group(2)} {m.group(3)}"
    m = re.search(r'\b(\d{1,2})\s*([AP]M)\b', tu)
    if m: return f"{m.group(1)}:00 {m.group(2)}"
    tl = t.lower()
    m = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(?:in\s+the\s+)?(morning|afternoon|evening|night)\b', tl)
    if m:
        h = int(m.group(1)); mn = m.group(2) or '00'; period = m.group(3)
        if period in ('afternoon','evening') and h < 12: h += 12
        if period == 'night' and h < 12: h += 12
        s = 'PM' if h >= 12 else 'AM'; h12 = h if h <= 12 else h - 12
        if h12 == 0: h12 = 12
        return f"{h12}:{mn} {s}"
    m = re.search(r'\b(\d{1,2}):(\d{2})\b', t)
    if m:
        h = int(m.group(1)); mn = m.group(2)
        s = 'PM' if h >= 12 else 'AM'; h12 = h if h <= 12 else h - 12
        if h12 == 0: h12 = 12
        return f"{h12}:{mn} {s}"
    return None


def fast_yes_no(text):
    t = text.lower().strip()
    if re.search(
        r'\b(yes|yeah|yep|yup|yea|ya|correct|confirm|confirmed|ok|okay|'
        r'sure|go\s+ahead|proceed|sounds\s+good|right|perfect|'
        r'looks\s+good|book\s+it|do\s+it|fine|absolutely|definitely|please)\b', t
    ):
        return 'yes'
    if re.search(
        r'\b(no|nope|nah|naa|wrong|change|edit|different|incorrect|'
        r'not\s+right|modify|update|fix)\b', t
    ):
        return 'no'
    return None


# ── VOICE INTERFACE ───────────────────────────────────────────────────────────
class VoiceInterface:
    def __init__(self, use_voice=True):
        self.use_voice = use_voice and (AUDIO_BACKEND is not None) and TTS_AVAILABLE
        if self.use_voice:
            self.engine = pyttsx3.init()
            voices = self.engine.getProperty('voices')
            if len(voices) > 1:
                self.engine.setProperty('voice', voices[1].id)
            self.engine.setProperty('rate', 150)
            self.engine.setProperty('volume', 1.0)
            if SPEECH_RECOGNITION_AVAILABLE:
                self.recognizer = sr.Recognizer()
        else:
            print("\n[WARNING] Running in TEXT MODE")

    def speak(self, text):
        print(f"\nAgent: {text}")
        if self.use_voice and TTS_AVAILABLE:
            try:
                tts_text = text.replace("*", "").replace("\n", ". ")
                self.engine.say(tts_text)
                self.engine.runAndWait()
            except Exception as e:
                print(f"TTS Error: {e}")

    def record_audio_sounddevice(self, duration=DURATION):
        try:
            print(f"[RECORDING] {duration}s...")
            audio_data = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype=np.int16)
            sd.wait()
            return audio_data
        except Exception as e:
            print(f"Recording error: {e}"); return None

    def audio_to_text_sounddevice(self, audio_data):
        if not SPEECH_RECOGNITION_AVAILABLE: return "error"
        try:
            buf = io.BytesIO()
            wavfile.write(buf, SAMPLE_RATE, audio_data); buf.seek(0)
            with sr.AudioFile(buf) as src:
                audio = self.recognizer.record(src)
                text  = self.recognizer.recognize_google(audio)
                print(f"Patient: {text}"); return text
        except sr.UnknownValueError: return "unknown"
        except Exception as e: print(f"Recognition error: {e}"); return "error"

    def listen(self):
        if self.use_voice and AUDIO_BACKEND == "sounddevice":
            try:
                audio_data = self.record_audio_sounddevice()
                return self.audio_to_text_sounddevice(audio_data) if audio_data is not None else "error"
            except Exception as e:
                print(f"Microphone error: {e}. Falling back to text.")
        return input("\nPatient (type): ").strip()


# ── GOOGLE CALENDAR ───────────────────────────────────────────────────────────
class GoogleCalendarManager:
    def __init__(self):
        self.service = self._authenticate()

    def _authenticate(self):
        creds = None
        if os.path.exists("token.pickle"):
            with open("token.pickle", "rb") as f:
                creds = pickle.load(f)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                for attempt in range(3):
                    try: creds.refresh(Request()); break
                    except Exception as e:
                        if attempt == 2: raise
                        time.sleep(2)
            else:
                flow  = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.pickle", "wb") as f:
                pickle.dump(creds, f)
        return build("calendar", "v3", credentials=creds)

    def is_available(self, start_dt, end_dt):
        res = self.service.events().list(
            calendarId="primary", timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(), singleEvents=True,
        ).execute()
        return len(res.get("items", [])) == 0

    def create_appointment(self, name, phone, start_dt, reason, customer_id=None):
        end_dt = start_dt + timedelta(minutes=APPOINTMENT_DURATION_MIN)
        if not self.is_available(start_dt, end_dt): return None
        desc = f"Patient: {name}\nPhone: {phone}\nReason: {reason}"
        if customer_id: desc = f"Customer ID: {customer_id}\n" + desc
        event = {
            "summary":     f"Dental - {name}",
            "description": desc,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": TIMEZONE},
        }
        created = self.service.events().insert(calendarId="primary", body=event).execute()
        return created["id"]

    def find_appointment(self, name, phone, date):
        start  = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TIMEZONE))
        end    = start + timedelta(days=1)
        events = self.service.events().list(
            calendarId="primary", timeMin=start.isoformat(),
            timeMax=end.isoformat(), q=name,
        ).execute().get("items", [])
        for e in events:
            if phone in e.get("description", ""): return e
        return None

    def cancel(self, event_id):
        self.service.events().delete(calendarId="primary", eventId=event_id).execute()


# ── DENTAL VOICE AGENT ────────────────────────────────────────────────────────
class DentalVoiceAgent:
    def __init__(self, use_voice=True):
        self.calendar       = GoogleCalendarManager()
        self.sheets         = GoogleSheetsManager()
        self.voice          = VoiceInterface(use_voice=use_voice)
        self.state          = {}
        self.awaiting_field = None
        self.reset_state()

    def reset_state(self):
        self.state = {
            "intent":             None,
            "patient_type":       None,
            "customer_id":        None,
            "name":               None,
            "phone":              None,
            "date":               None,
            "time":               None,
            "new_date":           None,
            "new_time":           None,
            "reason":             None,
            "customer_confirmed": False,
            "workflow_state":     "IDLE",
        }
        self.awaiting_field = None

    # ── LLM — last resort only ────────────────────────────────────────────────
    def _call_llm(self, text):
        system = (
            'Dental appointment assistant. Extract from user input. '
            'REPLY ONLY IN VALID JSON, nothing else:\n'
            '{"intent":"book|reschedule|cancel|view_appointments|none",'
            '"patient_type":"new|old|empty","name":"or empty","phone":"digits only or empty",'
            '"customer_id":"CUST### or empty","date":"YYYY-MM-DD or empty",'
            '"time":"H:MM AM/PM or empty","new_date":"YYYY-MM-DD or empty",'
            '"new_time":"H:MM AM/PM or empty","reason":"or empty",'
            '"user_confirmed":false,"user_rejected":false,'
            '"general_answer":"1-sentence answer if general question, else empty"}'
        )
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model":      OLLAMA_MODEL,
                    "messages":   [{"role": "system", "content": system},
                                   {"role": "user",   "content": text}],
                    "stream":     False,
                    "keep_alive": -1,
                    "options":    {"num_predict": LLM_NUM_PREDICT,
                                   "temperature": 0.1, "num_ctx": LLM_NUM_CTX},
                },
                timeout=LLM_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            raw   = resp.json()["message"]["content"].strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match: return None
            parsed = json.loads(re.sub(r',\s*([}\]])', r'\1', match.group()))

            # Normalise via fast extractors
            if parsed.get("customer_id"):
                cid = fast_extract_customer_id(parsed["customer_id"])
                if cid: parsed["customer_id"] = cid
            if parsed.get("phone"):
                parsed["phone"] = re.sub(r'[^\d]', '', parsed["phone"])
            for k in ("date", "new_date"):
                if parsed.get(k):
                    try: datetime.strptime(parsed[k], "%Y-%m-%d")
                    except: parsed[k] = fast_extract_date(text)
            for k in ("time", "new_time"):
                if parsed.get(k):
                    parsed[k] = fast_extract_time(parsed[k]) or fast_extract_time(text)
            pt = fast_patient_type(text)
            if pt: parsed["patient_type"] = pt
            return parsed

        except requests.exceptions.Timeout:
            print("[LLM] Timeout")
        except Exception as e:
            print(f"[LLM] Error: {e}")
        return None

    # ── STATE HELPERS ─────────────────────────────────────────────────────────
    def _update(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None and v != "":
                if k == "intent" and v == "none" and self.state.get("intent"):
                    continue
                self.state[k] = v

    def _missing(self):
        intent = self.state.get("intent")
        pt     = self.state.get("patient_type")

        if not pt:
            return ["patient_type"]
        if pt == "old":
            if not self.state.get("customer_id"):
                return ["customer_id"]
            if not self.state.get("customer_confirmed"):
                return ["customer_confirmation"]

        if intent == "book":
            fields = ["name", "phone", "date", "time", "reason"]
        elif intent == "reschedule":
            fields = ["name", "phone", "date", "time", "new_date", "new_time"]
        elif intent == "cancel":
            fields = ["name", "phone", "date", "time"]
        else:
            fields = []

        return [f for f in fields if not self.state.get(f)]

    def _prompt_for(self, field):
        intent = self.state.get("intent")
        prompts = {
        "patient_type":  "Are you a new or existing patient?",
        "customer_id":   "Please tell me your customer ID, for example CUST001.",
        "name":          "What is your full name?",
        "phone":         "What is your phone number?",
        "date": (
            "What is the date of your existing appointment?"
            if intent in ("reschedule", "cancel")
            else "What date would you like? You can say tomorrow or give a specific date."
        ),
        "time": (
            "What time is your existing appointment?"
            if intent in ("reschedule", "cancel")
            else "What time would you prefer? We are open 9 AM to 5 PM."
        ),
        "reason":    "What is the reason for your visit?",
        "new_date":  "What is your preferred new date?",
        "new_time":  "What is your preferred new time?",
    }
        return prompts.get(field, f"Please provide your {field}.") 

    def _confirm_prompt(self):
        s = self.state; i = s.get("intent", "")
        if i == "book":
            # Pre-generate customer ID for new patients so it shows in confirmation
            if s.get("patient_type") == "new" and not s.get("customer_id"):
                try:
                    s["customer_id"] = self.sheets.generate_customer_id()
                except Exception:
                    s["customer_id"] = None
            cid_note = f" Your new customer ID will be {s['customer_id']}." if s.get("patient_type") == "new" and s.get("customer_id") else ""
            return (f"Just to confirm — booking for {s['name']} on {s['date']} "
                    f"at {s['time']} for {s['reason']}.{cid_note} Say yes to confirm or no to change.")
        if i == "reschedule":
            return (f"Moving {s['name']}'s appointment from {s['date']} at {s['time']} "
                    f"to {s['new_date']} at {s['new_time']}. Say yes or no.")
        if i == "cancel":
            return (f"Cancelling {s['name']}'s appointment on {s['date']} at {s['time']}. "
                    f"Say yes to confirm or no to cancel.")
        return "Shall I go ahead? Say yes to confirm or no to edit."

    # ── FAST FIELD EXTRACTION ─────────────────────────────────────────────────
    def _extract_fast(self, text):
        """
        Extract every possible field from text using fast regex only (<1ms).
        Returns a dict of found values.  Covers >90% of real inputs.

        Guards:
        - Never overwrites patient_type once it is already set in state.
        - Never extracts customer_id for a known new patient.
        - Never extracts customer_id when we are waiting for a different field.
        """
        found = {}
        af    = self.awaiting_field
        state = self.state

        # Intent
        intent = fast_extract_intent(text)
        if intent: found["intent"] = intent

        # Patient type — only extract if not yet decided
        if not state.get("patient_type"):
            pt = fast_patient_type(text)
            if pt: found["patient_type"] = pt

        # Customer ID — only for existing patients or when explicitly asked
        is_new_patient = (
            state.get("patient_type") == "new"
            or found.get("patient_type") == "new"
        )
        want_cid = (
            af == "customer_id"
            or (state.get("patient_type") == "old" and not state.get("customer_id"))
        )
        if want_cid and not is_new_patient:
            cid = fast_extract_customer_id(text, awaiting=(af == "customer_id"))
            if cid: found["customer_id"] = cid

        # Name — only when waiting for it or clearly stated
        name = fast_extract_name(text, awaiting=(af == "name"))
        if name: found["name"] = name

        # Phone — only when not already set (prevents phone being re-extracted
        # from later messages that happen to contain digits)
        if not state.get("phone"):
            phone = fast_extract_phone(text)
            if phone: found["phone"] = phone

        # Date / new_date
        date_val = fast_extract_date(text)
        if date_val:
            if af == "new_date" or (state.get("date") and not state.get("new_date")):
                found["new_date"] = date_val
            elif not state.get("date"):
                found["date"] = date_val

        # Time / new_time
        time_val = fast_extract_time(text)
        if time_val:
            if af == "new_time" or (state.get("time") and not state.get("new_time")):
                found["new_time"] = time_val
            elif not state.get("time"):
                found["time"] = time_val

        # Reason — only when explicitly waiting for it
        if af == "reason":
            stripped = re.sub(
                r'\b(my|i|the|have|a|an|for|is|need|want|it|reason|visit|'
                r'because|came|coming|here)\b',
                '', text.lower()
            ).strip(" .,")
            if stripped and len(stripped) > 2:
                found["reason"] = stripped.title()

        return found

    # ── MAIN RESPONSE GENERATOR ───────────────────────────────────────────────
    def generate_response(self, text):
        try:
            # ── 1. Confirmation fast-path (no LLM) ──────────────────────────
            if self.state.get("workflow_state") == "WAITING_CONFIRMATION":
                decision = fast_yes_no(text)
                if decision == "yes":
                    self.state["workflow_state"] = "COMPLETED"
                    return self._execute()
                if decision == "no":
                    self.state["workflow_state"] = "COLLECTING_DETAILS"
                    return "What would you like to change? You can say name, date, time, or reason."
                return "Please say yes to confirm or no to make changes."

            # ── 2. FAQ short-circuit (no LLM) ───────────────────────────────
            if self.awaiting_field != "customer_id":
                t_lower = text.lower()
                for faq in FAQ_DATABASE.values():
                    for kw in faq.get("keywords", []):
                        if kw in t_lower:
                            return faq["answer"]

            # ── 3. Fast extraction (no LLM) ──────────────────────────────────
            fast_found = self._extract_fast(text)
            print(f"[FAST] {fast_found}")

            self._update(**fast_found)

            # Clear awaiting_field if just resolved
            if self.awaiting_field and fast_found.get(self.awaiting_field):
                self.awaiting_field = None

            # ── 4. Decide if LLM is needed ───────────────────────────────────
            already_in_flow  = bool(self.state.get("intent") or self.state.get("patient_type"))
            intent_resolved  = bool(self.state.get("intent"))
            need_llm = not intent_resolved and not already_in_flow and not fast_found.get("intent")

            if need_llm:
                print("[LLM] Calling — intent not resolved by fast extractors")
                llm_data = self._call_llm(text)
                if llm_data:
                    for k, v in llm_data.items():
                        if v and not self.state.get(k):
                            self.state[k] = v
                    if not self.state.get("intent"):
                        ga = llm_data.get("general_answer", "")
                        if ga: return ga
                        return "I can help you book, reschedule, or cancel an appointment. What would you like to do?"
                else:
                    return "I can help you book, reschedule, or cancel an appointment. What would you like to do?"

            # ── 5. Collect missing fields ────────────────────────────────────
            missing = self._missing()

            if missing:
                self.state["workflow_state"] = "COLLECTING_DETAILS"
                f = missing[0]
                self.awaiting_field = f

                if f == "customer_confirmation":
                    c = self.sheets.get_customer_by_id(self.state["customer_id"])
                    if c:
                        self._update(name=c["name"], phone=c["phone"], customer_confirmed=True)
                        self.awaiting_field = None
                        missing2 = self._missing()
                        if missing2:
                            self.awaiting_field = missing2[0]
                            return f"Welcome back, {c['name']}! {self._prompt_for(missing2[0])}"
                        self.state["workflow_state"] = "WAITING_CONFIRMATION"
                        return self._confirm_prompt()
                    else:
                        self.state["customer_id"] = None
                        self.awaiting_field       = "patient_type"
                        return "I couldn't find that ID. Are you a new patient, or please try a different ID?"

                return self._prompt_for(f)

            # ── 6. All fields ready → confirm ────────────────────────────────
            if self.state["workflow_state"] != "COMPLETED":
                self.state["workflow_state"] = "WAITING_CONFIRMATION"
                return self._confirm_prompt()

            return self._execute()

        except Exception as e:
            import traceback
            print(f"[AGENT ERROR] {e}")
            traceback.print_exc()
            return "I'm sorry, something went wrong. Could you please repeat that?"

    def _execute(self):
        i = self.state.get("intent")
        if i == "book":              return self._book()
        if i == "reschedule":        return self._reschedule()
        if i == "cancel":            return self._cancel()
        if i == "view_appointments": return self._view()
        return "How else can I help you?"

    # ── DATETIME HELPER ───────────────────────────────────────────────────────
    def _parse_dt(self, date_str, time_str):
        if not date_str or not time_str:
            raise ValueError("Date or time is missing.")
        t = fast_extract_time(time_str) or time_str
        try:
            dt = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %I:%M %p")
        except ValueError:
            raise ValueError(f"Could not understand '{date_str} {time_str}'. Please use a format like 10:30 AM.")
        return dt.replace(tzinfo=ZoneInfo(TIMEZONE))

    def _is_biz_hours(self, dt):
        return dt.weekday() != 6 and 9 <= dt.hour < 17

    # ── APPOINTMENT ACTIONS ───────────────────────────────────────────────────
    def _book(self):
        try: start = self._parse_dt(self.state.get("date"), self.state.get("time"))
        except ValueError as e: return str(e)

        if not self._is_biz_hours(start):
            return "We are open Monday to Saturday, 9 AM to 5 PM. Please choose another slot."

        today = datetime.now(ZoneInfo(TIMEZONE)).date()
        days  = (start.date() - today).days
        if days < 0:  return "That date is in the past. Please choose a future date."
        if days > 3:  return "We only accept bookings up to 3 days in advance."

        try:
            cid = self.state.get("customer_id") or self.sheets.generate_customer_id()
            eid = self.calendar.create_appointment(
                self.state["name"], self.state["phone"], start,
                self.state.get("reason", ""), cid
            )
            if eid:
                self.sheets.log_appointment(
                    cid, self.state["name"], self.state["phone"],
                    self.state["date"], self.state["time"], self.state.get("reason", "")
                )
                date_str = self.state["date"]
                time_str = self.state["time"]
                self.reset_state()
                return (f"Your appointment is booked! Your customer ID is {cid}. "
                        f"We will see you on {date_str} at {time_str}. Is there anything else?")
            return "That time slot is already taken. Please choose a different time."
        except Exception as e:
            print(f"[BOOK ERROR] {e}"); return "Sorry, I could not complete the booking. Please try again."

    def _reschedule(self):
        try: new_start = self._parse_dt(self.state.get("new_date"), self.state.get("new_time"))
        except ValueError as e: return str(e)

        if not self._is_biz_hours(new_start):
            return "We are open Monday to Saturday, 9 AM to 5 PM. Please choose another slot."

        try:
            old = self.calendar.find_appointment(self.state["name"], self.state["phone"], self.state["date"])
            if not old: return "I could not find your existing appointment. Please check the date."
            self.calendar.cancel(old["id"])
            eid = self.calendar.create_appointment(
                self.state["name"], self.state["phone"], new_start,
                self.state.get("reason", ""), self.state.get("customer_id")
            )
            if eid:
                self.sheets.update_appointment(
                    self.state.get("customer_id"),
                    self.state["date"], self.state["time"],
                    self.state["new_date"], self.state["new_time"]
                )
                nd, nt = self.state["new_date"], self.state["new_time"]
                self.reset_state()
                return f"Rescheduled! Your new appointment is on {nd} at {nt}. Anything else?"
            # Restore original if new slot taken
            try:
                orig = self._parse_dt(self.state["date"], self.state["time"])
                self.calendar.create_appointment(
                    self.state["name"], self.state["phone"], orig,
                    self.state.get("reason",""), self.state.get("customer_id")
                )
            except Exception: pass
            return "That new slot is already taken. Your original appointment has been kept."
        except Exception as e:
            print(f"[RESCHEDULE ERROR] {e}"); return "Sorry, I could not reschedule. Please try again."

    def _cancel(self):
        try:
            event = self.calendar.find_appointment(self.state["name"], self.state["phone"], self.state["date"])
            if not event: return "I could not find your appointment. Please check the date."
            self.calendar.cancel(event["id"])
            self.sheets.delete_appointment(self.state.get("customer_id"), self.state["date"], self.state["time"])
            d = self.state["date"]
            self.reset_state()
            return f"Your appointment on {d} has been cancelled. Is there anything else?"
        except Exception as e:
            print(f"[CANCEL ERROR] {e}"); return "Sorry, I could not cancel. Please try again."

    def _view(self):
        cid = self.state.get("customer_id")
        if not cid: return "I need your customer ID to look up appointments."
        try:
            appts = self.sheets.get_appointments_by_id(cid)
            if not appts: return "No upcoming appointments found for your account."
            lines = [f"{a['appointment_date']} at {a['appointment_time']}" for a in appts]
            self.reset_state()
            return "Your upcoming appointments: " + ", and ".join(lines) + ". Anything else?"
        except Exception as e:
            print(f"[VIEW ERROR] {e}"); return "Sorry, I could not retrieve your appointments."

    # ── MAIN LOOP ─────────────────────────────────────────────────────────────
    def run(self):
        self.voice.speak("Hello! Welcome to Smile Dental. How can I help you today?")
        while True:
            text = self.voice.listen()
            if not text or text in ("exit", "quit"):
                self.voice.speak("Goodbye! Have a great day."); break
            if text in ("unknown", "error"):
                self.voice.speak("Sorry, I didn't catch that. Could you please repeat?"); continue
            self.voice.speak(self.generate_response(text))


if __name__ == "__main__":
    agent = DentalVoiceAgent()
    agent.run()