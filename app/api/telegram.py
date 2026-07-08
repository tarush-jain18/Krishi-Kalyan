"""
app/api/telegram.py

Changes in this version
-----------------------
1. Real farmer registration flow added between language selection and the
   normal AI flow.  No hardcoded "demo_user" anywhere.

2. user_id is now always str(update.message.from_user.id) — the Telegram
   numeric user ID, which is also the Firestore document ID written by
   RegistrationService.

3. New gate ORDER (top to bottom in the message handler):
     /start  → reset everything; send language picker
     /help   → send help menu
     Language not yet chosen (onboarding gate) → send language picker
     Registration in progress → delegate ALL text to registration_service
     Registration not yet done (first time after language pick) → start registration
     Normal AI flow → text / photo / voice / audio / video_note

4. Callback query for language buttons (set_lang:XX) now triggers
   registration_service.start() after onboarding completes, sending the
   first registration question immediately.

5. All expert escalation, callback handling, audio pipeline, Firestore,
   Gemini are completely unchanged.

6. _handle_audio() now receives real user_id so expert tickets are filed
   under the correct farmer.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from telegram import Update

from app.core.config import settings
from app.engine.decision_engine import decision_engine
from app.services.expert_service import expert_service
from app.services.expert_summary import generate_expert_summary
from app.services.onboarding import (
    LANG_CALLBACK_PREFIX,
    onboarding_service,
)
from app.services.registration_service import registration_service
from app.services.speech_to_text import speech_to_text_service
from app.services.telegram_media import download_and_convert_audio, download_photo
from app.services.telegram_service import (
    EXPERT_CALLBACK_PREFIX,
    send_callback_answer,
    send_message,
    send_message_with_keyboard,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TELEGRAM_API_BASE = "https://api.telegram.org"

_FALLBACK_CAPTION = "Please identify and diagnose this crop."

_SORRY_AUDIO = (
    "Sorry, I could not understand the audio. "
    "Please try again or type your question."
)

# Sent when the farmer types before pressing the language picker button
_PLEASE_PICK_LANGUAGE = (
    "👆 Please select your language from the buttons above first."
)

# Sent when a registered farmer sends a non-text update while we can't
# handle it (e.g. sticker) — unchanged from original
_UNSUPPORTED = (
    "Sorry, I can only handle text, voice messages, and crop photos. "
    "Please try one of those."
)


# ---------------------------------------------------------------------------
# Internal: send a raw Telegram API payload
# (used for onboarding / registration messages that carry reply_markup)
# ---------------------------------------------------------------------------

async def _send_payload(payload: Dict[str, Any]) -> None:
    url = f"{_TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code != 200 or not resp.json().get("ok"):
        logger.error(
            "sendMessage failed status=%s body=%s",
            resp.status_code,
            resp.text,
        )
    else:
        logger.info("Payload sent chat_id=%s", payload.get("chat_id"))


# ---------------------------------------------------------------------------
# Internal: answer a Telegram callback query (thin wrapper)
# ---------------------------------------------------------------------------

async def _answer_callback(callback_query_id: str, text: str = "") -> None:
    url = (
        f"{_TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}"
        "/answerCallbackQuery"
    )
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)


# ---------------------------------------------------------------------------
# Internal: pending context store (expert escalation — unchanged)
# ---------------------------------------------------------------------------

_pending_context: Dict[str, Dict[str, Any]] = {}


def _store_pending(
    user_id: str,
    *,
    chat_id: int,
    context: Dict[str, Any],
    question: str,
    ai_response: str,
    image_path: Optional[str] = None,
    voice_path: Optional[str] = None,
    voice_transcript: Optional[str] = None,
) -> None:
    _pending_context[user_id] = {
        "chat_id": chat_id,
        "context": context,
        "question": question,
        "ai_response": ai_response,
        "image_path": image_path,
        "voice_path": voice_path,
        "voice_transcript": voice_transcript,
    }
    logger.info(
        "Pending context stored user_id=%s language=%s",
        user_id,
        context.get("detected_language", "unknown"),
    )


def _pop_pending(user_id: str) -> Optional[Dict[str, Any]]:
    return _pending_context.pop(user_id, None)


# ---------------------------------------------------------------------------
# Internal: audio handler
# NOW receives real user_id instead of "demo_user"
# ---------------------------------------------------------------------------

async def _handle_audio(
    chat_id: int,
    user_id: str,   # ← real Telegram user ID (string)
    file_id: str,
) -> None:
    wav_path = await download_and_convert_audio(file_id)

    raw_result = await asyncio.to_thread(
        speech_to_text_service.transcribe,
        str(wav_path),
    )

    if isinstance(raw_result, dict):
        transcribed_text = raw_result.get("text", "").strip()
    else:
        transcribed_text = (raw_result or "").strip()

    logger.info("Transcription result: %r", transcribed_text)

    if not transcribed_text:
        await send_message(chat_id, _SORRY_AUDIO)
        return

    result, context = await decision_engine.async_process(
        user_id=user_id,
        message=transcribed_text,
    )

    _store_pending(
        user_id,
        chat_id=chat_id,
        context=context,
        question=transcribed_text,
        ai_response=result,
        voice_path=str(wav_path),
        voice_transcript=transcribed_text,
    )

    await send_message_with_keyboard(
        chat_id=chat_id,
        ai_response=result,
        context_key=user_id,
    )


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    data   = await request.json()
    update = Update.de_json(data, None)

    logger.info("========== TELEGRAM WEBHOOK ==========")
    logger.info("%s", data)
    logger.info("======================================")

    # ════════════════════════════════════════════════════════════════════
    # CALLBACK QUERY  (inline button press)
    # Handles:
    #   set_lang:XX        — language selection (onboarding)
    #   expert_request:XX  — expert escalation
    # ════════════════════════════════════════════════════════════════════
    if update.callback_query:
        cq            = update.callback_query
        cq_id         = cq.id
        chat_id       = cq.message.chat.id
        callback_data = cq.data or ""

        await _answer_callback(cq_id)

        # ── LANGUAGE BUTTON ─────────────────────────────────────────────
        if callback_data.startswith(LANG_CALLBACK_PREFIX):
            lang_code = callback_data[len(LANG_CALLBACK_PREFIX):]

            onboarding_service.complete(chat_id, lang_code)
            logger.info(
                "Language selected chat_id=%s lang=%s", chat_id, lang_code
            )

            # Send the welcome message
            welcome = onboarding_service.welcome_payload(chat_id, lang_code)
            await _send_payload(welcome)

            # ── NEW: immediately start registration ──────────────────────
            # The farmer has chosen their language; now collect their profile.
            first_question = registration_service.start(chat_id, lang_code)
            await send_message(chat_id, first_question)
            # ────────────────────────────────────────────────────────────

            return {"status": "language_set_registration_started", "language": lang_code}

        # ── EXPERT BUTTON ───────────────────────────────────────────────
        if callback_data.startswith(EXPERT_CALLBACK_PREFIX):
            user_id = callback_data[len(EXPERT_CALLBACK_PREFIX):]

            await send_callback_answer(cq_id, text="Processing your request…")

            pending = _pop_pending(user_id)
            if not pending:
                logger.warning(
                    "No pending context for user_id=%s — session expired",
                    user_id,
                )
                await send_message(
                    chat_id,
                    "⚠️ Your session has expired. Please ask your question again.",
                )
                return {"status": "session_expired"}

            context          = pending["context"]
            question         = pending["question"]
            ai_response      = pending["ai_response"]
            image_path       = pending.get("image_path")
            voice_path       = pending.get("voice_path")
            voice_transcript = pending.get("voice_transcript")

            try:
                ai_summary = await asyncio.to_thread(
                    generate_expert_summary,
                    question=question,
                    ai_response=ai_response,
                    context=context,
                )
            except Exception as exc:
                logger.exception("AI summary failed: %s", exc)
                ai_summary = "Expert summary unavailable. Please review the AI response."

            try:
                ticket_id = await asyncio.to_thread(
                    expert_service.create_ticket,
                    user_id=user_id,
                    chat_id=chat_id,
                    context=context,
                    question=question,
                    ai_response=ai_response,
                    ai_summary=ai_summary,
                    image_path=image_path,
                    voice_path=voice_path,
                    voice_transcript=voice_transcript,
                )
                logger.info(
                    "Expert ticket created ticket_id=%s user_id=%s",
                    ticket_id, user_id,
                )
            except Exception as exc:
                logger.exception(
                    "Ticket creation failed user_id=%s: %s", user_id, exc
                )
                await send_message(
                    chat_id,
                    "❌ Could not create your expert request. Please try again.",
                )
                return {"status": "ticket_creation_failed"}

            await send_message(
                chat_id, expert_service.confirmation_message(ticket_id)
            )
            return {"status": "ticket_created", "ticket_id": ticket_id}

        # ── UNKNOWN BUTTON ──────────────────────────────────────────────
        await _answer_callback(cq_id)
        return {"status": "ignored"}

    # ════════════════════════════════════════════════════════════════════
    # REGULAR MESSAGE
    # ════════════════════════════════════════════════════════════════════
    if update.message is None:
        return {"status": "ignored"}

    chat_id = update.message.chat.id

    # ── REAL USER ID ─────────────────────────────────────────────────────
    # The Telegram user ID is numeric; we store it as a string everywhere
    # because Firestore document IDs are strings and Python dicts key by str.
    # This replaces the previous hardcoded "demo_user".
    user_id: str = str(update.message.from_user.id)

    # ── /start COMMAND ──────────────────────────────────────────────────
    if update.message.text and update.message.text.strip() == "/start":
        logger.info("/start received chat_id=%s user_id=%s", chat_id, user_id)
        # Reset BOTH onboarding and registration so the farmer can restart
        # cleanly at any time (e.g. if they want to change their profile).
        onboarding_service.reset(chat_id)
        registration_service.reset(chat_id)
        onboarding_service.mark_awaiting(chat_id)
        await _send_payload(onboarding_service.language_picker_payload(chat_id))
        return {"status": "onboarding_started"}

    # ── /help COMMAND ───────────────────────────────────────────────────
    if update.message.text and update.message.text.strip() == "/help":
        logger.info("/help received chat_id=%s", chat_id)
        await _send_payload(onboarding_service.help_payload(chat_id))
        return {"status": "help_sent"}

    # ── ONBOARDING GATE — language not yet chosen ────────────────────────
    # This fires for: first-ever message from a new user, OR any message
    # while still in AWAITING_LANG state.
    if not onboarding_service.is_done(chat_id):
        logger.info(
            "Onboarding not done chat_id=%s — sending language picker", chat_id
        )
        onboarding_service.mark_awaiting(chat_id)
        if update.message.text:
            await send_message(chat_id, _PLEASE_PICK_LANGUAGE)
        await _send_payload(onboarding_service.language_picker_payload(chat_id))
        return {"status": "awaiting_language"}

    # ── REGISTRATION GATE — farmer is mid-registration ───────────────────
    # Any text that arrives while is_in_progress() is True is treated as
    # an answer to the current registration question.
    # Photos / voice during registration are politely redirected — the farmer
    # must type their answer for each field.
    if registration_service.is_in_progress(chat_id):
        logger.info(
            "Registration in progress chat_id=%s user_id=%s", chat_id, user_id
        )

        if update.message.text:
            reg_result = registration_service.handle_answer(
                chat_id, update.message.text
            )
            await send_message(chat_id, reg_result.reply)

            if reg_result.is_complete:
                logger.info(
                    "Registration complete chat_id=%s user_id=%s",
                    chat_id, user_id,
                )
            return {"status": "registration_in_progress"}

        # Non-text during registration (photo, voice, etc.) — nudge farmer
        await send_message(
            chat_id,
            "📝 Please type your answer. Voice and photos are available after registration is complete."
        )
        return {"status": "registration_awaiting_text"}

    # ── REGISTRATION GATE — not yet started after language pick ──────────
    # Edge case: onboarding is DONE but registration was never started.
    # This can happen if the server was restarted mid-session or if
    # registration_service.reset() was called without triggering start().
    if not registration_service.is_registered(chat_id):
        logger.info(
            "Onboarding done but registration not started chat_id=%s — starting now",
            chat_id,
        )
        lang = onboarding_service.get_language(chat_id) or "en"
        first_question = registration_service.start(chat_id, lang)
        await send_message(chat_id, first_question)
        return {"status": "registration_started"}

    # ════════════════════════════════════════════════════════════════════
    # NORMAL AI FLOW (both onboarding and registration complete)
    # Everything below is identical to the previous version, except
    # user_id is now the real Telegram ID (str) instead of "demo_user".
    # ════════════════════════════════════════════════════════════════════

    # Inject the farmer's chosen language so prompt_builder stamps
    # FARMER_LANGUAGE correctly even before detect_language() runs.
    chosen_lang: Optional[str] = onboarding_service.get_language(chat_id)

    # ---------------------------------------------------------------- TEXT
    if update.message.text:
        logger.info("TEXT chat_id=%s user_id=%s", chat_id, user_id)

        result, context = await decision_engine.async_process(
            user_id=user_id,
            message=update.message.text,
        )

        if chosen_lang and context.get("detected_language") == "en":
            context["detected_language"] = chosen_lang

        _store_pending(
            user_id,
            chat_id=chat_id,
            context=context,
            question=update.message.text,
            ai_response=result,
        )

        await send_message_with_keyboard(
            chat_id=chat_id,
            ai_response=result,
            context_key=user_id,
        )
        return {"status": "ok"}

    # --------------------------------------------------------------- PHOTO
    if update.message.photo:
        logger.info("PHOTO chat_id=%s user_id=%s", chat_id, user_id)

        largest_photo = update.message.photo[-1]
        caption       = update.message.caption or _FALLBACK_CAPTION

        image_path = await download_photo(largest_photo.file_id)

        result, context = await decision_engine.async_process(
            user_id=user_id,
            message=caption,
            image_path=str(image_path),
        )

        if chosen_lang and context.get("detected_language") == "en":
            context["detected_language"] = chosen_lang

        _store_pending(
            user_id,
            chat_id=chat_id,
            context=context,
            question=caption,
            ai_response=result,
            image_path=str(image_path),
        )

        await send_message_with_keyboard(
            chat_id=chat_id,
            ai_response=result,
            context_key=user_id,
        )
        return {"status": "ok"}

    # --------------------------------------------------------------- VOICE
    if update.message.voice:
        logger.info(
            "VOICE chat_id=%s user_id=%s file_id=%s",
            chat_id, user_id, update.message.voice.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.voice.file_id)
        return {"status": "ok"}

    # --------------------------------------------------------------- AUDIO
    if update.message.audio:
        logger.info(
            "AUDIO chat_id=%s user_id=%s file_id=%s",
            chat_id, user_id, update.message.audio.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.audio.file_id)
        return {"status": "ok"}

    # ---------------------------------------------------------- VIDEO NOTE
    if update.message.video_note:
        logger.info(
            "VIDEO_NOTE chat_id=%s user_id=%s file_id=%s",
            chat_id, user_id, update.message.video_note.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.video_note.file_id)
        return {"status": "ok"}

    logger.info("Unsupported message type chat_id=%s — ignoring", chat_id)
    return {"status": "ignored"}