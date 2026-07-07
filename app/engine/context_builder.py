import logging
from typing import Any, Dict

from app.core.exceptions import ContextBuilderException, FirestoreException
from app.database.firestore import firestore_service
from app.services.weather.service import weather_service
from app.services.earth_engine import earth_engine_service


logger = logging.getLogger(__name__)


class ContextBuilder:
    def __init__(self, firestore: Any = firestore_service) -> None:
        self.firestore = firestore

    def build(self, user_id: str) -> Dict[str, Any]:
        normalized_user_id = (user_id or "").strip()

        if not normalized_user_id:
            raise ContextBuilderException(
                message="user_id is required to build context",
                details={"field": "user_id"},
            )

        try:

            logger.info("========================================")
            logger.info("Context Builder Started")

            logger.info("Loading User")
            user = self.firestore.get_user(normalized_user_id)

            logger.info("Loading Farm")
            farm = self.firestore.get_farm(normalized_user_id)
            logger.info("Farm Data: %s", farm)
            # ---------------- SOIL DATA ----------------

            logger.info("Loading Soil Data")

            soil = {
                "N": farm.get("N"),
                "P": farm.get("P"),
                "K": farm.get("K"),
                "ph": farm.get("ph"),
            }

            logger.info("Soil Data Loaded")
            logger.info(soil)

            district = self._resolve_district(user=user, farm=farm)

            logger.info("Loading Village Context")
            village = self.firestore.get_village_context(district)

            # ---------------- WEATHER ----------------

            weather = {}

            try:
                logger.info("Loading Weather")

                latitude = farm.get("latitude")
                longitude = farm.get("longitude")

                weather = weather_service.get_current_weather(
                    district=district,
                    latitude=float(latitude),
                    longitude=float(longitude),
                )

                logger.info("Weather Loaded")
                logger.info(
                    "NASA Rainfall (Current + Previous Month): %.2f mm",
                    weather.rainfall,
                )

            except Exception as e:
                logger.warning("Weather unavailable : %s", e)

            # ---------------- EARTH ENGINE ----------------

            crop_health = {}

            try:

                if latitude is not None and longitude is not None:

                    logger.info("Loading Crop Health")

                    crop_health = earth_engine_service.get_crop_health(
                        latitude=float(latitude),
                        longitude=float(longitude),
                    )

                    logger.info("Crop Health Loaded")
                    logger.info(crop_health)

                else:

                    logger.warning(
                        "Farm coordinates missing. Earth Engine skipped."
                    )

            except Exception as e:

                logger.warning(
                    "Earth Engine unavailable : %s",
                    e,
                )

            # ---------------- FINAL CONTEXT ----------------

            context = {
                "user_id": normalized_user_id,
                "language": (
                    user.get("language")
                    or user.get("preferred_language")
                    or "en"
                ),
                "user": user,
                "farm": farm,
                "soil": soil,
                "village": village,
                "weather": weather,
                "crop_health": crop_health,
            }

            logger.info("Context Built Successfully")
            logger.info("========================================")

            return context

        except FirestoreException:
            raise

        except Exception as exc:

            logger.exception(
                "Context builder failed user_id=%s",
                normalized_user_id,
            )

            raise ContextBuilderException(
                message="Unable to build farmer context",
                details={
                    "user_id": normalized_user_id,
                    "type": exc.__class__.__name__,
                },
            ) from exc

    @staticmethod
    def _resolve_district(
        user: Dict[str, Any],
        farm: Dict[str, Any],
    ) -> str:

        district = (
            user.get("district")
            or user.get("location", {}).get("district")
            or farm.get("district")
            or farm.get("location", {}).get("district")
            or ""
        )

        return str(district).strip()


context_builder = ContextBuilder()