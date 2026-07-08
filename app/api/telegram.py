"""
app/api/telegram.py  [MODIFIED]

Changes vs original:
  1. After every AI response, calls send_message_with_keyboard() instead of
     send_message() — this appends the "👨‍🌾 Send to Expert" InlineKeyboard.

  2. A pending_context store (in-memory dict) holds the last AI result +
     context per user so the callback handler can create a ticket without
     re-running the Decision Engine.

  3. New callback_query branch handles when the farmer presses the button:
       a. Answer the callback (removes loading spinner immediately).
       b. Generate AI summary via expert_summary.
       c. Create the expert ticket via expert_service.
       d. Reply with confirmation + ticket ID.

  4. All existing message-type branches (text / photo / voice / audio /
     video_note) are UNCHANGED in logic — only the final send_message() call
     is replaced with send_message_with_keyboard().

Does NOT:
  - Modify DecisionEngine
  - Modify ContextBuilder
  - Duplicate Telegram or Firestore code
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from telegram import Update

from app.engine.decision_engine import decision_engine
from app.services.expert_service import expert_service
from app.services.expert_summary import generate_expert_summary
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

_FALLBACK_CAPTION = "Please identify and diagnose this crop."

_SORRY_AUDIO = (
    "Sorry, I could not understand the audio. "
    "Please try again or type your question."
)

# ---------------------------------------------------------------------------
# In-memory pending context store
#
# Key   : user_id (str)
# Value : {
#     "context"          : Dict — the full context from decision_engine
#     "question"         : str  — the farmer's question / transcript
#     "ai_response"      : str  — the AI text sent to the farmer
#     "image_path"       : str | None
#     "voice_path"       : str | None
#     "voice_transcript" : str | None
#     "chat_id"          : int
# }
#
# This store is process-scoped (lives as long as the FastAPI worker).
# For multi-worker deployments, replace with Redis or Firestore-backed cache.
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
    logger.info("Pending context stored for user_id=%s", user_id)


def _pop_pending(user_id: str) -> Optional[Dict[str, Any]]:
    return _pending_context.pop(user_id, None)


# ---------------------------------------------------------------------------
# Internal: handle audio (UNCHANGED logic; only send call replaced)
# ---------------------------------------------------------------------------

async def _handle_audio(
    chat_id: int,
    user_id: str,
    file_id: str,
    voice_path_str: Optional[str] = None,
) -> None:
    """
    Shared handler for voice, audio, and video_note messages.
    Downloads the file, converts to WAV, transcribes, then calls decision engine.
    """
    wav_path = await download_and_convert_audio(file_id)

    raw_result = speech_to_text_service.transcribe(str(wav_path))

    if isinstance(raw_result, dict):
        transcribed_text = raw_result.get("text", "").strip()
    else:
        transcribed_text = (raw_result or "").strip()

    logger.info("Transcription result: %r", transcribed_text)

    if not transcribed_text:
        await send_message(chat_id, _SORRY_AUDIO)
        return

    # Expose context from decision_engine
    context = decision_engine.context_builder.build(user_id=user_id)

    result = decision_engine.process(
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
    if isinstance(result, tuple):
        print("===== RESULT IS TUPLE =====")
        print(result)
        print("===========================")
        result = result[0]
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
    """
    Telegram webhook endpoint.

    Routes incoming updates by message type:
      • text         → decision_engine.process() → send_message_with_keyboard()
      • photo        → download_photo() → decision_engine.process() → keyboard
      • voice        → audio pipeline → decision_engine.process() → keyboard
      • audio        → audio pipeline → decision_engine.process() → keyboard
      • video_note   → audio pipeline → decision_engine.process() → keyboard
      • callback_query (button press) → create expert ticket → confirm to farmer
    """
    data = await request.json()

    logger.info("========== TELEGRAM WEBHOOK ==========")
    logger.info("%s", data)
    logger.info("======================================")

    update = Update.de_json(data, None)

    # -------------------------------------------------------- CALLBACK QUERY
    # Farmer pressed the "👨‍🌾 Send to Expert" inline button.
    if update.callback_query:
        cq = update.callback_query
        callback_data: str = cq.data or ""
        chat_id: int = cq.message.chat.id
        callback_query_id: str = cq.id

        logger.info(
            "Received callback_query callback_data=%s chat_id=%s",
            callback_data,
            chat_id,
        )

        if not callback_data.startswith(EXPERT_CALLBACK_PREFIX):
            # Unknown button — just acknowledge silently
            await send_callback_answer(callback_query_id)
            return {"status": "ignored"}

        # Extract user_id from callback_data
        user_id = callback_data[len(EXPERT_CALLBACK_PREFIX):]

        # Answer immediately to remove loading spinner
        await send_callback_answer(
            callback_query_id,
            text="Processing your request…",
        )

        # Retrieve pending context
        pending = _pop_pending(user_id)
        if not pending:
            logger.warning(
                "No pending context found for user_id=%s — cannot create ticket",
                user_id,
            )
            await send_message(
                chat_id,
                "⚠️ Your session has expired. Please ask your question again and then press the button.",
            )
            return {"status": "session_expired"}

        context: Dict[str, Any] = pending["context"]
        question: str = pending["question"]
        ai_response: str = pending["ai_response"]
        image_path: Optional[str] = pending.get("image_path")
        voice_path: Optional[str] = pending.get("voice_path")
        voice_transcript: Optional[str] = pending.get("voice_transcript")

        # Generate AI summary
        logger.info("Generating expert AI summary for user_id=%s", user_id)
        try:
            ai_summary = generate_expert_summary(
                question=question,
                ai_response=ai_response,
                context=context,
            )
        except Exception as exc:
            logger.exception("AI summary generation failed: %s", exc)
            ai_summary = "Expert summary generation failed. Please review the AI response."

        # Create expert ticket in Firestore
        logger.info("Creating expert ticket for user_id=%s", user_id)
        try:
            ticket_id = expert_service.create_ticket(
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
                ticket_id,
                user_id,
            )
        except Exception as exc:
            logger.exception("Expert ticket creation failed user_id=%s: %s", user_id, exc)
            await send_message(
                chat_id,
                "❌ Sorry, we could not create your expert request at this time. Please try again.",
            )
            return {"status": "ticket_creation_failed"}

        # Confirm to farmer
        confirmation = expert_service.confirmation_message(ticket_id)
        await send_message(chat_id, confirmation)
        logger.info(
            "Telegram callback handled successfully ticket_id=%s chat_id=%s",
            ticket_id,
            chat_id,
        )
        return {"status": "ticket_created", "ticket_id": ticket_id}

    # -------------------------------------------------------- REGULAR MESSAGE
    if update.message is None:
        logger.info("Update contains no message — ignoring")
        return {"status": "ignored"}

    chat_id = update.message.chat.id
    user_id = "demo_user"  # Replace with actual user ID retrieval logic

    # ------------------------------------------------------------------ TEXT
    if update.message.text:
        logger.info("Received TEXT message from chat_id=%s", chat_id)

        # Build context so we can store it for ticket creation
        try:
            context = decision_engine.context_builder.build(user_id=user_id)
        except Exception as exc:
            logger.warning("Could not pre-build context: %s", exc)
            context = {}

        result = decision_engine.process(
            user_id=user_id,
            message=update.message.text,
        )

        _store_pending(
            user_id,
            chat_id=chat_id,
            context=context,
            question=update.message.text,
            ai_response=result,
        )
        if isinstance(result, tuple):
            print("===== RESULT IS TUPLE =====")
            print(result)
            print("===========================")
            result = result[0]
        await send_message_with_keyboard(
            chat_id=chat_id,
            ai_response=result,
            context_key=user_id,
        )
        return {"status": "ok"}

    # ----------------------------------------------------------------- PHOTO
    if update.message.photo:
        logger.info("Received PHOTO message from chat_id=%s", chat_id)

        largest_photo = update.message.photo[-1]
        caption = update.message.caption or _FALLBACK_CAPTION

        logger.info(
            "Photo file_id=%s caption=%r",
            largest_photo.file_id,
            caption,
        )

        image_path = await download_photo(largest_photo.file_id)

        try:
            context = decision_engine.context_builder.build(user_id=user_id)
        except Exception as exc:
            logger.warning("Could not pre-build context: %s", exc)
            context = {}

        result = decision_engine.process(
            user_id=user_id,
            message=caption,
            image_path=str(image_path),
        )

        _store_pending(
            user_id,
            chat_id=chat_id,
            context=context,
            question=caption,
            ai_response=result,
            image_path=str(image_path),
        )
        if isinstance(result, tuple):
            print("===== RESULT IS TUPLE =====")
            print(result)
            print("===========================")
            result = result[0]
        await send_message_with_keyboard(
            chat_id=chat_id,
            ai_response=result,
            context_key=user_id,
        )
        return {"status": "ok"}

    # ----------------------------------------------------------------- VOICE
    if update.message.voice:
        logger.info(
            "Received VOICE message from chat_id=%s file_id=%s",
            chat_id,
            update.message.voice.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.voice.file_id)
        return {"status": "ok"}

    # ----------------------------------------------------------------- AUDIO
    if update.message.audio:
        logger.info(
            "Received AUDIO message from chat_id=%s file_id=%s filename=%s",
            chat_id,
            update.message.audio.file_id,
            update.message.audio.file_name,
        )
        await _handle_audio(chat_id, user_id, update.message.audio.file_id)
        return {"status": "ok"}

    # ------------------------------------------------------------- VIDEO NOTE
    if update.message.video_note:
        logger.info(
            "Received VIDEO_NOTE from chat_id=%s file_id=%s",
            chat_id,
            update.message.video_note.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.video_note.file_id)
        return {"status": "ok"}

    # -------------------------------------------------------- UNSUPPORTED TYPE
    logger.info(
        "Received unsupported message type from chat_id=%s — ignoring",
        chat_id,
    )
    return {"status": "ignored"}
