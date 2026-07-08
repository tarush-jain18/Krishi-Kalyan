"""
app/services/telegram_service.py  [MODIFIED]

Changes vs original:
  - Added send_message_with_keyboard()   — sends a reply + "Send to Expert" InlineKeyboard.
  - Added send_callback_answer()         — answers a callback_query (removes spinner on button).
  - Original send_message() is UNCHANGED.

All new functions reuse the same _TELEGRAM_API_BASE constant and
settings.TELEGRAM_BOT_TOKEN — no new HTTP clients created.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram hard-limits a single sendMessage to 4096 characters.
_MAX_MESSAGE_LENGTH = 4096

# Callback data constant — matched in telegram.py callback handler
EXPERT_CALLBACK_PREFIX = "expert_request:"


# ---------------------------------------------------------------------------
# Internal helpers  (unchanged from original)
# ---------------------------------------------------------------------------

def _split_message(text: str) -> list[str]:
    """
    Split long text into chunks that fit within Telegram's 4096-char limit.
    Splits on newlines where possible to avoid cutting mid-word.
    """
    if len(text) <= _MAX_MESSAGE_LENGTH:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= _MAX_MESSAGE_LENGTH:
            chunks.append(text)
            break

        split_at = text.rfind("\n", 0, _MAX_MESSAGE_LENGTH)
        if split_at == -1:
            split_at = _MAX_MESSAGE_LENGTH

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()

    return chunks


# ---------------------------------------------------------------------------
# Original send_message — UNCHANGED
# ---------------------------------------------------------------------------

async def send_message(chat_id: int, text: str) -> None:
    """
    Send a text reply to a Telegram chat.

    Automatically splits messages longer than 4096 characters.
    Sends without parse_mode to avoid Markdown parse errors from AI output
    containing unbalanced asterisks or underscores.
    """
    if not text or not text.strip():
        logger.warning("send_message called with empty text for chat_id=%s", chat_id)
        return

    url = f"{_TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = _split_message(text.strip())

    logger.info(
        "Sending message to chat_id=%s chunks=%d total_chars=%d",
        chat_id,
        len(chunks),
        len(text),
    )

    async with httpx.AsyncClient(timeout=30) as client:
        for i, chunk in enumerate(chunks, start=1):
            response = await client.post(
                url,
                json={"chat_id": chat_id, "text": chunk},
            )

            if response.status_code != 200:
                logger.error(
                    "sendMessage failed chunk=%d/%d status=%s body=%s",
                    i,
                    len(chunks),
                    response.status_code,
                    response.text,
                )
                continue

            body = response.json()
            if not body.get("ok"):
                logger.error(
                    "sendMessage ok=false chunk=%d/%d response=%s",
                    i,
                    len(chunks),
                    body,
                )
                continue

            logger.info(
                "Message chunk %d/%d sent to chat_id=%s",
                i,
                len(chunks),
                chat_id,
            )


# ---------------------------------------------------------------------------
# NEW: send_message_with_keyboard
# ---------------------------------------------------------------------------

async def send_message_with_keyboard(
    chat_id: int,
    ai_response: str,
    context_key: str,
) -> None:
    """
    Send the AI response followed by a divider and an InlineKeyboard with
    the 'Send to Expert' button.

    Parameters
    ----------
    chat_id      : Telegram chat ID to send to.
    ai_response  : The AI-generated response text shown to the farmer.
    context_key  : A short key (e.g. user_id) embedded in the callback_data
                   so the callback handler knows which user pressed the button.

    Telegram InlineKeyboard layout:
      ──────────────
      Need help from an agricultural expert?
      [ 👨‍🌾 Send to Expert ]

    The callback_data carries the context_key so the handler can look up
    the pending context from an in-memory store.
    """
    if not ai_response or not ai_response.strip():
        logger.warning(
            "send_message_with_keyboard called with empty ai_response chat_id=%s",
            chat_id,
        )
        return

    url = f"{_TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

    # --- Send the AI response text first (split if needed) ---
    chunks = _split_message(ai_response.strip())

    logger.info(
        "Sending AI response with expert keyboard to chat_id=%s chunks=%d",
        chat_id,
        len(chunks),
    )

    async with httpx.AsyncClient(timeout=30) as client:

        # Send all text chunks first (no keyboard on these)
        for i, chunk in enumerate(chunks, start=1):
            response = await client.post(
                url,
                json={"chat_id": chat_id, "text": chunk},
            )
            if response.status_code != 200 or not response.json().get("ok"):
                logger.error(
                    "sendMessage (text chunk) failed chunk=%d status=%s body=%s",
                    i,
                    response.status_code,
                    response.text,
                )
            else:
                logger.info("AI response chunk %d/%d sent", i, len(chunks))

        # Now send the keyboard prompt as a separate message
        keyboard_text = (
            "──────────────\n"
            "Need help from an agricultural expert?"
        )

        callback_data = f"{EXPERT_CALLBACK_PREFIX}{context_key}"

        inline_keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "👨‍🌾 Send to Expert",
                        "callback_data": callback_data,
                    }
                ]
            ]
        }

        keyboard_response = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": keyboard_text,
                "reply_markup": inline_keyboard,
            },
        )

        if keyboard_response.status_code != 200:
            logger.error(
                "sendMessage (keyboard) failed status=%s body=%s",
                keyboard_response.status_code,
                keyboard_response.text,
            )
        else:
            body = keyboard_response.json()
            if not body.get("ok"):
                logger.error("sendMessage (keyboard) ok=false response=%s", body)
            else:
                logger.info(
                    "Expert keyboard sent to chat_id=%s callback_data=%s",
                    chat_id,
                    callback_data,
                )


# ---------------------------------------------------------------------------
# NEW: send_callback_answer
# ---------------------------------------------------------------------------

async def send_callback_answer(
    callback_query_id: str,
    text: Optional[str] = None,
    show_alert: bool = False,
) -> None:
    """
    Answer a Telegram callback_query to remove the loading spinner on the button.

    This must be called within 10 seconds of receiving a callback_query,
    otherwise Telegram shows a timeout error to the user.

    Parameters
    ----------
    callback_query_id : The id field from the callback_query object.
    text              : Optional pop-up text shown to the user (up to 200 chars).
    show_alert        : If True, shows the text as an alert dialog; else a toast.
    """
    url = f"{_TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"

    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
        payload["show_alert"] = show_alert

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)

    if response.status_code != 200 or not response.json().get("ok"):
        logger.error(
            "answerCallbackQuery failed callback_query_id=%s status=%s body=%s",
            callback_query_id,
            response.status_code,
            response.text,
        )
    else:
        logger.info(
            "Callback query answered callback_query_id=%s",
            callback_query_id,
        )
