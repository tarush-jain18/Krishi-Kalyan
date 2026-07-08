"""
app/services/expert_service.py

Expert Escalation Service for Krishi Kalyan.

Responsibilities:
  - Generate unique ticket IDs (KRK-XXXXXXXX)
  - Build and store expert tickets in Firestore (expert_requests collection)
  - Update tickets with expert replies and resolved status
  - Compose Telegram messages for farmer-facing ticket confirmations

Reuses:
  - firestore_service  (app/database/firestore.py)
  - telegram_service   (app/services/telegram_service.py)

Does NOT:
  - Call Gemini directly  → that is done in expert_summary.py
  - Duplicate Firestore client creation
  - Duplicate Telegram send logic
"""

import logging
import random
import string
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.exceptions import FirestoreException
from app.database.firestore import firestore_service
from app.services.telegram_service import send_message_with_keyboard, send_message

logger = logging.getLogger(__name__)

_COLLECTION = "expert_requests"


# ---------------------------------------------------------------------------
# Ticket ID generator
# ---------------------------------------------------------------------------

def _generate_ticket_id() -> str:
    """Generate a human-readable ticket ID: KRK-XXXXXXXX (8 uppercase hex chars)."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"KRK-{suffix}"


# ---------------------------------------------------------------------------
# Ticket builder
# ---------------------------------------------------------------------------

def _build_ticket(
    *,
    ticket_id: str,
    user_id: str,
    chat_id: int,
    context: Dict[str, Any],
    question: str,
    ai_response: str,
    ai_summary: str,
    image_path: Optional[str],
    voice_path: Optional[str],
    voice_transcript: Optional[str],
) -> Dict[str, Any]:
    """
    Assemble the full expert ticket document from available context.

    Context is expected to be the dict produced by ContextBuilder.build() —
    enriched by DecisionEngine with 'snapshot', 'image_path', etc.
    """
    now = datetime.now(timezone.utc)

    user: Dict[str, Any] = context.get("user", {})
    farm: Dict[str, Any] = context.get("farm", {})
    soil: Dict[str, Any] = context.get("soil", {})
    weather_obj = context.get("weather")
    crop_health: Dict[str, Any] = context.get("crop_health", {})
    snapshot: Dict[str, Any] = context.get("snapshot", {})
    village: Dict[str, Any] = context.get("village", {})

    # ------------------------------------------------------------------
    # Weather map  (weather is a Pydantic model or plain dict)
    # ------------------------------------------------------------------
    if weather_obj and hasattr(weather_obj, "model_dump"):
        weather_raw = weather_obj.model_dump(mode="json")
    elif isinstance(weather_obj, dict):
        weather_raw = weather_obj
    else:
        weather_raw = {}

    weather_map = {
        "temperature": weather_raw.get("temperature"),
        "humidity": weather_raw.get("humidity"),
        "rainfall": weather_raw.get("rainfall"),
        "wind_speed": weather_raw.get("wind_speed"),
        "condition": weather_raw.get("condition"),
    }

    # ------------------------------------------------------------------
    # Soil map
    # ------------------------------------------------------------------
    soil_map = {
        "N": soil.get("N"),
        "P": soil.get("P"),
        "K": soil.get("K"),
        "ph": soil.get("ph"),
    }

    # ------------------------------------------------------------------
    # Satellite map
    # ------------------------------------------------------------------
    satellite_map = {
        "ndvi": crop_health.get("ndvi"),
        "crop_health": crop_health.get("crop_health"),
    }

    # ------------------------------------------------------------------
    # Resolve district / village / season
    # ------------------------------------------------------------------
    district = (
        user.get("district")
        or user.get("location", {}).get("district")
        or farm.get("district")
        or ""
    )
    village_name = (
        user.get("village")
        or farm.get("village")
        or village.get("village")
        or ""
    )
    season = farm.get("season") or village.get("season") or ""
    from pprint import pprint

    print("\n===== WEATHER MAP =====")
    pprint(weather_map)

    print("\n===== SOIL MAP =====")
    pprint(soil_map)

    print("\n===== SATELLITE MAP =====")
    pprint(satellite_map)

    print("\n===== SNAPSHOT =====")
    pprint(snapshot)
    if isinstance(ai_response, tuple):
        ai_response = ai_response[0]
    ticket: Dict[str, Any] = {
        # Metadata
        "ticket_id": ticket_id,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
        "status": "Pending",
        "priority": "Normal",
        "assigned_to": None,
        "platform": "Telegram",
        # Farmer identifiers
        "user_id": user_id,
        "telegram_chat_id": chat_id,
        "farmer_name": user.get("name") or user.get("farmer_name") or "",
        "phone": user.get("phone") or user.get("mobile") or "",
        # Farm context
        "crop": farm.get("current_crop") or snapshot.get("crop") or "",
        "district": district,
        "village": village_name,
        "season": season,
        # Query
        "question": question,
        "query_type": "General",
        # AI fields
        "ai_summary": ai_summary,
        "ai_response": ai_response[0] if isinstance(ai_response, tuple) else ai_response,
        "expert_reply": None,
        # Media
        "image_path": image_path or "",
        "voice_path": voice_path or "",
        "voice_transcript": voice_transcript or "",
        # Structured environment data
        "weather": weather_map,
        "soil": soil_map,
        "satellite": satellite_map,
        "farm_snapshot": snapshot,
    }

    return ticket


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ExpertService:

    def __init__(self, db_service=None) -> None:
        self._db = db_service or firestore_service

    # ------------------------------------------------------------------
    # create_ticket
    # ------------------------------------------------------------------
    def create_ticket(
        self,
        *,
        user_id: str,
        chat_id: int,
        context: Dict[str, Any],
        question: str,
        ai_response: str,
        ai_summary: str,
        image_path: Optional[str] = None,
        voice_path: Optional[str] = None,
        voice_transcript: Optional[str] = None,
    ) -> str:
        """
        Build an expert ticket and persist it in Firestore.

        Returns the generated ticket_id (e.g. 'KRK-A1B2C3D4').
        Raises FirestoreException on persistence failure.
        """
        ticket_id = _generate_ticket_id()

        logger.info(
            "Creating expert ticket ticket_id=%s user_id=%s chat_id=%s",
            ticket_id,
            user_id,
            chat_id,
        )

        ticket = _build_ticket(
            ticket_id=ticket_id,
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

        try:
            doc_ref = self._db.client.collection(_COLLECTION).document()
            from pprint import pprint
            print("\n========== TICKET ==========")

            for key, value in ticket.items():
                print(f"{key}: {type(value)}")
                if not isinstance(value, (str, int, float, bool, dict, list, type(None))):
                    print(">>> NON-SERIALIZABLE FIELD:", key)
                    pprint(value)

            print("============================\n")
            doc_ref.set(ticket)
            logger.info(
                "Expert ticket created doc_id=%s ticket_id=%s",
                doc_ref.id,
                ticket_id,
            )
        except Exception as exc:
            logger.exception(
                "Failed to create expert ticket ticket_id=%s",
                ticket_id,
            )
            raise FirestoreException(
                message="Failed to create expert ticket",
                details={
                    "ticket_id": ticket_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc

        return ticket_id

    # ------------------------------------------------------------------
    # resolve_ticket
    # ------------------------------------------------------------------
    def resolve_ticket(
        self,
        *,
        ticket_id: str,
        expert_reply: str,
    ) -> Dict[str, Any]:
        """
        Mark a ticket as Resolved and store the expert reply.

        Returns the updated ticket document dict.
        Raises FirestoreException if the ticket is not found or update fails.
        """
        now = datetime.now(timezone.utc)

        logger.info("Resolving expert ticket ticket_id=%s", ticket_id)

        try:
            query = (
                self._db.client
                .collection(_COLLECTION)
                .where("ticket_id", "==", ticket_id)
                .limit(1)
                .get()
            )
        except Exception as exc:
            logger.exception("Firestore query failed for ticket_id=%s", ticket_id)
            raise FirestoreException(
                message="Firestore query failed",
                details={"ticket_id": ticket_id},
            ) from exc

        if not query:
            raise FirestoreException(
                message=f"Expert ticket not found: {ticket_id}",
                details={"ticket_id": ticket_id},
            )

        doc = query[0]
        ticket_data: Dict[str, Any] = doc.to_dict() or {}

        update_payload = {
            "status": "Resolved",
            "expert_reply": expert_reply,
            "resolved_at": now,
            "updated_at": now,
        }

        try:
            doc.reference.update(update_payload)
            logger.info("Expert ticket resolved ticket_id=%s", ticket_id)
        except Exception as exc:
            logger.exception("Failed to update expert ticket ticket_id=%s", ticket_id)
            raise FirestoreException(
                message="Failed to update expert ticket",
                details={"ticket_id": ticket_id, "type": exc.__class__.__name__},
            ) from exc

        ticket_data.update(update_payload)
        return ticket_data

    # ------------------------------------------------------------------
    # get_all_tickets
    # ------------------------------------------------------------------
    def get_all_tickets(self) -> list:
        """
        Return all expert tickets ordered by created_at descending (newest first).
        Used by the Streamlit dashboard.
        """
        try:
            docs = (
                self._db.client
                .collection(_COLLECTION)
                .order_by("created_at", direction="DESCENDING")
                .get()
            )
            tickets = []
            for doc in docs:
                data = doc.to_dict() or {}
                data["_doc_id"] = doc.id
                tickets.append(data)

            logger.info("Dashboard loaded %d tickets", len(tickets))
            return tickets

        except Exception as exc:
            logger.exception("Failed to load expert tickets from Firestore")
            raise FirestoreException(
                message="Failed to load expert tickets",
                details={"type": exc.__class__.__name__},
            ) from exc

    # ------------------------------------------------------------------
    # confirmation_message  (helper — composes farmer-facing text)
    # ------------------------------------------------------------------
    @staticmethod
    def confirmation_message(ticket_id: str) -> str:
        return (
            f"✅ Your request has been forwarded to an agricultural expert.\n\n"
            f"🎫 Ticket ID: {ticket_id}\n\n"
            f"Our agricultural expert will review your case shortly."
        )

    # ------------------------------------------------------------------
    # expert_reply_message  (helper — composes expert-reply text)
    # ------------------------------------------------------------------
    @staticmethod
    def expert_reply_message(ticket_id: str, reply: str) -> str:
        return (
            f"👨‍🌾 Agricultural Expert Reply\n\n"
            f"🎫 Ticket: {ticket_id}\n\n"
            f"{reply}\n\n"
            f"Thank you for using Krishi Kalyan."
        )


expert_service = ExpertService()
