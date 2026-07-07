from typing import Dict, Any

from app.services.weather.service import weather_service
from app.services.earth_engine import earth_engine_service


def get_pest_risk(
    district: str,
    latitude: float,
    longitude: float,
    crop: str,
) -> Dict[str, Any]:

    weather = weather_service.get_current_weather(district)

    crop_health = earth_engine_service.get_crop_health(
        latitude=latitude,
        longitude=longitude,
    )

    humidity = weather.humidity
    temperature = weather.temperature
    ndvi = crop_health["ndvi"]

    risk = "Low"
    pest = "None"
    reasons = []

    if humidity > 75:
        risk = "Medium"
        pest = "Fungal Diseases"
        reasons.append("High humidity favors fungal growth.")

    if humidity > 80 and temperature > 25:
        risk = "High"
        pest = "Aphids / Whiteflies"
        reasons.append("Warm and humid weather increases pest activity.")

    if ndvi < 0.3:
        risk = "High"
        reasons.append("Satellite data indicates stressed vegetation.")

    return {
        "crop": crop,
        "risk": risk,
        "likely_pest": pest,
        "temperature": temperature,
        "humidity": humidity,
        "ndvi": ndvi,
        "reasons": reasons,
    }