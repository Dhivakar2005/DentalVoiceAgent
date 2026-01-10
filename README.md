# 🦷 Smile Dental - AI Voice Assistant

A modern dental appointment booking website powered by AI voice assistant technology. Book, reschedule, or cancel appointments using natural voice commands or text input.

## ✨ Features

- **🎤 Voice-First Interface**: Natural language voice commands powered by Web Speech API
- **🤖 AI-Powered**: Gemini AI for intelligent conversation and intent parsing
- **📅 Google Calendar Integration**: Automatic appointment management
- **📊 Google Sheets Appointment Log**: Every booking is logged as a separate historical entry
- **👥 Smart Patient Recognition**: Skips redundant questions for returning patients using Customer IDs
- **🔍 View Appointments**: Patients can list all their upcoming bookings by ID
- **⏰ Business Hours**: Strict validation for Mon-Sat, 9 AM - 5 PM
- **💬 Dual Input**: Support for both voice and text input

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- Google Calendar API credentials
- Google Sheets API enabled
- Gemini API key (Google AI)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Dhivakar2005/DentalVoiceAgent.git
   cd DentalVoiceAgent
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up Gemini API Key**
   - Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
   - Create a new API key
   - Open `app.py` and replace `YOUR_GEMINI_API_KEY_HERE` with your actual API key

4. **Set up Google APIs**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Enable **Google Calendar API** and **Google Sheets API**
   - Create OAuth 2.0 credentials (Desktop app)
   - Download as `credentials.json` in the project root

### Running the Application

1. **Start the Flask server**
   ```bash
   python server.py
   ```

2. **Open your browser**
   - Navigate to `http://localhost:5000`

## 🎯 Usage Examples

### Booking as a New Patient

**Agent:** "Are you a new or old patient?"
**You:** "New"
**Agent:** "What's your name?"
... (Agent asks for Name, Phone, Date, Time, Reason)
**Agent:** "Confirmed! Your Customer ID is **CUST001**. Please save this!"

### Booking as a Returning Patient

**Agent:** "Are you a new or old patient?"
**You:** "Old"
**Agent:** "Please tell me your customer ID."
**You:** "CUST001"
**Agent:** "Welcome back, John! Is your phone still 555-1234? Say 'yes' to confirm."
**You:** "Yes"
**Agent:** "What date would you like to book for?" (Skips Name/Phone!)

### Viewing Your Appointments

**You:** "Show me my appointments"
**Agent:** "What is your customer ID?"
**You:** "CUST001"
**Agent:** "I found 2 appointments for you: Jan 10 at 10:00 AM and Jan 15 at 2:00 PM."

### Canceling an Appointment

**You:** "Cancel my appointment on Jan 10"
**Agent:** (Confirmed) "I have cleared those details from your schedule."
*Note: Cancellation clears the Date/Time fields in the log but keeps your customer record.*

## 🏗️ Project Structure

```
Dental/
├── app.py                  # Core agent logic & Gemini parsing
├── google_sheets_manager.py # Google Sheets Appointment Log integration
├── server.py              # Flask web server
├── requirements.txt       # Python dependencies
├── credentials.json       # Google API credentials
├── token.pickle          # Auth token
├── templates/
│   └── index.html        # Main UI
└── static/
    ├── css/style.css     # Premium Glassmorphism styling
    └── js/app.js         # Frontend voice/UI logic
```

## 🔧 Technical Details

### Backend (Python)

- **Flask**: Web server framework
- **DentalVoiceAgent**: Core AI agent with stateful conversation
- **Google Sheets Manager**: Appointment logging and retrieval (Appointment Log model)
- **Google Calendar API**: Real-time calendar synchronization
- **Gemini AI**: Natural language understanding and intent parsing
- **pyttsx3 / SpeechRecognition**: Local voice support (CLI/Debug mode)

### Frontend (JavaScript)

- **Web Speech API**: Browser-based voice input/output
- **Fetch API**: Communication with Flask backend
- **Vanilla JavaScript**: No framework dependencies
- **Premium CSS**: Glassmorphism and responsive design

### AI Capabilities

- **Intent Recognition**: Book, Reschedule, Cancel, and **View Appointments**
- **Smart Logic**: Automatic skipping of known patient fields (Name, Phone)
- **Historical Logging**: Every appointment is tracked as a unique row in Google Sheets
- **Strict Validation**: Business hours (9 AM - 5 PM) and date/time normalization
- **Context Awareness**: Remembers patient type and identity throughout the session

## 🎨 Design Features

- **Color Palette**: Professional dental blues and cyans
- **Glassmorphism**: Modern frosted glass effects
- **Smooth Animations**: Floating cards, transitions
- **Dark Mode Ready**: CSS variables for easy theming
- **Accessibility**: Semantic HTML, ARIA labels

## 🔒 Security Notes

### Important: Protect Your API Keys!

**Never commit sensitive files to Git:**
- `credentials.json` - Your Google Calendar OAuth credentials
- `token.pickle` - Your Google Calendar access token
- API keys in `app.py`

**Before pushing to GitHub:**
1. ✅ The `.gitignore` file is already configured to exclude sensitive files
2. ✅ Replace `YOUR_GEMINI_API_KEY_HERE` in `app.py` with your actual key locally
3. ✅ Never commit your real API key to version control

**For Production:**
- Use environment variables for API keys:
  ```python
  import os
  GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY_HERE')
  ```
- Implement user authentication
- Add rate limiting to prevent API abuse
- Use HTTPS in production environments
- Store credentials in secure secret management systems

## 🐛 Troubleshooting

### Voice input not working
- Ensure you're using Chrome, Edge, or Safari
- Grant microphone permissions when prompted
- Check browser console for errors

### Calendar integration issues
- Verify `credentials.json` is valid
- Delete `token.pickle` and re-authenticate
- Check Google Calendar API is enabled

### Server won't start
- Ensure all dependencies are installed
- Check port 5000 is not in use
- Verify Python version is 3.8+

## 📝 License

This project is for educational and demonstration purposes.

## 🤝 Support

For issues or questions, please check the troubleshooting section or review the code comments.

---

**Built with ❤️ using AI-powered voice technology**
