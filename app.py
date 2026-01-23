import os
import pickle
import re
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import io
import sounddevice as sd
import soundfile as sf
from scipy.io import wavfile
AUDIO_BACKEND = "sounddevice"

import pyttsx3
TTS_AVAILABLE = True

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


# CONFIG 
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5-coder:3b"
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets"
]
TIMEZONE = "Asia/Kolkata"
APPOINTMENT_DURATION_MIN = 10

SAMPLE_RATE = 16000
DURATION = 10

# VOICE INTERFACE
class VoiceInterface:
    def __init__(self, use_voice=True):
        self.use_voice = use_voice and (AUDIO_BACKEND is not None) and TTS_AVAILABLE
        
        if self.use_voice:
            if TTS_AVAILABLE:
                self.engine = pyttsx3.init()
                voices = self.engine.getProperty('voices')
                if len(voices) > 1:
                    self.engine.setProperty('voice', voices[1].id)
                self.engine.setProperty('rate', 150)
                self.engine.setProperty('volume', 0.9)
            
            if SPEECH_RECOGNITION_AVAILABLE:
                self.recognizer = sr.Recognizer()
        else:
            print("\n⚠️  Running in TEXT MODE")

    def speak(self, text):
        print(f"\nAgent: {text}")
        if self.use_voice and TTS_AVAILABLE:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception as e:
                print(f"TTS Error: {e}")

    def record_audio_sounddevice(self, duration=DURATION):
        try:
            print(f"🎤 Recording for {duration} seconds... Speak now!")
            audio_data = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype=np.int16)
            sd.wait()
            print("✅ Recording complete")
            return audio_data
        except Exception as e:
            print(f"Recording error: {e}")
            return None

    def audio_to_text_sounddevice(self, audio_data):
        if not SPEECH_RECOGNITION_AVAILABLE:
            return "error"
        try:
            wav_buffer = io.BytesIO()
            wavfile.write(wav_buffer, SAMPLE_RATE, audio_data)
            wav_buffer.seek(0)
            with sr.AudioFile(wav_buffer) as source:
                audio = self.recognizer.record(source)
                text = self.recognizer.recognize_google(audio)
                print(f"Patient: {text}")
                return text
        except sr.UnknownValueError:
            return "unknown"
        except Exception as e:
            print(f"Recognition error: {e}")
            return "error"

    def listen(self):
        if self.use_voice and AUDIO_BACKEND == "sounddevice":
            try:
                audio_data = self.record_audio_sounddevice()
                return self.audio_to_text_sounddevice(audio_data) if audio_data is not None else "error"
            except Exception as e:
                print(f"Microphone error: {e}. Falling back to text.")
                return input("\nPatient (type): ").strip()
        else:
            return input("\nPatient (type): ").strip()

# GOOGLE CALENDAR
class GoogleCalendarManager:
    def __init__(self):
        self.service = self.authenticate()

    def authenticate(self):
        creds = None
        try:
            with open("token.pickle", "rb") as token:
                creds = pickle.load(token)
        except FileNotFoundError:
            pass
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.pickle", "wb") as token:
                pickle.dump(creds, token)
        return build("calendar", "v3", credentials=creds)

    def is_available(self, start_dt, end_dt):
        events = self.service.events().list(
            calendarId="primary", timeMin=start_dt.isoformat(), timeMax=end_dt.isoformat(), singleEvents=True
        ).execute().get("items", [])
        return len(events) == 0

    def create_appointment(self, name, phone, start_dt, reason, customer_id=None):
        end_dt = start_dt + timedelta(minutes=APPOINTMENT_DURATION_MIN)
        if not self.is_available(start_dt, end_dt):
            return None
        
        description = f"Patient: {name}\nPhone: {phone}\nReason: {reason}"
        if customer_id:
            description = f"Customer ID: {customer_id}\n" + description
        
        event = {
            "summary": f"Dental - {name}",
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        }
        created = self.service.events().insert(calendarId="primary", body=event).execute()
        return created["id"], created["htmlLink"]

    def find_appointment(self, name, phone, date):
        """Find appointment by name, phone, and date"""
        start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TIMEZONE))
        end = start + timedelta(days=1)
        
        # Search by name first
        events = self.service.events().list(
            calendarId="primary", 
            timeMin=start.isoformat(), 
            timeMax=end.isoformat(), 
            q=name, 
            singleEvents=True
        ).execute().get("items", [])
        
        # Filter by phone number in description if provided
        if phone and events:
            matching_events = []
            for event in events:
                description = event.get("description", "")
                # Extract phone from description and compare
                if phone in description.replace("-", "").replace(" ", ""):
                    matching_events.append(event)
            return matching_events[0] if matching_events else None
        
        return events[0] if events else None

    def reschedule(self, event_id, new_start):
        new_end = new_start + timedelta(minutes=APPOINTMENT_DURATION_MIN)
        if not self.is_available(new_start, new_end):
            return False
        event = self.service.events().get(calendarId="primary", eventId=event_id).execute()
        event["start"]["dateTime"] = new_start.isoformat()
        event["end"]["dateTime"] = new_end.isoformat()
        self.service.events().update(calendarId="primary", eventId=event_id, body=event).execute()
        return True

    def cancel(self, event_id):
        self.service.events().delete(calendarId="primary", eventId=event_id).execute()

