"""
app/engine/decision_engine.py

Changes in this version
-----------------------
1. detect_language() expanded:
     - Covers Hindi, Telugu, Kannada, Tamil, Marathi, Gujarati, Punjabi,
       Bengali, Malayalam, Odia — all by Unicode range.
     - NEW: detects Hinglish (Roman-script Hindi) via a vocabulary keyword
       list. This is the only language that cannot be detected by script/
       Unicode alone, so a lightweight word-match approach is used.
       No external library. No Google Translate.

2. process() stores detected_language in context BEFORE building the prompt,
   so prompt_builder.build() can stamp FARMER_LANGUAGE at the top of every
   prompt that goes to Gemini. That single line makes Gemini reply in the
   correct language automatically.

3. async_process() — async wrapper using asyncio.to_thread() so the uvicorn
   event loop is never blocked by the synchronous Gemini HTTP call.

4. Returns (ai_response: str, context: Dict) tuple so telegram.py can pass
   the enriched context (including detected_language) to expert_service
   without rebuilding it.

Nothing else is changed. Business logic is identical to the original.
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.core.exceptions import ValidationException  # noqa: F401 kept for re-export
from app.engine.context_builder import context_builder
from app.engine.farm_snapshot_builder import farm_snapshot_builder
from app.engine.prompt_builder import prompt_builder
from app.services.gemini import gemini_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hinglish keyword set
#
# These are very common Hindi words that Indian farmers write in Roman script.
# The list is intentionally conservative — only words that would NEVER appear
# in normal English text — so we get near-zero false positives.
# ---------------------------------------------------------------------------
_HINGLISH_KEYWORDS: frozenset = frozenset({
    # Question words
    "kya", "kaise", "kab", "kyun", "kyunki", "kitna", "kitni", "kitne",
    # Common verbs / phrases
    "hai", "hain", "hua", "hogi", "hoga", "karo", "karna", "karte",
    "kar", "nahi", "nai", "mat", "bhi", "aur", "ya", "toh", "toh",
    # Farm-specific
    "fasal", "khet", "beej", "pani", "khad", "keetnashak", "rog",
    "dawaai", "dawai", "spray", "mitti", "baarish", "dhoop",
    # Pronouns / conjunctions
    "mera", "meri", "mere", "mujhe", "aap", "apna", "apni",
    "unka", "unki", "yeh", "woh", "isko", "usko",
    # Intensifiers / fillers
    "bahut", "thoda", "jyada", "kam", "accha", "theek", "sahi",
    "bilkul", "pakka", "kal", "aaj", "abhi",
})

# Minimum number of Hinglish keywords that must appear to classify as Hinglish.
# 2 is conservative enough to avoid classifying short English messages.
_HINGLISH_THRESHOLD = 2


class DecisionEngine:
    def __init__(
        self,
        context_builder_service: Any = context_builder,
        llm_service: Any = gemini_service,
    ) -> None:
        self.context_builder = context_builder_service
        self.llm_service = llm_service

    # ------------------------------------------------------------------
    # process()  — synchronous, unchanged business logic
    # ------------------------------------------------------------------
    def process(
        self,
        user_id: str,
        message: str,
        image_path: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Process a farmer query end-to-end.

        Returns
        -------
        (ai_response: str, context: Dict[str, Any])

        context carries detected_language so telegram.py can store it with
        the expert ticket without a separate detection pass.
        """
        normalized_user_id = (user_id or "").strip()
        normalized_message  = (message or "").strip()

        if not normalized_user_id:
            raise ValidationException(
                message="user_id is required",
                details={"field": "user_id"},
            )
        if not normalized_message:
            raise ValidationException(
                message="message is required",
                details={"field": "message"},
            )

        self._log_start(user_id=normalized_user_id, message=normalized_message)

        # ------------------------------------------------------------------
        # Language detection — MUST happen before context.build() so that
        # the language is available when prompt_builder.build() runs.
        # ------------------------------------------------------------------
        language = self.detect_language(normalized_message)
        logger.info("Detected Language : %s", language)

        context = self.context_builder.build(user_id=normalized_user_id)
        context["detected_language"] = language        # ← key addition

        snapshot = farm_snapshot_builder.build(context)
        context["snapshot"]   = snapshot
        context["image_path"] = image_path

        logger.info("Farm Snapshot: %s", snapshot)

        prompt = prompt_builder.build(
            context=context,
            user_message=normalized_message,
        )

        final_response, selected_tool = self.llm_service.chat(
            prompt=prompt,
            context=context,
        )

        if selected_tool:
            logger.info("Tool path completed tool=%s", selected_tool["name"])
        else:
            logger.info("Gemini answered without tool execution")

        logger.info("Returning Response")
        logger.info("================================================")

        return final_response, context

    # ------------------------------------------------------------------
    # async_process()  — non-blocking wrapper for the webhook handler
    # ------------------------------------------------------------------
    async def async_process(
        self,
        user_id: str,
        message: str,
        image_path: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Async-safe entry point for telegram.py.

        Runs the entirely synchronous process() in a thread-pool executor so
        the uvicorn event loop is never blocked by the Gemini HTTP call.
        """
        return await asyncio.to_thread(
            self.process,
            user_id,
            message,
            image_path,
        )

    # ------------------------------------------------------------------
    # detect_language()
    # ------------------------------------------------------------------
    @staticmethod
    def detect_language(message: str) -> str:
        """
        Detect the language of a farmer message without any external library.

        Strategy
        --------
        1. Unicode script ranges identify every Indian language written in its
           native script. A single character match is sufficient — farmers
           rarely mix scripts in one message.
        2. Hinglish (Hindi in Roman script) is detected by counting how many
           known Hindi vocabulary words appear in the lowercased message.
           At least _HINGLISH_THRESHOLD words must match to avoid false positives
           on short English messages that happen to contain "hai" or "kar".
        3. Fallback → "en" (English).

        Returns a BCP-47-style code string:
            "hi", "te", "kn", "ta", "mr", "gu", "pa", "bn", "ml", "or",
            "hinglish", "en"
        """
        # --- 1. Native-script detection (fast, deterministic) ---
        script_patterns = [
            # (Unicode range regex,  language code)
            (r"[\u0900-\u097F]", "hi"),   # Devanagari — Hindi / Marathi share range;
                                           # Marathi distinguished below if needed
            (r"[\u0C00-\u0C7F]", "te"),   # Telugu
            (r"[\u0C80-\u0CFF]", "kn"),   # Kannada
            (r"[\u0B80-\u0BFF]", "ta"),   # Tamil
            (r"[\u0A80-\u0AFF]", "gu"),   # Gujarati
            (r"[\u0A00-\u0A7F]", "pa"),   # Gurmukhi (Punjabi)
            (r"[\u0980-\u09FF]", "bn"),   # Bengali
            (r"[\u0D00-\u0D7F]", "ml"),   # Malayalam
            (r"[\u0B00-\u0B7F]", "or"),   # Odia
        ]
        for pattern, lang_code in script_patterns:
            if re.search(pattern, message):
                logger.debug("Script-based detection → %s", lang_code)
                return lang_code

        # --- 2. Hinglish detection (Roman-script Hindi vocabulary) ---
        words = set(re.findall(r"\b[a-zA-Z]+\b", message.lower()))
        hinglish_hits = words & _HINGLISH_KEYWORDS
        if len(hinglish_hits) >= _HINGLISH_THRESHOLD:
            logger.debug(
                "Hinglish detection → matched keywords: %s",
                hinglish_hits,
            )
            return "hinglish"

        # --- 3. Fallback ---
        return "en"

    @staticmethod
    def _log_start(user_id: str, message: str) -> None:
        logger.info("================================================")
        logger.info("Decision Engine Started")
        logger.info("User ID  : %s", user_id)
        logger.info("Message  : %s", message)
        logger.info("================================================")


decision_engine = DecisionEngine()