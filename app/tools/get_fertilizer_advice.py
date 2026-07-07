from typing import Dict, Any

from app.services.weather.service import weather_service
from app.services.earth_engine import earth_engine_service


def get_fertilizer_advice(
    district: str,
    latitude: float,
    longitude: float,
    crop: str,
    soil_type: str,
) -> Dict[str, Any]:

    weather = weather_service.get_current_weather(district)

    crop_health = earth_engine_service.get_crop_health(
        latitude=latitude,
        longitude=longitude,
    )

    ndvi = crop_health["ndvi"]

    fertilizer = "Balanced NPK (19:19:19)"
    dose = "25 kg/acre"
    timing = "Within the next 2 days"
    reasons = []

    if "black" in soil_type.lower():
        fertilizer = "NPK 20:20:0"
        dose = "50 kg/acre"
        reasons.append("Suitable for black cotton soil.")

    if crop.lower() == "cotton":
        fertilizer = "NPK 20:20:0"
        dose = "50 kg/acre"
        reasons.append("Recommended for cotton during vegetative growth.")

    if ndvi < 0.30:
        reasons.append(
            "Satellite data indicates crop stress. Nutrient supplementation may help."
        )

    if weather.rain_probability > 60:
        timing = "Wait until rainfall decreases."
        reasons.append(
            "Heavy rainfall can reduce fertilizer effectiveness."
        )

    return {
        "crop": crop,
        "soil_type": soil_type,
        "fertilizer": fertilizer,
        "dose": dose,
        "application_time": timing,
        "temperature": weather.temperature,
        "rain_probability": weather.rain_probability,
        "ndvi": ndvi,
        "reasons": reasons,
    }