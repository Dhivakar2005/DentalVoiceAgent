import os
import pickle
import re
import json
import time
from typing import Any
from datetime import datetime, timedelta, timezone
import structlog
logger = structlog.get_logger(__name__)

#  TIMEZONE FALLBACK ─
def get_tz():
    """
    Returns a robust timezone object for Asia/Kolkata (UTC+5:30).
    Avoids NameError if zoneinfo or backports.zoneinfo is missing.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Kolkata")
    except ImportError:
        try:
            from backports.zoneinfo import ZoneInfo
            return ZoneInfo("Asia/Kolkata")
        except ImportError:
            # Absolute fallback: Manual UTC+5:30 offset
            from datetime import timezone
            return timezone(timedelta(hours=5, minutes=30))

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
from google.auth.exceptions import RefreshError
from google_sheets_manager import GoogleSheetsManager
from vector_db_manager import VectorDBManager

try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False

#  CONFIG
OLLAMA_BASE_URL          = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL             = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]
TIMEZONE                 = "Asia/Kolkata"
APPOINTMENT_DURATION_MIN = 10
SAMPLE_RATE              = 16000
DURATION                 = 3
LLM_TIMEOUT_SECONDS      = 10
# Context window and word count configuration
LLM_NUM_CTX              = 2048   
LLM_NUM_PREDICT          = 150    
OPEN_HOUR                = 9    # 9 AM
CLOSE_HOUR               = 17   # 5 PM

#  LOGIC LOADER
def load_logic():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "logic.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error("load_logic_error", error=str(e))
        return {}

LOGIC            = load_logic()
FAQ_DATABASE     = {}                              # LLM handles FAQ now
_INTENT_PATTERNS = LOGIC.get("intent_patterns", {})
PROMPTS          = LOGIC.get("prompts", {})
SYSTEM_MESSAGES  = LOGIC.get("system_messages", {})

# ─
#  FAST KEYWORD EXTRACTORS  (<1 ms, no LLM)
# ─

def fast_extract_intent(text):
    t = text.lower().strip()
    # English intent patterns (from logic.json)
    for intent, patterns in _INTENT_PATTERNS.items():
        for p in patterns:
            if re.search(p, t):
                return intent
    # Tamil intent patterns (Unicode + transliteration)
    if re.search(
        r'(பதிவு\s+செய்|புக்\s+பண்ண|appointment\s+வேண்டும்|அப்பாயின்மெண்ட்|'
        r'book\s+pannanum|appointment\s+pannanum|panna\s+venum|'
        r'நியமனம்\s+வேண்டும்|appointment\s+panna\s+venum|'
        r'pannanum|pannunga)', text
    ):
        return 'book'
    if re.search(r'(ரத்து|cancel\s+பண்ண|நியமனம்\s+ரத்து)', text):
        return 'cancel'
    if re.search(r'(நேரம்\s+மாற்ற|மாற்ற\s+வேண்டும்|reschedule\s+பண்ண|date\s+மாற்ற)', text):
        return 'reschedule'
    if re.search(r'(appointment\s+பார்க்க|நியமனம்\s+பார்க்க|எப்போது\s+appointment)', text):
        return 'view_appointments'
    # Hindi intent patterns (Unicode + transliteration)
    if re.search(
        r'(appointment\s+बुक|बुक\s+करन|appointment\s+चाहिए|appointment\s+लेन|'
        r'book\s+karna|book\s+karo|appointment\s+chahiye|'
        r'appointment\s+lena|book\s+karein|appointment\s+book)', text
    ):
        return 'book'
    if re.search(r'(रद्द|cancel\s+करन|appointment\s+रद्द)', text):
        return 'cancel'
    if re.search(r'(appointment\s+बदल|समय\s+बदल|reschedule\s+करन)', text):
        return 'reschedule'
    return None


def fast_patient_type(text):
    """
    Handles typos (exesting, existng, exisiting) and all natural phrasings.
    Also handles Tamil and Hindi patient type phrases.
    Short-circuits on long messages (>8 words) to avoid false matches.
    """
    t = text.lower().strip()
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
    if re.match(r'^new$', t) or re.match(r'^new\s+patient$', t):
        return 'new'

    existing_patterns = [
        r'\bex[ise]{0,3}[ts][tin]{0,3}[gi]?\w*\b',
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
    if re.match(r'^(old|existing)$', t):
        return 'old'

    # Tamil patient type patterns (Unicode)
    if re.search(r'(புதிய\s+நோயாளி|புதிய\s+patient|முதல்\s+முறை|pudhiya)', text):
        return 'new'
    if re.search(
        r'(பழைய\s+நோயாளி|பழைய\s+patient|முன்பு\s+வந்(தேன்|திருக்கிறேன்)|'
        r'உங்கள்\s+கிளினிக்கின்|முன்னாடி\s+வந்தேன்)', text
    ):
        return 'old'

    # Hindi patient type patterns (Unicode)
    if re.search(r'(नया\s+मरीज|नया\s+patient|पहली\s+बार)', text):
        return 'new'
    if re.search(r'(पुराना\s+मरीज|पुराना\s+patient|पहले\s+आया|पहले\s+आई)', text):
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
    # English: typical introduction phrases
    m = re.search(
        r'\b(?:my\s+name\s+is|name\s*[:\-]\s*|this\s+is|call\s+me|name\s+is|i\s*am|i\s*m|myself)\s+([A-Za-z]+(?:\s+[A-Za-z]{2,})?)',
        t, re.IGNORECASE
    )
    if m:
        raw_name = m.group(1).strip()
        name_parts = raw_name.split()
        fillers = {"here", "this", "speaking", "myself", "patient", "is", "a", "calling", "at", "the"}
        if name_parts and name_parts[0].lower() in fillers:
            return None
        name = " ".join(name_parts).strip()
        while name and len(name.split()[-1]) == 1:
            words = name.split()
            name = " ".join(words[:-1]).strip()
        name = name.title()
        if len(name) >= 2: return name

    # Tamil: "நான் X பேசுகிறேன்" or "என் பெயர் X"
    m = re.search(
        r'(?:நான்\s+|என்\s+பெயர்\s+|என்னுடைய\s+பெயர்\s+)([A-Za-z][A-Za-z\s]{1,30})(?:\s+பேசுகிறேன்|$)',
        t
    )
    if m:
        name = m.group(1).strip().title()
        if len(name) >= 2: return name

    # Hindi: "मेरा नाम X है" or "मैं X बोल रहा"
    m = re.search(
        r'(?:मेरा\s+नाम\s+|मैं\s+)([A-Za-z][A-Za-z\s]{1,30})(?:\s+है|\s+बोल\s+रहा|$)',
        t
    )
    if m:
        name = m.group(1).strip().title()
        if len(name) >= 2: return name

    if awaiting:
        if re.match(r'^[A-Za-z]+(?:\s+[A-Za-z]+)?$', t) and 2 <= len(t) <= 40:
            _EXCLUDE = {
                'yes','no', 'new', 'old', 'book', 'cancel', 'reschedule', 'appointment',
                'patient', 'existing', 'hello', 'hi', 'hey', 'okay', 'ok', 'sure', 'thanks',
                'nothing', 'nothing else', 'bye', 'goodbye'
            }
            words = t.lower().split()
            if not any(w in _EXCLUDE for w in words):
                fillers = {"here", "speaking", "is", "this", "myself"}
                if words[-1] in fillers:
                    return words[0].title()
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
    today = datetime.now(get_tz())

    if re.search(r'\btoday\b', t):
        return today.strftime("%Y-%m-%d")
    if re.search(r'\b(tomorrow|tommorow|tommorrow|tomorow)\b', t):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r'\bday\s+after\s+(tomorrow|tommorow|tommorrow|tomorow)\b', t):
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
    # Pattern 1: Day Month
    m = re.search(
        r'\b(\d{1,2})(?:\s*(?:st|nd|rd|th))?\s*(?:of|the|tha)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*',
        t, re.IGNORECASE
    )
    if m:
        day = int(m.group(1)); mon_str = m.group(2).lower()[:3]; mon = _MONTHS[mon_str]
        yr = today.year
        try:
            base = datetime(yr, mon, day, tzinfo=get_tz())
            if base.date() < today.date(): yr += 1
            return datetime(yr, mon, day).strftime("%Y-%m-%d")
        except: pass

    # Pattern 2: Month Day
    m = re.search(
        r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(?:the|tha)?\s*(\d{1,2})(?:\s*(?:st|nd|rd|th))?\b',
        t, re.IGNORECASE
    )
    if m:
        mon_str = m.group(1).lower()[:3]; mon = _MONTHS[mon_str]; day = int(m.group(2))
        yr = today.year
        try:
            base = datetime(yr, mon, day, tzinfo=get_tz())
            if base.date() < today.date(): yr += 1
            return datetime(yr, mon, day).strftime("%Y-%m-%d")
        except: pass

    # Pattern 3: Weekdays (e.g., Monday, Next Friday)
    _WEEKDAYS = {
        'monday':0, 'tuesday':1, 'wednesday':2, 'thursday':3, 'friday':4, 'saturday':5, 'sunday':6, 
        'mon':0, 'tue':1, 'wed':2, 'thu':3, 'fri':4, 'sat':5, 'sun':6
    }
    m = re.search(r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b', t, re.IGNORECASE)
    if m:
        target_wd = _WEEKDAYS[m.group(1).lower()]
        days_ahead = target_wd - today.weekday()
        if days_ahead < 0:  # If the day has passed this week, advance to next week
            days_ahead += 7
        if re.search(r'\bnext\s+' + m.group(1).lower() + r'\b', t, re.IGNORECASE):
            if days_ahead == 0: days_ahead = 7 # "next Monday" on a Monday = next week
            else: days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


def fast_extract_time(text):
    t = text.strip()
    t = re.sub(r'\b([ap])\.?m\.?\b', r'\1m', t, flags=re.IGNORECASE)
    t_clean = re.sub(r'\b(at|on|for|the|in)\b', ' ', t, flags=re.IGNORECASE).strip()
    tu = t_clean.upper()

    m = re.search(r'\b(\d{1,2}):(\d{2})\s*([AP]M)\b', tu)
    if m: return f"{m.group(1)}:{m.group(2)} {m.group(3)}"
    m = re.search(r'\b(\d{1,2})\s*([AP]M)\b', tu)
    if m: return f"{m.group(1)}:00 {m.group(2)}"

    tl = t_clean.lower()
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
def fast_extract_reason(text):
    """
    Cleans up the reason by removing conversational filler words.
    Returns a 1-3 word strictly dental/medical reason.
    """
    t = text.lower().strip()
    
    # Remove phrases commonly used before/after a reason
    fillers = [
        r'\bmy\s+name\s+is\b',
        r'\bi\s+am\s+called\b',
        r'\bi\s+am\b',
        r'\bthis\s+is\b',
        r'\bi\s+is\b',
        r'\bi\s+(have|need|want|got)\s+(a|an)?\b',
        r'\bi\s+(need|want)\s+to\s+(book|schedule|see)\b',
        r'\bappointment\s+for\b',
        r'\breason\s+is\b',
        r'\bfor\s+my\b',
        r'\bi\s+am\s+having\b',
        r'\bplease\b',
        r'\btoday\b',
        r'\btomorrow\b',
        r'\bhere\b',
        r'\bkindly\b'
    ]
    
    clean = t
    for p in fillers:
        clean = re.sub(p, '', clean, flags=re.IGNORECASE).strip()
    
    # Remove leading/trailing common stop words
    stop_words = {"a", "an", "the", "is", "to", "for", "with", "on", "at", "my"}
    words = clean.split()
    while words and words[0] in stop_words:
        words.pop(0)
    while words and words[-1] in stop_words:
        words.pop(-1)
        
    result = " ".join(words).title()
    return result if len(result) > 2 else None


def fast_yes_no(text):
    t = text.lower().strip()
    if re.search(
        r'\b(yes|yeah|yep|yup|yea|ya|correct|confirm|confirmed|confirming|ok|okay|'
        r'sure|go\s+ahead|proceed|sounds\s+good|right|perfect|'
        r'looks\s+good|book\s+it|book\s+the\s+appointment|do\s+it|fine|'
        r'absolutely|definitely|please|just\s+do\s+it|that\s+is\s+it)\b', t
    ):
        return 'yes'
    # Tamil yes
    if re.search(r'(ஆம்|சரி|ஓகே|சரிதான்|ஆமாம்)', text):
        return 'yes'
    # Hindi yes
    if re.search(r'(हाँ|हां|ठीक\s*है|बिल्कुल|हाँ\s*जी)', text):
        return 'yes'

    if re.search(
        r'\b(no|nope|nah|naa|wrong|change|edit|different|incorrect|'
        r'not\s+right|modify|update|fix|cancel\s+that|wait|none|'
        r'something\s+else|different\s+time|other|other\s+times?)\b', t
    ):
        return 'no'
    # Tamil no
    if re.search(r'(வேண்டாம்|வேண்டா|இல்லை)', text):
        return 'no'
    # Hindi no
    if re.search(r'(नहीं|नही|मत)', text):
        return 'no'

    return None


# ─
#  VOICE INTERFACE
# ─
class VoiceInterface:
    def __init__(self, use_voice=True):
        self.use_voice = use_voice and (AUDIO_BACKEND is not None) and TTS_AVAILABLE
        if self.use_voice:
            self.engine = pyttsx3.init()
            voices = self.engine.getProperty('voices')
            if len(voices) > 0:
                self.engine.setProperty('voice', voices[0].id)
            self.engine.setProperty('rate', 100)
            self.engine.setProperty('volume', 1.0)
            if SPEECH_RECOGNITION_AVAILABLE:
                self.recognizer = sr.Recognizer()
        else:
            logger.warning("running_in_text_mode")

    def speak(self, text):
        logger.info("agent_speak", text=text)
        if self.use_voice and TTS_AVAILABLE:
            try:
                tts_text = text.replace("*", "").replace("\n", ". ")
                self.engine.say(tts_text)
                self.engine.runAndWait()
            except Exception as e:
                logger.error("tts_error", error=str(e))

    def record_audio_sounddevice(self, duration=DURATION):
        try:
            logger.info("recording_started", duration=duration)
            audio_data = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype=np.int16)
            sd.wait()
            return audio_data
        except Exception as e:
            logger.error("recording_error", error=str(e)); return None

    def audio_to_text_sounddevice(self, audio_data):
        if not SPEECH_RECOGNITION_AVAILABLE: return "error"
        try:
            buf = io.BytesIO()
            wavfile.write(buf, SAMPLE_RATE, audio_data); buf.seek(0)
            with sr.AudioFile(buf) as src:
                audio = self.recognizer.record(src)
                text  = self.recognizer.recognize_google(audio)
                logger.info("patient_speak", text=text); return text
        except sr.UnknownValueError: return "unknown"
        except Exception as e: logger.error("recognition_error", error=str(e)); return "error"

    def listen(self):
        if self.use_voice and AUDIO_BACKEND == "sounddevice":
            try:
                audio_data = self.record_audio_sounddevice()
                return self.audio_to_text_sounddevice(audio_data) if audio_data is not None else "error"
            except Exception as e:
                logger.warning("microphone_error_fallback", error=str(e))
        return input("\nPatient (type): ").strip()


# ─
#  GOOGLE CALENDAR
# ─
class GoogleCalendarManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(GoogleCalendarManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self.service = self._authenticate()
        self._initialized = True

    def _authenticate(self):
        creds = None
        if os.path.exists("token.pickle"):
            try:
                with open("token.pickle", "rb") as f:
                    creds = pickle.load(f)
            except (TypeError, pickle.UnpicklingError, EOFError) as e:
                logger.error("corrupt_token_pickle_detected_calendar", error=str(e))
                try:
                    # Don't delete here if SheetsManager already might have, but safe to try
                    if os.path.exists("token.pickle"):
                        os.remove("token.pickle")
                        logger.info("deleted_corrupt_token_pickle_calendar")
                except:
                    pass
            except Exception as e:
                logger.error("unexpected_token_load_error_calendar", error=str(e))

        if not creds or not (hasattr(creds, 'valid') and creds.valid):
            if creds and creds.expired and creds.refresh_token:
                for attempt in range(3):
                    try: 
                        creds.refresh(Request())
                        break
                    except RefreshError as e:
                        logger.warning("token_refresh_error_invalid_grant", error=str(e))
                        creds = None # Trigger Flow
                        if os.path.exists("token.pickle"):
                            try: os.remove("token.pickle")
                            except: pass
                        break
                    except Exception as e:
                        if attempt == 2: raise
                        time.sleep(2)
            
            if not creds or not (hasattr(creds, 'valid') and creds.valid):
                flow  = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
                with open("token.pickle", "wb") as f:
                    pickle.dump(creds, f)
        return build("calendar", "v3", credentials=creds)

    def is_available(self, start_dt, end_dt, customer_id=None):
        res = self.service.events().list(
            calendarId="primary", timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(), singleEvents=True,
        ).execute()
        items = res.get("items", [])
        if not items:
            return True
        
        # Self-bypass logic: if ALL matching events belong to this customer, return True
        if customer_id:
            for e in items:
                desc = e.get("description", "")
                if f"Customer ID: {customer_id}" not in desc:
                    return False
            return True
            
        return len(items) == 0

    def create_appointment(self, name, phone, start_dt, reason, customer_id=None):
        end_dt = start_dt + timedelta(minutes=APPOINTMENT_DURATION_MIN)
        if not self.is_available(start_dt, end_dt, customer_id=customer_id): return None
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

    def find_appointment(self, name, phone, date, time_str=None):
        start  = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=get_tz())
        end    = start + timedelta(days=1)
        # We fetch all events for the day and manually filter to ensure strict phone matching
        events = self.service.events().list(
            calendarId="primary", timeMin=start.isoformat(),
            timeMax=end.isoformat(), singleEvents=True
        ).execute().get("items", [])
        
        search_phone = str(phone).strip()
        target_time = None
        if time_str:
            try:
                # Use simple normalization
                t = str(time_str).strip().upper().replace(" ", "")
                if ":" not in t:
                    import re
                    m = re.match(r"(\d+)(AM|PM)", t)
                    if m: t = f"{m.group(1)}:00{m.group(2)}"
                # Convert to standard format
                dt = datetime.strptime(t, "%I:%M%p" if "AM" in t or "PM" in t else "%H:%M")
                target_time = dt.strftime("%I:%M %p").lstrip("0")
            except:
                target_time = time_str.strip().upper()

        for e in events:
            desc = e.get("description", "")
            # Match strictly on phone number to avoid "ghost" matches from similar names
            if search_phone in desc:
                if target_time:
                    # Double check the start time matches
                    e_start = e.get("start", {}).get("dateTime")
                    if e_start:
                        e_dt = datetime.fromisoformat(e_start)
                        e_time = e_dt.strftime("%I:%M %p").lstrip("0")
                        if e_time != target_time:
                            continue
                return e
        return None

    def cancel(self, event_id):
        if not event_id or event_id == "SHEETS_ONLY":
            logger.info("skipping_calendar_cancel_sheets_only_or_missing")
            return
        self.service.events().delete(calendarId="primary", eventId=event_id).execute()


# ─
#  DENTAL VOICE AGENT
# ─
class DentalVoiceAgent:
    def __init__(self, use_voice=True, streaming=True, calendar=None, sheets=None, vdb=None):
        self.calendar       = calendar or GoogleCalendarManager()
        self.sheets         = sheets   or GoogleSheetsManager()
        self.vdb            = vdb      or VectorDBManager()
        self.voice          = VoiceInterface(use_voice=use_voice)
        self.prompts        = PROMPTS
        self.messages       = SYSTEM_MESSAGES
        # FIX 3 — streaming flag controls whether _stream_string sleeps.
        # Set streaming=False for Twilio (non-SSE) paths to avoid artificial delay.
        self.streaming      = streaming
        self.state: dict[str, Any] = {}
        self.awaiting_field = None
        self.reset_state()

        # FIX 4 — build the static parts of the LLM system prompt once at init.
        self._llm_base_system = self._build_base_system()

    #  FIX 4: static system prompt fragment (built once, reused every call) 
    def _build_base_system(self):
        few_shots = (
            "EXAMPLES:\n"
            'User: "I\'m new here." -> {"intent":"book","patient_type":"new"}\n'
            'User: "i am rahul" -> {"name":"Rahul"}\n'
            'User: "Yes, confirm the booking." -> {"user_confirmed":true}\n'
            'User: "Sounds good, go ahead." -> {"user_confirmed":true}\n'
            'User: "No, I want to change it." -> {"user_rejected":true}\n'
            'User: "Wrong time, make it 3 PM." -> {"time":"3:00 PM"}\n'
            'User: "I have a toothache." -> {"intent":"book","reason":"toothache"}\n'
            'User: "Can I change the time?" -> {"intent":"none","general_answer":"Sure! What should the new time be?"}\n'
        )
        return (
            "You are a strict multilingual clinical assistant for 'Smile Dental' clinic. "
            "You MUST respond in the same language the user is using (English, Tamil, Hindi, Malayalam, etc.). "
            "Extract user intents and dental entities into ONLY JSON format. "
            "HARD CONSTRAINTS:\n"
            "1. Do NOT hallucinate or guess surnames/last names. Use ONLY the name provided by the user.\n"
            "2. If intent is unclear (e.g. 'hi' or 'who are you'), return {\"intent\":\"none\"}.\n"
            "3. Strictly focus on these intents: book, reschedule, cancel, view_appointments.\n"
            "4. For returning patients, map their phone number into the 'phone' field (10 digits).\n"
            "5. If the user mentions a date or time, extract it accurately relative to today.\n"
            "6. If the user's message is a greeting or a question about dental services, provide a SHORT, helpful response in 'general_answer' (limit 20 words).\n"
            "7. NO SMALL TALK except for 'general_answer'. Return ONLY JSON.\n"
            f"{few_shots}\n"
            'REPLY ONLY WITH VALID JSON, no explanation, no markdown:\n'
            '{"intent":"book|reschedule|cancel|view_appointments|none",'
            '"patient_type":"new|old|empty",'
            '"name":"name provided by user or empty",'
            '"phone":"10 digits or empty",'
            '"customer_id":"CUST### or empty",'
            '"date":"YYYY-MM-DD or empty (IF NOT EXPLICITLY STATED)",'
            '"time":"H:MM AM/PM or empty (IF NOT EXPLICITLY STATED)",'
            '"new_date":"YYYY-MM-DD or empty (IF NOT EXPLICITLY STATED)",'
            '"new_time":"H:MM AM/PM or empty (IF NOT EXPLICITLY STATED)",'
            '"reason":"STRICT 1-3 word dental reason only (e.g. Toothache, Checkup, Braces). NO filler text.",'
            '"user_confirmed":false,"user_rejected":false,'
            '"general_answer":"SHORT DENTAL-ONLY ANSWER (NO SMALL TALK) or empty"}'
        )

    def reset_state(self):
        self.state = {
            "intent":               None,
            "patient_type":         None,
            "customer_id":          None,
            "name":                 None,
            "phone":                None,
            "date":                 None,
            "time":                 None,
            "new_date":             None,
            "new_time":             None,
            "reason":               None,
            "customer_confirmed":   False,
            "new_patient_greeted":  False,
            "old_appointment_verified": False,
            "old_appointment_not_found": False,
            "workflow_state":       "IDLE",
            "suggestion_turn":      0,
        }
        self.awaiting_field = None

    def validate_time(self, t_str):
        if not t_str: return True
        try:
            dt = datetime.strptime(t_str, "%I:%M %p")
            return OPEN_HOUR <= dt.hour < CLOSE_HOUR
        except Exception as e:
            logger.error("time_validation_failed", time_str=t_str, error=str(e))
            return True

    #  FIX 4/5: LLM call — injects only dynamic parts into pre-built base 
    def _call_llm(self, text, awaiting_field=None, context="", stream=False):
        today_str = datetime.now(get_tz()).strftime("%Y-%m-%d")

        _FIELD_HINTS = {
            "patient_type":  "Is the user new or existing/old patient? Reply new or old.",
            "customer_id":   "Extract customer ID (format CUST###, e.g. CUST001). User may say just the number like '1' or '001'.",
            "name":          "Extract the patient's full name.",
            "phone":         "Extract the 10-digit mobile number (digits only).",
            "date":          f"Extract appointment date as YYYY-MM-DD. Today is {today_str}. Interpret: tomorrow, next Monday, etc.",
            "time":          "Extract appointment time as H:MM AM/PM.",
            "new_date":      f"Extract the NEW preferred date for rescheduling as YYYY-MM-DD. Today is {today_str}.",
            "new_time":      "Extract the NEW preferred time for rescheduling as H:MM AM/PM.",
            "reason":        "Extract the reason for the dental visit (e.g. checkup, toothache, cleaning).",
        }
        hint = ""
        if awaiting_field and awaiting_field in _FIELD_HINTS:
            hint = f" FOCUS: {_FIELD_HINTS[awaiting_field]}"

        # Compose final system prompt by injecting dynamic parts into the cached base
        system = (
            f"Today: {today_str}.{hint}\n"
            f"KNOWLEDGE BASE CONTEXT (Use this to answer questions):\n{context}\n\n"
            + self._llm_base_system
        )

        try:
            _t0 = time.time()
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model":      OLLAMA_MODEL,
                    "messages":   [{"role": "system", "content": system},
                                   {"role": "user",   "content": text}],
                    "stream":     stream,
                    "keep_alive": -1,
                    "options":    {
                        "num_predict": LLM_NUM_PREDICT,
                        "temperature": 0.1,
                        "num_ctx":     LLM_NUM_CTX,   # FIX 5 — 1024 prevents prompt truncation
                        "num_gpu":     -1
                    },
                },
                timeout=LLM_TIMEOUT_SECONDS,
                stream=stream
            )
            _latency_ms = int((time.time() - _t0) * 1000)
            resp.raise_for_status()

            if stream: return resp

            raw = resp.json()["message"]["content"].strip()
            raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
            raw = re.sub(r'```(?:json)?\s*(.*?)\s*```', r'\1', raw, flags=re.DOTALL).strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                logger.warning("llm_no_json_found", raw_response=raw[:200])
                return None
            parsed = json.loads(re.sub(r',\s*([}\]])', r'\1', match.group()))

            # Normalise phone
            if parsed.get("phone"):
                digits = re.sub(r'[^\d]', '', str(parsed["phone"]))
                if len(digits) == 12 and digits.startswith('91'): digits = digits[2:]
                if len(digits) == 11 and digits.startswith('0'):  digits = digits[1:]
                parsed["phone"] = digits if len(digits) == 10 else ""

            # Normalise customer_id
            if parsed.get("customer_id"):
                cid = fast_extract_customer_id(str(parsed["customer_id"]), awaiting=True)
                parsed["customer_id"] = cid if cid else ""

            # Validate / fallback date fields
            for k in ("date", "new_date"):
                if parsed.get(k):
                    try: datetime.strptime(parsed[k], "%Y-%m-%d")
                    except: parsed[k] = fast_extract_date(text) or ""

            # Validate / fallback time fields
            for k in ("time", "new_time"):
                if parsed.get(k):
                    norm = fast_extract_time(str(parsed[k])) or fast_extract_time(text)
                    parsed[k] = norm or ""
            
            # Clean Reason
            if parsed.get("reason"):
                parsed["reason"] = fast_extract_reason(str(parsed["reason"])) or parsed["reason"]

            logger.info("llm_inference_result", latency_ms=_latency_ms, extracted=parsed)
            return parsed
        except requests.exceptions.Timeout:
            logger.error("llm_timeout")
        except Exception as e:
            logger.error("llm_error", error=str(e))
        return None

    #  STATE HELPERS 
    def _update(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None and v != "":
                if k == "intent" and v == "none" and self.state.get("intent"):
                    continue
                
                # Reset suggestion turn if date changes
                if k in ("date", "new_date") and v != self.state.get(k):
                    self.state["suggestion_turn"] = 0
                
                self.state[k] = v
                
                # Handle patient type initialization logic centrally
                if k == "patient_type":
                    if v == "old":
                        self.state["customer_id"]        = None
                        self.state["customer_confirmed"] = False
                        # Only reset if they aren't already set (preserves Cold Start info)
                        if not self.state.get("name"):  self.state["name"] = None
                        if not self.state.get("phone"): self.state["phone"] = None
                    elif v == "new":
                        self.state["new_patient_greeted"] = False

                # Auto-verify customer for existing patients when phone is provided
                if k == "phone" and self.state.get("patient_type") == "old":
                    if not self.state.get("customer_confirmed"):
                        c = self.sheets.get_customer_by_phone(v)
                        if c:
                            self.state.update({
                                "name": c["name"],
                                "customer_id": c["customer_id"],
                                "customer_confirmed": True,
                                "welcome_back_pending": True,
                                "phone_not_found": False
                            })
                            logger.info("auto_verify_found", phone=v, name=c['name'])
                        else:
                            self.state.update({
                                "phone_not_found": True,
                                "phone": None
                            })

                # Early verification for rescheduling — check Sheet before asking for new slot
                s = self.state
                if (s.get("intent") == "reschedule" and s.get("date") and s.get("time") 
                    and s.get("customer_id") and not s.get("old_appointment_verified")):
                    
                    logger.info("early_reschedule_verification_triggered", cid=s["customer_id"], date=s["date"], time=s["time"])
                    row_num = self.sheets.find_appointment_row(s["customer_id"], s["date"], s["time"])
                    
                    if row_num:
                        s["old_appointment_verified"] = True
                        s["old_appointment_not_found"] = False
                        logger.info("reschedule_verification_success", row=row_num)
                        # Fetch and carry over reason from Sheets
                        try:
                            # We fetch col F (Reason) - index 5
                            res = self.sheets.service.spreadsheets().values().get(
                                spreadsheetId=self.sheets.spreadsheet_id,
                                range=f"{self.sheets.sheet_name}!F{row_num}"
                            ).execute()
                            reason = res.get('values', [[]])[0][0] if res.get('values') else None
                            if reason:
                                s["reason"] = reason
                                logger.info("reschedule_reason_carried_over", reason=reason)
                        except Exception as e:
                            logger.error("carry_over_reason_failed", error=str(e))
                    else:
                        logger.warning("reschedule_verification_failed")
                        s["old_appointment_not_found"] = True
                        # Reset so they are asked again
                        s["date"] = None
                        s["time"] = None


    def _missing(self):
        intent = self.state.get("intent")
        pt     = self.state.get("patient_type")

        if not pt:
            return ["patient_type"]

        if pt == "old":
            if not self.state.get("phone"):
                return ["phone"]


        if pt == "new" and intent == "book":
            if not self.state.get("new_patient_greeted"):
                return ["new_patient_greet"]

        if intent == "book":
            fields = ["name", "phone", "date", "time", "reason"]
        elif intent == "reschedule":
            if not self.state.get("old_appointment_verified"):
                fields = ["name", "phone", "date", "time"]
            else:
                fields = ["name", "phone", "date", "time", "new_date", "new_time"]
        elif intent == "cancel":
            fields = ["name", "phone", "date", "time"]
        else:
            fields = []

        return [f for f in fields if not self.state.get(f)]

    def _prompt_for(self, field):
        intent = self.state.get("intent")
        pt     = self.state.get("patient_type")

        if field == "patient_type":
            return "Are you a new patient, or have you visited us before? (Existing patient)"
        if field == "customer_id" and pt == "old":
            return "Welcome back! Please provide your Customer ID (e.g., CUST001) so I can find your records."
        if field == "date":
            if intent == "book":
                return "On which date would you like to book your appointment? (e.g., Tomorrow, or March 25th)"
            if self.state.get("old_appointment_not_found"):
                return self.messages.get("reschedule_not_found")
            return "What is the date of your existing appointment?"
        if field == "time":
            if intent == "book":
                return "At what time? We are open from 9 AM to 5 PM."
            if self.state.get("old_appointment_not_found"):
                return self.messages.get("reschedule_time_not_found")
            return "What time is your existing appointment?"
        if field == "new_time" and intent == "reschedule":
            return "What is the new time for your appointment?"
        return self.prompts.get(field, f"Please provide your {field}.")

    def _confirm_prompt(self):
        s = self.state; i = s.get("intent", "")
        # Auto-identify doctor for confirmation
        res = s.get("reason") or ""
        dt = s.get("date") or s.get("new_date")
        tm = s.get("time") or s.get("new_time")
        doc_info = self.sheets.db.get_best_doctor(res, dt, tm)
        doctor_name = doc_info["doctor_name"] if doc_info else "Dr. General"

        if i == "book":
            if not s.get("reason"):
                self.awaiting_field = "reason"
                return self._prompt_for("reason")
            if s.get("patient_type") == "new" and not s.get("customer_id"):
                try: s["customer_id"] = self.sheets.generate_customer_id()
                except Exception: s["customer_id"] = None
            msg = self.messages.get("confirm_booking")
            return msg.format(doctor=doctor_name, name=s['name'], date=s['date'], time=s['time'], reason=s['reason'])

        if i == "reschedule":
            return f"Moving {s['name']}'s appointment with {doctor_name} to {s['new_date']} at {s['new_time']}. Say yes or no."
        if i == "cancel":
            return f"Cancelling {s['name']}'s appointment on {s['date']} at {s['time']}. Say yes to confirm or no to cancel."
        return "Shall I go ahead? Say yes to confirm or no to edit."

    #  FAST FIELD EXTRACTION 
    def _extract_fast(self, text):
        """
        Extract every possible field using fast regex only (<1ms).
        Guards against false positives — see inline comments.
        """
        found = {}
        af    = self.awaiting_field
        state = self.state

        intent = fast_extract_intent(text)
        if intent: found["intent"] = intent

        if not state.get("patient_type"):
            pt = fast_patient_type(text)
            if pt: found["patient_type"] = pt

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
            if not cid and af == "customer_id":
                m = re.search(r'(\d{1,4})', text)
                if m: cid = f"CUST{m.group(1).zfill(3)}"
            if cid: found["customer_id"] = cid

        name = fast_extract_name(text, awaiting=(af == "name"))
        if name: found["name"] = name

        if not state.get("phone"):
            phone = fast_extract_phone(text)
            if phone: found["phone"] = phone

        current_intent = found.get("intent") or state.get("intent")
        is_reschedule  = (current_intent == "reschedule")

        date_val = fast_extract_date(text)
        if date_val:
            # FIX: If we are in reschedule mode, and either we are already at the confirmation stage 
            # (af is None) OR we specifically want the new_date, prioritize new_date.
            if is_reschedule:
                if af == "new_date" or (state.get("date") and (af is None or af == "new_time")):
                    found["new_date"] = date_val
                else:
                    found["date"] = date_val
            else:
                found["date"] = date_val

        time_val = fast_extract_time(text)
        if time_val:
            if is_reschedule:
                if af == "new_time" or (state.get("time") and (af is None or af == "new_date")):
                    found["new_time"] = time_val
                else:
                    found["time"] = time_val
            else:
                found["time"] = time_val

        if af == "reason":
            meta_patterns = [
                r'\b(change|edit|update|modify|move|shift|different|wrong)\b',
                r'\b(time|date|day|hour|moment)\b'
            ]
            if any(re.search(p, text.lower()) for p in meta_patterns):
                logger.warning("ignored_meta_command_as_reason", text=text)
                return found
            stripped = re.sub(
                r'\b(my|i|the|have|a|an|for|is|need|want|it|reason|visit|'
                r'because|came|coming|here)\b',
                '', text.lower()
            ).strip(" .,")
            if stripped and len(stripped) > 2:
                found["reason"] = stripped.title()

        return found

    # ─
    #  FIX 1: fast-path bypass — skip LLM when regex is enough
    # ─
    def _try_fast_path(self, text, fast_found):
        """
        Returns (handled: bool, generator_or_none).

        Handles three cases without touching the LLM:
          A) We're awaiting a specific simple field and regex already extracted it.
          B) We're in WAITING_CONFIRMATION and fast_yes_no resolves the decision.
          C) Goodbye / thanks (already handled upstream, kept for safety).

        If handled=True the caller should yield from the returned generator and return.
        """
        af = self.awaiting_field
        
        # New Case — User rejects a time suggestion ("none of these", "something else")
        if af in ("time", "new_time") and fast_yes_no(text) == "no" and not fast_found.get(af):
            def _gen_reject():
                self.state["suggestion_turn"] += 1
                yield from self._stream_string(self._prompt_for(af))
            return True, _gen_reject()

        # Case A — awaiting a specific field AND it was just extracted
        _SIMPLE_FIELDS = {"name", "phone", "date", "time", "new_date", "new_time", "reason", "customer_id", "patient_type"}
        if af in _SIMPLE_FIELDS and fast_found.get(af):
            def _gen():
                self._update(**fast_found)
                self.awaiting_field = None
                # Also carry over any bonus fields found in same message
                for k, v in fast_found.items():
                    if k != af and v:
                        self._update(**{k: v})

                # Validate time if we just collected it
                for time_key in ("time", "new_time"):
                    if fast_found.get(time_key) and not self.validate_time(self.state.get(time_key)):
                        self.state[time_key] = None
                        self.awaiting_field  = time_key
                        msg = "I'm sorry, we are only open from 9 AM to 5 PM. Could you please specify a different time?"
                        yield from self._stream_string(msg)
                        return

                missing = self._missing()
                if missing:
                    self.state["workflow_state"] = "COLLECTING_DETAILS"
                    f = missing[0]
                    self.awaiting_field = f
                    msg = ""
                    if self.state.get("phone_not_found"):
                        msg = "I couldn't find a record for that number. Could you check the number, or should we set you up as a new patient? "
                        self.state["phone_not_found"] = False
                    elif self.state.get("welcome_back_pending"):
                        msg = f"Welcome back, {self.state['name']}! "
                        self.state["welcome_back_pending"] = False
                    yield from self._stream_string(msg + self._prompt_for(f))
                else:
                    # All fields collected — move to confirmation
                    self.state["workflow_state"] = "WAITING_CONFIRMATION"
                    yield from self._stream_string(self._confirm_prompt())
                return True, _gen()

        # Case C — Cold Start (Name/Phone provided before intent or at the very start)
        if af is None and (fast_found.get("name") or fast_found.get("phone")):
            def _gen_cold():
                # Update what we found
                self._update(**fast_found)
                
                # If we also found an intent, jump straight to details collection
                if fast_found.get("intent"):
                    missing = self._missing()
                    if missing:
                        self.state["workflow_state"] = "COLLECTING_DETAILS"
                        self.awaiting_field = missing[0]
                        greet = f"Nice to meet you, {self.state['name']}! " if fast_found.get("name") else ""
                        yield from self._stream_string(greet + self._prompt_for(self.awaiting_field))
                    else:
                        self.state["workflow_state"] = "WAITING_CONFIRMATION"
                        yield from self._stream_string(self._confirm_prompt())
                else:
                    # Just a name provided, ask how to help
                    greet = f"Nice to meet you, {self.state['name']}! " if fast_found.get("name") else ""
                    yield from self._stream_string(greet + "How can I help you today?")
            return True, _gen_cold()

        # Case B — confirmation turn resolved by fast yes/no
        if self.state.get("workflow_state") == "WAITING_CONFIRMATION":
            decision = fast_yes_no(text)

            # Task 3 Enhancement: Handle fast meta-update during confirmation (regex only)
            _META_KEYS = {"date", "time", "new_date", "new_time", "name", "reason"}
            found_meta = {k: v for k, v in fast_found.items() if k in _META_KEYS and v}

            if found_meta:
                def _gen_meta():
                    self._update(**found_meta)
                    yield from self._stream_string(
                        "Got it! I've updated your information. " + self._confirm_prompt()
                    )
                return True, _gen_meta()

            if decision:
                def _gen_confirm():
                    if decision == "yes":

                        self.state["workflow_state"] = "COMPLETED"
                        yield from self._stream_string(self._execute())
                    else:
                        self.state["workflow_state"] = "COLLECTING_DETAILS"
                        self.awaiting_field = "reconfirm_field"
                        yield from self._stream_string(
                            "What would you like to change? You can say name, date, time, or reason."
                        )
                return True, _gen_confirm()

        return False, None

    # ─
    #  MAIN RESPONSE GENERATOR
    # ─
    def generate_response(self, text):
        try:
            fast_found = self._extract_fast(text)

            # 1. FAQ short-circuit (fast path — no LLM)
            # Only trigger FAQ if we don't have an active intent yet.
            if not self.state.get("intent") and self.awaiting_field not in ("customer_id", "phone", "name"):
                t_lower = text.lower()
                for faq in LOGIC.get("faq_database", {}).values():
                    for kw in faq.get("keywords", []):
                        if kw in t_lower:
                            logger.info("fast_faq_match", keyword=kw)
                            yield from self._stream_string(faq["answer"])
                            return

            # 2. Goodbye / thanks
            t_lower = text.lower().strip(" .?!")
            goodbye_patterns = [
                r'\b(thank|thanks|thank you)\b',
                r'\b(bye|goodbye|ttyl|see you|nothing else|no more|that is it|that\'s it)\b',
                r'\b(no\s+thank\s*s?|no\s+i\s+am\s+good|im\s+good|nothing\s+else|nothing)\b',
                r'^nothing$', r'^no$', r'^no\s*thanks?$', r'^that\s+is\s+it$', r'^that\'s\s+it$'
            ]
            if any(re.search(p, t_lower) for p in goodbye_patterns):
                self.reset_state()
                yield from self._stream_string(self.messages.get("goodbye", "Goodbye! Have a great day."))
                return

            #  FIX 1: attempt fast path before calling LLM 
            handled, gen = self._try_fast_path(text, fast_found)
            if handled:
                logger.info("fast_path_resolved", field=self.awaiting_field)
                yield from gen
                return

            # 3. LLM extraction — only reached when fast path couldn't resolve
            # FIX 2: skip RAG during structured field-collection turns
            _SKIP_RAG_STATES  = {"COLLECTING_DETAILS", "WAITING_CONFIRMATION"}
            _SKIP_RAG_FIELDS  = {"name", "phone", "date", "time", "new_date",
                                  "new_time", "reason", "customer_id", "patient_type"}
                                  
            # Use RAG ONLY if intent is currently 'none' or we cannot fast-extract a booking intent.
            current_intent = fast_found.get("intent") or self.state.get("intent")
            
            # FAST EXECUTION: Completely bypass the LLM for transactional workflows
            # This guarantees sub-second replies (<1s) for standard booking/rescheduling.
            skip_llm = current_intent in ("book", "reschedule", "cancel", "view_appointments")

            skip_rag = (
                self.state.get("workflow_state") in _SKIP_RAG_STATES
                or self.awaiting_field in _SKIP_RAG_FIELDS
                or skip_llm
            )
            
            if skip_rag:
                context = ""
                logger.info("rag_skipped", workflow_state=self.state.get('workflow_state'), awaiting=self.awaiting_field)
            else:
                logger.info("querying_vector_db")
                context = self.vdb.get_context(text)

            if skip_llm:
                logger.info("bypassed_llm", intent=current_intent)
                llm_raw = {}
            else:
                logger.info("calling_llm", awaiting=self.awaiting_field)
                llm_raw = self._call_llm(text, awaiting_field=self.awaiting_field, context=context)

            # Merge fast_found with LLM results
            llm_data = fast_found.copy()
            if llm_raw:
                for k, v in llm_raw.items():
                    if k == "customer_id": continue   # always use regex for CID
                    if v and v not in ("empty", "none", "null", "Empty", "None"):
                        llm_data[k] = v

            if llm_data or fast_found:
                protected = set()
                if self.state.get("customer_confirmed"): protected.update({"name", "phone"})

                llm_pt       = llm_data.get("patient_type", "")
                asked_for_pt = (self.awaiting_field == "patient_type")

                if (llm_pt == "new" and self.state.get("patient_type") == "old" and asked_for_pt):
                    self.state["patient_type"]       = "new"
                    self.state["customer_id"]        = None
                    self.state["customer_confirmed"] = False
                    self.awaiting_field              = None

                edit_mode  = (self.state.get("workflow_state") == "COLLECTING_DETAILS"
                              and self.awaiting_field is None)
                meta_update = False

                for k, v in llm_data.items():
                    if k in protected: continue
                    if k == "patient_type": continue
                    if v and v not in ("empty", "none", "null", "Empty", "None"):
                        if k == "intent" and self.state.get("intent") in ("book", "reschedule", "cancel"):
                            continue
                        if self.state.get(k):
                            if k != self.awaiting_field and not edit_mode:
                                if k in ("date", "time", "new_date", "new_time"):
                                    self._update(**{k: v})
                                    meta_update = True
                                continue
                        self._update(**{k: v})

                if not self.state.get("patient_type") and llm_pt in ("new", "old") and asked_for_pt:
                    self._update(patient_type=llm_pt)

                if self.awaiting_field and self.state.get(self.awaiting_field):
                    self.awaiting_field = None

                if not self.state.get("intent"):
                    if llm_data.get("name"):
                        yield from self._stream_string(
                            f"Nice to meet you, {llm_data['name']}! How can I help you today?"
                        )
                        return
                    ga = llm_data.get("general_answer", "")
                    if ga and ga not in ("empty", "none", "null", "Empty", "None"):
                        yield from self._stream_string(ga)
                        return
                    yield from self._stream_string(self.messages.get("help_options"))
                    return

                intent_now = self.state.get("intent")
                pt_now     = self.state.get("patient_type")

                if pt_now == "new" and intent_now == "reschedule":
                    self.state["intent"] = "book"
                    self.state["new_patient_greeted"] = False
                    yield from self._stream_string(
                        "As a new patient, you don't have an existing appointment to reschedule. "
                        "Let me help you book one instead! Are you a new or existing patient?"
                    )
                    return

                if pt_now == "new" and intent_now == "cancel":
                    self.reset_state()
                    yield from self._stream_string(
                        "As a new patient, you don't have an existing appointment to cancel. "
                        "Would you like to book one instead?"
                    )
                    return

                if self.state.get("time") and not self.validate_time(self.state["time"]):
                    self.state["time"] = None
                    self.awaiting_field = "time"
                    slots = self.sheets.get_available_slots(self.state.get("date"), reason=self.state.get("reason"), offset=self.state.get("suggestion_turn", 0), customer_id=self.state.get("customer_id"))
                    suggestion = f" (Suggested times: {', '.join(slots)})" if slots else ""
                    yield from self._stream_string(
                        "I'm sorry, we are only open from 9 AM to 5 PM. "
                        f"Could you please specify a different time?{suggestion}"
                    )
                    return

                if self.state.get("new_time") and not self.validate_time(self.state["new_time"]):
                    self.state["new_time"] = None
                    self.awaiting_field = "new_time"
                    slots = self.sheets.get_available_slots(self.state.get("new_date"), reason=self.state.get("reason"), offset=self.state.get("suggestion_turn", 0), customer_id=self.state.get("customer_id"))
                    suggestion = f" (Available: {', '.join(slots)})" if slots else ""
                    yield from self._stream_string(
                        "I'm sorry, we are only open from 9 AM to 5 PM. "
                        f"Could you please specify a different time for your rescheduled appointment?{suggestion}"
                    )
                    return

                # Confirmation turn — LLM resolved user_confirmed / user_rejected
                if self.state.get("workflow_state") == "WAITING_CONFIRMATION":
                    decision = fast_yes_no(text)
                    if llm_data.get("user_confirmed"): decision = "yes"
                    if llm_data.get("user_rejected"):  decision = "no"

                    # Task 3: Prioritize meta_update (instant field updates during confirmation)
                    if meta_update:
                        yield from self._stream_string(
                            "Got it! I've updated your information. " + self._confirm_prompt()
                        )
                        return

                    if decision == "yes":
                        self.state["workflow_state"] = "COMPLETED"
                        yield from self._stream_string(self._execute())
                        return


                    if decision == "no":
                        self.state["workflow_state"] = "COLLECTING_DETAILS"
                        self.awaiting_field = "reconfirm_field"
                        yield from self._stream_string(
                            "What would you like to change? You can say name, date, time, or reason."
                        )
                        return

                    for field in ("name", "date", "time", "reason"):
                        if field in text.lower():
                            self.awaiting_field = field
                            yield from self._stream_string(f"Sure, what should the {field} be?")
                            return

                    yield from self._stream_string(
                        "Please say yes to confirm or no to make changes."
                    )
                    return

            else:
                if llm_data and llm_data.get("general_answer"):
                    missing_fields = self._missing()
                    suffix = " " + self._prompt_for(missing_fields[0]) if missing_fields else ""
                    yield from self._stream_string(str(llm_data["general_answer"]) + suffix)
                    return

                text_clean = text.lower().strip()
                field_patterns = {
                    "name":   r'\b(name|who|called)\b',
                    "date":   r'\b(date|day|monday|tuesday|wednesday|thursday|friday|saturday|when)\b',
                    "time":   r'\b(time|hour|moment|clock|am|pm)\b',
                    "reason": r'\b(reason|why|service|treatment|procedure)\b'
                }

                if any(kw in text_clean for kw in ("change", "edit", "wrong", "update", "different", "modify")):
                    for field, pattern in field_patterns.items():
                        if re.search(pattern, text_clean):
                            self.awaiting_field = field
                            yield from self._stream_string(
                                f"Sure! I can update that for you. What should the {field} be?"
                            )
                            return

                for field in ("name", "date", "time", "reason"):
                    if text_clean == field:
                        self.awaiting_field = field
                        yield from self._stream_string(f"Sure, what should the {field} be?")
                        return

                if len(text_clean) < 4:
                    yield from self._stream_string(
                        "Hello! I'm here to help with your dental visit. What would you like to do today?"
                    )
                    return

                yield from self._stream_string(self.messages.get("did_not_catch"))
                return

            missing = self._missing()
            if missing:
                self.state["workflow_state"] = "COLLECTING_DETAILS"
                f = missing[0]
                self.awaiting_field = f

                if f == "new_patient_greet":
                    self.state["new_patient_greeted"] = True
                    self.awaiting_field = "name"
                    msg = ("Welcome! Your patient record will be created automatically after "
                           "your first appointment is booked. " + self._prompt_for("name"))
                    yield from self._stream_string(msg)
                    return


                prefix = ""
                if self.state.get("phone_not_found"):
                    prefix = "I couldn't find a record for that number. Could you check the number, or should we set you up as a new patient? "
                    self.state["phone_not_found"] = False
                elif self.state.get("welcome_back_pending"):
                    prefix = f"Welcome back, {self.state['name']}! "
                    self.state["welcome_back_pending"] = False
                
                yield from self._stream_string(prefix + self._prompt_for(f))
                return

            if self.state["workflow_state"] != "COMPLETED":
                try:
                    start = self._parse_dt(self.state.get("date"), self.state.get("time"))
                    if not self._is_biz_hours(start):
                        if start.weekday() == 6:
                            self.state["date"] = None
                            self.state["time"] = None
                            self.awaiting_field = "date"
                            yield from self._stream_string(
                                "We are closed on Sundays. Please choose another date."
                            )
                            return
                        else:
                            self.state["time"] = None
                            self.awaiting_field = "time"
                            slots = self.sheets.get_available_slots(self.state.get("date"), customer_id=self.state.get("customer_id"))
                            import random
                            if slots: slots = random.sample(slots, min(5, len(slots)))
                            suggestion = f" (Suggested times: {', '.join(slots)})" if slots else ""
                            yield from self._stream_string(
                                f"We are only open Monday to Saturday, 9 AM to 5 PM. Could you choose another time?{suggestion}"
                            )
                            return
                    
                    # ALSO check new_date/new_time for Reschedule
                    if self.state.get("intent") == "reschedule":
                        n_start = self._parse_dt(self.state.get("new_date"), self.state.get("new_time"))
                        if not self._is_biz_hours(n_start):
                            if n_start.weekday() == 6:
                                self.state["new_date"] = None
                                self.state["new_time"] = None
                                self.awaiting_field = "new_date"
                                yield from self._stream_string(
                                    "Your new appointment date cannot be a Sunday. Please choose another date."
                                )
                                return
                            else:
                                self.state["new_time"] = None
                                self.awaiting_field = "new_time"
                                slots = self.sheets.get_available_slots(self.state.get("new_date"), customer_id=self.state.get("customer_id"))
                                import random
                                if slots: slots = random.sample(slots, min(5, len(slots)))
                                suggestion = f" (Suggested times: {', '.join(slots)})" if slots else ""
                                yield from self._stream_string(
                                    f"Our opening hours are Mon-Sat, 9 AM to 5 PM. Please choose a different time for your new slot.{suggestion}"
                                )
                                return
                except Exception: pass

                self.state["workflow_state"] = "WAITING_CONFIRMATION"
                yield from self._stream_string(self._confirm_prompt())
                return

            # Execute and handle potential custom-returned errors
            result = self._execute()
            if "I'm sorry" in result or "Please choose" in result or "We are closed" in result:
                # If execution failed (e.g. Sunday during _reschedule), reset state to allow correction
                self.state["workflow_state"] = "COLLECTING_DETAILS"
                if self.state.get("new_time"): self.awaiting_field = "new_time"
                elif self.state.get("time"):    self.awaiting_field = "time"
            
            yield from self._stream_string(result)

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("agent_error", error=str(e), traceback=tb)
            yield from self._stream_string(self.messages.get("unknown_error"))

    #  FIX 3: streaming delay is opt-in (False for Twilio/voice paths) ─
    def _stream_string(self, s):
        """
        Yield the response word-by-word.
        """
        if not s:
            return
        if not isinstance(s, str):
            s = str(s)
        words = s.split(' ')
        for i, word in enumerate(words):
            yield word + (' ' if i < len(words) - 1 else '')
            if self.streaming:
                time.sleep(0.02)

    def _execute(self):
        i = self.state.get("intent")
        if i == "book":              return self._book()
        if i == "reschedule":        return self._reschedule()
        if i == "cancel":            return self._cancel()
        if i == "view_appointments": return self._view()
        return self.messages.get("how_else_help")

    #  DATETIME HELPER ─
    def _parse_dt(self, date_str, time_str):
        if not date_str or not time_str:
            raise ValueError("Date or time is missing.")
        t = fast_extract_time(time_str) or time_str
        if len(t) > 10 and ' ' in t:
            parts = t.split()
            if parts[-1].upper() in ('AM', 'PM') and len(parts) >= 2:
                t = f"{parts[-2]} {parts[-1]}"
            else:
                t = parts[-1]
        try:
            dt = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %I:%M %p")
        except ValueError:
            try:
                dt = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M")
            except ValueError:
                raise ValueError(
                    f"Could not understand '{date_str} {time_str}'. "
                    "Please use a format like 10:30 AM."
                )
        # Tag with Kolkata timezone
        tz = get_tz()
        return dt.replace(tzinfo=tz) if tz else dt

    def _is_biz_hours(self, dt):
        if dt.weekday() == 6: return False
        if not (9 <= dt.hour < 17): return False
        if dt.hour == 13: return False # 1:00 PM to 2:00 PM Lunch Break
        return True

    #  APPOINTMENT ACTIONS ─
    def _book(self):
        try: start = self._parse_dt(self.state.get("date"), self.state.get("time"))
        except ValueError as e: return str(e)

        if not self._is_biz_hours(start):
            if start.weekday() == 6:
                self.state["date"] = None
                self.state["time"] = None
                return "We are closed on Sundays. Please choose another date and time."
            elif start.hour == 13:
                self.state["time"] = None
                return "Our lunch break is between 1:00 PM and 2:00 PM. Please suggest a different time."
            else:
                self.state["time"] = None
                return "We are only open from 9 AM to 5 PM. What other time would you like to choose?"

        today = datetime.now(get_tz()).date()
        days  = (start.date() - today).days
        if days < 0:
            self.state["date"] = None
            return self.messages.get("past_date")
        if days > 10:
            self.state["date"] = None
            return self.messages.get("advanced_booking_limit")

        try:
            cid = self.state.get("customer_id") or self.sheets.generate_customer_id()
            eid = self.calendar.create_appointment(
                self.state["name"], self.state["phone"], start,
                self.state.get("reason", ""), cid
            )
            if eid:
                doctor_name = self.sheets.log_appointment(
                    cid, self.state["name"], self.state["phone"],
                    self.state["date"], self.state["time"], self.state.get("reason", "")
                )
                date_str = self.state["date"]
                time_str = self.state["time"]
                # Removal of self.reset_state() so session persists
                msg = self.messages.get("appointment_booked")
                return msg.format(doctor=doctor_name, cid=cid, date=date_str, time=time_str)
            rejected_time = self.state["time"]
            turn = self.state.get("suggestion_turn", 0)
            self.state["time"] = None
            slots = self.sheets.get_available_slots(self.state["date"], offset=turn, reason=self.state.get("reason"), target_time=rejected_time, customer_id=self.state.get("customer_id"))
            msg = self.messages.get("slot_taken")
            if slots:
                msg += f" The closest available times are {', '.join(slots)}. What time would you prefer?"
            self.state["suggestion_turn"] = turn + 1
            return msg
        except Exception as e:
            logger.error("book_error", error=str(e)); return self.messages.get("booking_error")

    def _reschedule(self):
        try: new_start = self._parse_dt(self.state.get("new_date"), self.state.get("new_time"))
        except ValueError as e: return str(e)

        if not self._is_biz_hours(new_start):
            if new_start.weekday() == 6:
                self.state["new_date"] = None
                self.state["new_time"] = None
                return "We are closed on Sundays. Please choose another date and time."
            elif new_start.hour == 13:
                self.state["new_time"] = None
                return "Our lunch break is between 1:00 PM and 2:00 PM. Please suggest a different time."
            else:
                self.state["new_time"] = None
                return self.messages.get("closed_biz_hours")

        try:
            old = self.calendar.find_appointment(
                self.state["name"], self.state["phone"], self.state["date"], self.state.get("time")
            )
            
            # Fallback to Sheets Search if calendar missed it
            if not old:
                logger.info("calendar_search_failed_trying_sheets_search")
                row = self.sheets.find_appointment_row(
                    self.state.get("customer_id"), 
                    self.state["date"], 
                    self.state["time"], 
                    name=self.state.get("name"),
                    phone=self.state.get("phone")
                )
                if row:
                    old = {"id": "SHEETS_ONLY", "summary": "Legacy Appointment"}
            
            if not old: return self.messages.get("appointment_not_found")

            #  Carry over the reason from the old calendar event 
            # When rescheduling via voice, the user only gives a new date/time.
            # The reason stays the same as the original booking.
            reason = self.state.get("reason", "")
            if not reason:
                desc = old.get("description", "")
                for line in desc.splitlines():
                    if line.lower().startswith("reason:"):
                        reason = line.split(":", 1)[1].strip()
                        break
                self.state["reason"] = reason
                logger.info("reschedule_carry_over_reason", reason=reason)
            # ─

            self.calendar.cancel(old["id"])
            eid = self.calendar.create_appointment(
                self.state["name"], self.state["phone"], new_start,
                reason, self.state.get("customer_id")
            )
            if eid:
                doctor_name = self.sheets.update_appointment(
                    self.state.get("customer_id"),
                    self.state["date"], self.state["time"],
                    self.state["new_date"], self.state["new_time"],
                    name=self.state.get("name"),
                    phone=self.state.get("phone"),
                    reason=reason
                )
                nd, nt = self.state["new_date"], self.state["new_time"]
                # Removal of self.reset_state() so session persists
                msg = self.messages.get("appointment_rescheduled")
                return msg.format(doctor=doctor_name, date=nd, time=nt)
            try:
                orig = self._parse_dt(self.state["date"], self.state["time"])
                self.calendar.create_appointment(
                    self.state["name"], self.state["phone"], orig,
                    self.state.get("reason", ""), self.state.get("customer_id")
                )
            except Exception: pass
            
            rejected_time = self.state["new_time"]
            turn = self.state.get("suggestion_turn", 0)
            self.state["new_time"] = None
            
            slots = self.sheets.get_available_slots(self.state["new_date"], offset=turn, reason=reason, target_time=rejected_time, customer_id=self.state.get("customer_id"))
            msg = self.messages.get("slot_taken")
            if slots:
                msg += f" The closest available times are {', '.join(slots)}. What time would you prefer?"
            self.state["suggestion_turn"] = turn + 1
            return msg
        except Exception as e:
            logger.error("reschedule_error", error=str(e)); return self.messages.get("reschedule_error")

    def _cancel(self):
        try:
            event = self.calendar.find_appointment(
                self.state["name"], self.state["phone"], self.state["date"], self.state.get("time")
            )
            if not event: return self.messages.get("appointment_not_found")
            
            # Extract reason if absent from state context
            reason = self.state.get("reason", "")
            if not reason and event:
                desc = event.get("description", "")
                m = re.search(r'Reason:\s*(.*)', desc)
                if m: reason = m.group(1).strip()
                
            self.calendar.cancel(event["id"])
            self.sheets.delete_appointment(
                self.state.get("customer_id"), self.state["date"], self.state["time"]
            )
            
            #  Cascade Cleanup & Notify 
            try:
                from scheduling_automation.future_appointments import FutureAppointmentsManager
                fa = FutureAppointmentsManager()
                # Scoped cleanup for future appointments linked to this one
                fa.delete_future_row(self.state.get("customer_id"), appt_date=self.state["date"], reason=reason)
                
                from scheduling_automation.whatsapp_service import send_cancellation_notice
                send_cancellation_notice(self.state["phone"], self.state["name"], self.state["date"])
            except Exception as e:
                logger.error("cancel_hooks_failed", error=str(e))
            d = self.state["date"]
            # Removal of self.reset_state() so session persists
            msg = self.messages.get(
                "appointment_cancelled",
                "Your appointment on {date} has been cancelled. Is there anything else?"
            )
            return msg.format(date=d)
        except Exception as e:
            logger.error("cancel_error", error=str(e)); return self.messages.get("cancel_error")

    def _view(self):
        cid = self.state.get("customer_id")
        if not cid: return self.prompts.get("customer_id")
        try:
            appts = self.sheets.get_appointments_by_id(cid)
            if not appts: return self.messages.get("no_appointments")

            today = datetime.now(ZoneInfo(TIMEZONE)).date()
            upcoming_appts = []
            for a in appts:
                try:
                    appt_date = datetime.strptime(a['appointment_date'], "%Y-%m-%d").date()
                    if appt_date >= today:
                        upcoming_appts.append(a)
                except:
                    continue

            if not upcoming_appts:
                return self.messages.get("no_appointments")

            lines = [f"{a['appointment_date']} at {a['appointment_time']}" for a in upcoming_appts]
            # Removal of self.reset_state() so session persists
            msg = self.messages.get(
                "view_appointments",
                "Your upcoming appointments: {lines}. Anything else?"
            )
            return msg.format(lines=", and ".join(lines))
        except Exception as e:
            logger.error("view_error", error=str(e)); return self.messages.get("view_error")

    #  UNIFIED AGENT BRIDGES 
    def _book_custom(self, name, phone, date, time, reason):
        """Helper for Deepgram Agent to book directly via name+phone."""
        self.state.update({"name": name, "phone": phone, "date": date, "time": time, "reason": reason, "intent": "book"})
        return self._book()

    def _book_custom_by_id(self, customer_id, date, time, reason):
        """
        Book using a verified customer_id (no name/phone needed).
        Resolves name+phone from Sheets so the calendar entry is fully populated.
        """
        customer = self.sheets.get_customer_by_id(customer_id)
        if not customer:
            return f"I'm sorry, I could not find a record for customer ID {customer_id}. Please try again."
        self.state.update({
            "customer_id": customer_id,
            "name":        customer.get("name", ""),
            "phone":       customer.get("phone", ""),
            "date":        date,
            "time":        time,
            "reason":      reason,
            "intent":      "book",
            "customer_confirmed": True,
        })
        return self._book()

    def _reschedule_custom(self, name, phone, old_date, new_date, new_time):
        """Helper for Deepgram Agent to reschedule directly via name+phone."""
        self.state.update({"name": name, "phone": phone, "date": old_date, "new_date": new_date, "new_time": new_time, "intent": "reschedule"})
        return self._reschedule()

    def _reschedule_custom_by_id(self, customer_id, old_date, old_time, new_date, new_time):
        """Reschedule using a verified customer_id. Carries reason from existing appointment."""
        customer = self.sheets.get_customer_by_id(customer_id)
        if not customer:
            return f"I'm sorry, I could not find a record for customer ID {customer_id}. Please try again."

        # Pre-fetch the reason from Sheets — match by date AND time for accuracy
        reason = ""
        try:
            appts = self.sheets.get_appointments_by_id(customer_id)
            old_time_upper = str(old_time).strip().upper()
            for a in appts:
                if (a.get("appointment_date") == old_date and
                        str(a.get("appointment_time", "")).strip().upper() == old_time_upper):
                    reason = a.get("appointment_reason", "")
                    break
            # Fallback: match by date only if time-exact match fails
            if not reason:
                for a in appts:
                    if a.get("appointment_date") == old_date:
                        reason = a.get("appointment_reason", "")
                        break
            logger.info("reschedule_pre_fetched_reason",
                        reason=reason, customer_id=customer_id,
                        old_date=old_date, old_time=old_time)
        except Exception as e:
            logger.warning("reschedule_reason_fetch_error", error=str(e))

        self.state.update({
            "customer_id":        customer_id,
            "name":               customer.get("name", ""),
            "phone":              customer.get("phone", ""),
            "date":               old_date,
            "time":               old_time,   # ← required by _reschedule() to find calendar event
            "new_date":           new_date,
            "new_time":           new_time,
            "reason":             reason,
            "intent":             "reschedule",
            "customer_confirmed": True,
        })
        return self._reschedule()

    def _cancel_custom(self, name, phone, date):
        """Helper for Deepgram Agent to cancel directly via name+phone."""
        self.state.update({"name": name, "phone": phone, "date": date, "intent": "cancel"})
        return self._cancel()

    def _cancel_custom_by_id(self, customer_id, date, time):
        """Cancel using a verified customer_id and exact time for precision."""
        customer = self.sheets.get_customer_by_id(customer_id)
        if not customer:
            return f"I'm sorry, I could not find a record for customer ID {customer_id}. Please try again."
        self.state.update({
            "customer_id": customer_id,
            "name":        customer.get("name", ""),
            "phone":       customer.get("phone", ""),
            "date":        date,
            "time":        time,    # Now required to find correct row in Sheets/Calendar
            "intent":      "cancel",
            "customer_confirmed": True,
        })
        return self._cancel()

    #  MAIN LOOP (CLI / voice) ─
    def run(self):
        self.voice.speak(self.messages.get("welcome"))
        while True:
            text = self.voice.listen()
            if not text or text in ("exit", "quit"):
                self.voice.speak(self.messages.get("goodbye")); break
            if text in ("unknown", "error"):
                self.voice.speak(self.messages.get("did_not_catch")); continue
            # CLI run() fully consumes the generator — no streaming delay needed
            self.voice.speak("".join(self.generate_response(text)))


if __name__ == "__main__":
    agent = DentalVoiceAgent(streaming=False)
    agent.run()
