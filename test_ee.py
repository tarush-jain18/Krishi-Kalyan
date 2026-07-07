import os
import ee
from dotenv import load_dotenv

load_dotenv()

SERVICE_ACCOUNT = os.getenv("EARTH_ENGINE_SERVICE_ACCOUNT")
KEY_FILE = os.getenv("EARTH_ENGINE_CREDENTIALS")

credentials = ee.ServiceAccountCredentials(
    SERVICE_ACCOUNT,
    KEY_FILE
)

ee.Initialize(credentials)

print("✅ Earth Engine Connected")

image = ee.Image("USGS/SRTMGL1_003")

print(image.getInfo()["type"])