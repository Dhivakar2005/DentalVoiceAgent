// ===========================
// VOICE ASSISTANT APPLICATION
// ===========================

class VoiceAssistant {
    constructor(suffix = '') {
        this.sessionId = null;
        this.isActive = false;
        this.isListening = false;
        this.recognition = null;
        this.suffix = suffix; // '' for main page, 'Modal' for modal

        // DOM Elements - append suffix to IDs for modal support
        this.startBtn = document.getElementById('startBtn' + suffix);
        this.resetBtn = document.getElementById('resetBtn' + suffix);
        this.endBtn = document.getElementById('endBtn' + suffix);
        this.messageInput = document.getElementById('messageInput' + suffix);
        this.voiceBtn = document.getElementById('voiceBtn' + suffix);
        this.sendBtn = document.getElementById('sendBtn' + suffix);
        this.conversationContainer = document.getElementById('conversationContainer' + suffix);
        this.statusText = document.getElementById('statusText' + suffix);
        this.statusDot = suffix === 'Modal'
            ? document.getElementById('statusDot' + suffix)
            : document.querySelector('.status-dot');

        this.initializeEventListeners();
        this.initializeSpeechRecognition();
    }

    initializeEventListeners() {
        this.startBtn.addEventListener('click', () => this.startSession());
        this.resetBtn.addEventListener('click', () => this.resetSession());
        this.endBtn.addEventListener('click', () => this.endSession());
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        this.voiceBtn.addEventListener('click', () => this.toggleVoiceInput());

        // Enter key to send message
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    initializeSpeechRecognition() {
        // Check if browser supports Web Speech API
        if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            this.recognition = new SpeechRecognition();
            this.recognition.continuous = false;
            this.recognition.interimResults = false;
            this.recognition.lang = 'en-US';

            this.recognition.onresult = (event) => {
                const transcript = event.results[0][0].transcript;
                this.messageInput.value = transcript;
                this.sendMessage();
            };

            this.recognition.onerror = (event) => {
                console.error('Speech recognition error:', event.error);
                this.updateStatus('Error with voice input. Please try again.', 'error');
                this.stopListening();
            };

            this.recognition.onend = () => {
                this.stopListening();
            };
        } else {
            console.warn('Speech recognition not supported in this browser');
            this.voiceBtn.style.display = 'none';
        }
    }

    async startSession() {
        try {
            this.updateStatus('Starting session...', 'loading');

            const response = await fetch('/api/start-session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();

            if (data.success) {
                this.sessionId = data.session_id;
                this.isActive = true;

                // Update UI
                this.startBtn.style.display = 'none';
                this.resetBtn.style.display = 'block';
                this.endBtn.style.display = 'block';
                this.messageInput.disabled = false;
                this.voiceBtn.disabled = false;
                this.sendBtn.disabled = false;

                // Clear welcome message and add agent greeting
                this.conversationContainer.innerHTML = '';
                this.addMessage('agent', data.message);

                this.updateStatus('Ready - Type or speak your message', 'active');
                this.messageInput.focus();
            } else {
                this.updateStatus('Failed to start session', 'error');
            }
        } catch (error) {
            console.error('Error starting session:', error);
            this.updateStatus('Connection error. Please try again.', 'error');
        }
    }

    async sendMessage() {
        const message = this.messageInput.value.trim();

        if (!message || !this.isActive) return;

        // Add user message to UI
        this.addMessage('user', message);
        this.messageInput.value = '';

        // Update status
        this.updateStatus('Processing...', 'loading');

        try {
            const response = await fetch('/api/send-message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    message: message
                })
            });

            const data = await response.json();

