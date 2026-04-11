import os
from dotenv import load_dotenv
load_dotenv()
import logger_setup
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
from voice_agent.dental_functions import FUNCTION_MAP as DENTAL_FUNCTION_MAP, reset_patient_session
import asyncio
import base64
import structlog
import websockets

#  WhatsApp Scheduling Automation ─
from scheduling_automation.automation_engine import AutomationEngine
from scheduling_automation.sheet_watcher     import SheetWatcher
from scheduling_automation.scheduler         import build_scheduler
from scheduling_automation.webhook_server    import register_automation_routes

#  Multilingual Support 
from language_service import get_deepgram_language_config, detect_language, get_language_instruction, normalize_input

#  APP SETUP      
app = Flask(__name__, static_folder='static', template_folder='templates')
sock = Sock(app)   # flask-sock — Twilio Media Streams WebSocket

# every restart and logs out all users.
# Moved to env-based initialization
CORS(app)

# Deepgram/Voice Service init
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY").strip('"\' ') if os.getenv("DEEPGRAM_API_KEY") else None
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN").strip('"\' ') if os.getenv("TWILIO_AUTH_TOKEN") else None
app.secret_key = os.getenv("FLASK_SECRET_KEY", "smile-dental-secret-key")
logger = structlog.get_logger("server.voice")


#  DEEPGRAM AGENT VOICE CONFIG

