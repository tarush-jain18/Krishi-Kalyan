from app.services.earth_engine import earth_engine_service

result = earth_engine_service.get_ndvi(
    latitude=18.4386,
    longitude=79.1288,
)

print(result)