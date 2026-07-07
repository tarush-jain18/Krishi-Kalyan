from typing import Any, Dict, List

from pydantic import BaseModel, Field


class OpenWeatherRequest(BaseModel):
    district: str = Field(..., min_length=1)
    country_code: str = Field(default="IN", min_length=2, max_length=2)

    @property
    def query(self) -> str:
        return f"{self.district.strip()},{self.country_code.upper()}"


class OpenWeatherCurrentResponse(BaseModel):
    name: str
    dt: int
    main: Dict[str, Any]
    weather: List[Dict[str, Any]]
    wind: Dict[str, Any] = Field(default_factory=dict)
    rain: Dict[str, Any] = Field(default_factory=dict)


class OpenWeatherForecastItem(BaseModel):
    dt: int
    main: Dict[str, Any]
    weather: List[Dict[str, Any]]
    wind: Dict[str, Any] = Field(default_factory=dict)
    pop: float = 0.0


class OpenWeatherForecastResponse(BaseModel):
    list: List[OpenWeatherForecastItem]
