"""
app/api/expert.py

FastAPI router exposing the Expert Escalation API endpoints.

Endpoints:
  POST /expert/reply
      Called by the Streamlit dashboard when an expert sends a reply.
      Updates Firestore and sends the reply to the farmer via Telegram.

  GET  /expert/tickets
      Returns all expert tickets (newest first) as JSON.
      Primarily used for debugging; the dashboard reads Firestore directly.

Reuses:
  - expert_service   (app/services/expert_service.py)
  - telegram_service (app/services/telegram_service.py)

Does NOT:
  - Duplicate Firestore client
  - Duplicate Telegram send logic
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.exceptions import FirestoreException
from app.services.expert_service import expert_service
from app.services.telegram_service import send_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expert", tags=["Expert"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ExpertReplyRequest(BaseModel):
    ticket_id: str = Field(..., min_length=1, description="Ticket ID e.g. KRK-XXXXXXXX")
    expert_reply: str = Field(..., min_length=1, description="Expert's reply text")


# ---------------------------------------------------------------------------
# POST /expert/reply
# ---------------------------------------------------------------------------

@router.post("/reply")
async def expert_reply(body: ExpertReplyRequest) -> Dict[str, Any]:
    """
    Receive an expert reply from the Streamlit dashboard.

    Steps:
      1. Resolve the ticket in Firestore (status=Resolved, expert_reply stored).
      2. Retrieve the farmer's telegram_chat_id from the resolved ticket.
      3. Send the expert reply back to the farmer via Telegram.
    """
    logger.info(
        "Expert reply received ticket_id=%s reply_length=%d",
        body.ticket_id,
        len(body.expert_reply),
    )

    # 1. Update Firestore
    try:
        ticket = expert_service.resolve_ticket(
            ticket_id=body.ticket_id,
            expert_reply=body.expert_reply,
        )
        logger.info("Ticket resolved in Firestore ticket_id=%s", body.ticket_id)
    except FirestoreException as exc:
        logger.error(
            "Firestore update failed ticket_id=%s error=%s",
            body.ticket_id,
            exc.message,
        )
        return {
            "success": False,
            "error": exc.to_error_response(),
        }

    # 2. Send Telegram reply to farmer
    chat_id = ticket.get("telegram_chat_id")
    if not chat_id:
        logger.warning(
            "No telegram_chat_id found for ticket_id=%s — Telegram reply skipped",
            body.ticket_id,
        )
        return {
            "success": True,
            "ticket_id": body.ticket_id,
            "telegram_sent": False,
            "warning": "telegram_chat_id missing on ticket",
        }

    reply_text = expert_service.expert_reply_message(
        ticket_id=body.ticket_id,
        reply=body.expert_reply,
    )

    try:
        await send_message(int(chat_id), reply_text)
        logger.info(
            "Expert reply sent via Telegram chat_id=%s ticket_id=%s",
            chat_id,
            body.ticket_id,
        )
    except Exception as exc:
        logger.exception(
            "Telegram send failed for expert reply ticket_id=%s chat_id=%s",
            body.ticket_id,
            chat_id,
        )
        return {
            "success": False,
            "error": {
                "code": "TELEGRAM_SEND_FAILED",
                "message": str(exc),
                "ticket_id": body.ticket_id,
            },
        }

    return {
        "success": True,
        "ticket_id": body.ticket_id,
        "telegram_sent": True,
    }


# ---------------------------------------------------------------------------
# GET /expert/tickets  (debug / admin endpoint)
# ---------------------------------------------------------------------------

@router.get("/tickets")
def list_tickets() -> Dict[str, Any]:
    """Return all expert tickets from Firestore, newest first."""
    try:
        tickets = expert_service.get_all_tickets()
        logger.info("Tickets endpoint returned %d records", len(tickets))
        return {
            "success": True,
            "count": len(tickets),
            "tickets": tickets,
        }
    except FirestoreException as exc:
        return {
            "success": False,
            "error": exc.to_error_response(),
        }
