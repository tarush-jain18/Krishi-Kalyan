"""
app/api/telegram.py

Changes in this version
-----------------------
1. Removed the broken `from app.services.language_detector import detect_language`
   import — that module does not exist and the variable `message` it was called
   with was undefined at both call sites anyway.
   Language detection is now entirely inside DecisionEngine.detect_language()
   and is automatically propagated through context["detected_language"].

2. Every `decision_engine.process()` call is replaced with
   `await decision_engine.async_process()` — the non-blocking wrapper that
   runs the synchronous Gemini call in a thread pool via asyncio.to_thread().
   This fixes the "Lock blocking" server hang.

3. Tuple unpacking is now explicit everywhere:
       result, context = await decision_engine.async_process(...)
   No more isinstance(result, tuple) runtime guards.

4. speech_to_text_service.transcribe() (CPU-bound Whisper) is also wrapped
   in asyncio.to_thread() inside _handle_audio.

5. expert_service.create_ticket() and generate_expert_summary() (both
   blocking — Firestore write and Gemini call respectively) are wrapped in
   asyncio.to_thread() inside the callback_query handler.

6. context["detected_language"] is stored in _pending_context so the expert
   ticket carries the farmer's language for future use by the dashboard or
   expert reply translation.

Architecture is otherwise identical to the previous version.
"""

import asyncio
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
#     "chat_id"          : int
#     "context"          : Dict   ← includes detected_language
#     "question"         : str
#     "ai_response"      : str
#     "image_path"       : str | None
#     "voice_path"       : str | None
#     "voice_transcript" : str | None
# }
#
# Process-scoped. For multi-worker deployments replace with Redis.
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
        "context": context,          # carries detected_language
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
# _handle_audio — voice / audio / video_note
# ---------------------------------------------------------------------------

async def _handle_audio(
    chat_id: int,
    user_id: str,
    file_id: str,
) -> None:
    """
    Shared handler for all audio-type messages.

    Blocking calls offloaded to thread pool:
      - speech_to_text_service.transcribe()  (CPU-bound Whisper)
      - decision_engine.async_process()      (blocking Gemini HTTP)
    """
    wav_path = await download_and_convert_audio(file_id)

    # Whisper is CPU-bound — keep the event loop free
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

    # async_process runs the full blocking pipeline in a thread.
    # It also detects language and stores it in context["detected_language"].
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
    """
    Telegram webhook endpoint.

    Message type routing:
      text         → async_process() → send_message_with_keyboard()
      photo        → download_photo() → async_process() → keyboard
      voice        → _handle_audio()
      audio        → _handle_audio()
      video_note   → _handle_audio()
      callback_query (button press) → create expert ticket → confirm to farmer
    """
    data = await request.json()

    logger.info("========== TELEGRAM WEBHOOK ==========")
    logger.info("%s", data)
    logger.info("======================================")

    update = Update.de_json(data, None)

    # -------------------------------------------------------- CALLBACK QUERY
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
            await send_callback_answer(callback_query_id)
            return {"status": "ignored"}

        user_id = callback_data[len(EXPERT_CALLBACK_PREFIX):]

        # Answer immediately — removes the loading spinner
        await send_callback_answer(callback_query_id, text="Processing your request…")

        pending = _pop_pending(user_id)
        if not pending:
            logger.warning(
                "No pending context for user_id=%s — session expired",
                user_id,
            )
            await send_message(
                chat_id,
                "⚠️ Your session has expired. Please ask your question again and then press the button.",
            )
            return {"status": "session_expired"}

        context: Dict[str, Any]  = pending["context"]    # includes detected_language
        question: str            = pending["question"]
        ai_response: str         = pending["ai_response"]
        image_path: Optional[str] = pending.get("image_path")
        voice_path: Optional[str] = pending.get("voice_path")
        voice_transcript: Optional[str] = pending.get("voice_transcript")

        # Generate AI summary (calls Gemini — blocking → thread)
        logger.info("Generating expert AI summary for user_id=%s", user_id)
        try:
            ai_summary = await asyncio.to_thread(
                generate_expert_summary,
                question=question,
                ai_response=ai_response,
                context=context,
            )
        except Exception as exc:
            logger.exception("AI summary generation failed: %s", exc)
            ai_summary = "Expert summary generation failed. Please review the AI response."

        # Create Firestore ticket (blocking → thread)
        logger.info("Creating expert ticket for user_id=%s", user_id)
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
                "Expert ticket created ticket_id=%s user_id=%s language=%s",
                ticket_id,
                user_id,
                context.get("detected_language", "unknown"),
            )
        except Exception as exc:
            logger.exception(
                "Expert ticket creation failed user_id=%s: %s",
                user_id,
                exc,
            )
            await send_message(
                chat_id,
                "❌ Sorry, we could not create your expert request at this time. Please try again.",
            )
            return {"status": "ticket_creation_failed"}

        confirmation = expert_service.confirmation_message(ticket_id)
        await send_message(chat_id, confirmation)
        logger.info(
            "Callback handled ticket_id=%s chat_id=%s",
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

        # async_process detects language internally and stores it in context
        result, context = await decision_engine.async_process(
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

        # Caption language drives the response language
        result, context = await decision_engine.async_process(
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

        await send_message_with_keyboard(
            chat_id=chat_id,
            ai_response=result,
            context_key=user_id,
        )
        return {"status": "ok"}

    # ----------------------------------------------------------------- VOICE
    if update.message.voice:
        logger.info(
            "Received VOICE message chat_id=%s file_id=%s",
            chat_id,
            update.message.voice.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.voice.file_id)
        return {"status": "ok"}

    # ----------------------------------------------------------------- AUDIO
    if update.message.audio:
        logger.info(
            "Received AUDIO message chat_id=%s file_id=%s filename=%s",
            chat_id,
            update.message.audio.file_id,
            update.message.audio.file_name,
        )
        await _handle_audio(chat_id, user_id, update.message.audio.file_id)
        return {"status": "ok"}

    # ------------------------------------------------------------- VIDEO NOTE
    if update.message.video_note:
        logger.info(
            "Received VIDEO_NOTE chat_id=%s file_id=%s",
            chat_id,
            update.message.video_note.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.video_note.file_id)
        return {"status": "ok"}

    # -------------------------------------------------------- UNSUPPORTED TYPE
    logger.info(
        "Unsupported message type from chat_id=%s — ignoring",
        chat_id,
    )
    return {"status": "ignored"}