"""
app/services/onboarding.py

Krishi Kalyan — Language-first onboarding service.

Responsibilities
----------------
1. Track each chat's onboarding state in memory:
     NEW          → never seen this chat
     AWAITING_LANG → language picker sent, waiting for button press
     DONE          → language chosen, welcome menu shown, normal flow active

2. Provide all translated strings for:
     - The language picker message + InlineKeyboard
     - The welcome menu (shown after language is chosen)
     - The /help menu

3. Expose a simple API used only by telegram.py:
     onboarding_service.is_done(chat_id)        → bool
     onboarding_service.needs_language(chat_id) → bool
     onboarding_service.get_language(chat_id)   → str | None  (lang code)
     onboarding_service.reset(chat_id)          → None  (called on /start)
     onboarding_service.mark_awaiting(chat_id)  → None
     onboarding_service.complete(chat_id, lang) → None
     onboarding_service.language_picker_payload(chat_id) → dict  (Telegram API payload)
     onboarding_service.welcome_payload(chat_id, lang)   → dict
     onboarding_service.help_payload(chat_id)            → dict

Design principles
-----------------
- Zero external libraries. No Google Translate. No langdetect.
- All translations are hand-crafted constants — farmers see natural,
  dialect-appropriate text, not machine-translated strings.
- Storage is in-memory (per the project requirement). The dict is
  keyed by chat_id (int). On server restart users go through onboarding
  again — acceptable for this deployment model.
- telegram.py is the ONLY caller. Nothing else imports this module.
- DecisionEngine.detect_language() is NOT called here — during onboarding
  the farmer hasn't sent any text yet; language comes from the button press.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Onboarding states
# ---------------------------------------------------------------------------
_STATE_NEW           = "NEW"
_STATE_AWAITING_LANG = "AWAITING_LANG"
_STATE_DONE          = "DONE"

# Callback prefix for language buttons — must not clash with expert prefix
LANG_CALLBACK_PREFIX = "set_lang:"

# ---------------------------------------------------------------------------
# Language catalogue
#
# Each entry:
#   code          BCP-47 code used by DecisionEngine / prompt_builder
#   button_label  Text shown on the Telegram InlineKeyboard button
#   native_name   Language name in that script (shown in the picker message)
# ---------------------------------------------------------------------------
LANGUAGES = [
    {"code": "en",       "button_label": "🇬🇧 English",    "native_name": "English"},
    {"code": "hi",       "button_label": "🇮🇳 हिंदी",       "native_name": "हिंदी"},
    {"code": "hinglish", "button_label": "🤙 Hinglish",    "native_name": "Hinglish"},
    {"code": "te",       "button_label": "🌾 తెలుగు",       "native_name": "తెలుగు"},
    {"code": "ta",       "button_label": "🌾 தமிழ்",        "native_name": "தமிழ்"},
    {"code": "kn",       "button_label": "🌾 ಕನ್ನಡ",        "native_name": "ಕನ್ನಡ"},
    {"code": "mr",       "button_label": "🌾 मराठी",        "native_name": "मराठी"},
    {"code": "gu",       "button_label": "🌾 ગુજરાતી",      "native_name": "ગુજરાતી"},
    {"code": "pa",       "button_label": "🌾 ਪੰਜਾਬੀ",       "native_name": "ਪੰਜਾਬੀ"},
    {"code": "bn",       "button_label": "🌾 বাংলা",        "native_name": "বাংলা"},
    {"code": "ml",       "button_label": "🌾 മലയാളം",       "native_name": "മലയാളം"},
    {"code": "or",       "button_label": "🌾 ଓଡ଼ିଆ",        "native_name": "ଓଡ଼ିଆ"},
]

# Build a fast lookup: code → entry
_LANG_BY_CODE: Dict[str, Dict] = {l["code"]: l for l in LANGUAGES}

# ---------------------------------------------------------------------------
# Translations
#
# Every string a farmer might see during onboarding is written here in the
# target language. No machine translation — these are natural phrases.
#
# Keys in each dict:
#   picker_prompt   Text sent with the language-picker keyboard
#   welcome         Full welcome message shown after language choice
#   help            /help message
# ---------------------------------------------------------------------------
_STRINGS: Dict[str, Dict[str, str]] = {

    "en": {
        "picker_prompt": (
            "🌾 Welcome to Krishi Kalyan!\n\n"
            "Please choose your language:"
        ),
        "welcome": (
            "🌾 Welcome to Krishi Kalyan!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 Crop Recommendation      │\n"
            "│ 💧 Irrigation Advice        │\n"
            "│ 🧪 Fertilizer Advice        │\n"
            "│ 🍂 Disease Diagnosis        │\n"
            "│ 👨‍🌾 Contact Expert          │\n"
            "└─────────────────────────────┘\n\n"
            "Send me:\n"
            "🎤 A voice message\n"
            "📷 A crop photo\n"
            "💬 A text question\n\n"
            "Type /help anytime to see this menu again."
        ),
        "help": (
            "🌾 Krishi Kalyan — Available Services\n\n"
            "🌱 Crop Recommendation\n"
            "   Example: Which crop should I grow?\n\n"
            "💧 Irrigation Advice\n"
            "   Example: How much water should I give my cotton crop?\n\n"
            "🧪 Fertilizer Recommendation\n"
            "   Example: Recommend fertilizer for my soil.\n\n"
            "🍂 Disease Diagnosis\n"
            "   Upload a crop photo.\n\n"
            "🎤 Voice Support\n"
            "   Send a voice message in your language.\n\n"
            "👨‍🌾 Expert Support\n"
            "   After every AI response, tap \"Send to Expert\"."
        ),
    },

    "hi": {
        "picker_prompt": (
            "🌾 कृषि कल्याण में आपका स्वागत है!\n\n"
            "कृपया अपनी भाषा चुनें:"
        ),
        "welcome": (
            "🌾 कृषि कल्याण में आपका स्वागत है!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 फसल की सिफारिश          │\n"
            "│ 💧 सिंचाई की सलाह           │\n"
            "│ 🧪 खाद की सिफारिश           │\n"
            "│ 🍂 रोग पहचान               │\n"
            "│ 👨‍🌾 विशेषज्ञ से संपर्क     │\n"
            "└─────────────────────────────┘\n\n"
            "मुझे भेजें:\n"
            "🎤 वॉइस मैसेज\n"
            "📷 फसल की फोटो\n"
            "💬 टेक्स्ट में सवाल\n\n"
            "कभी भी /help टाइप करें।"
        ),
        "help": (
            "🌾 कृषि कल्याण — उपलब्ध सेवाएं\n\n"
            "🌱 फसल की सिफारिश\n"
            "   उदाहरण: मुझे कौन सी फसल उगानी चाहिए?\n\n"
            "💧 सिंचाई की सलाह\n"
            "   उदाहरण: मेरी कपास को कितना पानी चाहिए?\n\n"
            "🧪 खाद की सिफारिश\n"
            "   उदाहरण: मेरी मिट्टी के लिए खाद बताएं।\n\n"
            "🍂 रोग पहचान\n"
            "   फसल की फोटो भेजें।\n\n"
            "🎤 आवाज़ सहायता\n"
            "   अपनी भाषा में वॉइस मैसेज भेजें।\n\n"
            "👨‍🌾 विशेषज्ञ सहायता\n"
            "   हर AI जवाब के बाद \"Send to Expert\" दबाएं।"
        ),
    },

    "hinglish": {
        "picker_prompt": (
            "🌾 Krishi Kalyan mein aapka swagat hai!\n\n"
            "Apni bhasha chunein:"
        ),
        "welcome": (
            "🌾 Krishi Kalyan mein aapka swagat hai!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 Fasal ki Salah           │\n"
            "│ 💧 Sinchai ki Advice        │\n"
            "│ 🧪 Khad ki Salah            │\n"
            "│ 🍂 Rog Pahchaan             │\n"
            "│ 👨‍🌾 Expert se Baat          │\n"
            "└─────────────────────────────┘\n\n"
            "Mujhe bhejein:\n"
            "🎤 Voice message\n"
            "📷 Fasal ki photo\n"
            "💬 Text mein sawaal\n\n"
            "Kabhi bhi /help type karein."
        ),
        "help": (
            "🌾 Krishi Kalyan — Upalabdh Sevaayein\n\n"
            "🌱 Fasal ki Salah\n"
            "   Example: Mujhe kaun si fasal ugaani chahiye?\n\n"
            "💧 Sinchai ki Advice\n"
            "   Example: Meri kapas ko kitna pani chahiye?\n\n"
            "🧪 Khad ki Salah\n"
            "   Example: Meri mitti ke liye khad batao.\n\n"
            "🍂 Rog Pahchaan\n"
            "   Fasal ki photo bhejein.\n\n"
            "🎤 Awaaz Sahayata\n"
            "   Apni bhasha mein voice message bhejein.\n\n"
            "👨‍🌾 Expert Sahayata\n"
            "   Har AI jawab ke baad \"Send to Expert\" dabayein."
        ),
    },

    "te": {
        "picker_prompt": (
            "🌾 కృషి కళ్యాణ్‌కు స్వాగతం!\n\n"
            "దయచేసి మీ భాషను ఎంచుకోండి:"
        ),
        "welcome": (
            "🌾 కృషి కళ్యాణ్‌కు స్వాగతం!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 పంట సిఫారసు              │\n"
            "│ 💧 నీటిపారుదల సలహా          │\n"
            "│ 🧪 ఎరువుల సిఫారసు           │\n"
            "│ 🍂 వ్యాధి నిర్ధారణ           │\n"
            "│ 👨‍🌾 నిపుణుడిని సంప్రదించండి │\n"
            "└─────────────────────────────┘\n\n"
            "నాకు పంపండి:\n"
            "🎤 వాయిస్ మెసేజ్\n"
            "📷 పంట ఫోటో\n"
            "💬 టెక్స్ట్ ప్రశ్న\n\n"
            "ఎప్పుడైనా /help టైప్ చేయండి."
        ),
        "help": (
            "🌾 కృషి కళ్యాణ్ — అందుబాటులో ఉన్న సేవలు\n\n"
            "🌱 పంట సిఫారసు\n"
            "   ఉదా: నేను ఏ పంట పండించాలి?\n\n"
            "💧 నీటిపారుదల సలహా\n"
            "   ఉదా: నా పత్తి పంటకు ఎంత నీరు కావాలి?\n\n"
            "🧪 ఎరువుల సిఫారసు\n"
            "   ఉదా: నా మట్టికి ఎరువు సూచించండి.\n\n"
            "🍂 వ్యాధి నిర్ధారణ\n"
            "   పంట ఫోటో అప్‌లోడ్ చేయండి.\n\n"
            "🎤 వాయిస్ సపోర్ట్\n"
            "   మీ భాషలో వాయిస్ మెసేజ్ పంపండి.\n\n"
            "👨‍🌾 నిపుణుడి సహాయం\n"
            "   ప్రతి AI సమాధానం తర్వాత \"Send to Expert\" నొక్కండి."
        ),
    },

    "ta": {
        "picker_prompt": (
            "🌾 கிருஷி கல்யாணுக்கு வரவேற்கிறோம்!\n\n"
            "உங்கள் மொழியை தேர்ந்தெடுக்கவும்:"
        ),
        "welcome": (
            "🌾 கிருஷி கல்யாணுக்கு வரவேற்கிறோம்!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 பயிர் பரிந்துரை          │\n"
            "│ 💧 நீர்ப்பாசன ஆலோசனை       │\n"
            "│ 🧪 உர பரிந்துரை             │\n"
            "│ 🍂 நோய் கண்டறிதல்           │\n"
            "│ 👨‍🌾 நிபுணரை தொடர்பு கொள்  │\n"
            "└─────────────────────────────┘\n\n"
            "எனக்கு அனுப்பவும்:\n"
            "🎤 குரல் செய்தி\n"
            "📷 பயிர் புகைப்படம்\n"
            "💬 உரை கேள்வி\n\n"
            "எப்போதும் /help தட்டச்சு செய்யலாம்."
        ),
        "help": (
            "🌾 கிருஷி கல்யாண் — கிடைக்கும் சேவைகள்\n\n"
            "🌱 பயிர் பரிந்துரை\n"
            "   உதா: நான் எந்த பயிர் வளர்க்கலாம்?\n\n"
            "💧 நீர்ப்பாசன ஆலோசனை\n"
            "   உதா: என் பருத்திக்கு எவ்வளவு நீர் தேவை?\n\n"
            "🧪 உர பரிந்துரை\n"
            "   உதா: என் மண்ணுக்கு உரம் பரிந்துரைக்கவும்.\n\n"
            "🍂 நோய் கண்டறிதல்\n"
            "   பயிர் புகைப்படம் பதிவேற்றவும்.\n\n"
            "🎤 குரல் ஆதரவு\n"
            "   உங்கள் மொழியில் குரல் செய்தி அனுப்பவும்.\n\n"
            "👨‍🌾 நிபுணர் ஆதரவு\n"
            "   ஒவ்வொரு AI பதிலுக்கும் \"Send to Expert\" அழுத்தவும்."
        ),
    },

    "kn": {
        "picker_prompt": (
            "🌾 ಕೃಷಿ ಕಲ್ಯಾಣಕ್ಕೆ ಸ್ವಾಗತ!\n\n"
            "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಭಾಷೆ ಆಯ್ಕೆ ಮಾಡಿ:"
        ),
        "welcome": (
            "🌾 ಕೃಷಿ ಕಲ್ಯಾಣಕ್ಕೆ ಸ್ವಾಗತ!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 ಬೆಳೆ ಶಿಫಾರಸು             │\n"
            "│ 💧 ನೀರಾವರಿ ಸಲಹೆ             │\n"
            "│ 🧪 ಗೊಬ್ಬರ ಶಿಫಾರಸು           │\n"
            "│ 🍂 ರೋಗ ಪತ್ತೆ                │\n"
            "│ 👨‍🌾 ತಜ್ಞರನ್ನು ಸಂಪರ್ಕಿಸಿ     │\n"
            "└─────────────────────────────┘\n\n"
            "ನನಗೆ ಕಳುಹಿಸಿ:\n"
            "🎤 ವಾಯ್ಸ್ ಸಂದೇಶ\n"
            "📷 ಬೆಳೆ ಫೋಟೋ\n"
            "💬 ಪಠ್ಯ ಪ್ರಶ್ನೆ\n\n"
            "ಯಾವಾಗಲಾದರೂ /help ಟೈಪ್ ಮಾಡಿ."
        ),
        "help": (
            "🌾 ಕೃಷಿ ಕಲ್ಯಾಣ — ಲಭ್ಯವಿರುವ ಸೇವೆಗಳು\n\n"
            "🌱 ಬೆಳೆ ಶಿಫಾರಸು\n"
            "   ಉದಾ: ನಾನು ಯಾವ ಬೆಳೆ ಬೆಳೆಯಬೇಕು?\n\n"
            "💧 ನೀರಾವರಿ ಸಲಹೆ\n"
            "   ಉದಾ: ನನ್ನ ಹತ್ತಿ ಬೆಳೆಗೆ ಎಷ್ಟು ನೀರು ಬೇಕು?\n\n"
            "🧪 ಗೊಬ್ಬರ ಶಿಫಾರಸು\n"
            "   ಉದಾ: ನನ್ನ ಮಣ್ಣಿಗೆ ಗೊಬ್ಬರ ಸೂಚಿಸಿ.\n\n"
            "🍂 ರೋಗ ಪತ್ತೆ\n"
            "   ಬೆಳೆ ಫೋಟೋ ಅಪ್‌ಲೋಡ್ ಮಾಡಿ.\n\n"
            "🎤 ಧ್ವನಿ ಬೆಂಬಲ\n"
            "   ನಿಮ್ಮ ಭಾಷೆಯಲ್ಲಿ ವಾಯ್ಸ್ ಸಂದೇಶ ಕಳುಹಿಸಿ.\n\n"
            "👨‍🌾 ತಜ್ಞ ಬೆಂಬಲ\n"
            "   ಪ್ರತಿ AI ಉತ್ತರದ ನಂತರ \"Send to Expert\" ಒತ್ತಿರಿ."
        ),
    },

    "mr": {
        "picker_prompt": (
            "🌾 कृषी कल्याणमध्ये आपले स्वागत आहे!\n\n"
            "कृपया आपली भाषा निवडा:"
        ),
        "welcome": (
            "🌾 कृषी कल्याणमध्ये आपले स्वागत आहे!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 पीक शिफारस               │\n"
            "│ 💧 सिंचन सल्ला               │\n"
            "│ 🧪 खत शिफारस                │\n"
            "│ 🍂 रोग निदान                │\n"
            "│ 👨‍🌾 तज्ञांशी संपर्क साधा    │\n"
            "└─────────────────────────────┘\n\n"
            "मला पाठवा:\n"
            "🎤 व्हॉइस मेसेज\n"
            "📷 पीकाचा फोटो\n"
            "💬 मजकूर प्रश्न\n\n"
            "कधीही /help टाइप करा."
        ),
        "help": (
            "🌾 कृषी कल्याण — उपलब्ध सेवा\n\n"
            "🌱 पीक शिफारस\n"
            "   उदा: मी कोणते पीक घ्यावे?\n\n"
            "💧 सिंचन सल्ला\n"
            "   उदा: माझ्या कापसाला किती पाणी लागते?\n\n"
            "🧪 खत शिफारस\n"
            "   उदा: माझ्या मातीसाठी खत सुचवा.\n\n"
            "🍂 रोग निदान\n"
            "   पीकाचा फोटो अपलोड करा.\n\n"
            "🎤 आवाज सहाय्य\n"
            "   आपल्या भाषेत व्हॉइस मेसेज पाठवा.\n\n"
            "👨‍🌾 तज्ञ सहाय्य\n"
            "   प्रत्येक AI उत्तरानंतर \"Send to Expert\" दाबा."
        ),
    },

    "gu": {
        "picker_prompt": (
            "🌾 કૃષિ કલ્યાણમાં આપનું સ્વાગત છે!\n\n"
            "કૃપા કરીને તમારી ભાષા પસંદ કરો:"
        ),
        "welcome": (
            "🌾 કૃષિ કલ્યાણમાં આપનું સ્વાગત છે!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 પાક ભલામણ               │\n"
            "│ 💧 સિંચાઈ સલાહ              │\n"
            "│ 🧪 ખાતર ભલામણ              │\n"
            "│ 🍂 રોગ નિદાન               │\n"
            "│ 👨‍🌾 નિષ્ણાતનો સંપર્ક       │\n"
            "└─────────────────────────────┘\n\n"
            "મને મોકલો:\n"
            "🎤 વૉઇસ મેસેજ\n"
            "📷 પાકનો ફોટો\n"
            "💬 ટેક્સ્ટ પ્રશ્ન\n\n"
            "ગમે ત્યારે /help ટાઇપ કરો."
        ),
        "help": (
            "🌾 કૃષિ કલ્યાણ — ઉપલબ્ધ સેવાઓ\n\n"
            "🌱 પાક ભલામણ\n"
            "   ઉદા: મારે કયો પાક ઉગાડવો જોઈએ?\n\n"
            "💧 સિંચાઈ સલાહ\n"
            "   ઉદા: મારા કપાસને કેટલું પાણી જોઈએ?\n\n"
            "🧪 ખાતર ભલામણ\n"
            "   ઉદા: મારી જમીન માટે ખાતર સૂચવો.\n\n"
            "🍂 રોગ નિદાન\n"
            "   પાકનો ફોટો અપલોડ કરો.\n\n"
            "🎤 વૉઇસ સપોર્ટ\n"
            "   તમારી ભાષામાં વૉઇસ મેસેજ મોકલો.\n\n"
            "👨‍🌾 નિષ્ણાત સહાય\n"
            "   દરેક AI જવાબ પછી \"Send to Expert\" દબાવો."
        ),
    },

    "pa": {
        "picker_prompt": (
            "🌾 ਕ੍ਰਿਸ਼ੀ ਕਲਿਆਣ ਵਿੱਚ ਤੁਹਾਡਾ ਸੁਆਗਤ ਹੈ!\n\n"
            "ਕਿਰਪਾ ਕਰਕੇ ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ:"
        ),
        "welcome": (
            "🌾 ਕ੍ਰਿਸ਼ੀ ਕਲਿਆਣ ਵਿੱਚ ਤੁਹਾਡਾ ਸੁਆਗਤ ਹੈ!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 ਫ਼ਸਲ ਸਿਫ਼ਾਰਸ਼            │\n"
            "│ 💧 ਸਿੰਚਾਈ ਸਲਾਹ             │\n"
            "│ 🧪 ਖਾਦ ਸਿਫ਼ਾਰਸ਼             │\n"
            "│ 🍂 ਬਿਮਾਰੀ ਪਛਾਣ             │\n"
            "│ 👨‍🌾 ਮਾਹਰ ਨਾਲ ਸੰਪਰਕ         │\n"
            "└─────────────────────────────┘\n\n"
            "ਮੈਨੂੰ ਭੇਜੋ:\n"
            "🎤 ਵੌਇਸ ਮੈਸੇਜ\n"
            "📷 ਫ਼ਸਲ ਦੀ ਫੋਟੋ\n"
            "💬 ਟੈਕਸਟ ਸਵਾਲ\n\n"
            "ਕਦੇ ਵੀ /help ਟਾਈਪ ਕਰੋ।"
        ),
        "help": (
            "🌾 ਕ੍ਰਿਸ਼ੀ ਕਲਿਆਣ — ਉਪਲਬਧ ਸੇਵਾਵਾਂ\n\n"
            "🌱 ਫ਼ਸਲ ਸਿਫ਼ਾਰਸ਼\n"
            "   ਉਦਾਹਰਨ: ਮੈਨੂੰ ਕਿਹੜੀ ਫ਼ਸਲ ਉਗਾਉਣੀ ਚਾਹੀਦੀ ਹੈ?\n\n"
            "💧 ਸਿੰਚਾਈ ਸਲਾਹ\n"
            "   ਉਦਾਹਰਨ: ਮੇਰੀ ਕਪਾਹ ਨੂੰ ਕਿੰਨਾ ਪਾਣੀ ਚਾਹੀਦਾ ਹੈ?\n\n"
            "🧪 ਖਾਦ ਸਿਫ਼ਾਰਸ਼\n"
            "   ਉਦਾਹਰਨ: ਮੇਰੀ ਮਿੱਟੀ ਲਈ ਖਾਦ ਦੱਸੋ।\n\n"
            "🍂 ਬਿਮਾਰੀ ਪਛਾਣ\n"
            "   ਫ਼ਸਲ ਦੀ ਫੋਟੋ ਅਪਲੋਡ ਕਰੋ।\n\n"
            "🎤 ਵੌਇਸ ਸਹਾਇਤਾ\n"
            "   ਆਪਣੀ ਭਾਸ਼ਾ ਵਿੱਚ ਵੌਇਸ ਮੈਸੇਜ ਭੇਜੋ।\n\n"
            "👨‍🌾 ਮਾਹਰ ਸਹਾਇਤਾ\n"
            "   ਹਰ AI ਜਵਾਬ ਤੋਂ ਬਾਅਦ \"Send to Expert\" ਦਬਾਓ।"
        ),
    },

    "bn": {
        "picker_prompt": (
            "🌾 কৃষি কল্যাণে আপনাকে স্বাগতম!\n\n"
            "অনুগ্রহ করে আপনার ভাষা বেছে নিন:"
        ),
        "welcome": (
            "🌾 কৃষি কল্যাণে আপনাকে স্বাগতম!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 ফসল পরামর্শ              │\n"
            "│ 💧 সেচ পরামর্শ              │\n"
            "│ 🧪 সার পরামর্শ              │\n"
            "│ 🍂 রোগ নির্ণয়              │\n"
            "│ 👨‍🌾 বিশেষজ্ঞের সাথে যোগাযোগ │\n"
            "└─────────────────────────────┘\n\n"
            "আমাকে পাঠান:\n"
            "🎤 ভয়েস মেসেজ\n"
            "📷 ফসলের ছবি\n"
            "💬 টেক্সট প্রশ্ন\n\n"
            "যেকোনো সময় /help টাইপ করুন।"
        ),
        "help": (
            "🌾 কৃষি কল্যাণ — উপলব্ধ সেবাসমূহ\n\n"
            "🌱 ফসল পরামর্শ\n"
            "   উদা: আমার কোন ফসল চাষ করা উচিত?\n\n"
            "💧 সেচ পরামর্শ\n"
            "   উদা: আমার তুলায় কতটা পানি দরকার?\n\n"
            "🧪 সার পরামর্শ\n"
            "   উদা: আমার মাটির জন্য সার সুপারিশ করুন।\n\n"
            "🍂 রোগ নির্ণয়\n"
            "   ফসলের ছবি আপলোড করুন।\n\n"
            "🎤 ভয়েস সহায়তা\n"
            "   আপনার ভাষায় ভয়েস মেসেজ পাঠান।\n\n"
            "👨‍🌾 বিশেষজ্ঞ সহায়তা\n"
            "   প্রতিটি AI উত্তরের পরে \"Send to Expert\" চাপুন।"
        ),
    },

    "ml": {
        "picker_prompt": (
            "🌾 കൃഷി കല്യാണിലേക്ക് സ്വാഗതം!\n\n"
            "ദയവായി നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക:"
        ),
        "welcome": (
            "🌾 കൃഷി കല്യാണിലേക്ക് സ്വാഗതം!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 വിള ശുപാർശ               │\n"
            "│ 💧 ജലസേചന ഉപദേശം           │\n"
            "│ 🧪 വളം ശുപാർശ               │\n"
            "│ 🍂 രോഗ നിർണ്ണയം             │\n"
            "│ 👨‍🌾 വിദഗ്ധനെ ബന്ധപ്പെടുക   │\n"
            "└─────────────────────────────┘\n\n"
            "എനിക്ക് അയക്കൂ:\n"
            "🎤 വോയ്‌സ് മെസേജ്\n"
            "📷 വിളയുടെ ഫോട്ടോ\n"
            "💬 ടെക്‌സ്റ്റ് ചോദ്യം\n\n"
            "എപ്പോഴും /help ടൈപ്പ് ചെയ്യാം."
        ),
        "help": (
            "🌾 കൃഷി കല്യാൺ — ലഭ്യമായ സേവനങ്ങൾ\n\n"
            "🌱 വിള ശുപാർശ\n"
            "   ഉദാ: ഞാൻ ഏത് വിള കൃഷി ചെയ്യണം?\n\n"
            "💧 ജലസേചന ഉപദേശം\n"
            "   ഉദാ: എന്റെ പഞ്ഞിക്ക് എത്ര വെള്ളം വേണം?\n\n"
            "🧪 വളം ശുപാർശ\n"
            "   ഉദാ: എന്റെ മണ്ണിന് അനുയോജ്യമായ വളം നിർദ്ദേശിക്കൂ.\n\n"
            "🍂 രോഗ നിർണ്ണയം\n"
            "   വിളയുടെ ഫോട്ടോ അപ്‌ലോഡ് ചെയ്യൂ.\n\n"
            "🎤 വോയ്‌സ് സഹായം\n"
            "   നിങ്ങളുടെ ഭാഷയിൽ വോയ്‌സ് മെസേജ് അയക്കൂ.\n\n"
            "👨‍🌾 വിദഗ്ധ സഹായം\n"
            "   ഓരോ AI മറുപടിക്കും ശേഷം \"Send to Expert\" അമർത്തൂ."
        ),
    },

    "or": {
        "picker_prompt": (
            "🌾 କୃଷି କଲ୍ୟାଣରେ ଆପଣଙ୍କୁ ସ୍ୱାଗତ!\n\n"
            "ଦୟାକରି ଆପଣଙ୍କ ଭାଷା ବାଛନ୍ତୁ:"
        ),
        "welcome": (
            "🌾 କୃଷି କଲ୍ୟାଣରେ ଆପଣଙ୍କୁ ସ୍ୱାଗତ!\n"
            "┌─────────────────────────────┐\n"
            "│ 🌱 ଫସଲ ସୁପାରିଶ              │\n"
            "│ 💧 ଜଳସେଚନ ପରାମର୍ଶ           │\n"
            "│ 🧪 ଖତ ସୁପାରିଶ               │\n"
            "│ 🍂 ରୋଗ ନିର୍ଣ୍ଣୟ              │\n"
            "│ 👨‍🌾 ବିଶେଷଜ୍ଞଙ୍କ ସହ ଯୋଗାଯୋଗ │\n"
            "└─────────────────────────────┘\n\n"
            "ମୋତେ ପଠାନ୍ତୁ:\n"
            "🎤 ଭଏସ୍ ମେସେଜ\n"
            "📷 ଫସଲ ଫଟୋ\n"
            "💬 ପାଠ୍ୟ ପ୍ରଶ୍ନ\n\n"
            "ଯେକୌଣସି ସମୟରେ /help ଟାଇପ୍ କରନ୍ତୁ।"
        ),
        "help": (
            "🌾 କୃଷି କଲ୍ୟାଣ — ଉପଲବ୍ଧ ସେବା\n\n"
            "🌱 ଫସଲ ସୁପାରିଶ\n"
            "   ଉଦା: ମୁଁ କେଉଁ ଫସଲ ଚାଷ କରିବି?\n\n"
            "💧 ଜଳସେଚନ ପରାମର୍ଶ\n"
            "   ଉଦା: ମୋ କପାସ ପାଇଁ କେତେ ପାଣି ଦରକାର?\n\n"
            "🧪 ଖତ ସୁପାରିଶ\n"
            "   ଉଦା: ମୋ ମାଟି ପାଇଁ ଖତ ପରାମର୍ଶ ଦିଅନ୍ତୁ।\n\n"
            "🍂 ରୋଗ ନିର୍ଣ୍ଣୟ\n"
            "   ଫସଲ ଫଟୋ ଅପ୍‌ଲୋଡ୍ କରନ୍ତୁ।\n\n"
            "🎤 ଭଏସ୍ ସହାୟତା\n"
            "   ଆପଣଙ୍କ ଭାଷାରେ ଭଏସ୍ ମେସେଜ ପଠାନ୍ତୁ।\n\n"
            "👨‍🌾 ବିଶେଷଜ୍ଞ ସହାୟତା\n"
            "   ପ୍ରତ୍ୟେକ AI ଉତ୍ତର ପରେ \"Send to Expert\" ଦାବନ୍ତୁ।"
        ),
    },
}


# ---------------------------------------------------------------------------
# OnboardingService
# ---------------------------------------------------------------------------

class OnboardingService:
    """
    Tracks per-chat onboarding state and builds all Telegram API payloads
    for the language picker and welcome menu.
    """

    def __init__(self) -> None:
        # chat_id (int) → {"state": str, "language": str | None}
        self._store: Dict[int, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def _get(self, chat_id: int) -> Dict[str, Any]:
        return self._store.setdefault(chat_id, {"state": _STATE_NEW, "language": None})

    def is_done(self, chat_id: int) -> bool:
        return self._get(chat_id)["state"] == _STATE_DONE

    def needs_language(self, chat_id: int) -> bool:
        return self._get(chat_id)["state"] in (_STATE_NEW, _STATE_AWAITING_LANG)

    def get_language(self, chat_id: int) -> Optional[str]:
        return self._get(chat_id).get("language")

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def reset(self, chat_id: int) -> None:
        """Called on /start — always restart onboarding."""
        self._store[chat_id] = {"state": _STATE_NEW, "language": None}
        logger.info("Onboarding reset chat_id=%s", chat_id)

    def mark_awaiting(self, chat_id: int) -> None:
        self._get(chat_id)["state"] = _STATE_AWAITING_LANG
        logger.info("Onboarding awaiting language chat_id=%s", chat_id)

    def complete(self, chat_id: int, lang: str) -> None:
        entry = self._get(chat_id)
        entry["state"]    = _STATE_DONE
        entry["language"] = lang
        logger.info("Onboarding complete chat_id=%s language=%s", chat_id, lang)

    # ------------------------------------------------------------------
    # Telegram API payload builders
    # ------------------------------------------------------------------

    def language_picker_payload(self, chat_id: int) -> Dict[str, Any]:
        """
        Returns a sendMessage payload with the language-picker InlineKeyboard.
        Buttons are arranged in 2 columns for readability on mobile.
        """
        # Build rows of 2 buttons each
        buttons = [
            {"text": lang["button_label"], "callback_data": f"{LANG_CALLBACK_PREFIX}{lang['code']}"}
            for lang in LANGUAGES
        ]
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]

        return {
            "chat_id": chat_id,
            "text": _STRINGS["en"]["picker_prompt"],   # English — farmer hasn't chosen yet
            "reply_markup": {"inline_keyboard": rows},
        }

    def welcome_payload(self, chat_id: int, lang: str) -> Dict[str, Any]:
        """Returns a plain sendMessage payload for the welcome menu."""
        strings = _STRINGS.get(lang, _STRINGS["en"])
        return {
            "chat_id": chat_id,
            "text": strings["welcome"],
        }

    def help_payload(self, chat_id: int) -> Dict[str, Any]:
        """Returns a plain sendMessage payload for the /help menu."""
        lang = self.get_language(chat_id) or "en"
        strings = _STRINGS.get(lang, _STRINGS["en"])
        return {
            "chat_id": chat_id,
            "text": strings["help"],
        }


onboarding_service = OnboardingService()