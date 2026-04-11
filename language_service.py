"""
language_service.py
─
Multilingual support layer for Smile Dental.

Detects Tamil, Hindi, or English from patient input and provides:
  - Language detection (offline, no API key needed)
  - LLM prompt instruction per language
  - Pre-written WhatsApp message templates in all 3 languages

Supported Languages:
  "en" — English (default)
  "ta" — Tamil (Unicode script)
  "hi" — Hindi (Unicode script)

Usage:
  from language_service import detect_language, get_language_instruction, build_whatsapp_message
"""

import re
import structlog

logger = structlog.get_logger(__name__)

#  Constants 

SUPPORTED_LANGUAGES = {"en", "ta", "hi"}
DEFAULT_LANGUAGE = "en"

#  Language Detection 

# Tamil Unicode range: U+0B80–U+0BFF
_TAMIL_PATTERN  = re.compile(r'[\u0B80-\u0BFF]')
# Hindi/Devanagari Unicode range: U+0900–U+097F
_HINDI_PATTERN  = re.compile(r'[\u0900-\u097F]')

# Tamil transliteration keywords (common words typed in English)
_TAMIL_TRANSLIT = re.compile(
    r'\b(vanakkam|nandri|romba|theriyum|illai|sollunga|appointment|podanum|'
    r'book|pannanum|pannunga|panna|cancel|eppo|enna|yenna|ungalukku|enakku|'
    r'varuven|varom|varen|paakanum|naan|naa|naa\s+appointment|naa\s+book)\b',
    re.IGNORECASE
)

# Hindi transliteration keywords
_HINDI_TRANSLIT = re.compile(
    r'\b(namaste|namaskar|dhanyawad|shukriya|theek|haan|nahi|mujhe|mera|'
    r'appointment|chahiye|karna|kab|kaise|batao|kal|aaj|abhi|theek hai|'
    r'cancel|book|karo|karein|milega|milenge)\b',
    re.IGNORECASE
)


def detect_language(text: str) -> str:
    """
    Detect the language of a given text string.

    Priority:
      1. Unicode script detection (most reliable — actual Tamil/Hindi characters)
      2. Transliteration keyword matching (typed Roman-script Tamil/Hindi)
      3. langdetect library (statistical, as fallback)
      4. Default to English

    Returns: "en", "ta", or "hi"
    """
    if not text or not text.strip():
        return DEFAULT_LANGUAGE

    t = text.strip()

    # 1. Unicode Script Detection (highest confidence)
    if _TAMIL_PATTERN.search(t):
        logger.info("language_detected_unicode", lang="ta", sample=t[:30])
        return "ta"
    if _HINDI_PATTERN.search(t):
        logger.info("language_detected_unicode", lang="hi", sample=t[:30])
        return "hi"

    # 2. Transliteration Keyword Detection
    if _TAMIL_TRANSLIT.search(t):
        logger.info("language_detected_translit", lang="ta", sample=t[:30])
        return "ta"
    if _HINDI_TRANSLIT.search(t):
        logger.info("language_detected_translit", lang="hi", sample=t[:30])
        return "hi"

    # 3. Statistical Fallback — langdetect (installed via requirements)
    try:
        from langdetect import detect, LangDetectException
        detected = detect(t)
        logger.info("language_detected_langdetect", raw=detected, sample=t[:30])
        if detected == "ta":
            return "ta"
        if detected == "hi":
            return "hi"
        # Treat all other detected codes as English
        return "en"
    except Exception:
        pass

    # 4. Default
    return DEFAULT_LANGUAGE


def get_language_instruction(lang: str) -> str:
    """
    Return a prompt instruction to append to LLM system prompts.
    This tells the LLM (Ollama or GPT-4o-mini) which language to reply in.
    """
    instructions = {
        "ta": (
            "CRITICAL LANGUAGE RULE: The patient is communicating in Tamil. "
            "You MUST reply entirely in Tamil (Unicode script, e.g. நன்றி). "
            "Do NOT mix English into your response. "
            "All JSON field values that are spoken responses must be in Tamil."
        ),
        "hi": (
            "CRITICAL LANGUAGE RULE: The patient is communicating in Hindi. "
            "You MUST reply entirely in Hindi (Devanagari script, e.g. धन्यवाद). "
            "Do NOT mix English into your response. "
            "All JSON field values that are spoken responses must be in Hindi."
        ),
        "en": (
            "Reply in English."
        ),
    }
    return instructions.get(lang, instructions["en"])


