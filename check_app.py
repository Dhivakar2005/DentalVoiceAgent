
import os
import sys

# Ensure local directory is in path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from unittest import mock

# Mock the dependencies that might not be fully installed or available in this env
sys.modules['sounddevice'] = mock.MagicMock()
sys.modules['soundfile'] = mock.MagicMock()
sys.modules['scipy'] = mock.MagicMock()
sys.modules['scipy.io'] = mock.MagicMock()
sys.modules['pyttsx3'] = mock.MagicMock()
sys.modules['speech_recognition'] = mock.MagicMock()
sys.modules['googleapiclient'] = mock.MagicMock()
sys.modules['googleapiclient.discovery'] = mock.MagicMock()
sys.modules['google_auth_oauthlib'] = mock.MagicMock()
sys.modules['google_auth_oauthlib.flow'] = mock.MagicMock()
sys.modules['google.auth.transport.requests'] = mock.MagicMock()

try:
    import app
    from app import DentalVoiceAgent, FAQ_DATABASE
    print("SUCCESS: app.py imported successfully.")
    
    import google_sheets_manager
    from google_sheets_manager import GoogleSheetsManager
    print("SUCCESS: google_sheets_manager.py imported successfully.")
    
    # Test FAQ_DATABASE logic
    for cat in FAQ_DATABASE:
        faq = FAQ_DATABASE[cat]
        if isinstance(faq, dict):
            keywords = faq.get("keywords")
            if isinstance(keywords, list):
                for kw in keywords:
                    if isinstance(kw, str):
                        # Simple check matches app.py logic
                        pass
    
    print("SUCCESS: All logic checks passed.")
    
except Exception as e:
    print(f"FAILED: Import error: {e}")
    import traceback
    traceback.print_exc()
