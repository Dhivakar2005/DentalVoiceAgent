from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from functools import wraps
import os
import uuid
import json
from datetime import datetime
from app import DentalVoiceAgent, VoiceInterface
from database_manager import DatabaseManager
import threading
from twilio.twiml.voice_response import VoiceResponse

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.urandom(24)
CORS(app)

# Initialize Database
db = DatabaseManager(app)

# Store active sessions
sessions = {}

class WebVoiceAgent:
    """Wrapper for DentalVoiceAgent to work with web interface"""
    def __init__(self, session_id):
        self.session_id = session_id
        # Initialize agent in text mode for web
        self.agent = DentalVoiceAgent(use_voice=False)
        self.conversation_history = []
        
    def process_message(self, user_message):
        """Process user message and return agent response"""
        try:
            # Add user message to history
            self.conversation_history.append({
                "role": "user",
                "message": user_message,
                "timestamp": datetime.now().isoformat()
            })
            
            # Get response from agent
            response = self.agent.generate_response(user_message)
            
            # Add agent response to history
            self.conversation_history.append({
                "role": "agent",
                "message": response,
                "timestamp": datetime.now().isoformat()
            })
            
            return {
                "success": True,
                "response": response,
                "state": self.agent.state,
                "conversation": self.conversation_history
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": "Sorry, I encountered an error. Please try again."
            }
    
    def reset(self):
        """Reset conversation state"""
        self.agent.reset_state()
        self.conversation_history = []

# AUTHENTICATION DECORATORS
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"success": False, "error": "Login required"}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            return jsonify({"success": False, "error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated_function

# AUTHENTICATION ROUTES
@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'POST':
        data = request.json
        email = data.get('email')
        password = data.get('password')
        
        user = db.authenticate_user(email, password)
        if user:
            session['user_id'] = str(user['_id'])
            session['email'] = user['email']
            session['name'] = user['name']
            session['role'] = user.get('role', 'user')
            return jsonify({
                "success": True, 
                "message": "Login successful",
                "role": session['role']
            })
        return jsonify({"success": False, "error": "Invalid email or password"}), 401
    
    return render_template('login.html', type='signin')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        data = request.json
        email = data.get('email')
        password = data.get('password')
        name = data.get('name')
        
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
    """Serve the main website"""
    if 'user_id' not in session:
        return render_template('login.html', type='signin')
    return render_template('index.html', user_name=session.get('name'))

# ADMIN DASHBOARD ROUTES
@app.route('/admin')
def admin_dashboard():
    """Serve the admin dashboard"""
    if 'role' not in session or session['role'] != 'admin':
        return render_template('login.html', type='signin', error="Admin access required")
    return render_template('admin.html')

@app.route('/api/admin/data')
@admin_required
def get_admin_data():
    """Fetch data for admin dashboard from Google Sheets and Calendar"""
    try:
        # Use a temporary agent to get sheet/calendar managers
        agent = DentalVoiceAgent(use_voice=False)
        
        # 1. Get appointments from Google Sheets
        appointments = agent.sheets.get_all_customers() # This now gets from Master or log depending on logic, but let's get all appointment rows
        
        # 2. Get events from Google Calendar
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = agent.calendar.service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=50, singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        return jsonify({
            "success": True,
            "appointments": appointments,
            "calendar_events": events
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/start-session', methods=['POST'])
def start_session():
    """Initialize a new conversation session"""
    session_id = str(uuid.uuid4())
    sessions[session_id] = WebVoiceAgent(session_id)
    
    return jsonify({
        "success": True,
        "session_id": session_id,
        "message": "Hello! Welcome to Smile Dental. How can I help you today?"
    })

@app.route('/api/send-message', methods=['POST'])
def send_message():
    """Process user message"""
    data = request.json
    session_id = data.get('session_id')
    message = data.get('message', '').strip()
    
    if not session_id or session_id not in sessions:
        return jsonify({
            "success": False,
            "error": "Invalid session. Please start a new session."
        }), 400
    
    if not message:
        return jsonify({
            "success": False,
            "error": "Message cannot be empty"
        }), 400
    
    # Process message
    agent = sessions[session_id]
    result = agent.process_message(message)
    
    return jsonify(result)

@app.route('/api/reset-session', methods=['POST'])
def reset_session():
    """Reset conversation state"""
    data = request.json
    session_id = data.get('session_id')
    
    if not session_id or session_id not in sessions:
        return jsonify({
            "success": False,
            "error": "Invalid session"
        }), 400
    
    sessions[session_id].reset()
    
    return jsonify({
        "success": True,
        "message": "Session reset successfully"
    })

@app.route('/api/end-session', methods=['POST'])
def end_session():
    """End and cleanup session"""
    data = request.json
    session_id = data.get('session_id')
    
    if session_id and session_id in sessions:
        del sessions[session_id]
    
    return jsonify({
        "success": True,
        "message": "Session ended"
    })

@app.route('/api/get-history', methods=['GET'])
def get_history():
    """Get conversation history"""
    session_id = request.args.get('session_id')
    
    if not session_id or session_id not in sessions:
        return jsonify({
            "success": False,
            "error": "Invalid session"
        }), 400
    
    agent = sessions[session_id]
    
    return jsonify({
        "success": True,
        "history": agent.conversation_history,
        "state": agent.agent.state
    })

# TWILIO VOICE ROUTE
@app.route('/twilio/voice', methods=['POST'])
@app.route('/api/twilio/voice', methods=['POST'])
def twilio_voice():
    """Handle incoming Twilio voice calls"""
    # Get Twilio request data
    call_sid = request.values.get('CallSid', None)
    speech_result = request.values.get('SpeechResult', None)
    
    resp = VoiceResponse()
    
    try:
        if not call_sid:
            resp.say("System error. No call ID found.")
            return str(resp)
            
        # reuse or create session based on CallSid
        if call_sid not in sessions:
            print(f"📞 New Call: {call_sid}")
            sessions[call_sid] = WebVoiceAgent(call_sid)
            # Initial greeting for new call
            greeting = "Hello! Welcome to Smile Dental. How can I help you today?"
            
            # Use Gather to capture speech
            gather = resp.gather(input='speech', action='/twilio/voice', speechTimeout='auto')
            gather.say(greeting)
            
            # If no input, redirect back to this route to loop or just end
            resp.redirect('/twilio/voice')
            return str(resp)

        # Continue conversation
        if speech_result:
            print(f"🗣️ User ({call_sid}): {speech_result}")
            agent = sessions[call_sid]
            result = agent.process_message(speech_result)
            response_text = result['response']
            print(f"🤖 Agent: {response_text}")
            
            # Respond and listen again
            gather = resp.gather(input='speech', action='/twilio/voice', speechTimeout='auto')
            gather.say(response_text)
            resp.redirect('/twilio/voice')
        else:
            # If we got here via redirect without speech (timeout/silence)
            # We can prompt again or just wait
            gather = resp.gather(input='speech', action='/twilio/voice', speechTimeout='auto')
            gather.say("I am listening.")
            resp.redirect('/twilio/voice')
            
        return str(resp)
    except Exception as e:
        print(f"❌ Twilio Error: {e}")
        import traceback
        with open("error_log.txt", "w") as f:
            f.write(f"Error: {e}\n")
            traceback.print_exc(file=f)
        resp.say("I'm sorry, an error occurred in the system.")
        return str(resp)

if __name__ == '__main__':
    print("=" * 60)
    print("SMILE DENTAL - Web Server")
    print("=" * 60)
    print("\n🌐 Starting server at http://localhost:5000")
    print("📱 Open your browser and navigate to the URL above")
    print("\n" + "=" * 60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
