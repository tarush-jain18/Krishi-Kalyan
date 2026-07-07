from app.services.weather.nasa_client import nasa_client

print(
    nasa_client.get_recent_rainfall(
        latitude=18.4386,
        longitude=79.1288,
    )
)