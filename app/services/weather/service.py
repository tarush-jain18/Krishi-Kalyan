import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from app.services.weather.nasa_client import nasa_client
from app.core.exceptions import WeatherException
from app.services.weather.client import OpenWeatherClient
from app.services.weather.models import (
    WeatherAdvisory,
    WeatherForecastItem,
    WeatherSnapshot,
)
from app.services.weather.schemas import (
    OpenWeatherCurrentResponse,
    OpenWeatherForecastResponse,
)


logger = logging.getLogger(__name__)


class WeatherService:
    def __init__(self, client: Optional[OpenWeatherClient] = None) -> None:
        self._client = client

    @property
    def client(self) -> OpenWeatherClient:
        if self._client is None:
            self._client = OpenWeatherClient()
        return self._client

    def get_current_weather(self,district: str,latitude: float,longitude: float,) -> WeatherSnapshot:
        normalized_district = self._normalize_district(district)

        try:
            payload = self.client.get_current_weather(normalized_district)
            response = OpenWeatherCurrentResponse.model_validate(payload)
            rainfall = nasa_client.get_recent_rainfall(
                latitude=latitude,
                longitude=longitude,
            )

            weather = self._build_current_weather(
                district=normalized_district,
                response=response,
                rainfall=rainfall,
            )
            logger.info(
                "Current weather loaded district=%s condition=%s temperature=%s",
                normalized_district,
                weather.condition,
                weather.temperature,
            )
            return weather
        except WeatherException:
            raise
        except Exception as exc:
            logger.exception(
                "Failed to parse current weather district=%s",
                normalized_district,
            )
            raise WeatherException(
                message="Failed to parse current weather",
                details={
                    "district": normalized_district,
                    "type": exc.__class__.__name__,
                },
            ) from exc

    def get_forecast(self, district: str) -> List[WeatherForecastItem]:
        normalized_district = self._normalize_district(district)

        try:
            payload = self.client.get_forecast(normalized_district)
            response = OpenWeatherForecastResponse.model_validate(payload)
            forecast = [
                self._build_forecast_item(
                    district=normalized_district,
                    item=item,
                )
                for item in response.list[:8]
            ]
            logger.info(
                "Weather forecast loaded district=%s items=%s",
                normalized_district,
                len(forecast),
            )
            return forecast
        except WeatherException:
            raise
        except Exception as exc:
            logger.exception(
                "Failed to parse weather forecast district=%s",
                normalized_district,
            )
            raise WeatherException(
                message="Failed to parse weather forecast",
                details={
                    "district": normalized_district,
                    "type": exc.__class__.__name__,
                },
            ) from exc

    def get_weather_advisory(self, district: str) -> WeatherAdvisory:
        normalized_district = self._normalize_district(district)
        logger.info("Generating weather advisory district=%s", normalized_district)

        current_weather = self.get_current_weather(normalized_district)
        forecast = self.get_forecast(normalized_district)
        advisories = self._generate_advisories(
            current_weather=current_weather,
            forecast=forecast,
        )
        risk_level = self._calculate_risk_level(
            current_weather=current_weather,
            forecast=forecast,
        )

        advisory = WeatherAdvisory(
            district=normalized_district,
            current_weather=current_weather,
            forecast=forecast,
            advisories=advisories,
            risk_level=risk_level,
        )
        logger.info(
            "Weather advisory generated district=%s risk_level=%s",
            normalized_district,
            risk_level,
        )
        return advisory

    @staticmethod
    def _normalize_district(district: str) -> str:
        normalized_district = (district or "").strip()
        if not normalized_district:
            raise WeatherException(
                message="district is required for weather advisory",
                details={"field": "district"},
            )
        return normalized_district

    @staticmethod
    def _build_current_weather(
        district: str,
        response: OpenWeatherCurrentResponse,
        rainfall: float,
    ) -> WeatherSnapshot:
        condition = WeatherService._condition(response.weather)
        return WeatherSnapshot(
            district=district,
            temperature=float(response.main.get("temp", 0.0)),
            humidity=int(response.main.get("humidity", 0)),
            wind_speed=float(response.wind.get("speed", 0.0)),
            rain_probability=WeatherService._current_rain_probability(
                condition=condition,
                rain=response.rain,
            ),
            condition=condition,
            rainfall=rainfall,
            observed_at=datetime.utcfromtimestamp(response.dt),
        )

    @staticmethod
    def _build_forecast_item(
        district: str,
        item: Any,
    ) -> WeatherForecastItem:
        return WeatherForecastItem(
            district=district,
            forecast_time=datetime.utcfromtimestamp(item.dt),
            temperature=float(item.main.get("temp", 0.0)),
            humidity=int(item.main.get("humidity", 0)),
            wind_speed=float(item.wind.get("speed", 0.0)),
            rain_probability=WeatherService._percentage(item.pop),
            rainfall=float((item.rain or {}).get("3h",0.0)),
            condition=WeatherService._condition(item.weather),
        )

    @staticmethod
    def _condition(weather_items: List[Dict[str, Any]]) -> str:
        if not weather_items:
            return "Unknown"
        return str(
            weather_items[0].get("main")
            or weather_items[0].get("description")
            or "Unknown"
        )

    @staticmethod
    def _current_rain_probability(
        condition: str,
        rain: Dict[str, Any],
    ) -> int:
        if rain:
            return 100
        if condition.lower() in {"rain", "drizzle", "thunderstorm"}:
            return 80
        if condition.lower() == "clouds":
            return 30
        return 0

    @staticmethod
    def _percentage(value: float) -> int:
        return max(0, min(100, int(round(float(value) * 100))))

    @staticmethod
    def _generate_advisories(
        current_weather: WeatherSnapshot,
        forecast: List[WeatherForecastItem],
    ) -> List[str]:
        advisories: List[str] = []
        max_rain_probability = WeatherService._max_rain_probability(
            current_weather=current_weather,
            forecast=forecast,
        )
        max_temperature = WeatherService._max_temperature(
            current_weather=current_weather,
            forecast=forecast,
        )
        max_wind_speed = WeatherService._max_wind_speed(
            current_weather=current_weather,
            forecast=forecast,
        )

        if max_rain_probability >= 80:
            advisories.append("Heavy rainfall warning")
            advisories.append("Delay irrigation")
            advisories.append("Avoid pesticide spraying")
        elif max_rain_probability >= 60:
            advisories.append("Delay irrigation")
            advisories.append("Avoid pesticide spraying")

        if max_temperature >= 38:
            advisories.append("High heat warning")

        if max_wind_speed >= 8:
            advisories.append("Avoid pesticide spraying")

        if WeatherService._is_good_sowing_window(
            current_weather=current_weather,
            forecast=forecast,
        ):
            advisories.append("Good day for sowing")

        if not advisories:
            advisories.append("Weather is suitable for routine farm activity")

        return list(dict.fromkeys(advisories))

    @staticmethod
    def _calculate_risk_level(
        current_weather: WeatherSnapshot,
        forecast: List[WeatherForecastItem],
    ) -> str:
        max_rain_probability = WeatherService._max_rain_probability(
            current_weather=current_weather,
            forecast=forecast,
        )
        max_temperature = WeatherService._max_temperature(
            current_weather=current_weather,
            forecast=forecast,
        )
        max_wind_speed = WeatherService._max_wind_speed(
            current_weather=current_weather,
            forecast=forecast,
        )

        if max_rain_probability >= 80 or max_temperature >= 40:
            return "high"
        if max_rain_probability >= 60 or max_temperature >= 36 or max_wind_speed >= 8:
            return "moderate"
        return "low"

    @staticmethod
    def _is_good_sowing_window(
        current_weather: WeatherSnapshot,
        forecast: List[WeatherForecastItem],
    ) -> bool:
        max_rain_probability = WeatherService._max_rain_probability(
            current_weather=current_weather,
            forecast=forecast,
        )
        max_temperature = WeatherService._max_temperature(
            current_weather=current_weather,
            forecast=forecast,
        )
        max_wind_speed = WeatherService._max_wind_speed(
            current_weather=current_weather,
            forecast=forecast,
        )
        return (
            35 <= max_rain_probability <= 75
            and 20 <= max_temperature <= 35
            and max_wind_speed < 8
        )

    @staticmethod
    def _max_rain_probability(
        current_weather: WeatherSnapshot,
        forecast: List[WeatherForecastItem],
    ) -> int:
        values = [current_weather.rain_probability]
        values.extend(item.rain_probability for item in forecast)
        return max(values)

    @staticmethod
    def _max_temperature(
        current_weather: WeatherSnapshot,
        forecast: List[WeatherForecastItem],
    ) -> float:
        values = [current_weather.temperature]
        values.extend(item.temperature for item in forecast)
        return max(values)

    @staticmethod
    def _max_wind_speed(
        current_weather: WeatherSnapshot,
        forecast: List[WeatherForecastItem],
    ) -> float:
        values = [current_weather.wind_speed]
        values.extend(item.wind_speed for item in forecast)
        return max(values)


weather_service = WeatherService()