# DENTAL AGENT WITH STATE
class DentalVoiceAgent:
    def __init__(self, use_voice=True):
        self.calendar = GoogleCalendarManager()
        self.sheets = GoogleSheetsManager()
        self.voice = VoiceInterface(use_voice=use_voice)
        
        # Conversation state to preserve information
        self.state = {
            "intent": None,
            "patient_type": None, # "new" or "old"
            "customer_id": None,
            "name": None,
            "phone": None,
            "date": None,
            "time": None,
            "new_date": None,  # For reschedule
            "new_time": None,  # For reschedule
            "reason": None,
            "customer_confirmed": False  # Track if customer confirmed their identity
        }
        self.awaiting_field = None  # Track what we're waiting for

    def reset_state(self):
        """Reset conversation state"""
        self.state = {k: None for k in self.state}
        self.state["customer_confirmed"] = False
        self.awaiting_field = None

    def parse_with_llm(self, text, context_state=None):
        """Enhanced LLM parser with context awareness using Llama 3"""
        today = datetime.now(ZoneInfo(TIMEZONE))
        
        # Handle customer confirmation
        if self.awaiting_field == "customer_confirmation":
            text_lower = text.lower().strip()
            if any(word in text_lower for word in ["yes", "yeah", "yep", "correct", "right", "confirm"]):
                self.state["customer_confirmed"] = True
                self.awaiting_field = None
                return {"intent": self.state.get("intent"), "customer_confirmed": True}
            elif any(word in text_lower for word in ["no", "nope", "wrong", "incorrect"]):
                self.state["customer_id"] = None
                self.state["name"] = None
                self.state["phone"] = None
                self.state["patient_type"] = None
                self.awaiting_field = "patient_type"
                return {"intent": self.state.get("intent"), "customer_confirmed": False}
        
        # Handle patient type (new/old)
        if self.awaiting_field == "patient_type":
            text_lower = text.lower().strip()
            if any(word in text_lower for word in ["new", "first time", "never been"]):
                self.state["patient_type"] = "new"
                self.awaiting_field = None
                return {"patient_type": "new"}
            elif any(word in text_lower for word in ["old", "returning", "been before", "already", "yes"]):
                self.state["patient_type"] = "old"
                self.awaiting_field = None
                return {"patient_type": "old"}

        # Handle customer ID check
        if self.awaiting_field == "customer_id":
            # Detect patient type first if not already known
            if not self.state.get("patient_type"):
                text_lower = text.lower().strip()
                if any(word in text_lower for word in ["new", "first time", "never"]):
                    self.state["patient_type"] = "new"
                    return {"patient_type": "new"}
                elif any(word in text_lower for word in ["old", "returning", "already"]):
                    self.state["patient_type"] = "old"
                    # continue to extract ID if present
            
            text_lower = text.lower().strip()
            if any(word in text_lower for word in ["no", "nope", "dont have"]):
                self.state["patient_type"] = "new"
                return {"patient_type": "new"}
                
            # Try to extract customer ID (format: CUST###)
            match = re.search(r'CUST\d{3}', text.upper())
            if match:
                return {"intent": self.state.get("intent"), "customer_id": match.group()}
        
        context_info = ""
        if context_state:
            context_info = f"\nCurrent conversation state: {json.dumps(context_state, indent=2)}"
        
        # Add awaiting field context
        awaiting_info = ""
        if self.awaiting_field:
            awaiting_info = f"\n\nIMPORTANT: The user is currently being asked for '{self.awaiting_field}'. Map their response to this field."
            if self.awaiting_field == "new_date":
                awaiting_info += " Put the date in 'new_date' field, NOT 'date'."
            elif self.awaiting_field == "new_time":
                awaiting_info += " Put the time in 'new_time' field, NOT 'time'."
            elif self.awaiting_field == "date":
                awaiting_info += " This is the OLD appointment date for reschedule/cancel."
            elif self.awaiting_field == "time":
                awaiting_info += " This is the OLD appointment time for reschedule/cancel."
            elif self.awaiting_field == "customer_id":
                awaiting_info += " Extract customer ID in format CUST### or detect if user is new customer."
        
        system_prompt = f"""You are a dental appointment assistant. Extract booking information from user input.

CRITICAL INSTRUCTIONS:
1. Identify intent: "book", "reschedule", "cancel", or "view_appointments"
2. Convert ANY date format to YYYY-MM-DD
3. Convert ANY time format to 12-hour format with AM/PM (e.g., "11:00 AM")
4. Extract phone numbers (remove spaces, keep only digits)
5. Extract names (handle variations like "my name is X", "I'm X", "it's X", "X speaking")
6. If user only provides partial info (just name, just phone), extract what's there
7. For RESCHEDULE: extract both OLD appointment info (date/time) AND NEW appointment info (new_date/new_time)
8. For VIEW_APPOINTMENTS: detect phrases like "what are my appointments", "show my bookings", "list my schedule"

Current context:
- Today: {today.strftime("%Y-%m-%d")}
- Current year: {today.year}{context_info}{awaiting_info}

IMPORTANT: Extract ALL available information, even if incomplete.

Return ONLY this JSON (no markdown, no explanation):
{{
  "intent": "book/reschedule/cancel/view_appointments or empty",
  "name": "extracted name or empty",
  "phone": "digits only, no spaces",
  "date": "YYYY-MM-DD or empty (OLD appointment date for reschedule/cancel)",
  "time": "HH:MM AM/PM or empty (OLD appointment time for reschedule/cancel)",
  "new_date": "YYYY-MM-DD or empty (NEW date for reschedule)",
  "new_time": "HH:MM AM/PM or empty (NEW time for reschedule)",
  "reason": "extracted reason or empty"
}}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ]

        try:
            # Make request to Ollama API
            ollama_url = f"{OLLAMA_BASE_URL}/api/chat"
            payload = {
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    "num_predict": 500
                }
            }
            
            response = requests.post(ollama_url, json=payload, timeout=30)
            response.raise_for_status()
            
            # Extract the response content
            result = response.json()
            raw = result.get("message", {}).get("content", "").strip()
            
            raw = re.sub(r"^```json\s*", "", raw, flags=re.I)
            raw = re.sub(r"```\s*$", "", raw).strip()
            parsed = json.loads(raw)
            return self.validate_and_fix(parsed, text)
        except Exception as e:
            print(f"[LLM Error] {e}")
            return self.fallback_parse(text)

    def validate_and_fix(self, parsed, original):
        """Validate and correct Gemini output"""
        today = datetime.now(ZoneInfo(TIMEZONE))
        
        # Clean phone number (remove spaces, keep only digits)
        if parsed.get("phone"):
            parsed["phone"] = re.sub(r'\s+', '', parsed["phone"])
            parsed["phone"] = re.sub(r'[^\d]', '', parsed["phone"])
        
        # Validate dates
        for date_key in ["date", "new_date"]:
            if parsed.get(date_key):
                # Handle cases where Gemini puts time in the date field
                # Format check: YYYY-MM-DD HH:MM AM/PM
                if " " in str(parsed[date_key]):
                    parts = str(parsed[date_key]).split(" ", 1)
                    parsed[date_key] = parts[0]
                    time_key = "time" if date_key == "date" else "new_time"
                    if not parsed.get(time_key):
                        parsed[time_key] = self.normalize_time(parts[1])
                
                try:
                    datetime.strptime(parsed[date_key], "%Y-%m-%d")
                except:
                    try:
                        parsed[date_key] = self.extract_date(original)
                    except:
                        parsed[date_key] = None
        
        # Validate times
        for time_key in ["time", "new_time"]:
            if parsed.get(time_key):
                parsed[time_key] = self.normalize_time(parsed[time_key])
        
        return parsed

    def is_business_hours(self, dt):
        """Check if a datetime is within Mon-Sat 9 AM - 5 PM"""
        # Weekday: 0=Mon, 6=Sun. Monday-Saturday = 0-5
        if dt.weekday() == 6:  # Sunday
            return False
        
        # 9 AM to 5 PM
        hour = dt.hour
        if hour < 9 or hour >= 17:
            return False
            
        return True

    def normalize_time(self, time_str):
        """Convert ANY time format to HH:MM AM/PM"""
        if not time_str:
            return None
        
        time_str = str(time_str).strip()
        
        # Already correct: "11:00 AM"
        if re.match(r"^\d{1,2}:\d{2}\s*[AP]\.?M\.?$", time_str, re.I):
            return re.sub(r"\.+", "", time_str.upper())
        
        # "11 AM" or "11AM"
        m = re.match(r"^(\d{1,2})\s*([AP])\.?M\.?$", time_str, re.I)
        if m:
            return f"{m.group(1)}:00 {m.group(2).upper()}M"
        
        # 24-hour: "14:00" or "14:30"
        m = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
        if m:
            hour, minute = int(m.group(1)), m.group(2)
            period = "AM" if hour < 12 else "PM"
            if hour == 0:
                hour = 12
            elif hour > 12:
                hour -= 12
            return f"{hour}:{minute} {period}"
        
        # Just number: "11" or "14"
        m = re.match(r"^(\d{1,2})$", time_str)
        if m:
            hour = int(m.group(1))
            period = "AM" if hour < 12 else "PM"
            if hour > 12:
                hour -= 12
            return f"{hour}:00 {period}"
        
        return None

    def extract_date(self, text):
        """Extract date from ANY format"""
        today = datetime.now(ZoneInfo(TIMEZONE))
        text_lower = text.lower()
        
        # YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
        
        # DD/MM/YYYY or DD-MM-YYYY
        m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
        if m:
            return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
        
        # Month names
        months = {
            'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
            'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'october': 10, 'oct': 10,
            'november': 11, 'nov': 11, 'december': 12, 'dec': 12
        }
        
        for month_name, month_num in months.items():
            patterns = [
                # January 6 2026, January 6th 2026
                (rf"({month_name})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+(\d{{4}})", [2, 3]),
                # 6 January 2026, 6th January 2026
                (rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_name})\s+(\d{{4}})", [1, 3]),
                # January 6, January 6th (current year)
                (rf"({month_name})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s|$|,)", [2, None]),
                # Just month and year
                (rf"({month_name})\s+(\d{{4}})", [None, 2])
            ]
            
            for pattern, indices in patterns:
                m = re.search(pattern, text_lower)
                if m:
                    groups = m.groups()
                    day = groups[indices[0]-1] if indices[0] else "01"
                    year = groups[indices[1]-1] if indices[1] else str(today.year)
                    
                    day_int = int(day)
                    year_int = int(year)
                    
                    if not indices[1]:
                        target = datetime(year_int, month_num, day_int)
                        if target < today:
                            year_int += 1
                    
                    return f"{year_int}-{str(month_num).zfill(2)}-{str(day_int).zfill(2)}"
        
        # Relative
        if "tomorrow" in text_lower:
            return (today + timedelta(days=1)).strftime("%Y-%m-%d")
        if "today" in text_lower:
            return today.strftime("%Y-%m-%d")
        if "next week" in text_lower:
            return (today + timedelta(days=7)).strftime("%Y-%m-%d")
        
        # Just a number (day)
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text)
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                try:
                    target = today.replace(day=day)
                    if target < today:
                        next_month = today.month + 1 if today.month < 12 else 1
                        next_year = today.year if today.month < 12 else today.year + 1
                        target = target.replace(month=next_month, year=next_year)
                    return target.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        
        return None

    def fallback_parse(self, text):
        """Fallback when Gemini fails - uses awaiting_field context"""
        phone_match = re.search(r'\d[\d\s]{7,}', text)
        name_match = re.search(r'(?:my name is|i am|i\'m|it\'s|this is)\s+([a-z\s]+)', text, re.I)
        
        # Extract date and time
        extracted_date = self.extract_date(text)
        extracted_time = self.normalize_time(
            m.group() if (m := re.search(r"\d{1,2}:\d{2}|\d{1,2}\s*[ap]m", text, re.I)) else ""
        )
        
        # Initialize result
        result = {
            "intent": None,
            "name": None,
            "phone": None,
            "date": None,
            "time": None,
            "new_date": None,
            "new_time": None,
            "reason": None
        }
        
        # Detect intent
        if "book" in text.lower():
            result["intent"] = "book"
        elif "reschedule" in text.lower():
            result["intent"] = "reschedule"
        elif "cancel" in text.lower():
            result["intent"] = "cancel"
        
        # Map extracted values based on awaiting_field
        if self.awaiting_field == "new_date" and extracted_date:
            result["new_date"] = extracted_date
        elif self.awaiting_field == "new_time" and extracted_time:
            result["new_time"] = extracted_time
        elif self.awaiting_field == "date" and extracted_date:
            result["date"] = extracted_date
        elif self.awaiting_field == "time" and extracted_time:
            result["time"] = extracted_time
        elif self.awaiting_field == "name" and name_match:
            result["name"] = name_match.group(1).strip()
        elif self.awaiting_field == "phone" and phone_match:
            result["phone"] = re.sub(r'\D', '', phone_match.group())
        elif self.awaiting_field == "reason":
            # When asking for reason, take the entire text as the reason
            result["reason"] = text.strip()
        else:
            # No specific field awaited, extract everything
            result["name"] = name_match.group(1).strip() if name_match else None
            result["phone"] = re.sub(r'\D', '', phone_match.group()) if phone_match else None
            result["date"] = extracted_date
            result["time"] = extracted_time
        
        return result

    def update_state(self, new_data):
        """Update state with new information, preserving existing data"""
        for key, value in new_data.items():
            if value:  # Only update if new value is not None/empty
                self.state[key] = value

    def get_missing_fields(self):
        """Determine what information is still needed based on intent.
        STRICT ENFORCEMENT: No null values allowed for required fields."""
        intent = self.state.get("intent")
        
        # 1. Ask patient type if unknown for ANY intent
        if not self.state.get("patient_type"):
            return ["patient_type"]
            
        # 2. Branch for OLD patient
        if self.state.get("patient_type") == "old":
            # Need ID first
            if not self.state.get("customer_id"):
                return ["customer_id"]
            
            # If ID provided, need confirmation
            if not self.state.get("customer_confirmed"):
                return ["customer_confirmation"]
            
            # Once confirmed, we have name/phone from customer record
            # But still need appointment details
            if intent == "book" or not intent:
                # STRICT: name, phone, date, time, reason (all required)
                required = ["name", "phone", "date", "time", "reason"]
                missing = [f for f in required if not self.state.get(f)]
                return missing
            elif intent == "reschedule":
                # STRICT: name, phone, old date, old time, new date, new time (NO reason)
                required = ["name", "phone", "date", "time", "new_date", "new_time"]
                missing = [f for f in required if not self.state.get(f)]
                return missing
            elif intent == "cancel":
                # STRICT: name, customer_id, date, time
                required = ["name", "customer_id", "date", "time"]
                missing = [f for f in required if not self.state.get(f)]
                return missing
            elif intent == "view_appointments":
                return [] # Just need ID, which it already has if confirmed
        
        # 3. Branch for NEW patient
        else:
            if intent == "book" or not intent:
                # STRICT: name, phone, date, time, reason (all required)
                required = ["name", "phone", "date", "time", "reason"]
                missing = [f for f in required if not self.state.get(f)]
                return missing
            elif intent == "reschedule":
                # STRICT: name, phone, old date, old time, new date, new time (NO reason)
                required = ["name", "phone", "date", "time", "new_date", "new_time"]
                missing = [f for f in required if not self.state.get(f)]
                return missing
            elif intent == "cancel":
                # STRICT: name, phone, date, time (new patients don't have customer_id yet)
                required = ["name", "phone", "date", "time"]
                missing = [f for f in required if not self.state.get(f)]
                return missing
        
        return []

    def generate_response(self, text):
        try:
            # Parse with context
            data = self.parse_with_llm(text, self.state)
            print(f"\n[DEBUG] Parsed: {json.dumps(data, indent=2)}")
            
            # Update state with new information
            self.update_state(data)
            print(f"[STATE] Current: {json.dumps(self.state, indent=2)}")
            
            # Check what's missing
            missing = self.get_missing_fields()
            
            if missing:
                field = missing[0]
                self.awaiting_field = field
                
                if field == "patient_type":
                    return "Are you a new or old patient?"
                
                if field == "customer_id":
                    return "Please tell me your customer ID."
                
                if field == "customer_confirmation":
                    # Look up customer
                    customer = self.sheets.get_customer_by_id(self.state["customer_id"])
                    if customer:
                        self.state["name"] = customer["name"]
                        self.state["phone"] = customer["phone"]
                        return f"I found your records, {customer['name']}! Is your phone number still {customer['phone']}? Say 'yes' to confirm or 'no' if this is incorrect."
                    else:
                        self.state["customer_id"] = None
                        self.awaiting_field = "patient_type"
                        return "I couldn't find that ID. Are you sure you're an old patient? Please say 'new' or 'old'."
                
                # Context-aware prompts based on intent
                intent = self.state.get("intent")
                
                prompts = {
                    "name": "May I have your full name?",
                    "phone": f"Thanks{', ' + self.state['name'] if self.state.get('name') else ''}! Could you please provide your phone number?",
                    "reason": (
                        "What is the reason for your visit? You may choose one of the following services or briefly describe your concern:\\n\\n"
                        "• General Dental Checkup & Consultation\\n"
                        "• Tooth Filling & Restoration\\n"
                        "• Root Canal Treatment\\n"
                        "• Braces & Teeth Alignment\\n"
                        "• Gum Treatment (Scaling & Periodontal Care)\\n"
                        "• Crowns, Bridges & Dentures\\n"
                        "• Tooth Extraction & Oral Surgery\\n"
                        "• Pediatric Dental Care (Child Dentistry)\\n"
                        "• Oral Diagnosis & Dental X-rays\\n"
                        "• Oral Lesion & Disease Evaluation\\n"
                    )
                }
                
                # Context-specific prompts for date/time
                if field == "date":
                    if intent == "reschedule" or intent == "cancel":
                        prompts["date"] = "What is the CURRENT date of your existing appointment that you want to change?"
                    else:
                        prompts["date"] = "What date would you like to schedule the appointment for?"
                
                if field == "time":
                    if intent == "reschedule" or intent == "cancel":
                        prompts["time"] = "What is the CURRENT time of your existing appointment?"
                    else:
                        prompts["time"] = "What time works best for you?"
                
                if field == "new_date":
                    prompts["new_date"] = "Great! Now, what NEW date would you like to reschedule to?"
                
                if field == "new_time":
                    prompts["new_time"] = "And what NEW time would you like for this appointment?"

                return prompts.get(field, f"Please provide your {field}.")
            
            # All fields collected - proceed with the action
            if self.state["intent"] == "book" or not self.state["intent"]:
                return self.book(self.state)
            elif self.state["intent"] == "reschedule":
                return self.reschedule_appointment(self.state)
            elif self.state["intent"] == "cancel":
                return self.cancel_appointment(self.state)
            elif self.state["intent"] == "view_appointments":
                return self.list_my_appointments(self.state)
            
            # If intent unknown but fields collected, default to booking
            if self.state.get("name") and self.state.get("phone"):
                self.state["intent"] = "book"
                return self.book(self.state)
            
            return "I'd be happy to help you book, reschedule, or cancel an appointment. What would you like to do?"
            
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            return "Sorry, I had trouble with that. Could you repeat?"

    def book(self, d):
        try:
            # Parse datetime for validation
            try:
                # Clean time string (sometimes contains date or unwanted text)
                time_str = d['time']
                if " " in d['date'] and not time_str:
                    d['date'], time_str = d['date'].split(" ", 1)
                
                start = datetime.strptime(f"{d['date']} {time_str}", "%Y-%m-%d %I:%M %p")
                start = start.replace(tzinfo=ZoneInfo(TIMEZONE))
            except Exception as e:
                print(f"[Date Conversion Error] {e}")
                return f"I'm sorry, I couldn't understand the time {d['time']} on {d['date']}. Could you please repeat that?"

            # Validate 3-day booking window (current date + 3 days)
            today = datetime.now(ZoneInfo(TIMEZONE)).replace(hour=0, minute=0, second=0, microsecond=0)
            max_booking_date = today + timedelta(days=3)
            appointment_date_only = start.replace(hour=0, minute=0, second=0, microsecond=0)
            
            if appointment_date_only < today:
                return "I'm sorry, you cannot book appointments for past dates. Please choose a date from today onwards."
            
            if appointment_date_only > max_booking_date:
                max_date_str = max_booking_date.strftime("%B %d, %Y")
                return f"I'm sorry, you can only book appointments up to 3 days in advance. The latest available date is {max_date_str}. Would you like to choose a date within this range?"

            # Check business hours
            if not self.is_business_hours(start):
                return "I'm sorry, our clinic is only open Monday to Saturday from 9:00 AM to 5:00 PM. Would you like to pick another time?"

            # Handle customer ID
            customer_id = d.get("customer_id")
            appointment_date = d['date']
            appointment_time = d['time']
            reason = d.get("reason", "")
            
            if not customer_id:
                # Generate NEW customer ID
                customer_id = self.sheets.generate_customer_id()
                id_message = f" Your customer ID is {customer_id}. Please save this for future bookings!"
            else:
                id_message = ""
            
            # Create calendar appointment
            result = self.calendar.create_appointment(
                d["name"], d["phone"], start, reason, customer_id
            )
            
            if not result:
                return "That time slot is taken. Would you like to try a different time?"
            
            # Log to Google Sheets ONLY ONCE after successful calendar creation
            self.sheets.log_appointment(customer_id, d["name"], d["phone"], appointment_date, appointment_time, reason)
            
            response = f"Perfect! Your appointment is confirmed for {d['name']} on {d['date']} at {d['time']}.{id_message} See you then!"
            self.reset_state()
            return response
            
        except Exception as e:
            print(f"[BOOK ERROR] {e}")
            import traceback
            traceback.print_exc()
            return "I couldn't complete the booking. Could you verify the date and time?"

    def reschedule_appointment(self, d):
        """Reschedule appointment with name, phone verification"""
        try:
            name = d.get("name")
            phone = d.get("phone")
            old_date = d.get("date")
            old_time = d.get("time")

            if not all([name, phone, old_date]):
                return "I'm missing some details about your existing appointment. Could you provide your name, phone, and the current date of the appointment?"

            # Find the existing appointment using name, phone, and old date
            event = self.calendar.find_appointment(name, phone, old_date)
            
            if not event:
                return f"I couldn't find an appointment for {name} with phone {phone} on {old_date}. Please check the details."
            
            # Parse new datetime for validation
            new_date = d.get("new_date")
            new_time = d.get("new_time")
            
            if not new_date or not new_time:
                return "I need to know both the new date and the new time you'd like to reschedule to."

            try:
                # Robust cleaning similar to book()
                if " " in new_date and not new_time:
                    new_date, new_time = new_date.split(" ", 1)
                
                new_start = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %I:%M %p")
                new_start = new_start.replace(tzinfo=ZoneInfo(TIMEZONE))
            except Exception as e:
                print(f"[Reschedule Date Conversion Error] {e}")
                return f"I couldn't understand the new time {new_time} on {new_date}. Could you please repeat that?"

            # Validate 3-day booking window (current date + 3 days)
            today = datetime.now(ZoneInfo(TIMEZONE)).replace(hour=0, minute=0, second=0, microsecond=0)
            max_booking_date = today + timedelta(days=3)
            new_appointment_date_only = new_start.replace(hour=0, minute=0, second=0, microsecond=0)
            
            if new_appointment_date_only < today:
                return "I'm sorry, you cannot reschedule to a past date. Please choose a date from today onwards."
            
            if new_appointment_date_only > max_booking_date:
                max_date_str = max_booking_date.strftime("%B %d, %Y")
                return f"I'm sorry, you can only reschedule appointments up to 3 days in advance. The latest available date is {max_date_str}. Would you like to choose a date within this range?"

            # Check business hours
            if not self.is_business_hours(new_start):
                return "I'm sorry, our clinic is only open Monday to Saturday from 9:00 AM to 5:00 PM. Please choose a different time."
            
            # Update appointment log (find row and update Date/Time in same row)
            if d.get("customer_id"):
                self.sheets.update_appointment(d["customer_id"], old_date, old_time, new_date, new_time)
            
            # Delete old calendar event
            self.calendar.cancel(event["id"])
            
            # Create new calendar event with updated details
            customer_id = d.get("customer_id", "")
            reason = d.get("reason", "Rescheduled appointment")
            result = self.calendar.create_appointment(
                name, phone, new_start, reason, customer_id
            )
            
            if not result:
                return f"Sorry, {new_date} at {new_time} isn't available. Would you like to try another time?"
            
            response = f"Perfect! Your appointment has been rescheduled from {old_date} at {old_time} to {new_date} at {new_time}. See you then!"
            self.reset_state()
            return response
            
        except Exception as e:
            print(f"[RESCHEDULE ERROR] {e}")
            import traceback
            traceback.print_exc()
            return "I had trouble rescheduling. Could you verify all the details?"

    def cancel_appointment(self, d):
        """Cancel appointment with name, phone verification"""
        try:
            name = d.get("name")
            phone = d.get("phone")
            date = d.get("date")
            time = d.get("time")

            if not all([name, phone, date]):
                return "I'm missing some details to cancel the appointment. Could you provide your name, phone, and the date you booked for?"

            # Find the appointment using name, phone, and date
            event = self.calendar.find_appointment(name, phone, date)
            
            if not event:
                return f"I couldn't find an appointment for {name} with phone {phone} on {date}. Please check the details."
            
            # Cancel the appointment in calendar
            self.calendar.cancel(event["id"])
            
            # Delete from appointment log if customer ID is available
            if d.get("customer_id"):
                self.sheets.delete_appointment(d["customer_id"], date, time)
            
            response = f"Your appointment on {date} at {time} has been cancelled. Is there anything else I can help you with?"
            self.reset_state()
            return response
            
        except Exception as e:
            print(f"[CANCEL ERROR] {e}")
            import traceback
            traceback.print_exc()
            return "I had trouble cancelling the appointment. Could you please verify the date and try again?"
            
        except Exception as e:
            print(f"[CANCEL ERROR] {e}")
            import traceback
            traceback.print_exc()
            return "I had trouble cancelling the appointment. Could you verify the details?"

    def run(self):
        self.voice.speak("Hello! Welcome to Smile Dental. How can I help you today?")
        
        while True:
            user_input = self.voice.listen()
            
            if user_input in ["timeout", "unknown", "error"]:
                self.voice.speak("Didn't catch that. Please try again.")
                continue
            
            if any(w in user_input.lower() for w in ["exit", "quit", "bye", "goodbye"]):
                if self.state.get("name"):
                    self.voice.speak(f"Thanks {self.state['name']}! Have a great day!")
                else:
                    self.voice.speak("Thanks for contacting Smile Dental. Goodbye!")
                break
            
            response = self.generate_response(user_input)
            self.voice.speak(response)
            
    def list_my_appointments(self, d):
        """Helper to list all appointments for the current customer ID"""
        try:
            customer_id = d.get("customer_id")
            if not customer_id:
                return "I'm sorry, I need your customer ID to show your appointments."
            
            appointments = self.sheets.get_appointments_by_id(customer_id)
            
            if not appointments:
                return f"I couldn't find any appointments for Customer ID {customer_id}."
            
            # Simple list construction
            response = f"I found {len(appointments)} appointment(s) for you:\n"
            for appt in appointments:
                response += f"- {appt['appointment_date']} at {appt['appointment_time']} ({appt['appointment_reason']})\n"
            
            self.reset_state()
            return response
        except Exception as e:
            print(f"[LIST ERROR] {e}")
            return "I had trouble retrieving your appointments. Please try again."

# -----------------------------
# MAIN
# -----------------------------
def main():
    print("=" * 60)
    print("SMILE DENTAL - Stateful Conversation Agent (FIXED)")
    print("=" * 60)
    
    use_voice = AUDIO_BACKEND and TTS_AVAILABLE and SPEECH_RECOGNITION_AVAILABLE
    
    if not use_voice:
        print("\n⚠️  TEXT MODE")
    else:
        print(f"\n✅ Voice enabled ({AUDIO_BACKEND})")
    
    print("\n" + "=" * 60)
    print("SMART CONVERSATION - Natural dialogue for all actions!")
    print("=" * 60)
    print("\n📅 BOOKING:")
    print("   'Book an appointment for tomorrow at 10 AM'")
    print("   'My name is John, phone 555-1234'")
    print("\n♻️  RESCHEDULING:")
    print("   'Reschedule my appointment'")
    print("   Agent asks: Name? Phone? Current date/time? New date/time?")
    print("\n❌ CANCELLING:")
    print("   'Cancel my appointment'")
    print("   Agent asks: Name? Phone? Appointment date/time?")
    print("\n💡 Say 'exit' or 'quit' to stop")
    print("=" * 60 + "\n")
    
    try:
        agent = DentalVoiceAgent(use_voice=use_voice)
        agent.run()
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()