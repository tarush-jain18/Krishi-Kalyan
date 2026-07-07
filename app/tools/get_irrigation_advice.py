from app.services.weather.service import weather_service
from app.services.earth_engine import earth_engine_service


def get_irrigation_advice(
    district: str,
    latitude: float,
    longitude: float,
    crop: str,
):
    weather = weather_service.get_current_weather(district)

    crop_health = earth_engine_service.get_crop_health(
        latitude,
        longitude,
    )

    temperature = weather.temperature
    rain = weather.rain_probability
    ndvi = crop_health["ndvi"]

    irrigate = False
    reason = []

    if rain < 30:
        irrigate = True
        reason.append("Low probability of rainfall.")

    if temperature > 32:
        irrigate = True
        reason.append("High temperature.")

    if ndvi < 0.35:
        irrigate = True
        reason.append("Satellite indicates stressed vegetation.")

    if irrigate:
        advice = "Irrigation recommended."

    else:
        advice = "No irrigation needed today."

    return {
        "crop": crop,
        "temperature": temperature,
        "rain_probability": rain,
        "ndvi": ndvi,
        "recommendation": advice,
        "reason": reason,
    }