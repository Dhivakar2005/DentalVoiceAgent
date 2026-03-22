import os
import uuid
import json
import time
import threading
import traceback
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import requests as http_requests
from twilio.twiml.voice_response import VoiceResponse
from twilio.request_validator import RequestValidator

from app import DentalVoiceAgent, VoiceInterface, OLLAMA_BASE_URL, OLLAMA_MODEL
from database_manager import DatabaseManager

#  APP SETUP 
app = Flask(__name__, static_folder='static', template_folder='templates')

# every restart and logs out all users.
# app.secret_key = "smile-dental-secret-key"
# TWILIO_ACCOUNT_SID = "AC878d64388a378b523ba2af074bad4507"
# TWILIO_AUTH_TOKEN = "addbbc8df222b3cd5d75dd201ee6530c"

CORS(app)

# Session TTL in seconds — inactive sessions are cleaned up automatically
SESSION_TTL = 600   # 10 minutes

#  SHARED SINGLETONS 
# GoogleCalendarManager + OAuth build on every admin dashboard refresh.
_admin_agent = None
_admin_agent_lock = threading.Lock()

def get_admin_agent():
    global _admin_agent
    if _admin_agent is None:
        with _admin_agent_lock:
            if _admin_agent is None:
                _admin_agent = DentalVoiceAgent(use_voice=False)
    return _admin_agent

#  DATABASE 
db = DatabaseManager(app)

#  ACTIVE SESSIONS 
sessions      = {}
sessions_lock = threading.Lock()

# OLLAMA WARM-UP 
def warmup_ollama():
    """Load the model into GPU memory at startup to avoid cold-start delays."""
    try:
        print("[WARMUP] Warming up Ollama model — please wait...")
        resp = http_requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model":      OLLAMA_MODEL,
                "messages":   [{"role": "user", "content": "hi"}],
                "stream":     False,
                "keep_alive": -1,
                "options":    {"num_predict": 1}
            },
            timeout=120
        )
        if resp.status_code == 200:
            print("[OK] Ollama model is warm and ready!")
        else:
            print(f"[WARNING] Ollama warm-up returned status {resp.status_code}")
    except Exception as e:
        print(f"[WARNING] Ollama warm-up failed (model may load slowly on first request): {e}")

threading.Thread(target=warmup_ollama, daemon=True).start()

