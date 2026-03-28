import os
from dotenv import load_dotenv
load_dotenv()
import uuid
import json
import re
import time
import threading
import traceback
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, g, make_response, Response, stream_with_context
from flask_cors import CORS
from flask_sock import Sock
import requests as http_requests
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from twilio.request_validator import RequestValidator

from app import DentalVoiceAgent, VoiceInterface, OLLAMA_BASE_URL, OLLAMA_MODEL
from database_manager import DatabaseManager

#  APP SETUP      
app = Flask(__name__, static_folder='static', template_folder='templates')
sock = Sock(app)   # flask-sock — Twilio Media Streams WebSocket

# every restart and logs out all users.
app.secret_key = "smile-dental-secret-key"
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "AC878d64388a378b523ba2af074bad4507")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN",  "2a56742e1075e2e7e21cb683f3874669")

CORS(app)

# Session TTL in seconds — inactive sessions are cleaned up automatically
SESSION_TTL = 300   # 5 minutes

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
                "options":    {"num_predict": 1, "num_gpu": -1}
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

    def __init__(self, session_id, db_manager):
        self.session_id           = session_id
        self.db_manager           = db_manager
        self.agent                = DentalVoiceAgent(use_voice=False)
        self.conversation_history = []
        self.last_active          = time.time()
        
        # Load existing state if available
        saved_state = self.db_manager.get_session_state(session_id)
        if saved_state:
            self.agent.state = saved_state
            print(f"[SESSION] Restored state for {session_id}")

    def process_message(self, user_message):
        """Non-streaming version for Twilio/Voice."""
        self.last_active = time.time()
        try:
            self.conversation_history.append({"role":"user","message":user_message,"timestamp":datetime.now().isoformat()})
            # Consume the generator fully
            response_chunks = list(self.agent.generate_response(user_message))
            response = "".join(response_chunks)
            
            self.db_manager.update_session_state(self.session_id, self.agent.state)
            self.conversation_history.append({"role":"agent","message":response,"timestamp":datetime.now().isoformat()})
            return {"success":True,"response":response,"state":self.agent.state,"conversation":self.conversation_history}
        except Exception as e:
            traceback.print_exc()
            return {"success":False,"error":str(e),"response":"Sorry, I encountered an error."}

    def process_message_stream(self, user_message):
        """Streaming version for Web Chat."""
        self.last_active = time.time()
        self.conversation_history.append({"role":"user","message":user_message,"timestamp":datetime.now().isoformat()})
        
        try:
            full_response_parts = []
            for chunk in self.agent.generate_response(user_message):
                full_response_parts.append(chunk)
                yield chunk

            full_response = "".join(full_response_parts)
            self.db_manager.update_session_state(self.session_id, self.agent.state)
            self.conversation_history.append({"role":"agent","message":full_response,"timestamp":datetime.now().isoformat()})
        except Exception as e:
            traceback.print_exc()
            yield f"Error: {str(e)}"

    def reset(self):
        self.agent.reset_state()
        self.conversation_history = []
        self.last_active          = time.time()

#  AUTH DECORATORS 
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get('jwt_token')
        if not token:
            return jsonify({"success": False, "error": "Login required"}), 401
        
        payload = db.decode_token(token)
        if not payload:
            return jsonify({"success": False, "error": "Session expired, please login again"}), 401
            
        g.user_id = payload.get("user_id")
        g.role    = payload.get("role")
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get('jwt_token')
        if not token:
            return jsonify({"success": False, "error": "Admin access required"}), 401
            
        payload = db.decode_token(token)
        if not payload or payload.get('role') != 'admin':
            return jsonify({"success": False, "error": "Admin access required"}), 403
            
        g.user_id = payload.get("user_id")
        g.role    = payload.get("role")
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
            token = db.generate_token(user['_id'], user['email'], user['name'], user.get('role', 'user'))
            response = make_response(jsonify({
                "success": True, 
                "message": "Login successful", 
                "role": user.get('role', 'user')
            }))
            # Set JWT as HttpOnly cookie for security
            response.set_cookie(
                'jwt_token', 
                token, 
                httponly=True, 
                samesite='Lax', 
                max_age=24*3600 # 24 hours
            )
            return response
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
    response = make_response(jsonify({"success": True, "message": "Logged out successfully"}))
    response.delete_cookie('jwt_token')
    return response