def get_deepgram_language_config() -> dict:
    """
    Returns the Deepgram listen provider config for the Agent API.
    Nova-3 handles Tamil, Hindi, and English naturally.
    NOTE: do NOT include 'language' or 'detect_language' — those are STT-only
    fields and will cause UNPARSABLE_CLIENT_MESSAGE on the Agent API.
    """
    return {
        "type": "deepgram",
        "model": "nova-3",
        "endpointing": 100,
        "keyterms": [
            "appointment", "cancel", "reschedule", "book",
            "smile dental", "new patient", "existing patient",
            "verify", "confirm", "tomorrow", "today"
        ]
    }


#  WhatsApp Message Templates ─
# Pre-written in English, Tamil (Unicode), and Hindi (Devanagari).
# Uses Python .format(**kwargs) for variable injection.

TEMPLATES = {

    #  TYPE A: Booking Confirmation 
    "confirmation": {
        "en": (
            "Hello {name}, your appointment at {clinic} has been confirmed!\n\n"
            "Date    : {date}\n"
            "Time    : {time}\n"
            "Reason  : {reason}\n\n"
            "Please arrive 5 minutes early. See you soon!\n"
            "— {clinic}"
        ),
        "ta": (
            "வணக்கம் {name}, உங்கள் {clinic} மருத்துவமனையில் நியமனம் உறுதிப்படுத்தப்பட்டது!\n\n"
            "தேதி   : {date}\n"
            "நேரம்  : {time}\n"
            "காரணம் : {reason}\n\n"
            "5 நிமிடம் முன்பாக வருகை தரவும். நன்றி!\n"
            "— {clinic}"
        ),
        "hi": (
            "नमस्ते {name}, {clinic} में आपकी अपॉइंटमेंट की पुष्टि हो गई है!\n\n"
            "तारीख  : {date}\n"
            "समय    : {time}\n"
            "कारण   : {reason}\n\n"
            "कृपया 5 मिनट पहले पहुंचें। जल्द मिलते हैं!\n"
            "— {clinic}"
        ),
    },

    #  TYPE A: Modification Notice ─
    "modification": {
        "en": (
            "Hello {name}, your appointment at {clinic} has been updated.\n\n"
            "New Date : {date}\n"
            "New Time : {time}\n"
            "Reason   : {reason}\n\n"
            "If you have any questions, please call us at {clinic_number}.\n"
            "— {clinic}"
        ),
        "ta": (
            "வணக்கம் {name}, {clinic} இல் உங்கள் நியமனம் புதுப்பிக்கப்பட்டது.\n\n"
            "புதிய தேதி : {date}\n"
            "புதிய நேரம் : {time}\n"
            "காரணம்     : {reason}\n\n"
            "கேள்விகள் இருந்தால், {clinic_number} என்ற எண்ணில் அழைக்கவும்.\n"
            "— {clinic}"
        ),
        "hi": (
            "नमस्ते {name}, {clinic} में आपकी अपॉइंटमेंट अपडेट की गई है।\n\n"
            "नई तारीख : {date}\n"
            "नया समय  : {time}\n"
            "कारण     : {reason}\n\n"
            "कोई सवाल हो तो {clinic_number} पर कॉल करें।\n"
            "— {clinic}"
        ),
    },

    #  TYPE B: 36h Reminder 
    "reminder_36h": {
        "en": (
            "Hello {name}, just a friendly reminder from {clinic}!\n\n"
            "Your appointment is coming up:\n"
            "Date   : {date}\n"
            "Time   : {time}\n"
            "Reason : {reason}\n\n"
            "No action needed. We look forward to seeing you!\n"
            "— {clinic}"
        ),
        "ta": (
            "வணக்கம் {name}, {clinic} இலிருந்து நினைவூட்டல்!\n\n"
            "உங்கள் நியமனம் விரைவில் உள்ளது:\n"
            "தேதி   : {date}\n"
            "நேரம்  : {time}\n"
            "காரணம் : {reason}\n\n"
            "எந்த நடவடிக்கையும் தேவையில்லை. நன்றி!\n"
            "— {clinic}"
        ),
        "hi": (
            "नमस्ते {name}, {clinic} से एक याद दिलाना!\n\n"
            "आपकी अपॉइंटमेंट जल्द आने वाली है:\n"
            "तारीख  : {date}\n"
            "समय    : {time}\n"
            "कारण   : {reason}\n\n"
            "कोई कार्रवाई नहीं चाहिए। आपसे मिलने की प्रतीक्षा है!\n"
            "— {clinic}"
        ),
    },

    #  TYPE B: Same-Day Reminder ─
    "reminder_today": {
        "en": (
            "Good morning {name}! 🌟\n\n"
            "You have a dental appointment today at {clinic}.\n"
            "Time   : {time}\n"
            "Reason : {reason}\n\n"
            "Please arrive 5 minutes early. See you soon!\n"
            "— {clinic}"
        ),
        "ta": (
            "காலை வணக்கம் {name}! 🌟\n\n"
            "இன்று {clinic} இல் உங்கள் பல் சிகிச்சை நியமனம் உள்ளது.\n"
            "நேரம்  : {time}\n"
            "காரணம் : {reason}\n\n"
            "5 நிமிடம் முன்பாக வந்துவிடுங்கள். நன்றி!\n"
            "— {clinic}"
        ),
        "hi": (
            "सुप्रभात {name}! 🌟\n\n"
            "आज {clinic} में आपकी dental appointment है।\n"
            "समय   : {time}\n"
            "कारण  : {reason}\n\n"
            "कृपया 5 मिनट पहले पहुंचें। जल्द मिलते हैं!\n"
            "— {clinic}"
        ),
    },

    #  TYPE C: YES/NO Prediction Request 
    "prediction_request": {
        "en": (
            "Hello {name}, based on your treatment plan at {clinic},\n"
            "we have scheduled your next visit:\n\n"
            "Treatment : {treatment}\n"
            "Date      : {date}\n\n"
            "Please reply:\n"
            "✅ *YES* with your preferred time (e.g. YES 10 AM)\n"
            "❌ *NO* to decline\n\n"
            "— {clinic}"
        ),
        "ta": (
            "வணக்கம் {name}, உங்கள் சிகிச்சை திட்டத்தின் படி {clinic} இல்\n"
            "அடுத்த வருகை திட்டமிடப்பட்டுள்ளது:\n\n"
            "சிகிச்சை : {treatment}\n"
            "தேதி      : {date}\n\n"
            "பதிலளிக்கவும்:\n"
            "✅ *ஆம்* — விரும்பிய நேரத்தோடு (எ.கா. ஆம் காலை 10)\n"
            "❌ *வேண்டாம்* — மறுக்க\n\n"
            "— {clinic}"
        ),
        "hi": (
            "नमस्ते {name}, आपकी उपचार योजना के अनुसार {clinic} में\n"
            "अगली विजिट निर्धारित की गई है:\n\n"
            "उपचार : {treatment}\n"
            "तारीख : {date}\n\n"
            "कृपया जवाब दें:\n"
            "✅ *हाँ* — अपना पसंदीदा समय बताएं (जैसे हाँ सुबह 10)\n"
            "❌ *नहीं* — मना करने के लिए\n\n"
            "— {clinic}"
        ),
    },

    #  TYPE C: Future Visits Info 
    "future_visits_info": {
        "en": (
            "Hello {name}, your treatment *{treatment}* may require "
            "up to *{total_sittings} sittings*.\n\n"
            "We will send you a WhatsApp message before each predicted visit "
            "for you to confirm. No action needed right now.\n"
            "— {clinic}"
        ),
        "ta": (
            "வணக்கம் {name}, உங்கள் *{treatment}* சிகிச்சைக்கு "
            "மொத்தம் *{total_sittings} முறை* வரலாம்.\n\n"
            "ஒவ்வொரு வருகையின் முன்னும் WhatsApp மூலம் தெரிவிப்போம். "
            "இப்போது எந்த நடவடிக்கையும் தேவையில்லை.\n"
            "— {clinic}"
        ),
        "hi": (
            "नमस्ते {name}, आपके *{treatment}* उपचार के लिए "
            "कुल *{total_sittings} बार* आना पड़ सकता है।\n\n"
            "हर विजिट से पहले WhatsApp पर सूचना भेजी जाएगी। "
            "अभी कोई कार्रवाई नहीं चाहिए।\n"
            "— {clinic}"
        ),
    },

    #  TYPE D: YES Confirmation 
    "yes_confirmation": {
        "en": (
            "Confirmed! Your appointment at {clinic} has been booked.\n\n"
            "Date   : {date}\n"
            "Time   : {time}\n"
            "Reason : {reason}\n\n"
            "We will send you a reminder closer to the date.\n"
            "— {clinic}"
        ),
        "ta": (
            "உறுதிப்படுத்தப்பட்டது! {clinic} இல் உங்கள் நியமனம் பதிவு செய்யப்பட்டது.\n\n"
            "தேதி   : {date}\n"
            "நேரம்  : {time}\n"
            "காரணம் : {reason}\n\n"
            "தேதிக்கு முன் நினைவூட்டல் அனுப்புவோம்.\n"
            "— {clinic}"
        ),
        "hi": (
            "पुष्टि हो गई! {clinic} में आपकी अपॉइंटमेंट बुक हो गई।\n\n"
            "तारीख  : {date}\n"
            "समय    : {time}\n"
            "कारण   : {reason}\n\n"
            "तारीख से पहले रिमाइंडर भेजा जाएगा।\n"
            "— {clinic}"
        ),
    },

    #  TYPE D: NO Reply ─
    "no_reply": {
        "en": (
            "Thank you for letting us know, {name}. "
            "The appointment has been cancelled.\n\n"
            "Our team will contact you to reschedule.\n"
            "— {clinic}"
        ),
        "ta": (
            "தெரிவித்ததற்கு நன்றி, {name}. "
            "நியமனம் ரத்து செய்யப்பட்டது.\n\n"
            "எங்கள் குழுவினர் மறுதிட்டமிட தொடர்பு கொள்வர்.\n"
            "— {clinic}"
        ),
        "hi": (
            "बताने के लिए धन्यवाद, {name}. "
            "आपकी अपॉइंटमेंट रद्द कर दी गई है।\n\n"
            "हमारी टीम आपसे पुनर्निर्धारित करने के लिए संपर्क करेगी।\n"
            "— {clinic}"
        ),
    },

    #  TYPE D: Cancellation Notice ─
    "cancellation": {
        "en": (
            "Hello {name}, your appointment scheduled on *{date}* "
            "at {clinic} has been cancelled.\n\n"
            "Please call us at {clinic_number} to reschedule.\n"
            "— {clinic}"
        ),
        "ta": (
            "வணக்கம் {name}, {clinic} இல் *{date}* அன்று "
            "திட்டமிட்ட உங்கள் நியமனம் ரத்து செய்யப்பட்டது.\n\n"
            "மறுதிட்டமிட {clinic_number} எண்ணில் அழைக்கவும்.\n"
            "— {clinic}"
        ),
        "hi": (
            "नमस्ते {name}, {clinic} में *{date}* को निर्धारित "
            "आपकी अपॉइंटमेंट रद्द कर दी गई है।\n\n"
            "पुनर्निर्धारित करने के लिए {clinic_number} पर कॉल करें।\n"
            "— {clinic}"
        ),
    },

    #  TYPE D: Emergency Reply ─
    "emergency": {
        "en": (
            "🚨 We received your message. "
            "Please visit {clinic} immediately "
            "or call us directly at {clinic_number}.\n"
            "— {clinic}"
        ),
        "ta": (
            "🚨 உங்கள் செய்தி பெறப்பட்டது. "
            "உடனடியாக {clinic} க்கு வருகை தரவும் "
            "அல்லது {clinic_number} எண்ணில் நேரடியாக அழைக்கவும்.\n"
            "— {clinic}"
        ),
        "hi": (
            "🚨 आपका संदेश मिल गया। "
            "कृपया तुरंत {clinic} आएं "
            "या {clinic_number} पर सीधे कॉल करें।\n"
            "— {clinic}"
        ),
    },

    #  TYPE D: Fallback 
    "fallback": {
        "en": (
            "Thank you for reaching out to {clinic}. "
            "Our team will get back to you shortly.\n"
            "For urgent concerns, please call {clinic_number}.\n"
            "— {clinic}"
        ),
        "ta": (
            "{clinic} ஐ தொடர்பு கொண்டதற்கு நன்றி. "
            "எங்கள் குழுவினர் விரைவில் பதிலளிப்பர்.\n"
            "அவசர விஷயங்களுக்கு {clinic_number} எண்ணில் அழைக்கவும்.\n"
            "— {clinic}"
        ),
        "hi": (
            "{clinic} से संपर्क करने के लिए धन्यवाद। "
            "हमारी टीम जल्द ही आपसे संपर्क करेगी।\n"
            "आपातकालीन मामलों के लिए {clinic_number} पर कॉल करें।\n"
            "— {clinic}"
        ),
    },
}


