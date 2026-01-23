# 🦷 Smile Dental - AI-Powered Dental Assistant Platform

A premium, modern dental appointment management system with AI voice assistant, user authentication, and comprehensive admin dashboard. Built with cutting-edge glassmorphic UI design and powered by intelligent conversation technology.

## ✨ Key Features

### 🎤 **AI Voice Assistant**
- Natural language voice commands powered by Web Speech API
- Intelligent conversation flow with context awareness
- Supports both voice and text input modes
- Multi-language speech recognition

### 👥 **User Management**
- Secure authentication system (signup/login)
- Role-based access control (User/Admin)
- Customer ID system for permanent patient identification
- Customer Master database for patient records

### 📅 **Smart Appointment System**
- **Booking**: Collects name, phone, date, time, and reason (all required)
- **Rescheduling**: Updates same row in Google Sheets, deletes old calendar event
- **Cancellation**: Removes entire row from Sheets and deletes calendar event
- Strict field validation - no null values allowed
- Business hours enforcement (Mon-Sat, 9 AM - 5 PM)
- 3-day advance booking limit

### 📊 **Data Management**
- **Customer_Master Sheet**: Permanent patient records (ID, name, phone, creation date)
- **Customers Sheet**: Appointment log with full booking history
- Google Calendar integration with automatic event management
- Real-time synchronization between Sheets and Calendar

### 🎨 **Premium UI/UX**
- Glassmorphic dark theme with HSL color tokens
- Smooth animations and micro-interactions
- Responsive design for all devices
- Immersive agent interface with chat history scrolling
- Modern typography using 'Outfit' font

### 👨‍💼 **Admin Dashboard**
- View all appointments and patient records
- Calendar event monitoring
- Modern "Command Center" aesthetic
- Real-time data updates

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- Google Calendar API credentials
- Google Sheets API enabled
- Ollama with qwen2.5-coder:3b model (for local LLM)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Dhivakar2005/DentalVoiceAgent.git
   cd Dental
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up Ollama (Local LLM)**
   ```bash
   # Install Ollama from https://ollama.ai
   ollama pull qwen2.5-coder:3b
   ollama serve
   ```

4. **Set up Google APIs**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Enable **Google Calendar API** and **Google Sheets API**
   - Create OAuth 2.0 credentials (Desktop app)
   - Download as `credentials.json` in the project root

5. **Configure MongoDB (Optional)**
   - The system uses MongoDB for user authentication
   - Update connection string in `database_manager.py` if needed

### Running the Application

1. **Start Ollama server** (in a separate terminal)
   ```bash
   ollama serve
   ```

2. **Start the Flask server**
   ```bash
   python server.py
   ```

3. **Open your browser**
   - Navigate to `http://localhost:5000`
   - Sign up for a new account or login
   - Click "Launch Assistant" to start booking

## 🎯 Usage Guide

### Booking Workflow

**New Patient:**
1. Agent asks: "Are you a new or old patient?" → Say "New"
2. System collects: Name, Phone, Date, Time, Reason
3. Generates Customer ID (e.g., CUST001)
4. Logs to Customer_Master and Customers sheets
5. Creates Google Calendar event

**Returning Patient:**
1. Agent asks: "Are you a new or old patient?" → Say "Old"
2. Provide Customer ID
3. System confirms identity and prefills name/phone
4. Only asks for: Date, Time, Reason
5. Logs appointment to Customers sheet

### Rescheduling Workflow

1. Agent asks for: Name, Phone, CURRENT date, CURRENT time
2. Then asks for: NEW date, NEW time
3. Updates **same row** in Google Sheets
4. Deletes old calendar event and creates new one

### Cancellation Workflow

1. Agent asks for: Name, Customer ID, Date, Time
2. Deletes **entire row** from Customers sheet
3. Removes event from Google Calendar

## 🏗️ Project Structure

```
Dental/
├── app.py                      # Core AI agent logic & LLM parsing
├── google_sheets_manager.py    # Customer Master & Appointment log management
├── database_manager.py         # MongoDB user authentication
├── server.py                   # Flask web server with auth routes
├── requirements.txt            # Python dependencies
├── credentials.json            # Google API credentials
├── token.pickle               # Google auth token
├── sheets_config.json         # Spreadsheet configuration
├── templates/
│   ├── index.html             # Main app with glassmorphic UI
│   ├── login.html             # Authentication page
│   └── admin.html             # Admin dashboard
└── static/
    ├── css/style.css          # Premium design system
    └── js/app.js              # Voice assistant frontend logic
```

## 🔧 Technical Stack

### Backend
- **Flask**: Web server framework
- **Ollama (qwen2.5-coder:3b)**: Local LLM for intent parsing
- **Google Sheets API**: Data persistence
- **Google Calendar API**: Appointment scheduling
- **MongoDB**: User authentication database
- **pyttsx3**: Text-to-speech

### Frontend
- **Vanilla JavaScript**: No framework dependencies
- **Web Speech API**: Browser voice input/output
- **Modern CSS**: Glassmorphism with HSL tokens
- **'Outfit' Font**: Premium typography

### AI Features
- Intent recognition: book, reschedule, cancel
- Context-aware field collection
- Strict validation with no null values
- Date/time normalization
- Business hours enforcement

## 🔒 Security

### API Key Management
- Never commit `credentials.json` or `token.pickle`
- `.gitignore` pre-configured for security
- Use environment variables in production

### Authentication
- Bcrypt password hashing
- Session-based auth with Flask sessions
- Role-based access control (User/Admin)

### Data Protection
- Customer IDs are **permanent and immutable**
- MongoDB for secure user credential storage
- Google OAuth for API access

## 🐛 Troubleshooting

### Voice not working
- Use Chrome, Edge, or Safari
- Grant microphone permissions
- Check browser console for errors

### Ollama connection failed
- Ensure Ollama is running: `ollama serve`
- Verify model is installed: `ollama list`
- Check `http://localhost:11434` is accessible

### Google API issues
- Verify credentials.json is valid
- Delete token.pickle and re-authenticate
- Check API quotas in Google Console

### Duplicate appointments
- Fixed: System now logs only once after calendar creation
- Check for multiple calls to `log_appointment` if issue persists

### Agent loses context
- Fixed: Improved fallback handling
- System maintains booking intent throughout conversation

## 📝 Recent Updates

### Latest Improvements (January 2026)
- ✅ Fixed duplicate appointment logging
- ✅ Enhanced reschedule to delete old calendar events
- ✅ Enforced strict field validation (no null values)
- ✅ Context-aware prompts for reschedule/cancel
- ✅ Immutable Customer ID protection
- ✅ Fixed agent UI scrolling issues
- ✅ Premium glassmorphic UI overhaul
- ✅ Added authentication and admin dashboard

## � License

This project is for educational and demonstration purposes.

## 🤝 Contributing

For issues or improvements, please review the code and submit suggestions.

---

**Built with ❤️ using AI-powered voice technology and modern web design**
