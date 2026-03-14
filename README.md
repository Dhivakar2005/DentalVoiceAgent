# 🦷 Smile Dental - AI-Powered Dental Assistant Platform
![Full Website UI](https://i.ibb.co/example/ui.png)

A high-end, cinematic dental appointment platform that merges **Dark Luxury Editorial** aesthetics with advanced AI capabilities. Smile Dental redefines the clinical experience through a sophisticated glassmorphic interface, effortless motion, and a real-time AI assistant.

## ✦ Design Philosophy: "The Modern Editorial"
The platform has been rebuilt with a focus on **visual excellence** and **emotional resonance**:
-   **Editorial Palette**: Void Black (#080808), Pearl Ivory (#f5f5f5), and a signature **Vibrant Yellow** accent (#f5d142).
-   **Typography**: An asymmetric mix of high-contrast serif headers (**Cormorant Garamond**) and sharp, modern sans-serif bodies (**Neue Haas Grotesk**).
-   **Glassmorphism**: Deep background blurs and micro-border gradients for a translucent, premium feel.
-   **Cinematic Motion**: Hardware-accelerated parallax scrolling and "Reveal-on-Scroll" animations.

## ✨ Key Features

### 🤖 **Next-Gen AI Assistant**
The chatbot has been redesigned from the ground up to feel like a premium concierge.
-   **Conversational Alignment**: Smart left/right message grouping with animated entry.
-   **Aura Interaction**: A custom custom cursor that syncs with the assistant's state.
-   **100% SVG Iconography**: No generic emojis; every action is represented by a precise, sharp line icon.

### 📅 **Smart Scheduling Engine**
-   **Predictive Booking**: Natural language processing for instant appointment extraction.
-   **Real-Time Sync**: Instant two-way synchronization with Google Calendar and Sheets.
-   **Conflict Resolution**: Automated checking of clinic hours and existing schedule densities.

### 🖼️ **Pantone Swatch Team Cards**
Our team section uses a signature "Pantone Swatch" layout with a **Cinematic Wide (21:9)** aspect ratio, blending professional photography with editorial product-style presentation.

## 📞 Telephony Support (Twilio)
Smile Dental isn’t just a website; it’s a phone-ready agent. Call the clinic's Twilio-enabled number to parlé directly with the AI receptionist. It handles the same logic, calendar sync, and sheet updates as the web interface.

## 🔧 Technical Stack
-   **Backend**: Flask / Python 3.12
-   **AI Engine**: Ollama (qwen2.5-coder:3b) 
-   **Communication**: Twilio Voice API, TwiML, Ngrok
-   **Infrastructure**: Google Sheets API (v4), Google Calendar API (v3)
-   **Frontend**: Hardware-accelerated CSS3 (Grid/Flex), Vanilla JS, RequestAnimationFrame Motion.
-   **Typography**: Google Fonts Inter, Cormorant Garamond, DM Mono.

## 🚀 Installation & Local Development

1.  **Clone & Install Dependencies**
    ```bash
    git clone https://github.com/Dhivakar2005/DentalVoiceAgent.git
    pip install -r requirements.txt
    ```
2.  **Model Setup**
    ```bash
    ollama serve
    ollama pull qwen2.5-coder:3b
    ```
3.  **Secrets Management**
    -   Place `credentials.json` (Google Cloud) in the root.
    -   Configure your `.env` for MongoDB/Twilio credentials.
4.  **Launch**
    ```bash
    python server.py
    ```

## 🏗️ Project Architecture
```
Dental/
├── app.py                      # Core AI Concierge Logic
├── server.py                   # High-Performance Flask Backend
├── google_sheets_manager.py    # Sheet-as-DB Persistence
├── templates/                  # Modern Editorial Templates
├── static/                     
│   ├── css/style.css           # Design Tokens & Motion Logic
│   └── js/app.js               # Reactive Frontend Controllers
└── .gitignore                  # Optimized Environment Rules
```

---
