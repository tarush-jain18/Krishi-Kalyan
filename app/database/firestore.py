"""
app/database/firestore.py  [MODIFIED]

Changes vs original:
  - No new Firestore client is created. The existing self.client is reused.
  - Added two convenience methods:
      create_expert_ticket(ticket: dict) -> str    — returns Firestore doc ID
      update_expert_ticket(doc_id: str, fields: dict) -> None

  NOTE: expert_service.py accesses self._db.client directly for query
  flexibility (where-clause + order_by). These helper methods are provided
  as an optional thin wrapper for future use; they are not mandatory.

All original methods are UNCHANGED.
"""

import logging
from typing import Any, Dict, Optional

from google.cloud.exceptions import GoogleCloudError

from app.core.exceptions import FirestoreException
from app.database.firebase import db

logger = logging.getLogger(__name__)


class FirestoreService:
    def __init__(self, client: Any = None) -> None:
        self.client = client or db

    # ------------------------------------------------------------------
    # Original methods — UNCHANGED
    # ------------------------------------------------------------------

    def get_user(self, user_id: str) -> Dict[str, Any]:
        user = self._get_document("users", user_id)
        if user is None:
            raise FirestoreException(
                message=f"User not found: {user_id}",
                details={
                    "collection": "users",
                    "document_id": user_id,
                },
            )
        return user

    def get_farm(self, user_id: str) -> Dict[str, Any]:
        farm = self._get_document("farms", user_id)
        if farm is None:
            logger.warning(
                "Farm profile missing for user_id=%s; continuing with empty farm context",
                user_id,
            )
            return {}
        return farm

    def get_village_context(self, district: str) -> Dict[str, Any]:
        normalized_district = (district or "").strip().lower()
        if not normalized_district:
            logger.warning("District missing; continuing with empty village context")
            return {}

        village_context = self._get_document(
            "village_context",
            normalized_district,
        )
        if village_context is None:
            logger.warning(
                "Village context missing for district=%s; continuing with empty village context",
                normalized_district,
            )
            return {}
        return village_context

    def _get_document(
        self,
        collection: str,
        document_id: str,
    ) -> Optional[Dict[str, Any]]:
        if not document_id:
            raise FirestoreException(
                message=f"Document id is required for collection '{collection}'",
                details={"collection": collection},
            )

        try:
            logger.info(
                "Loading Firestore document collection=%s document_id=%s",
                collection,
                document_id,
            )
            snapshot = self.client.collection(collection).document(document_id).get()
        except GoogleCloudError as exc:
            logger.exception(
                "Firestore read failed collection=%s document_id=%s",
                collection,
                document_id,
            )
            raise FirestoreException(
                message="Firestore read failed",
                details={
                    "collection": collection,
                    "document_id": document_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected Firestore error collection=%s document_id=%s",
                collection,
                document_id,
            )
            raise FirestoreException(
                message="Unexpected Firestore error",
                details={
                    "collection": collection,
                    "document_id": document_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc

        if not snapshot.exists:
            logger.warning(
                "Firestore document not found collection=%s document_id=%s",
                collection,
                document_id,
            )
            return None

        data = snapshot.to_dict() or {}
        data.setdefault("id", document_id)
        return data

    # ------------------------------------------------------------------
    # NEW: Expert request helpers
    # ------------------------------------------------------------------

    def create_expert_ticket(self, ticket: Dict[str, Any]) -> str:
        """
        Create a new document in the expert_requests collection.

        Parameters
        ----------
        ticket : Full ticket dict as built by expert_service._build_ticket().

        Returns
        -------
        The auto-generated Firestore document ID (str).

        Raises FirestoreException on failure.
        """
        collection = "expert_requests"
        ticket_id = ticket.get("ticket_id", "unknown")

        try:
            doc_ref = self.client.collection(collection).document()
            doc_ref.set(ticket)
            logger.info(
                "Expert ticket stored collection=%s doc_id=%s ticket_id=%s",
                collection,
                doc_ref.id,
                ticket_id,
            )
            return doc_ref.id

        except GoogleCloudError as exc:
            logger.exception(
                "Firestore write failed collection=%s ticket_id=%s",
                collection,
                ticket_id,
            )
            raise FirestoreException(
                message="Failed to create expert ticket",
                details={
                    "collection": collection,
                    "ticket_id": ticket_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc

        except Exception as exc:
            logger.exception(
                "Unexpected error writing expert ticket ticket_id=%s",
                ticket_id,
            )
            raise FirestoreException(
                message="Unexpected error creating expert ticket",
                details={
                    "ticket_id": ticket_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc

    def update_expert_ticket(self, doc_id: str, fields: Dict[str, Any]) -> None:
        """
        Update specific fields on an expert_requests document.

        Parameters
        ----------
        doc_id  : The Firestore document ID returned by create_expert_ticket.
        fields  : Dict of fields to update (e.g. status, expert_reply, resolved_at).

        Raises FirestoreException on failure.
        """
        collection = "expert_requests"

        try:
            self.client.collection(collection).document(doc_id).update(fields)
            logger.info(
                "Expert ticket updated collection=%s doc_id=%s fields=%s",
                collection,
                doc_id,
                list(fields.keys()),
            )

        except GoogleCloudError as exc:
            logger.exception(
                "Firestore update failed collection=%s doc_id=%s",
                collection,
                doc_id,
            )
            raise FirestoreException(
                message="Failed to update expert ticket",
                details={
                    "collection": collection,
                    "doc_id": doc_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc

        except Exception as exc:
            logger.exception(
                "Unexpected error updating expert ticket doc_id=%s",
                doc_id,
            )
            raise FirestoreException(
                message="Unexpected error updating expert ticket",
                details={
                    "doc_id": doc_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc


firestore_service = FirestoreService()
