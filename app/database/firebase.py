import json
import logging
import os
from pathlib import Path
from typing import Optional

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

DEFAULT_CREDENTIALS_PATH = "firebase_key.json"


class FirebaseClient:
    def __init__(self, credentials_path: Optional[str] = None):
        self.credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._db = None

    def initialize(self):
        if self._db is not None:
            return self._db

        try:
            firebase_admin.get_app()
            logger.info("Firebase app already initialized")

        except ValueError:

            firebase_json = os.getenv("FIREBASE_CREDENTIALS")

            if firebase_json:
                logger.info("Initializing Firebase from FIREBASE_CREDENTIALS")

                cred = credentials.Certificate(
                    json.loads(firebase_json)
                )

            else:
                credential_path = Path(self.credentials_path)

                if not credential_path.exists():
                    raise RuntimeError(
                        f"Firebase credentials file not found: {credential_path}"
                    )

                logger.info(
                    "Initializing Firebase from %s",
                    credential_path,
                )

                cred = credentials.Certificate(str(credential_path))

            firebase_admin.initialize_app(cred)

        self._db = firestore.client()
        logger.info("Firestore client initialized")

        return self._db

    @property
    def db(self):
        return self.initialize()


firebase_client = FirebaseClient()
db = firebase_client.db