def build_whatsapp_message(template_key: str, lang: str, **kwargs) -> str:
    """
    Build a WhatsApp message from a template in the given language.

    Args:
        template_key: Key from TEMPLATES dict (e.g. "confirmation")
        lang: Language code "en", "ta", or "hi"
        **kwargs: Variables to inject (name, date, time, etc.)

    Returns:
        Formatted message string.
    """
    # Normalise language code — fallback to English if unsupported
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE

    template_group = TEMPLATES.get(template_key, {})
    template = template_group.get(lang) or template_group.get(DEFAULT_LANGUAGE, "")

    try:
        return template.format(**kwargs)
    except KeyError as e:
        logger.error("template_format_error", key=template_key, lang=lang, missing=str(e))
        # Fallback to English
        try:
            return template_group.get(DEFAULT_LANGUAGE, "").format(**kwargs)
        except Exception:
            return ""


def normalize_input(text: str, lang: str) -> str:
    """
    Normalize Tamil/Hindi patient input to English so aya-expanse:8b can understand it.

    Strategy:
      1. Replace known Tamil/Hindi dental phrases with English equivalents (phrase map).
      2. Strip remaining Tamil/Hindi Unicode characters (the LLM can't read them).
      3. Keep any English tokens already present (handles code-switching).
      4. Return a clean, English-readable string for intent extraction.

    The patient NEVER sees this — it's an internal translation step only.
    The RESPONSE is still composed in the patient's original language.

    Args:
        text: Raw patient message (Tamil, Hindi, or mixed)
        lang: Detected language code ("ta", "hi", "en")

    Returns:
        English-normalised string suitable for aya-expanse:8b.
    """
    if lang == "en":
        return text  # No normalization needed for English

    t = text.strip()

    if lang == "ta":
        #  Tamil → English phrase replacements 
        # Order matters: longer/specific phrases first, then single words
        TAMIL_MAP = [
            # Self-introduction
            (r'நான்\s+\S+\s+பேசுகிறேன்',   'my name is'),   # "நான் X பேசுகிறேன்" → "my name is"
            (r'என்\s+பெயர்',               'my name is'),
            (r'என்னுடைய\s+பெயர்',         'my name is'),
            (r'நான்',                       'i am'),
            # Intent — booking
            (r'அப்பாயின்மெண்ட்\s+பதிவு\s+செய்ய\s+வேண்டும்', 'book appointment'),
            (r'நியமனம்\s+பதிவு\s+செய்ய\s+வேண்டும்',         'book appointment'),
            (r'appointment\s+பதிவு\s+செய்ய\s+வேண்டும்',      'book appointment'),
            (r'appointment\s+வேண்டும்',                        'need appointment'),
            (r'பதிவு\s+செய்ய\s+வேண்டும்',                    'book appointment'),
            (r'பதிவு\s+பண்ண\s+வேண்டும்',                     'book appointment'),
            (r'புக்\s+பண்ண\s+வேண்டும்',                      'book appointment'),
            (r'book\s+பண்ண\s+வேண்டும்',                       'book appointment'),
            (r'book\s+pannanum',                               'book appointment'),
            (r'appointment\s+pannanum',                        'book appointment'),
            (r'appointment\s+panna\s+venum',                   'book appointment'),
            # Intent — cancel
            (r'ரத்து\s+செய்ய\s+வேண்டும்',                    'cancel appointment'),
            (r'cancel\s+பண்ண\s+வேண்டும்',                     'cancel appointment'),
            (r'ரத்து\s+பண்ண',                                  'cancel'),
            # Intent — reschedule
            (r'மாற்ற\s+வேண்டும்',                             'reschedule appointment'),
            (r'நேரம்\s+மாற்ற',                                'reschedule'),
            (r'date\s+மாற்ற',                                  'reschedule'),
            # Patient type — extended coverage
            (r'உங்கள்\s+கிளினிக்கின்\s+பழைய',  'existing patient'),
            (r'உங்கள்\s+கிளினிக்கின்',           'your clinic'),
            (r'பழைய\s+நோயாளி',                   'existing patient'),
            (r'பழைய\s+patient',                   'existing patient'),
            (r'pazhaiya\s+patient',               'existing patient'),
            (r'pazhaya\s+patient',                'existing patient'),
            (r'pudhiya\s+patient',                'new patient'),
            (r'புதிய\s+நோயாளி',                  'new patient'),
            (r'புதிய\s+patient',                  'new patient'),
            (r'முன்பு\s+வந்திருக்கிறேன்',        'i have been here before existing patient'),
            (r'முன்பு\s+வந்தேன்',                 'i came before existing patient'),
            (r'முன்னாடி\s+வந்தேன்',              'i visited before existing patient'),
            # YES / NO
            (r'\bஆம்\b',       'yes'),
            (r'\bசரி\b',       'yes'),
            (r'\bஓகே\b',       'okay'),
            (r'\bவேண்டாம்\b',  'no'),
            (r'\bவேண்டா\b',    'no'),
            # Greetings (strip — not useful for intent extraction)
            (r'வணக்கம்',       ''),
            (r'நன்றி',         ''),
            # Time words
            (r'நாளை',         'tomorrow'),
            (r'இன்று',         'today'),
            (r'இன்றைக்கு',     'today'),
            (r'காலை',         'morning'),
            (r'மாலை',         'evening'),
            (r'மதியம்',        'afternoon'),
            # Dental reasons
            (r'பல்\s+வலி',     'toothache'),
            (r'ஈறு\s+வலி',     'gum problem'),
            (r'பல்\s+சுத்தம்', 'cleaning'),
            (r'பொதுவான\s+பரிசோதனை', 'checkup'),
        ]
        for pattern, replacement in TAMIL_MAP:
            t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)

        # Extract name after "my name is" by keeping the next word if it's Latin
        # (e.g. "நான் Dhivakar பேசுகிறேன்" → "my name is Dhivakar")
        # Already handled by the first pattern above

    elif lang == "hi":
        #  Hindi → English phrase replacements 
        HINDI_MAP = [
            # Self-introduction
            (r'मेरा\s+नाम\s+(\S+)\s+है', r'my name is \1'),
            (r'मैं\s+(\S+)\s+बोल\s+रहा', r'my name is \1'),
            (r'मैं',                       'i am'),
            # Intent — booking
            (r'appointment\s+बुक\s+करनी\s+है', 'book appointment'),
            (r'appointment\s+चाहिए',            'need appointment'),
            (r'appointment\s+लेनी\s+है',        'book appointment'),
            (r'नया\s+appointment',              'book appointment'),
            (r'बुक\s+करना\s+है',               'book appointment'),
            (r'बुक\s+कराना\s+है',              'book appointment'),
            # Intent — cancel
            (r'appointment\s+रद्द\s+करनी\s+है', 'cancel appointment'),
            (r'रद्द\s+करना\s+है',               'cancel'),
            (r'cancel\s+करना\s+है',              'cancel appointment'),
            # Intent — reschedule
            (r'appointment\s+बदलनी\s+है', 'reschedule appointment'),
            (r'समय\s+बदलना\s+है',         'reschedule'),
            # Patient type
            (r'पुराना\s+मरीज',     'existing patient'),
            (r'पुराना\s+patient',  'existing patient'),
            (r'नया\s+मरीज',        'new patient'),
            (r'नया\s+patient',     'new patient'),
            (r'पहले\s+आया\s+हूं',  'i have been here before existing patient'),
            # YES / NO
            (r'\bहाँ\b',    'yes'),
            (r'\bहां\b',    'yes'),
            (r'\bठीक है\b', 'yes okay'),
            (r'\bहाँ जी\b', 'yes'),
            (r'\bनहीं\b',   'no'),
            (r'\bनही\b',    'no'),
            # Greetings
            (r'नमस्ते', ''),
            (r'धन्यवाद', ''),
            # Time words
            (r'\bकल\b',    'tomorrow'),
            (r'\bआज\b',    'today'),
            (r'\bसुबह\b',  'morning'),
            (r'\bशाम\b',   'evening'),
            (r'\bदोपहर\b', 'afternoon'),
            # Dental reasons
            (r'दांत\s+दर्द',     'toothache'),
            (r'मसूड़े\s+की\s+समस्या', 'gum problem'),
            (r'सफाई',             'cleaning'),
            (r'जांच',             'checkup'),
        ]
        for pattern, replacement in HINDI_MAP:
            t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)

    #  Strip remaining non-ASCII non-Latin Unicode (Tamil/Hindi chars not mapped) 
    # Keep: Latin letters, digits, spaces, common punctuation
    # This removes any unmapped Tamil/Hindi characters so aya-expanse:8b isn't confused
    t = re.sub(r'[^\x00-\x7F]+', ' ', t)

    # Collapse multiple spaces
    t = re.sub(r'\s{2,}', ' ', t).strip()

    logger.info("input_normalized", lang=lang, original=text[:50], normalized=t[:80])
    return t if t else text  # Safety: if normalization empties the string, return original