#  OLLAMA HEARTBEAT 
def ollama_heartbeat():
    """Ping Ollama every 4 min to keep the model in VRAM (default unload = 5 min)."""
    while True:
        try:
            time.sleep(240)
            http_requests.post(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        except Exception:
            pass

threading.Thread(target=ollama_heartbeat, daemon=True).start()

#  SESSION CLEANUP 
def cleanup_sessions():
    """Remove sessions that have been idle longer than SESSION_TTL."""
    while True:
        time.sleep(120)
        now   = time.time()
        stale = []
        with sessions_lock:
            for sid, agent in list(sessions.items()):
                if now - agent.last_active > SESSION_TTL:
                    stale.append(sid)
            for sid in stale:
                sessions.pop(sid, None)
        if stale:
            print(f"[CLEANUP] Removed {len(stale)} stale sessions")

threading.Thread(target=cleanup_sessions, daemon=True).start()

#  WEB VOICE AGENT WRAPPER 
class WebVoiceAgent:
    """Wraps DentalVoiceAgent for the web / Twilio interface."""

    def __init__(self, session_id):
        self.session_id           = session_id
        self.agent                = DentalVoiceAgent(use_voice=False)
        self.conversation_history = []
        self.last_active          = time.time()

    def process_message(self, user_message):
        self.last_active = time.time()
        try:
            self.conversation_history.append({
                "role":      "user",
                "message":   user_message,
                "timestamp": datetime.now().isoformat()
            })
            response = self.agent.generate_response(user_message)
            self.conversation_history.append({
                "role":      "agent",
                "message":   response,
                "timestamp": datetime.now().isoformat()
            })
            return {
                "success":      True,
                "response":     response,
                "state":        self.agent.state,
                "conversation": self.conversation_history
            }
        except Exception as e:
            return {
                "success":  False,
                "error":    str(e),
                "response": "Sorry, I encountered an error. Please try again."
            }

    def reset(self):
        self.agent.reset_state()
        self.conversation_history = []
        self.last_active          = time.time()

#  AUTH DECORATORS 
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"success": False, "error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({"success": False, "error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated

#  AUTH ROUTES 
@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'POST':
        data  = request.json or {}
        email = data.get('email', '').strip()
        password = data.get('password', '')
        user = db.authenticate_user(email, password)
        if user:
            session['user_id'] = str(user['_id'])
            session['email']   = user['email']
            session['name']    = user['name']
            session['role']    = user.get('role', 'user')
            return jsonify({"success": True, "message": "Login successful", "role": session['role']})
        return jsonify({"success": False, "error": "Invalid email or password"}), 401
    return render_template('login.html', type='signin')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        data  = request.json or {}
        email = data.get('email', '').strip()
        password = data.get('password', '')
        name  = data.get('name', '').strip()
        success, message = db.create_user(email, password, name)
        if success:
            return jsonify({"success": True, "message": message})
        return jsonify({"success": False, "error": message}), 400
    return render_template('login.html', type='signup')

@app.route('/logout')
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"})

@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template('login.html', type='signin')
    return render_template('index.html', user_name=session.get('name'), role=session.get('role'))

#  ADMIN ROUTES 
@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin':
        return render_template('login.html', type='signin', error="Admin access required")
    return render_template('admin.html')

@app.route('/api/admin/data')
@admin_required
def get_admin_data():
    """Return appointment data for the admin dashboard.
    Uses the shared singleton agent — no fresh OAuth per request.
    Only exposes the fields needed for the dashboard (no raw patient data dump).
    """
    try:
        agent        = get_admin_agent()
        appointments = agent.sheets.get_all_customers()

        now          = datetime.utcnow().isoformat() + 'Z'
        events_result = agent.calendar.service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=50,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        # Return only the fields the dashboard needs — no raw personal data dump
        safe_events = [
            {
                "summary": e.get("summary", ""),
                "start":   e.get("start", {}),
                "end":     e.get("end", {}),
                "status":  e.get("status", "")
            }
            for e in events_result.get('items', [])
        ]

        return jsonify({
            "success":         True,
            "appointments":    appointments,
            "calendar_events": safe_events
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

#  CHAT SESSION ROUTES 
@app.route('/api/start-session', methods=['POST'])
def start_session():
    session_id = str(uuid.uuid4())
    with sessions_lock:
        sessions[session_id] = WebVoiceAgent(session_id)
    return jsonify({
        "success":    True,
        "session_id": session_id,
        "message":    "Hello! Welcome to Smile Dental. How can I help you today?"
    })

@app.route('/api/send-message', methods=['POST'])
def send_message():
    data       = request.json or {}
    session_id = data.get('session_id', '').strip()
    message    = data.get('message', '').strip()

    with sessions_lock:
        agent = sessions.get(session_id)

    if not agent:
        return jsonify({"success": False, "error": "Invalid session. Please start a new session."}), 400
    if not message:
        return jsonify({"success": False, "error": "Message cannot be empty"}), 400

    result = agent.process_message(message)
    return jsonify(result)

@app.route('/api/reset-session', methods=['POST'])
def reset_session():
    data       = request.json or {}
    session_id = data.get('session_id', '').strip()

    with sessions_lock:
        agent = sessions.get(session_id)

    if not agent:
        return jsonify({"success": False, "error": "Invalid session"}), 400

    agent.reset()
    return jsonify({"success": True, "message": "Session reset successfully"})

@app.route('/api/end-session', methods=['POST'])
def end_session():
    data       = request.json or {}
    session_id = data.get('session_id', '').strip()
    if session_id:
        with sessions_lock:
            sessions.pop(session_id, None)
    return jsonify({"success": True, "message": "Session ended"})

@app.route('/api/get-history', methods=['GET'])
def get_history():
    session_id = request.args.get('session_id', '').strip()

    with sessions_lock:
        agent = sessions.get(session_id)

    if not agent:
        return jsonify({"success": False, "error": "Invalid session"}), 400

    return jsonify({
        "success": True,
        "history": agent.conversation_history,
        "state":   agent.agent.state
    })

#  TWILIO VOICE WEBHOOK 
def validate_twilio_request(f):
    """
    Decorator: verify that incoming requests are genuinely from Twilio.
    Skips validation when TWILIO_AUTH_TOKEN is not configured (dev mode).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not TWILIO_AUTH_TOKEN:
            # Dev mode — skip validation but warn
            print("[WARNING] TWILIO_AUTH_TOKEN not set — skipping signature validation")
            return f(*args, **kwargs)

        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        
        # Determine the public URL (essential when behind ngrok / proxies)
        # Use X-Forwarded headers if available, else fallback to request.url
        original_url = request.url
        if 'X-Forwarded-Proto' in request.headers and 'X-Forwarded-Host' in request.headers:
            proto = request.headers['X-Forwarded-Proto'].split(',')[0].strip()
            host  = request.headers['X-Forwarded-Host'].split(',')[0].strip()
            # If standard ports are used, append just the path
            original_url = f"{proto}://{host}{request.path}"
            # Re-append query string if present (request.path doesn't include it)
            if request.query_string:
                original_url += f"?{request.query_string.decode()}"

        post_vars = request.form.to_dict()
        signature = request.headers.get('X-Twilio-Signature', '')

        if not validator.validate(original_url, post_vars, signature):
            # Log the mismatch for debugging
            print(f"[SECURITY] Twilio signature validation FAILED")
            # print(f"  > Signature: {signature}")
            print(f"  > URL used for validation: {original_url}")
            
            # UNCOMMENT the line below to temporary bypass validation if you're stuck
            # return f(*args, **kwargs)
            
            return "Forbidden", 403
        
        print(f"[SECURITY] Twilio signature validation PASSED for: {original_url}")
        return f(*args, **kwargs)
    return decorated

@app.route('/twilio/voice', methods=['POST'])
@app.route('/api/twilio/voice', methods=['POST'])
@validate_twilio_request
def twilio_voice():
    """
    Handle incoming Twilio voice calls.

    Key fixes vs original:
      • speechTimeout='auto'  — VAD-based end-of-speech detection (was fixed '1')
      • speechModel='phone_call' + enhanced=True — faster, more accurate STT
      • language='en-IN'      — matches user base in Coimbatore
      • actionOnEmptyResult=True — prevents silent dead air on no-input
      • Removed resp.redirect() after Gather — eliminates extra round trip
      • Session keyed by CallSid — persists across turns of the same call
    """
    call_sid      = request.values.get('CallSid', '')
    speech_result = request.values.get('SpeechResult', '').strip()
    resp          = VoiceResponse()

    try:
        #  New call 
        if call_sid not in sessions:
            print(f"[CALL] New call: {call_sid}")
            with sessions_lock:
                sessions[call_sid] = WebVoiceAgent(call_sid)
            response_text = "Hello! Welcome to Smile Dental. How can I help you today?"

        #  Existing call with speech input 
        elif speech_result:
            print(f"[USER] ({call_sid}): {speech_result}")
            with sessions_lock:
                agent = sessions.get(call_sid)
            if agent:
                result        = agent.process_message(speech_result)
                response_text = result.get('response', "Sorry, I encountered an error.")
            else:
                response_text = "I'm sorry, your session expired. Please call again."
            print(f"[AGENT]: {response_text}")

        #  Redirect / silence / empty input 
        else:
            response_text = "I didn't catch that — could you please repeat?"

        #  Sanitize for TTS 
        tts_text = str(response_text).replace("*", "").replace("\n", ". ").strip()
        if not tts_text:
            tts_text = "I'm sorry, something went wrong. Please try again."

        #  Build TwiML — single Gather, no redirect 
        gather = resp.gather(
            input             = 'speech',
            action            = '/twilio/voice',
            method            = 'POST',
            speechTimeout     = 'auto',       # VAD end-of-speech — no fixed wait
            speechModel       = 'phone_call', # phone-optimised STT model
            enhanced          = True,         # Twilio enhanced STT
            language          = 'en-IN',      # Indian English — better accuracy
            timeout           = 5,            # seconds to wait for user to START
            actionOnEmptyResult = True        # fire webhook even on silence
        )
        ssml_text = f'<speak><prosody rate="100%">{tts_text}</prosody></speak>'
        gather.say(ssml_text, voice='Polly.Joanna', language='en-US')
        # No resp.redirect() here — removes one unnecessary HTTP round trip

        return str(resp)

    except Exception as e:
        print(f"[ERROR] Twilio handler caught exception: {e}")
        traceback.print_exc()
        
        # Log to file for persistence
        try:
            with open("twilio_error_log.txt", "a") as f:
                f.write(f"[{datetime.now().isoformat()}] ERROR: {e}\n")
                f.write(traceback.format_exc())
                f.write("-" * 40 + "\n")
        except:
            pass

        error_resp = VoiceResponse()
        error_resp.say("I'm sorry, a system error occurred. Please check the logs.")
        return str(error_resp)

#  ENTRY POINT 
if __name__ == '__main__':
    print("=" * 60)
    print("SMILE DENTAL - Web Server")
    print("=" * 60)
    print("\n[WEB] Starting server at http://localhost:5000")
    print("[TIP] Set FLASK_SECRET_KEY and TWILIO_AUTH_TOKEN in your environment")
    print("\n" + "=" * 60 + "\n")

    # debug=False in production — debug=True auto-reloads but is unsafe in prod
    app.run(debug=True, host='0.0.0.0', port=5000)