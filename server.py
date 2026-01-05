from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import os
import uuid
import json
from datetime import datetime
from app import DentalVoiceAgent, VoiceInterface
import threading

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.urandom(24)
CORS(app)

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

@app.route('/')
def index():
    """Serve the main website"""
    return render_template('index.html')

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

if __name__ == '__main__':
    print("=" * 60)
    print("SMILE DENTAL - Web Server")
    print("=" * 60)
    print("\n🌐 Starting server at http://localhost:5000")
    print("📱 Open your browser and navigate to the URL above")
    print("\n" + "=" * 60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
