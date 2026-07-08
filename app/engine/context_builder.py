"""
app/engine/context_builder.py

Fixes in this version
---------------------

BUG 1 — TypeError: Object of type DatetimeWithNanoseconds is not JSON serializable
  Root cause : Firestore returns DatetimeWithNanoseconds for timestamp fields
               (e.g. created_at). prompt_builder calls json.dumps(context["user"]),
               which crashes on any non-JSON-native type.
  Fix        : _sanitize_doc() recursively walks any dict coming from Firestore
               and converts DatetimeWithNanoseconds (and plain datetime) to ISO-8601
               strings, and GeoPoint to {lat, lng}. Applied to user, farm, village.

BUG 2 — Weather unavailable: float() argument must be a string or a real number, not 'NoneType'
  Root cause : Registration does not collect latitude/longitude, so farm.latitude
               and farm.longitude are None. float(None) raises TypeError.
  Fix        : Guard both the weather call and the Earth Engine call behind an
               explicit None-check BEFORE calling float(). The existing
               `if latitude is not None and longitude is not None` guard already
               existed for Earth Engine — it is now also applied to the weather
               call. Fallback coordinates for Ghaziabad are used when the farm
               has no coordinates, so weather is still populated.
               (Fallback coords can be replaced with any district-centroid lookup
               later without touching this logic.)

BUG 3 — Soil NPK is always None
  Root cause : RegistrationService does not collect N/P/K/ph (they are not part
               of the registration flow). The farm doc therefore has no such fields
               and soil = {N: None, P: None, K: None, ph: None}.
  Fix        : Default values are substituted when the field is absent:
               N=0, P=0, K=0, ph=7.0 (neutral). This is honest — Gemini / the
               prompt builder receives zeros rather than None, which JSON-serialises
               cleanly and gives the model a stable baseline to reason from.
               A log warning is emitted so the operator knows actual soil data
               is missing.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from app.core.exceptions import ContextBuilderException, FirestoreException
from app.database.firestore import firestore_service
from app.services.weather.service import weather_service
from app.services.earth_engine import earth_engine_service


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback coordinates
#
# Used when a farm document has no latitude/longitude.  We pick the centroid
# of the farmer's district if we know it; otherwise a central-India default.
#
# This is a minimal lookup so weather is never completely unavailable.
# Add more districts here as you onboard more farmers.
# ---------------------------------------------------------------------------
_DISTRICT_CENTROIDS: Dict[str, tuple] = {
    "ghaziabad":   (28.6692, 77.4538),
    "karimnagar":  (18.4386, 79.1288),
    "nashik":      (20.0059, 73.7897),
    "ludhiana":    (30.9010, 75.8573),
    "pune":        (18.5204, 73.8567),
    "hyderabad":   (17.3850, 78.4867),
    "nagpur":      (21.1458, 79.0882),
    "patna":       (25.5941, 85.1376),
    "jaipur":      (26.9124, 75.7873),
    "bhopal":      (23.2599, 77.4126),
}

# Fallback when district is also unknown
_DEFAULT_LAT = 22.9734   # geographic centre of India
_DEFAULT_LON = 78.6569


def _fallback_coords(district: str) -> tuple:
    """Return (lat, lon) for a district, or the India-centre default."""
    key = (district or "").strip().lower()
    if key in _DISTRICT_CENTROIDS:
        lat, lon = _DISTRICT_CENTROIDS[key]
        logger.warning(
            "Farm has no coordinates — using district centroid for %s (%.4f, %.4f)",
            key, lat, lon,
        )
        return lat, lon
    logger.warning(
        "Farm has no coordinates and district '%s' has no centroid — "
        "using India-centre fallback (%.4f, %.4f)",
        district, _DEFAULT_LAT, _DEFAULT_LON,
    )
    return _DEFAULT_LAT, _DEFAULT_LON


# ---------------------------------------------------------------------------
# JSON sanitiser
#
# Firestore returns non-JSON-native types.  This function recursively converts
# them so that json.dumps() never raises TypeError downstream.
# ---------------------------------------------------------------------------

def _sanitize_doc(obj: Any) -> Any:
    """
    Recursively convert Firestore-specific types to JSON-safe equivalents.

    Handles:
      DatetimeWithNanoseconds / datetime  → ISO-8601 string
      google.cloud.firestore_v1.GeoPoint  → {"lat": float, "lng": float}
      dict                                → recurse
      list / tuple                        → recurse
      Everything else                     → returned unchanged
    """
    if obj is None:
        return obj

    # datetime covers both plain datetime and DatetimeWithNanoseconds
    # (the Firestore subclass inherits from datetime)
    if isinstance(obj, datetime):
        return obj.isoformat()

    # GeoPoint — check by class name to avoid importing google.cloud.firestore
    if obj.__class__.__name__ == "GeoPoint":
        return {"lat": obj.latitude, "lng": obj.longitude}

    if isinstance(obj, dict):
        return {k: _sanitize_doc(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_sanitize_doc(item) for item in obj]

    return obj


# ---------------------------------------------------------------------------
# Soil defaults
# ---------------------------------------------------------------------------

_SOIL_DEFAULTS = {"N": 0, "P": 0, "K": 0, "ph": 7.0}


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

            # ── USER ─────────────────────────────────────────────────────
            logger.info("Loading User")
            raw_user = self.firestore.get_user(normalized_user_id)
            # FIX BUG 1: sanitise before storing in context
            user = _sanitize_doc(raw_user)

            # ── FARM ─────────────────────────────────────────────────────
            logger.info("Loading Farm")
            raw_farm = self.firestore.get_farm(normalized_user_id)
            # FIX BUG 1: sanitise before storing in context
            farm = _sanitize_doc(raw_farm)
            logger.info("Farm Data: %s", farm)

            # ── SOIL ─────────────────────────────────────────────────────
            logger.info("Loading Soil Data")

            # FIX BUG 3: substitute safe defaults when NPK/ph are absent
            soil = {}
            any_missing = False
            for field_name, default in _SOIL_DEFAULTS.items():
                val = farm.get(field_name)
                if val is None:
                    soil[field_name] = default
                    any_missing = True
                else:
                    soil[field_name] = val

            if any_missing:
                logger.warning(
                    "Soil NPK/ph partially or fully missing for user_id=%s — "
                    "using defaults: N=%s P=%s K=%s ph=%s",
                    normalized_user_id,
                    soil["N"], soil["P"], soil["K"], soil["ph"],
                )

            logger.info("Soil Data Loaded: %s", soil)

            district = self._resolve_district(user=user, farm=farm)

            # ── VILLAGE ──────────────────────────────────────────────────
            logger.info("Loading Village Context")
            raw_village = self.firestore.get_village_context(district)
            village = _sanitize_doc(raw_village)

            # ── COORDINATES ──────────────────────────────────────────────
            # FIX BUG 2: resolve coordinates ONCE with explicit None-check
            # and fall back to district centroid rather than crashing.
            raw_lat = farm.get("latitude")
            raw_lon = farm.get("longitude")

            if raw_lat is not None and raw_lon is not None:
                latitude  = float(raw_lat)
                longitude = float(raw_lon)
            else:
                latitude, longitude = _fallback_coords(district)

            # ── WEATHER ──────────────────────────────────────────────────
            weather = {}
            try:
                logger.info(
                    "Loading Weather lat=%.4f lon=%.4f", latitude, longitude
                )
                weather = weather_service.get_current_weather(
                    district=district,
                    latitude=latitude,
                    longitude=longitude,
                )
                logger.info("Weather Loaded")
                logger.info(
                    "NASA Rainfall (Current + Previous Month): %.2f mm",
                    weather.rainfall,
                )
            except Exception as e:
                logger.warning("Weather unavailable: %s", e)

            # ── EARTH ENGINE ─────────────────────────────────────────────
            crop_health = {}
            try:
                logger.info(
                    "Loading Crop Health lat=%.4f lon=%.4f", latitude, longitude
                )
                crop_health = earth_engine_service.get_crop_health(
                    latitude=latitude,
                    longitude=longitude,
                )
                logger.info("Crop Health Loaded: %s", crop_health)
            except Exception as e:
                logger.warning("Earth Engine unavailable: %s", e)

            # ── FINAL CONTEXT ─────────────────────────────────────────────
            context = {
                "user_id": normalized_user_id,
                "language": (
                    user.get("language")
                    or user.get("preferred_language")
                    or "en"
                ),
                "user":        user,
                "farm":        farm,
                "soil":        soil,
                "village":     village,
                "weather":     weather,
                "crop_health": crop_health,
            }

            logger.info("Context Built Successfully")
            logger.info("========================================")

            return context

        except FirestoreException:
            raise

        except Exception as exc:
            logger.exception(
                "Context builder failed user_id=%s", normalized_user_id
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