def _build_today_context() -> str:
    """
    Build a rich date-context string that is injected into the LLM system prompt
    on every call so the agent correctly resolves natural-language date references
    like 'today', 'tomorrow', 'this Friday', 'next Monday', etc.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    tz   = ZoneInfo("Asia/Kolkata")
    now  = datetime.now(tz)
    today      = now.date()
    tomorrow   = today + timedelta(days=1)
    day_after  = today + timedelta(days=2)
    max_date   = today + timedelta(days=6)

    def fmt(d):
        return f"{DAY_NAMES[d.weekday()]}, {d.strftime('%B %d, %Y')} [{d.isoformat()}]"

    lines = [
        f"- TODAY           : {fmt(today)}",
        f"- TOMORROW        : {fmt(tomorrow)}",
        f"- DAY AFTER TMR   : {fmt(day_after)}",
        f"- MAX BOOKING DATE: {fmt(max_date)}  (today + 6 days)",
        f"- CURRENT TIME    : {now.strftime('%I:%M %p')} IST",
        f"- CURRENT YEAR    : {today.year}",
    ]

    # Pre-compute all 7 days so the LLM can resolve "this Friday" etc.
    lines.append("- NEXT 7 DAYS     :")
    for i in range(7):
        d = today + timedelta(days=i)
        suffix = " ← TODAY" if i == 0 else (" ← TOMORROW" if i == 1 else "")
        lines.append(f"    {fmt(d)}{suffix}")

    return "\n".join(lines)


def _load_voice_config() -> dict:
    """
    Load dental_config.json and dynamically inject:
      1. Today's date context into the LLM system prompt.
      2. Multilingual STT config for Deepgram (Tamil + Hindi + English).
      3. A language detection instruction into the agent prompt.
    Called fresh on every incoming Twilio call.
    """
    config_path = os.path.join(os.path.dirname(__file__), "voice_agent", "dental_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 1. Inject today's date context
    today_context = _build_today_context()
    prompt = config["agent"]["think"]["prompt"]
    config["agent"]["think"]["prompt"] = prompt.replace("{TODAY_CONTEXT}", today_context)

    # 2. Enable multilingual STT (Tamil, Hindi, English auto-detection per utterance)
    config["agent"]["listen"]["provider"] = get_deepgram_language_config()

    # 3. Inject language rule at the START of the agent prompt
    lang_rule = (
        "LANGUAGE RULE: Detect the language the caller is speaking "
        "(English, Tamil, or Hindi). Reply in the SAME language throughout the call. "
        "If they switch languages, you switch too. Never mix languages in a single response.\n\n"
    )
    config["agent"]["think"]["prompt"] = lang_rule + config["agent"]["think"]["prompt"]

    logger.info("[CONFIG] Multilingual voice config applied (en/ta/hi). Date context injected.")
    return config

def _sts_connect():
    """Connect to Deepgram Agent API using the token subprotocol (pharmacy-pattern)."""
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    return websockets.connect(
        "wss://agent.deepgram.com/v1/agent/converse",
        subprotocols=["token", DEEPGRAM_API_KEY]
    )

# ─
#  ASYNC HELPERS — Pharmacy-pattern three-task pipeline
# ─
async def _sts_sender(sts_ws, audio_queue: asyncio.Queue):
    """Forward inbound Twilio audio to Deepgram Agent."""
    while True:
        chunk = await audio_queue.get()
        await sts_ws.send(chunk)


def _build_spoken_response(func_name: str, result: dict) -> str | None:
    """
    Build a short, natural spoken sentence from a tool result.
    Injected immediately after FunctionCallResponse so the agent speaks
    without waiting for a new LLM inference cycle (zero silence gap).
    Returns None only if no injection is needed.
    """
    # Every tool returns either {"result": "..."} or {"error": "..."}
    # Use whichever is present.
    spoken = result.get("result") or result.get("error")

    #  verify_patient: override with a cleaner confirmation prompt ─
    if func_name == "verify_patient":
        if result.get("found"):
            name = result.get("name", "")
            return f"I found your record. You are {name}. Is that correct?"
        else:
            return "I was not able to find a record with that number. Would you like to register as a new patient instead?"

    #  All other tools: use the pre-built natural sentence directly 
    # lookup_appointments, book_appointment, reschedule_appointment,
    # cancel_appointment all already return a full spoken sentence.
    if spoken:
        return spoken

    return None   # let Deepgram handle it naturally if nothing to say



async def _handle_function_calls(decoded: dict, sts_ws):
    """Execute a FunctionCallRequest received from Deepgram Agent.
    After sending FunctionCallResponse, immediately inject a spoken reply
    so there is zero silence between tool completion and agent speech.
    """
    try:
        for func_call in decoded.get("functions", []):
            func_name = func_call["name"]
            func_id   = func_call["id"]
            try:
                arguments = json.loads(func_call["arguments"])
            except Exception:
                arguments = func_call.get("arguments", {})

            logger.info("tool_called", tool=func_name, args=arguments)

            if func_name in DENTAL_FUNCTION_MAP:
                # Run potentially blocking I/O (Calendar/Sheets) in a thread
                result = await asyncio.to_thread(DENTAL_FUNCTION_MAP[func_name], **arguments)
            else:
                result = {"error": f"Unknown function: {func_name}"}

            # 1. Send the function result back to Deepgram Agent
            response = {
                "type":    "FunctionCallResponse",
                "id":      func_id,
                "name":    func_name,
                "content": json.dumps(result)
            }
            await sts_ws.send(json.dumps(response))
            logger.info("tool_result", tool=func_name, result=result)

            # 2. Immediately inject a spoken response — eliminates silence gap
            spoken = _build_spoken_response(func_name, result)
            if spoken:
                inject = {
                    "type":    "InjectAgentMessage",
                    "message": spoken
                }
                await sts_ws.send(json.dumps(inject))
                logger.info("inject", tool=func_name, spoken=spoken[:80] + "..." if len(spoken) > 80 else spoken)

    except Exception as e:
        logger.error("tool_error", error=str(e))




async def _sts_receiver(sts_ws, twilio_ws, streamsid_queue: asyncio.Queue):
    """Receive messages from Deepgram Agent and forward audio/events to Twilio."""
    streamsid = await streamsid_queue.get()

    async for message in sts_ws:
        if isinstance(message, str):
            logger.debug("dg_raw_message", msg=message)
            try:
                decoded = json.loads(message)
            except Exception:
                continue

            msg_type = decoded.get("type", "")

            if msg_type == "Error" or "error" in decoded:
                logger.error("deepgram_agent_error", payload=decoded)

            if msg_type == "UserStartedSpeaking":
                # Barge-in: clear Twilio's audio buffer so agent stops talking
                clear_msg = {"event": "clear", "streamSid": streamsid}
                await asyncio.to_thread(twilio_ws.send, json.dumps(clear_msg))

            elif msg_type == "FunctionCallRequest":
                await _handle_function_calls(decoded, sts_ws)

        else:
            # Binary audio from Deepgram TTS — forward to Twilio as fast as possible
            if streamsid:
                media_msg = {
                    "event":     "media",
                    "streamSid": streamsid,
                    "media":     {"payload": base64.b64encode(message).decode("ascii")}
                }
                await asyncio.to_thread(twilio_ws.send, json.dumps(media_msg))


async def _twilio_receiver(twilio_ws, audio_queue: asyncio.Queue, streamsid_queue: asyncio.Queue):
    """Receive Twilio Media Stream packets and feed audio to Deepgram Agent."""
    # 5 mulaw frames * 160 bytes = 800 bytes (~100 ms) — small buffer = low latency
    BUFFER_SIZE = 5 * 160
    inbuffer    = bytearray(b"")

    while True:
        try:
            message = await asyncio.to_thread(twilio_ws.receive)
            if message is None:
                break

            data  = json.loads(message)
            event = data.get("event", "")

            if event == "start":
                streamsid = data["start"]["streamSid"]
                logger.info("twilio_stream_started", streamsid=streamsid)
                streamsid_queue.put_nowait(streamsid)

            elif event == "connected":
                pass  # ignored

            elif event == "media":
                media = data["media"]
                if media.get("track") == "inbound":
                    chunk = base64.b64decode(media["payload"])
                    inbuffer.extend(chunk)

            elif event == "stop":
                logger.info("twilio_stream_stopped")
                break

            # Flush buffered audio in BUFFER_SIZE chunks
            while len(inbuffer) >= BUFFER_SIZE:
                audio_queue.put_nowait(bytes(inbuffer[:BUFFER_SIZE]))
                inbuffer = inbuffer[BUFFER_SIZE:]

        except Exception:
            break


async def wrap_task(task_coro, name):
    try:
        await task_coro
    except Exception as e:
        logger.error(f"task_failed_{name}", error=str(e), traceback=traceback.format_exc())

async def _media_stream_async(sync_ws):
    audio_queue     = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    try:
        async with _sts_connect() as sts_ws:
            config = _load_voice_config()
            await sts_ws.send(json.dumps(config))
            logger.info("deepgram_agent_session_started")

            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(wrap_task(_sts_sender(sts_ws, audio_queue), "sts_sender")),
                    asyncio.create_task(wrap_task(_sts_receiver(sts_ws, sync_ws, streamsid_queue), "sts_receiver")),
                    asyncio.create_task(wrap_task(_twilio_receiver(sync_ws, audio_queue, streamsid_queue), "twilio_receiver")),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                logger.warning("media_stream_task_completed", task_name=t.get_name() if hasattr(t, 'get_name') else str(t))
            for p in pending:
                p.cancel()
    except Exception as e:
        logger.error("media_stream_async_error", error=str(e), traceback=traceback.format_exc())

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

# OLLAMA MODEL AUTO-DETECT + WARM-UP
import app as _app_module  # so we can patch the module-level constant at runtime

def _get_available_ollama_models() -> list:
    """Return list of model names currently pulled in Ollama."""
    try:
        r = http_requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []

def _resolve_model(preferred: str, available: list) -> str:
    """
    Return 'preferred' if downloaded, otherwise fall back to the best
    available model so the server never crashes at startup.
    """
    # Exact match
    if preferred in available:
        return preferred
    # Prefix match (e.g. 'aya-expanse:8b' matches 'aya-expanse:8b-q4_0')
    for m in available:
        if m.startswith(preferred.split(":")[0]):
            return m
    # Fallback priority: phi3 > qwen > first available
    for fallback in ["phi3:mini", "qwen3.5:0.8b"]:
        if any(m.startswith(fallback.split(":")[0]) for m in available):
            for m in available:
                if m.startswith(fallback.split(":")[0]):
                    return m
    return available[0] if available else preferred

def _pull_model_background(model: str):
    """Pull a model in the background without blocking the server."""
    try:
        logger.info("[SERVER] Starting background pull", model=model)
        http_requests.post(
            f"{OLLAMA_BASE_URL}/api/pull",
            json={"name": model},
            timeout=1800  # 30 min max
        )
        logger.info("[SERVER] Background pull complete", model=model)
        # Hot-swap the model once downloaded
        _app_module.OLLAMA_MODEL = model
        logger.info("[SERVER] Model hot-swapped", model=model)
    except Exception as e:
        logger.warning("background_pull_failed", model=model, error=str(e))

def warmup_ollama():
    """Detect available models, fall back gracefully, then warm up."""
    available = _get_available_ollama_models()
    resolved  = _resolve_model(OLLAMA_MODEL, available)

    if resolved != OLLAMA_MODEL:
        logger.warning(
            "[SERVER] Preferred model not found, using fallback",
            preferred=OLLAMA_MODEL, fallback=resolved
        )
        # Patch the runtime constant so all future calls use the fallback
        _app_module.OLLAMA_MODEL = resolved
        # Start pulling the preferred model quietly in the background
        threading.Thread(
            target=_pull_model_background, args=(OLLAMA_MODEL,), daemon=True
        ).start()
    else:
        logger.info("[SERVER] Preferred model available", model=resolved)

    # Warm up whichever model we resolved
    try:
        http_requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model":      _app_module.OLLAMA_MODEL,
                "messages":   [{"role": "user", "content": "hi"}],
                "stream":     False,
                "keep_alive": -1,
                "options":    {"num_predict": 1, "num_gpu": -1}
            },
            timeout=120
        )
        logger.info("[SERVER] Ollama ready", model=_app_module.OLLAMA_MODEL)
    except Exception as e:
        logger.warning("ollama_warm_up_failed", error=str(e))

threading.Thread(target=warmup_ollama, daemon=True).start()

#  SHARED SINGLETONS — Initialized once at startup
from google_sheets_manager import GoogleSheetsManager
from vector_db_manager     import VectorDBManager
from app import GoogleCalendarManager

# Global manager instances
_shared_calendar = GoogleCalendarManager()
_shared_sheets   = GoogleSheetsManager()
_shared_vdb      = VectorDBManager()

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
            logger.info("removed_stale_sessions", count=len(stale))

threading.Thread(target=cleanup_sessions, daemon=True).start()

#  WEB VOICE AGENT WRAPPER 
class WebVoiceAgent:
    """Wraps DentalVoiceAgent for the web / Twilio interface."""

    def __init__(self, session_id, db_manager, calendar=None, sheets=None, vdb=None):
        self.session_id           = session_id
        self.db_manager           = db_manager
        # Pass shared managers to DentalVoiceAgent
        self.agent                = DentalVoiceAgent(
            use_voice=False, 
            calendar=calendar or _shared_calendar, 
            sheets=sheets or _shared_sheets, 
            vdb=vdb or _shared_vdb
        )
        self.conversation_history = []
        self.last_active          = time.time()
        self.detected_language    = "en"   # Default: English; updated on each message
        
        # Load existing state if available
        saved_state = self.db_manager.get_session_state(session_id)
        if saved_state:
            self.agent.state = saved_state
            self.detected_language = saved_state.get("language", "en")
            logger.info("restored_session_state", session_id=session_id)

    def process_message(self, user_message):
        """Non-streaming version for Twilio/Voice."""
        self.last_active = time.time()
        try:
            # 1. Detect language
            self.detected_language = detect_language(user_message)
            # 2. Normalize Tamil/Hindi → English for aya-expanse:8b, keep English tokens for it
            normalized = normalize_input(user_message, self.detected_language)
            # 3. Append reply-language instruction
            lang_hint = get_language_instruction(self.detected_language)
            enriched_message = f"{normalized} [{lang_hint}]" if self.detected_language != "en" else normalized

            self.conversation_history.append({"role":"user","message":user_message,"timestamp":datetime.now().isoformat()})
            response_chunks = list(self.agent.generate_response(enriched_message))
            response = "".join(response_chunks)

            self.agent.state["language"] = self.detected_language
            self.db_manager.update_session_state(self.session_id, self.agent.state)
            self.conversation_history.append({"role":"agent","message":response,"timestamp":datetime.now().isoformat()})
            return {"success":True,"response":response,"state":self.agent.state,"conversation":self.conversation_history,"language":self.detected_language}
        except Exception as e:
            traceback.print_exc()
            return {"success":False,"error":str(e),"response":"Sorry, I encountered an error."}

    def process_message_stream(self, user_message):
        """Streaming version for Web Chat. Includes language detection + normalization."""
        self.last_active = time.time()
        # 1. Detect language
        self.detected_language = detect_language(user_message)
        # 2. Normalize Tamil/Hindi → English for aya-expanse:8b
        normalized = normalize_input(user_message, self.detected_language)
        # 3. Append reply-language instruction
        lang_hint = get_language_instruction(self.detected_language)
        enriched_message = f"{normalized} [{lang_hint}]" if self.detected_language != "en" else normalized
        self.conversation_history.append({"role":"user","message":user_message,"timestamp":datetime.now().isoformat()})

        try:
            full_response_parts = []
            for chunk in self.agent.generate_response(enriched_message):
                full_response_parts.append(chunk)
                yield chunk

            full_response = "".join(full_response_parts)
            self.agent.state["language"] = self.detected_language
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
        sessions[session_id] = WebVoiceAgent(
            session_id, 
            db,
            calendar=_shared_calendar,
            sheets=_shared_sheets,
            vdb=_shared_vdb
        )
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
# Set TWILIO_SKIP_VALIDATION=true in .env to bypass signature checking during local/ngrok dev.
_SKIP_TWILIO_VALIDATION = os.getenv("TWILIO_SKIP_VALIDATION", "false").strip('"\' ').lower() == "true"

def validate_twilio_request(f):
    """
    Decorator: verify that incoming requests are genuinely from Twilio.
    Automatically skips when:
      - TWILIO_AUTH_TOKEN is not set
      - TWILIO_SKIP_VALIDATION=true  (set this flag when testing with ngrok in dev)
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        #  Dev bypass 
        if _SKIP_TWILIO_VALIDATION:
            logger.info("twilio_signature_validation_skipped")
            return f(*args, **kwargs)

        if not TWILIO_AUTH_TOKEN:
            logger.warning("twilio_auth_token_not_set_skipping_signature_validation")
            return f(*args, **kwargs)

        #  URL reconstruction (ngrok / proxy safe) 
        # ngrok sets X-Forwarded-Proto=https and uses the ngrok domain as
        # the Host header; X-Forwarded-Host is also present on most versions.
        original_url = request.url  # fallback (will be http://localhost:…)

        proto = (
            request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
            or request.scheme
        )
        host = (
            request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()
            or request.headers.get("Host", "").split(",")[0].strip()
        )

        if proto and host:
            original_url = f"{proto}://{host}{request.path}"
            if request.query_string:
                original_url += f"?{request.query_string.decode()}"

        post_vars = request.form.to_dict()
        signature = request.headers.get("X-Twilio-Signature", "")

        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        if not validator.validate(original_url, post_vars, signature):
            logger.error("twilio_signature_validation_failed", url=original_url, signature=signature[:20])
            return "Forbidden", 403

        logger.info("twilio_signature_validation_passed", url=original_url)
        return f(*args, **kwargs)
    return decorated

