# 🦷 Smile Dental - AI-Powered Dental Assistant Platform

A high-end dental appointment platform that redefines the clinical experience through a sophisticated glassmorphic interface, effortless motion, and a real-time AI assistant.

## ✨ Key Features

### 🤖 Next-Gen AI Assistant
The chatbot has been redesigned from the ground up to feel like a premium concierge.
-   **Conversational Alignment**: Smart left/right message grouping with animated entry.
-   **Aura Interaction**: A custom cursor that syncs with the assistant's state.
-   **Hybrid Intent Engine**: Combines fast regex-based extraction (<1ms) with LLM fallback for high reliability and speed.

### 📅 Smart Scheduling Engine
-   **Predictive Booking**: Natural language processing for instant appointment extraction.
-   **Real-Time Sync**: Instant two-way synchronization with Google Calendar and Sheets.
-   **Conflict Resolution**: Automated checking of clinic hours and existing schedule densities.
-   **Sheet-as-DB**: Advanced customer management via Google Sheets with **Offline Sync** capabilities.

### 📊 Admin Dashboard
A secure, real-time dashboard for clinic management:
-   **Appointment Overview**: View all upcoming bookings synced directly from Google Sheets.
-   **Calendar Feed**: Real-time list of events from the clinic's primary Google Calendar.
-   **User Management**: Secure login and role-based access for staff.

### 📞 Telephony Support (Twilio)
Smile Dental is a phone-ready agent. Call the clinic's Twilio-enabled number to interact with the AI receptionist.
-   **Natural Conversation**: Uses VAD (Voice Activity Detection) for seamless turn-taking.
-   **Indian English Support**: Optimized for regional accents (`en-IN`) with high-fidelity TTS (Polly Joanna).
-   **Enhanced STT**: Hardware-accelerated speech-to-text for near-zero latency.

## 🔧 Technical Stack
-   **Backend**: Flask / Python 3.12
-   **AI Engine**: Ollama (qwen3.5:0.8b)
<!-- -   **Database**: MongoDB (User Auth), Google Sheets (Customer Records) -->
-   **Communication**: Twilio Voice API, TwiML, Ngrok
-   **Infrastructure**: Google Sheets API (v4), Google Calendar API (v3)
-   **Frontend**: Hardware-accelerated CSS3, Vanilla JS, RequestAnimationFrame Motion.

## 🚀 Installation & Local Development

1.  **Clone & Install Dependencies**
    ```bash
    git clone https://github.com/Dhivakar2005/DentalVoiceAgent.git
    pip install -r requirements.txt
    ```
2.  **Model Setup**
    ```bash
    ollama serve
    ollama pull qwen3.5:0.8b
    ```
3.  **Secrets Management**
    -   Place `credentials.json` (Google Cloud) in the root.
    -   Configure your `.env` or environment variables:
        - `FLASK_SECRET_KEY`: Secure key for session management.
        - `MONGO_URI`: MongoDB connection string.
        - `TWILIO_AUTH_TOKEN`: For webhook signature validation.
4.  **Launch**
    ```bash
    python server.py
    ```

## 🏗️ Project Architecture
```
Dental/
├ app.py                      # Core AI Concierge & Logic Loader
├ server.py                   # High-Performance Flask Backend (Web & Twilio)
├ google_sheets_manager.py    # Sheet-as-DB Persistence & Offline Sync
├ database_manager.py         # MongoDB User Authentication
├ logic.json                  # FAQ Database & Intent Patterns
├ templates/                  # Modern Editorial Templates
├ static/                     
│   ├ css/style.css           # Design Tokens & Motion Logic
│   └ js/app.js               # Reactive Frontend Controllers
└ .gitignore                  # Optimized Environment Rules
```

---
