"""
app/services/registration_service.py

Farmer Registration Service for Krishi Kalyan.

Responsibilities
----------------
1. Track each chat's registration step in memory (per-chat state machine).
2. Ask one question at a time, validate each answer, re-prompt on bad input.
3. Accumulate answers in a temporary in-memory dict.
4. On completion, write two Firestore documents:
     users/{telegram_id}   — identity + location + language
     farms/{telegram_id}   — crop + land + soil + irrigation
5. Expose a minimal, clean API to telegram.py:
     registration_service.is_registered(chat_id)          → bool
     registration_service.is_in_progress(chat_id)         → bool
     registration_service.start(chat_id, language)         → str  (first question)
     registration_service.handle_answer(chat_id, text)    → RegistrationResult

Design principles
-----------------
- Zero Gemini calls during registration.
- telegram.py only delegates; zero registration logic lives there.
- Storage is in-memory, keyed by chat_id (int). On server restart farmers
  re-register — acceptable for this deployment model.
- Firestore writes happen through firestore_service (same client used everywhere).
- All validation is in _STEPS; adding a new field = adding one dict entry.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from app.core.exceptions import FirestoreException
from app.database.firestore import firestore_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step definitions
#
# Each step is a dict with:
#   key         Field name in the accumulated answers dict.
#   prompt      Question text sent to the farmer (plain English — telegram.py
#               could translate this in future; for now English is fine since
#               the farmer already passed language-selection onboarding).
#   validator   Optional callable(str) → (bool, str).
#               Returns (True, cleaned_value) or (False, error_message).
#               If None the raw stripped text is accepted as-is.
# ---------------------------------------------------------------------------

def _validate_phone(value: str):
    """Accept Indian (+91XXXXXXXXXX) or local (10-digit) phone numbers."""
    cleaned = re.sub(r"[\s\-\(\)]", "", value)  # strip spaces/dashes/parens
    # Allow +91... or 91... or bare 10-digit number
    if re.match(r"^(\+91|91)?[6-9]\d{9}$", cleaned):
        # Normalise to +91XXXXXXXXXX
        digits = re.sub(r"^\+?91", "", cleaned)
        return True, f"+91{digits}"
    return False, (
        "⚠️ Please enter a valid 10-digit Indian mobile number.\n"
        "Example: 9876543210 or +919876543210"
    )


def _validate_land_size(value: str):
    """Accept a positive number (int or float) representing acres."""
    try:
        size = float(value.replace(",", "."))
        if size <= 0:
            raise ValueError
        # Round to 2 decimal places for clean storage
        return True, round(size, 2)
    except ValueError:
        return False, (
            "⚠️ Please enter a valid land size in acres.\n"
            "Example: 2  or  0.5  or  2.5"
        )


def _validate_non_empty(value: str):
    """Reject blank answers."""
    if value.strip():
        return True, value.strip()
    return False, "⚠️ This field cannot be empty. Please type your answer."


# Registration steps in order.
# Each step is executed sequentially; telegram.py just calls handle_answer().
_STEPS: List[Dict[str, Any]] = [
    {
        "key": "name",
        "prompt": "👤 Please enter your *full name*:",
        "validator": _validate_non_empty,
    },
    {
        "key": "phone",
        "prompt": (
            "📱 Please enter your *mobile number*:\n"
            "_(10-digit Indian number, e.g. 9876543210)_"
        ),
        "validator": _validate_phone,
    },
    {
        "key": "state",
        "prompt": "🗺️ Which *state* do you farm in?\n_(e.g. Telangana, Maharashtra, Punjab)_",
        "validator": _validate_non_empty,
    },
    {
        "key": "district",
        "prompt": "📍 Which *district*?\n_(e.g. Karimnagar, Nashik, Ludhiana)_",
        "validator": _validate_non_empty,
    },
    {
        "key": "village",
        "prompt": "🏘️ Which *village* or town?",
        "validator": _validate_non_empty,
    },
    {
        "key": "current_crop",
        "prompt": "🌾 What is your *current crop*?\n_(e.g. Cotton, Wheat, Rice, Tomato)_",
        "validator": _validate_non_empty,
    },
    {
        "key": "land_size",
        "prompt": (
            "📐 How many *acres* of land do you farm?\n"
            "_(Enter a number, e.g. 2 or 1.5)_"
        ),
        "validator": _validate_land_size,
    },
    {
        "key": "soil_type",
        "prompt": (
            "🟫 What is your *soil type*?\n"
            "_(e.g. Black Cotton Soil, Red Soil, Sandy Loam, Clay)_"
        ),
        "validator": _validate_non_empty,
    },
    {
        "key": "irrigation_type",
        "prompt": (
            "💧 What *irrigation method* do you use?\n"
            "_(e.g. Drip, Sprinkler, Flood, Rainfed)_"
        ),
        "validator": _validate_non_empty,
    },
]

# Total number of registration steps (used for progress display)
_TOTAL_STEPS = len(_STEPS)

# Success message sent after Firestore write
_SUCCESS_MESSAGE = (
    "✅ Registration completed successfully.\n\n"
    "🌾 Welcome to Krishi Kalyan!\n\n"
    "You can now ask farming questions using text, voice, or images."
)


# ---------------------------------------------------------------------------
# Registration states
# ---------------------------------------------------------------------------

class _RegState(Enum):
    NOT_STARTED   = auto()   # No /start received yet (or fully registered)
    IN_PROGRESS   = auto()   # Actively stepping through questions
    COMPLETE      = auto()   # Firestore saved; normal AI flow active


# ---------------------------------------------------------------------------
# RegistrationResult — returned by handle_answer() so telegram.py never
# needs to inspect internal state directly.
# ---------------------------------------------------------------------------

@dataclass
class RegistrationResult:
    """
    Encapsulates the outcome of processing one farmer answer.

    Attributes
    ----------
    reply           Text to send back to the farmer. Always non-empty.
    is_complete     True only when registration just finished (Firestore saved).
    error           True when the answer was invalid (reply contains the
                    error message + re-prompt).
    """
    reply:       str
    is_complete: bool = False
    error:       bool = False


# ---------------------------------------------------------------------------
# Per-chat session
# ---------------------------------------------------------------------------

@dataclass
class _Session:
    state:        _RegState             = _RegState.NOT_STARTED
    step_index:   int                   = 0       # index into _STEPS
    answers:      Dict[str, Any]        = field(default_factory=dict)
    language:     str                   = "en"


# ---------------------------------------------------------------------------
# RegistrationService
# ---------------------------------------------------------------------------

class RegistrationService:
    """
    Manages farmer registration state for all active chats.

    The only public callers are telegram.py.
    Firestore access goes through firestore_service (existing singleton).
    """

    def __init__(self, db_service=None) -> None:
        self._db = db_service or firestore_service
        # chat_id (int) → _Session
        self._sessions: Dict[int, _Session] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self, chat_id: int) -> _Session:
        """Return the session for chat_id, creating a blank one if absent."""
        if chat_id not in self._sessions:
            self._sessions[chat_id] = _Session()
        return self._sessions[chat_id]

    def _current_step(self, session: _Session) -> Dict[str, Any]:
        return _STEPS[session.step_index]

    def _progress_line(self, session: _Session) -> str:
        """Small breadcrumb so farmers know how far they are."""
        return f"_Step {session.step_index + 1} of {_TOTAL_STEPS}_\n\n"

    def _prompt_for_step(self, session: _Session) -> str:
        step = self._current_step(session)
        return self._progress_line(session) + step["prompt"]

    # ------------------------------------------------------------------
    # Public state queries (used by telegram.py guard clauses)
    # ------------------------------------------------------------------

    def is_registered(self, chat_id: int) -> bool:
        """
        True when registration is fully complete for this chat.
        This is the gate that allows the normal AI flow to proceed.
        """
        return self._get_session(chat_id).state == _RegState.COMPLETE

    def is_in_progress(self, chat_id: int) -> bool:
        """
        True while the farmer is still answering registration questions.
        telegram.py routes all incoming text here when this is True.
        """
        return self._get_session(chat_id).state == _RegState.IN_PROGRESS

    # ------------------------------------------------------------------
    # start()  — called from telegram.py after language selection
    # ------------------------------------------------------------------

    def start(self, chat_id: int, language: str = "en") -> str:
        """
        Initialise (or re-initialise) registration for chat_id.

        Parameters
        ----------
        chat_id  : Telegram chat / user ID.
        language : BCP-47 code chosen by the farmer during onboarding.

        Returns
        -------
        The text of the first registration question.
        """
        session = _Session(
            state=_RegState.IN_PROGRESS,
            step_index=0,
            answers={},
            language=language,
        )
        self._sessions[chat_id] = session
        logger.info("Registration started chat_id=%s language=%s", chat_id, language)
        return (
            "📋 *Let's set up your farmer profile.*\n"
            "This will only take a moment.\n\n"
            + self._prompt_for_step(session)
        )

    # ------------------------------------------------------------------
    # handle_answer()  — called from telegram.py for every message while
    #                    is_in_progress() is True.
    # ------------------------------------------------------------------

    def handle_answer(self, chat_id: int, text: str) -> RegistrationResult:
        """
        Process the farmer's answer to the current registration question.

        Flow
        ----
        1. Validate the answer using the current step's validator.
        2. On failure → return an error RegistrationResult; step does NOT advance.
        3. On success → store the cleaned value; advance step index.
        4. If more steps remain → return the next question.
        5. If all steps done → write Firestore docs; return success message.

        Parameters
        ----------
        chat_id : Telegram chat / user ID.
        text    : Raw text the farmer typed.

        Returns
        -------
        RegistrationResult — telegram.py sends result.reply and checks
        result.is_complete to decide whether to unlock the AI flow.
        """
        session = self._get_session(chat_id)

        if session.state != _RegState.IN_PROGRESS:
            # Safety guard: should never happen, but handle gracefully.
            logger.warning(
                "handle_answer called on non-in-progress session chat_id=%s state=%s",
                chat_id, session.state,
            )
            return RegistrationResult(
                reply="Please type /start to begin.",
                error=True,
            )

        step = self._current_step(session)
        raw_value = (text or "").strip()

        # ------------------------------------------------------------------
        # Validate
        # ------------------------------------------------------------------
        validator = step.get("validator")
        if validator:
            valid, result_value = validator(raw_value)
            if not valid:
                # result_value is the error message string
                error_text = (
                    result_value
                    + "\n\n"
                    + self._prompt_for_step(session)
                )
                logger.info(
                    "Validation failed chat_id=%s step=%s input=%r",
                    chat_id, step["key"], raw_value,
                )
                return RegistrationResult(reply=error_text, error=True)
        else:
            # No validator — accept as-is
            result_value = raw_value

        # ------------------------------------------------------------------
        # Store validated answer
        # ------------------------------------------------------------------
        session.answers[step["key"]] = result_value
        logger.info(
            "Registration step saved chat_id=%s step=%s value=%r",
            chat_id, step["key"], result_value,
        )

        # ------------------------------------------------------------------
        # Advance
        # ------------------------------------------------------------------
        session.step_index += 1

        if session.step_index < _TOTAL_STEPS:
            # More questions remain
            next_prompt = self._prompt_for_step(session)
            return RegistrationResult(reply=next_prompt)

        # ------------------------------------------------------------------
        # All steps complete → save to Firestore
        # ------------------------------------------------------------------
        try:
            self._save_to_firestore(chat_id, session)
        except FirestoreException as exc:
            logger.exception(
                "Firestore save failed during registration chat_id=%s", chat_id
            )
            # Reset step so farmer can retry the last save (not re-enter all data)
            session.step_index = _TOTAL_STEPS - 1
            return RegistrationResult(
                reply=(
                    "❌ We couldn't save your profile right now.\n"
                    "Please try again in a moment by typing your last answer again.\n\n"
                    f"Error details: {exc.message}"
                ),
                error=True,
            )

        # Mark complete in memory
        session.state = _RegState.COMPLETE
        logger.info("Registration complete chat_id=%s", chat_id)

        return RegistrationResult(reply=_SUCCESS_MESSAGE, is_complete=True)

    # ------------------------------------------------------------------
    # reset()  — called from /start so that existing farmers can re-register
    #            (or a crashed session can be restarted without a server restart)
    # ------------------------------------------------------------------

    def reset(self, chat_id: int) -> None:
        """
        Clear the registration session for chat_id.
        Called by telegram.py when /start is received.
        """
        self._sessions[chat_id] = _Session()
        logger.info("Registration session reset chat_id=%s", chat_id)

    # ------------------------------------------------------------------
    # _save_to_firestore()  — internal; called once after final answer
    # ------------------------------------------------------------------

    def _save_to_firestore(self, chat_id: int, session: _Session) -> None:
        """
        Write:
          users/{chat_id}   — identity doc
          farms/{chat_id}   — farm doc

        Both use the Telegram chat_id (as a string) as the Firestore document ID.
        This matches the key used by ContextBuilder.get_user() and get_farm().
        """
        doc_id = str(chat_id)
        answers = session.answers

        # ----------------------------------------------------------------
        # users/{telegram_id}
        # ----------------------------------------------------------------
        user_doc: Dict[str, Any] = {
            "telegram_id": chat_id,           # int  — matches Telegram user ID
            "name":        answers.get("name", ""),
            "phone":       answers.get("phone", ""),
            "state":       answers.get("state", ""),
            "district":    answers.get("district", ""),
            "village":     answers.get("village", ""),
            "language":    session.language,
            "created_at":  SERVER_TIMESTAMP,
            "is_registered": True,
        }

        try:
            self._db.client.collection("users").document(doc_id).set(user_doc)
            logger.info("User document saved users/%s", doc_id)
        except Exception as exc:
            logger.exception("Failed to save user document users/%s", doc_id)
            raise FirestoreException(
                message="Failed to save user profile",
                details={"doc_id": doc_id, "type": exc.__class__.__name__},
            ) from exc

        # ----------------------------------------------------------------
        # farms/{telegram_id}
        # ----------------------------------------------------------------
        farm_doc: Dict[str, Any] = {
            "user_id":        doc_id,               # str — links back to users/
            "current_crop":   answers.get("current_crop", ""),
            "land_size":      answers.get("land_size", 0.0),   # float (acres)
            "soil_type":      answers.get("soil_type", ""),
            "irrigation_type": answers.get("irrigation_type", ""),
            # district is copied here so ContextBuilder._resolve_district()
            # finds it even when the user doc hasn't been fully loaded yet.
            "district":       answers.get("district", ""),
            "village":        answers.get("village", ""),
            "created_at":     SERVER_TIMESTAMP,
        }

        try:
            self._db.client.collection("farms").document(doc_id).set(farm_doc)
            logger.info("Farm document saved farms/%s", doc_id)
        except Exception as exc:
            logger.exception("Failed to save farm document farms/%s", doc_id)
            raise FirestoreException(
                message="Failed to save farm profile",
                details={"doc_id": doc_id, "type": exc.__class__.__name__},
            ) from exc

        logger.info(
            "Firestore registration save complete "
            "users/%s farms/%s language=%s crop=%s",
            doc_id, doc_id, session.language, answers.get("current_crop"),
        )


# Module-level singleton — imported by telegram.py
registration_service = RegistrationService()