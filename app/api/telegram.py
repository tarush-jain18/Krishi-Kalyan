import logging

from fastapi import APIRouter, Request
from telegram import Update

from app.engine.decision_engine import decision_engine
from app.services.speech_to_text import speech_to_text_service
from app.services.telegram_media import download_and_convert_audio, download_photo
from app.services.telegram_service import send_message


logger = logging.getLogger(__name__)

router = APIRouter()

_FALLBACK_CAPTION = "Please identify and diagnose this crop."

_SORRY_AUDIO = (
    "Sorry, I could not understand the audio. "
    "Please try again or type your question."
)


async def _handle_audio(chat_id: int, user_id: str, file_id: str) -> None:
    """
    Shared handler for voice, audio, and video_note messages.
    Downloads the file, converts to WAV, transcribes, then calls decision engine.
    """
    wav_path = await download_and_convert_audio(file_id)

    raw_result = speech_to_text_service.transcribe(str(wav_path))

    # speech_to_text_service.transcribe() returns {"text": ..., "language": ..., "language_probability": ...}
    if isinstance(raw_result, dict):
        transcribed_text = raw_result.get("text", "").strip()
    else:
        transcribed_text = (raw_result or "").strip()

    logger.info("Transcription result: %r", transcribed_text)

    if not transcribed_text:
        await send_message(chat_id, _SORRY_AUDIO)
        return

    result = decision_engine.process(
        user_id=user_id,
        message=transcribed_text,
    )
    await send_message(chat_id, result)


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Telegram webhook endpoint.

    Routes incoming updates by message type:
      • text       → decision_engine.process()
      • photo      → download_photo() → decision_engine.process(image_path=...)
      • voice      → download_and_convert_audio() → speech_to_text_service.transcribe() → decision_engine.process()
      • audio      → download_and_convert_audio() → speech_to_text_service.transcribe() → decision_engine.process()
      • video_note → download_and_convert_audio() → speech_to_text_service.transcribe() → decision_engine.process()

    All media handling is in telegram_media.
    All outbound Telegram calls are in telegram_service.
    decision_engine and speech_to_text_service are never modified here.
    """
    data = await request.json()

    logger.info("========== TELEGRAM WEBHOOK ==========")
    logger.info("%s", data)
    logger.info("======================================")

    update = Update.de_json(data, None)

    if update.message is None:
        logger.info("Update contains no message — ignoring")
        return {"status": "ignored"}

    chat_id = update.message.chat.id
    user_id = "demo_user"  # Replace with actual user ID retrieval logic

    # ------------------------------------------------------------------ TEXT
    if update.message.text:
        logger.info("Received TEXT message from chat_id=%s", chat_id)

        result = decision_engine.process(
            user_id=user_id,
            message=update.message.text,
        )
        await send_message(chat_id, result)
        return {"status": "ok"}

    # ----------------------------------------------------------------- PHOTO
    if update.message.photo:
        logger.info("Received PHOTO message from chat_id=%s", chat_id)

        # Telegram sends photos as array of PhotoSize in ascending resolution.
        # The last entry is always the largest/highest quality.
        largest_photo = update.message.photo[-1]
        caption = update.message.caption or _FALLBACK_CAPTION

        logger.info(
            "Photo file_id=%s caption=%r",
            largest_photo.file_id,
            caption,
        )

        image_path = await download_photo(largest_photo.file_id)

        result = decision_engine.process(
            user_id=user_id,
            message=caption,
            image_path=str(image_path),
        )
        await send_message(chat_id, result)
        return {"status": "ok"}

    # ----------------------------------------------------------------- VOICE
    # Telegram voice messages are always Opus OGG sent via mic recording.
    if update.message.voice:
        logger.info(
            "Received VOICE message from chat_id=%s file_id=%s",
            chat_id,
            update.message.voice.file_id,
        )
        await _handle_audio(chat_id, user_id, update.message.voice.file_id)
        return {"status": "ok"}

    # ----------------------------------------------------------------- AUDIO
    # Telegram audio messages are file uploads: .m4a, .mp3, .flac, etc.
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
    # Telegram round video messages (video_note) also contain audio.
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