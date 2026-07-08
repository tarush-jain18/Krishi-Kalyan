import json
import tempfile
import os
import ee
from dotenv import load_dotenv

load_dotenv()


class EarthEngineService:

    def __init__(self):

        service_account = os.getenv("EARTH_ENGINE_SERVICE_ACCOUNT")
        credentials_json = os.getenv("EARTH_ENGINE_CREDENTIALS")

        if credentials_json:

            # Railway
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
            ) as f:

                f.write(credentials_json)
                credential_path = f.name

        else:

            # Local Mac
            credential_path = "app/credentials/earth_engine.json"

        credentials = ee.ServiceAccountCredentials(
            service_account,
            credential_path,
        )

        ee.Initialize(credentials)

    def get_crop_health(self, latitude, longitude):

        point = ee.Geometry.Point([longitude, latitude])

        area = point.buffer(100).bounds()

        image = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(area)
            .filterDate("2025-01-01", "2026-12-31")
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .sort("system:time_start", False)
            .first()
        )

        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

        ndvi_value = (
            ndvi.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=area,
                scale=10
            )
            .get("NDVI")
            .getInfo()
        )

        if ndvi_value is None:
            return {
                "status": "No satellite data available"
            }

        if ndvi_value > 0.7:
            health = "Excellent"

        elif ndvi_value > 0.5:
            health = "Healthy"

        elif ndvi_value > 0.3:
            health = "Moderate"

        else:
            health = "Poor"

        return {
            "ndvi": round(ndvi_value, 3),
            "crop_health": health
        }


earth_engine_service = EarthEngineService()
