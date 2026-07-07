import logging
from typing import Any, Dict, Optional

from google.cloud.exceptions import GoogleCloudError

from app.core.exceptions import FirestoreException
from app.database.firebase import db


logger = logging.getLogger(__name__)


class FirestoreService:
    def __init__(self, client: Any = None) -> None:
        self.client = client or db

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


firestore_service = FirestoreService()
