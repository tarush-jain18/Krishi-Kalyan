from datetime import datetime
from typing import List

from pydantic import BaseModel, Field


class WeatherSnapshot(BaseModel):
    district: str
    temperature: float
    humidity: int
    rainfall: float
    wind_speed: float
    rain_probability: int = Field(ge=0, le=100)
    rainfall: float
    condition: str
    observed_at: datetime


class WeatherForecastItem(BaseModel):
    district: str
    forecast_time: datetime
    temperature: float
    humidity: int
    wind_speed: float
    rain_probability: int = Field(ge=0, le=100)
    condition: str


class WeatherAdvisory(BaseModel):
    district: str
    current_weather: WeatherSnapshot
    forecast: List[WeatherForecastItem]
    advisories: List[str]
    risk_level: str