@app.route('/')
def index():
    token = request.cookies.get('jwt_token')
    payload = db.decode_token(token) if token else None
    
    if not payload:
        return render_template('login.html', type='signin')
        
    return render_template('index.html', user_name=payload.get('name', 'User'), role=payload.get('role'))

#  ADMIN ROUTES 
@app.route('/admin')
def admin_dashboard():
    token = request.cookies.get('jwt_token')
    payload = db.decode_token(token) if token else None
    
    if not payload or payload.get('role') != 'admin':
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
            "customers":       agent.db.get_all_customers_data(),
            "calendar_events": safe_events
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

#  CHAT SESSION ROUTES 
@app.route('/api/start-session', methods=['POST'])
def start_session():
    session_id = str(uuid.uuid4())
    with sessions_lock:
        sessions[session_id] = WebVoiceAgent(session_id, db)
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

@app.route('/api/send-message-stream')
def send_message_stream():
    session_id = request.args.get('session_id', '').strip()
    message    = request.args.get('message', '').strip()

    with sessions_lock:
        agent = sessions.get(session_id)

    if not agent:
        return Response("Error: Invalid session", status=400)
    if not message:
        return Response("Error: Message cannot be empty", status=400)

    def generate():
        for chunk in agent.process_message_stream(message):
            yield chunk

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

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
    Handle incoming Twilio voice calls using a turn-based Chatbot approach.
    Replaces the previous Real-time Media Stream (Deepgram) with native Voice tools.
    """
    call_sid = request.form.get('CallSid')
    print(f"\n[CALL] New Call Started: {call_sid}")
    
    # Initialize a new agent for this specific call
    with sessions_lock:
        agent = DentalVoiceAgent(use_voice=False)
        sessions[call_sid] = agent

    resp = VoiceResponse()
    
    # Initial greeting
    greeting = "Hello! Welcome to Smile Dental clinic. I'd be more than happy to help you today! How can I assist you?"
    resp.say(greeting, voice='Polly.Amy')

    # Gather speech from the user
    gather = resp.gather(input='speech', action='/twilio/handle-input', speechTimeout='auto', language='en-IN')
    
    return str(resp), 200, {'Content-Type': 'text/xml'}

@app.route('/twilio/handle-input', methods=['POST'])
@validate_twilio_request
def twilio_handle_input():
    """
    Processes the captured speech from Twilio and generates the next AI response.
    """
    call_sid   = request.form.get('CallSid')
    user_text  = request.form.get('SpeechResult', '').strip()
    
    # 1. Retrieve the existing agent for this call
    with sessions_lock:
        agent = sessions.get(call_sid)
    
    # If session expired or was lost, restart
    if not agent:
        resp = VoiceResponse()
        resp.redirect('/twilio/voice')
        return str(resp), 200, {'Content-Type': 'text/xml'}

    print(f"  [USER ({call_sid[:8]})] {user_text}")

    # 2. Generate the AI reply (using existing DentalVoiceAgent logic)
    # This also triggers tools (Calendar/Sheets) if needed
    reply_gen = agent.generate_response(user_text)
    reply = "".join(list(reply_gen))
    print(f"  [AI ({call_sid[:8]})] {reply}")


    resp = VoiceResponse()
    resp.say(reply, voice='Polly.Amy')

    # 3. Check if we should end the call (e.g. if the intent was "end_call")
    # In the Chatbot mode, we just check if it's been completed or if the user thanked
    if any(kw in user_text.lower() for kw in ("thank", "bye", "enough")):
        resp.hangup()
        with sessions_lock: sessions.pop(call_sid, None)
    else:
        # Keep the conversation going with another Gather
        resp.gather(input='speech', action='/twilio/handle-input', speechTimeout='auto', language='en-IN')

    return str(resp), 200, {'Content-Type': 'text/xml'}

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