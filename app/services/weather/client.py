import logging
import os
import time
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

from app.core.exceptions import WeatherException
from app.services.weather.schemas import OpenWeatherRequest


logger = logging.getLogger(__name__)


class OpenWeatherClient:
    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(
        self,
        api_key: str = "",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("WEATHER_API_KEY", "")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

        if not self.api_key:
            raise WeatherException(
                message="OpenWeather API key is not configured",
                details={"env": "WEATHER_API_KEY"},
            )

    def get_current_weather(self, district: str) -> Dict[str, Any]:
        request = OpenWeatherRequest(district=district)
        logger.info("Fetching current weather district=%s", district)
        return self._get(
            endpoint="/weather",
            params={
                "q": request.query,
                "appid": self.api_key,
                "units": "metric",
            },
        )

    def get_forecast(self, district: str) -> Dict[str, Any]:
        request = OpenWeatherRequest(district=district)
        logger.info("Fetching weather forecast district=%s", district)
        return self._get(
            endpoint="/forecast",
            params={
                "q": request.query,
                "appid": self.api_key,
                "units": "metric",
            },
        )

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                if not self._is_retryable_status(exc.response.status_code):
                    logger.exception(
                        "OpenWeather request failed endpoint=%s status_code=%s",
                        endpoint,
                        exc.response.status_code,
                    )
                    raise WeatherException(
                        message="OpenWeather request failed",
                        details={
                            "endpoint": endpoint,
                            "status_code": exc.response.status_code,
                            "response": exc.response.text,
                        },
                    ) from exc

                self._retry_or_raise(endpoint=endpoint, attempt=attempt, exc=exc)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._retry_or_raise(endpoint=endpoint, attempt=attempt, exc=exc)
            except Exception as exc:
                logger.exception("Unexpected OpenWeather client error")
                raise WeatherException(
                    message="Unexpected OpenWeather client error",
                    details={"endpoint": endpoint, "type": exc.__class__.__name__},
                ) from exc

        raise WeatherException(
            message="OpenWeather retry loop ended unexpectedly",
            details={"endpoint": endpoint},
        )

    def _retry_or_raise(
        self,
        endpoint: str,
        attempt: int,
        exc: Exception,
    ) -> None:
        if attempt >= self.max_retries:
            logger.exception(
                "OpenWeather request failed after retries endpoint=%s",
                endpoint,
            )
            raise WeatherException(
                message="OpenWeather request failed after retries",
                details={
                    "endpoint": endpoint,
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            ) from exc

        delay_seconds = self.backoff_seconds * (2 ** attempt)
        logger.warning(
            "OpenWeather request failed; retrying endpoint=%s attempt=%s max_retries=%s delay=%s",
            endpoint,
            attempt + 1,
            self.max_retries,
            delay_seconds,
        )
        time.sleep(delay_seconds)

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in {429, 500, 502, 503, 504}