#  Quick Self-Test 

if __name__ == "__main__":
    # Detection tests
    detection_tests = [
        ("Hello, I need to book an appointment",                                    "en"),
        ("வணக்கம், என்னுடைய appointment book பண்ணணும்",                             "ta"),
        ("நான் appointment வேண்டும்",                                               "ta"),
        ("நான் திவாகர் பேசுகிறேன். எனக்கு ஒரு அப்பாயின்மெண்ட் பதிவு செய்ய வேண்டும்.", "ta"),
        ("நான் உங்கள் கிளினிக்கின் பழைய நோயாளி.",                                 "ta"),
        ("नमस्ते, मुझे appointment बुक करनी है",                                    "hi"),
        ("haan theek hai, kal book karo",                                           "hi"),
        ("Hi, I want to cancel",                                                    "en"),
        ("appointment book pannanum",                                               "ta"),
        ("mujhe appointment chahiye",                                               "hi"),
    ]

    print("=" * 70)
    print("  Language Detection Self-Test")
    print("=" * 70)
    for text, expected in detection_tests:
        detected = detect_language(text)
        status = "✅" if detected == expected else "❌"
        print(f"{status} [{expected}→{detected}] '{text[:55]}'")

    # Normalization tests
    print("\n" + "=" * 70)
    print("  Normalization Self-Test (Tamil → English)")
    print("=" * 70)
    normalization_tests_ta = [
        "நான் திவாகர் பேசுகிறேன். எனக்கு ஒரு அப்பாயின்மெண்ட் பதிவு செய்ய வேண்டும்.",
        "நான் உங்கள் கிளினிக்கின் பழைய நோயாளி.",
        "appointment book pannanum",
        "appointment ரத்து பண்ண வேண்டும்",
        "நாளை காலை 10 மணிக்கு appointment வேண்டும்",
        "ஆம், சரி தான்",
        "வேண்டாம்",
    ]
    for text in normalization_tests_ta:
        normalized = normalize_input(text, "ta")
        print(f"  IN : {text[:55]}")
        print(f"  OUT: {normalized}")
        print()

    print("=" * 70)
    print("  Normalization Self-Test (Hindi → English)")
    print("=" * 70)
    normalization_tests_hi = [
        "नमस्ते, मुझे appointment बुक करनी है",
        "मैं पुराना मरीज हूं",
        "हाँ, ठीक है",
        "नहीं, cancel करना है",
        "कल सुबह appointment चाहिए",
    ]
    for text in normalization_tests_hi:
        normalized = normalize_input(text, "hi")
        print(f"  IN : {text[:55]}")
        print(f"  OUT: {normalized}")
        print()

    print(" Sample Tamil WhatsApp Confirmation ")
    msg = build_whatsapp_message(
        "confirmation", "ta",
        name="Dhivakar", clinic="Smile Dental",
        date="2026-04-10", time="10:30 AM", reason="Root Canal"
    )
    print(msg)