@app.route('/twilio/voice', methods=['POST'])
@app.route('/api/twilio/voice', methods=['POST'])
@validate_twilio_request
def twilio_voice():
    """
    Twilio voice webhook — returns TwiML that streams audio to our WebSocket.
    Greeting is handled by Deepgram Agent config (no resp.say here to avoid
    the double-greeting / race condition that caused calls to drop).
    """
    call_sid = request.form.get('CallSid', 'unknown')
    logger.info("incoming_call", call_sid=call_sid)

    # Determine the public host (ngrok / proxy friendly)
    host = (
        request.headers.get("X-Forwarded-Host")
        or request.headers.get("X-Forwarding-Host")
        or request.url.hostname
    )
    # Strip port if already included in the host header
    if host and ':' in host:
        host = host.split(':')[0]

    # The WebSocket URL MUST be wss:// in production (ngrok handles TLS offload)
    stream_url = f"wss://{host}/media-stream"
    logger.info("streaming_to", stream_url=stream_url)

    resp    = VoiceResponse()
    connect = Connect()
    connect.stream(url=stream_url)
    resp.append(connect)

    return str(resp), 200, {'Content-Type': 'text/xml'}

#  WEBSOCKET: Real-time Twilio Media Stream (Pharmacy-pattern)
@sock.route('/media-stream')
def media_stream(ws):
    """
    WebSocket handler for Twilio Media Streams.
    Uses the exact same three-task async architecture as the proven
    pharmacy bot — connects to Deepgram Agent API via raw websockets.
    """
    # Reset per-call identity session so each caller starts fresh
    reset_patient_session()
    logger.info("twilio_websocket_connected")
    try:
        asyncio.run(_media_stream_async(ws))
    except Exception as e:
        logger.error("stream_error", error=str(e))
    finally:
        logger.info("twilio_websocket_closed")

# NOTE: /twilio/handle-input (old Gather-based approach) has been removed.
# The Deepgram Agent WebSocket pipeline handles the full conversation now.

#  ENTRY POINT 
if __name__ == '__main__':
    logger.info("[SERVER] Starting server")
    try:
        wa_engine = AutomationEngine()
        wa_watcher = SheetWatcher(
            on_new=wa_engine.on_new_appointment,
            on_modified=wa_engine.on_appointment_modified,
            on_deleted=wa_engine.on_appointment_cancelled
        )
        register_automation_routes(app, wa_engine)
        wa_scheduler = build_scheduler(wa_engine, wa_watcher)
        wa_scheduler.start()
        logger.info("[SERVER] Automation ready")
        wa_watcher.check_for_changes()
    except Exception:
        logger.error("automation_error")

    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
