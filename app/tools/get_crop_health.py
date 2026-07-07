from app.services.earth_engine import earth_engine_service


def get_crop_health(latitude, longitude):

    return earth_engine_service.get_crop_health(
        latitude,
        longitude
    )