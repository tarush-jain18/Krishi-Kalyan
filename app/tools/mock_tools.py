from typing import Any, Dict, List

from app.services.weather.service import weather_service
from app.ml.model_manager import model_manager
from app.services.earth_engine import earth_engine_service


def get_crop_recommendation(snapshot):

    return model_manager.crop.predict(snapshot)


def get_pest_diagnosis(image_path: str):

    return model_manager.pest.predict(image_path)

def get_weather_advisory(district: str, crop: str = "", activity: str = "") -> Dict[str, Any]:
    district_value = (district or "").strip()
    if not district_value:
        raise ValueError("district is required")

    advisory = weather_service.get_weather_advisory(district_value).model_dump(
        mode="json"
    )
    advisory["crop"] = crop or "not provided"
    advisory["activity"] = activity or "general farm planning"
    return advisory



def get_irrigation_advice(
    district: str,
    latitude: float,
    longitude: float,
    crop: str = "",
) -> Dict[str, Any]:

    weather = weather_service.get_current_weather(
        district=district,
        latitude=latitude,
        longitude=longitude,
    )

    crop_health = earth_engine_service.get_crop_health(
        latitude=latitude,
        longitude=longitude,
    )

    ndvi = crop_health["ndvi"]

    irrigate = False
    reasons = []

    if weather.rain_probability < 30:
        irrigate = True
        reasons.append("Low probability of rainfall.")

    if weather.temperature > 32:
        irrigate = True
        reasons.append("High temperature may increase water demand.")

    if ndvi < 0.35:
        irrigate = True
        reasons.append("Satellite NDVI indicates crop stress.")

    if not reasons:
        reasons.append("Weather and satellite data do not indicate immediate irrigation.")

    return {
        "crop": crop or "Unknown",
        "temperature": weather.temperature,
        "humidity": weather.humidity,
        "rain_probability": weather.rain_probability,
        "ndvi": ndvi,
        "recommendation": (
            "Irrigation recommended"
            if irrigate
            else "No irrigation needed today"
        ),
        "reasons": reasons,
    }
