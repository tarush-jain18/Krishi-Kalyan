import logging
from datetime import date, timedelta
from typing import Dict

import httpx

logger = logging.getLogger(__name__)


class NasaWeatherClient:

    BASE_URL = (
        "https://power.larc.nasa.gov/api/temporal/daily/point"
    )

    def get_recent_rainfall(
        self,
        latitude: float,
        longitude: float,
    ) -> float:

        today = date.today()

        # First day of current month
        current_month_start = today.replace(day=1)

        # Last day of previous month
        previous_month_end = current_month_start - timedelta(days=1)

        # First day of previous month
        previous_month_start = previous_month_end.replace(day=1)

        start = previous_month_start.strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")

        logger.info(
            "Fetching NASA rainfall lat=%s lon=%s start=%s end=%s",
            latitude,
            longitude,
            start,
            end,
        )

        response = httpx.get(
            self.BASE_URL,
            params={
                "parameters": "PRECTOTCORR",
                "community": "AG",
                "longitude": longitude,
                "latitude": latitude,
                "start": start,
                "end": end,
                "format": "JSON",
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        rainfall_data: Dict[str, float] = (
            data["properties"]["parameter"]["PRECTOTCORR"]
        )

        total_rainfall = 0.0

        for value in rainfall_data.values():

            if value < 0:
                continue

            total_rainfall += float(value)

        logger.info(
            "NASA rainfall total = %.2f mm",
            total_rainfall,
        )

        return round(total_rainfall, 2)


nasa_client = NasaWeatherClient()