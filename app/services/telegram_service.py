import logging

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram hard-limits a single sendMessage to 4096 characters.
_MAX_MESSAGE_LENGTH = 4096


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
                # Continue sending remaining chunks rather than aborting
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