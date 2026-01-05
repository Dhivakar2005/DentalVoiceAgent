# 🦷 Smile Dental - AI Voice Assistant

A modern dental appointment booking website powered by AI voice assistant technology. Book, reschedule, or cancel appointments using natural voice commands or text input.

## ✨ Features

- **🎤 Voice-First Interface**: Natural language voice commands powered by Web Speech API
- **🤖 AI-Powered**: Gemini AI for intelligent conversation and intent parsing
- **📅 Google Calendar Integration**: Automatic appointment management
- **💬 Dual Input**: Support for both voice and text input
- **📱 Responsive Design**: Works seamlessly on desktop and mobile devices
- **🎨 Modern UI**: Beautiful glassmorphism design with smooth animations

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- Google Calendar API credentials
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
   
   ```python
   GEMINI_API_KEY = "your-actual-api-key-here"
   ```

4. **Set up Google Calendar API**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing one
   - Enable Google Calendar API
   - Create OAuth 2.0 credentials (Desktop app)
   - Download the credentials and save as `credentials.json` in the project root
   - You can use `credentials.json.example` as a template

5. **First Run Authorization**
   - On first run, you'll be prompted to authorize the application
   - A browser window will open for Google Calendar authorization
   - A `token.pickle` file will be created for future use

### Running the Application

1. **Start the Flask server**
   ```bash
   python server.py
   ```

2. **Open your browser**
   - Navigate to `http://localhost:5000`
   - The website will load automatically

3. **Start booking!**
   - Click "Start Voice Assistant" button
   - Speak or type your request
   - Follow the conversation flow

## 🎯 Usage Examples

### Booking an Appointment

**Voice/Text Input:**
```
"Book an appointment for tomorrow at 10 AM"
```

**Agent Response:**
```
What's your name?
```

**You:**
```
"John Smith"
```

**Agent:**
```
Great John! What's your phone number?
```

**You:**
```
"555-1234"
```

**Agent:**
```
Perfect! Your appointment is confirmed for John Smith on 2026-01-06 at 10:00 AM.
```

### Rescheduling an Appointment

**Voice/Text Input:**
```
"Reschedule my appointment"
```

**Agent will ask for:**
- Your name
- Phone number
- Current appointment date/time
- New desired date/time

### Canceling an Appointment

**Voice/Text Input:**
```
"Cancel my appointment on January 10"
```

**Agent will ask for:**
- Your name
- Phone number
- Appointment date/time for verification

## 🏗️ Project Structure

```
Dental/
├── app.py                  # Core voice agent logic (DO NOT MODIFY)
├── server.py              # Flask web server
├── requirements.txt       # Python dependencies
├── credentials.json       # Google Calendar API credentials
├── token.pickle          # Google Calendar auth token
├── templates/
│   └── index.html        # Main website template
└── static/
    ├── css/
    │   └── style.css     # Styling and design system
    └── js/
        └── app.js        # Frontend JavaScript logic
```

## 🔧 Technical Details

### Backend (Python)

- **Flask**: Web server framework
- **DentalVoiceAgent**: Core AI agent with stateful conversation
- **Google Calendar API**: Appointment management
- **Gemini AI**: Natural language understanding
- **pyttsx3**: Text-to-speech (CLI mode)
- **SpeechRecognition**: Speech-to-text (CLI mode)

### Frontend (JavaScript)

- **Web Speech API**: Browser-based voice input/output
- **Fetch API**: Communication with Flask backend
- **Vanilla JavaScript**: No framework dependencies
- **Responsive CSS**: Mobile-first design

### AI Capabilities

- **Intent Recognition**: Book, reschedule, cancel
- **Entity Extraction**: Name, phone, date, time, reason
- **Context Awareness**: Maintains conversation state
- **Date Parsing**: Handles multiple date formats
- **Time Normalization**: Converts to 12-hour format

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