            if (data.success) {
                // Add agent response to UI
                this.addMessage('agent', data.response);
                this.updateStatus('Ready - Type or speak your message', 'active');

                // Speak the response if supported
                this.speakText(data.response);
            } else {
                this.addMessage('agent', data.response || 'Sorry, something went wrong.');
                this.updateStatus('Error occurred', 'error');
            }
        } catch (error) {
            console.error('Error sending message:', error);
            this.addMessage('agent', 'Connection error. Please try again.');
            this.updateStatus('Connection error', 'error');
        }
    }

    async resetSession() {
        if (!this.sessionId) return;

        try {
            await fetch('/api/reset-session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_id: this.sessionId
                })
            });

            // Clear conversation
            this.conversationContainer.innerHTML = '';
            this.addMessage('agent', 'Session reset. How can I help you?');
            this.updateStatus('Ready - Type or speak your message', 'active');
            this.messageInput.value = '';
        } catch (error) {
            console.error('Error resetting session:', error);
        }
    }

    async endSession() {
        if (!this.sessionId) return;

        try {
            await fetch('/api/end-session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_id: this.sessionId
                })
            });
        } catch (error) {
            console.error('Error ending session:', error);
        }

        // Reset UI
        this.sessionId = null;
        this.isActive = false;
        this.startBtn.style.display = 'block';
        this.resetBtn.style.display = 'none';
        this.endBtn.style.display = 'none';
        this.messageInput.disabled = true;
        this.voiceBtn.disabled = true;
        this.sendBtn.disabled = true;
        this.messageInput.value = '';

        // Show welcome message
        this.conversationContainer.innerHTML = `
            <div class="welcome-message">
                <div class="assistant-avatar">🤖</div>
                <div class="message-bubble agent-message">
                    <p>Hi! I'm your dental assistant. I can help you:</p>
                    <ul>
                        <li>📅 Book a new appointment</li>
                        <li>♻️ Reschedule existing appointments</li>
                        <li>❌ Cancel appointments</li>
                    </ul>
                    <p>Click "Start" below to begin!</p>
                </div>
            </div>
        `;

        this.updateStatus('Ready to help', 'inactive');
    }

    toggleVoiceInput() {
        if (!this.recognition) {
            alert('Voice input is not supported in your browser. Please use Chrome, Edge, or Safari.');
            return;
        }

        if (this.isListening) {
            this.recognition.stop();
        } else {
            this.startListening();
        }
    }

    startListening() {
        this.isListening = true;
        this.voiceBtn.classList.add('listening');
        this.updateStatus('Listening... Speak now', 'listening');
        this.recognition.start();
    }

    stopListening() {
        this.isListening = false;
        this.voiceBtn.classList.remove('listening');
        if (this.isActive) {
            this.updateStatus('Ready - Type or speak your message', 'active');
        }
    }

    addMessage(role, text) {
        const messageGroup = document.createElement('div');
        messageGroup.className = `message-group ${role}`;

        const avatar = document.createElement('div');
        avatar.className = role === 'agent' ? 'assistant-avatar' : 'user-avatar';
        avatar.textContent = role === 'agent' ? '🤖' : '👤';

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${role}-message`;

        // Convert newlines to paragraphs
        const paragraphs = text.split('\n').filter(p => p.trim());
        paragraphs.forEach(p => {
            const para = document.createElement('p');
            para.textContent = p;
            bubble.appendChild(para);
        });

        messageGroup.appendChild(avatar);
        messageGroup.appendChild(bubble);

        this.conversationContainer.appendChild(messageGroup);

        // Scroll to bottom
        this.conversationContainer.scrollTop = this.conversationContainer.scrollHeight;
    }

    updateStatus(text, state) {
        this.statusText.textContent = text;

        // Update status dot
        this.statusDot.className = 'status-dot';
        if (state === 'active' || state === 'listening') {
            this.statusDot.classList.add('active');
        }
        if (state === 'listening') {
            this.statusDot.classList.add('listening');
        }
    }

    speakText(text) {
        // Use Web Speech API for text-to-speech
        if ('speechSynthesis' in window) {
            // Cancel any ongoing speech
            window.speechSynthesis.cancel();

            const utterance = new SpeechSynthesisUtterance(text);
            utterance.rate = 1.0;
            utterance.pitch = 1.0;
            utterance.volume = 0.9;

            // Try to use a female voice if available
            const voices = window.speechSynthesis.getVoices();
            const femaleVoice = voices.find(voice =>
                voice.name.includes('Female') ||
                voice.name.includes('Samantha') ||
                voice.name.includes('Victoria')
            );

            if (femaleVoice) {
                utterance.voice = femaleVoice;
            }

            window.speechSynthesis.speak(utterance);
        }
    }
}

// ===========================
// SMOOTH SCROLLING
// ===========================

function scrollToBooking() {
    document.getElementById('booking').scrollIntoView({
        behavior: 'smooth',
        block: 'start'
    });
}

function scrollToServices() {
    document.getElementById('services').scrollIntoView({
        behavior: 'smooth',
        block: 'start'
    });
}

// ===========================
// MODAL FUNCTIONS
// ===========================

let modalVoiceAssistant = null;

function openBookingModal() {
    const modal = document.getElementById('bookingModal');
    modal.classList.add('active');
    document.body.style.overflow = 'hidden'; // Prevent background scrolling

    // Initialize modal voice assistant if not already initialized
    if (!modalVoiceAssistant) {
        modalVoiceAssistant = new VoiceAssistant('Modal');
    }
}

function closeBookingModal() {
    const modal = document.getElementById('bookingModal');
    modal.classList.remove('active');
    document.body.style.overflow = ''; // Restore scrolling

    // End session if active
    if (modalVoiceAssistant && modalVoiceAssistant.isActive) {
        modalVoiceAssistant.endSession();
    }
}

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeBookingModal();
    }
});

// ===========================
// INITIALIZE APP
// ===========================

document.addEventListener('DOMContentLoaded', () => {
    // Load voices for speech synthesis
    if ('speechSynthesis' in window) {
        window.speechSynthesis.onvoiceschanged = () => {
            window.speechSynthesis.getVoices();
        };
    }

    console.log('Voice Assistant Modal initialized');
});
