import logging
from typing import Any, Callable, Dict, List

from google.genai import types
from app.tools.get_pest_risk import get_pest_risk
from app.tools.get_fertilizer_advice import get_fertilizer_advice
from app.core.exceptions import ToolExecutionException

from app.ml.model_manager import model_manager
from app.tools.mock_tools import (
    get_pest_diagnosis,
    get_crop_recommendation,
    get_weather_advisory,
    
    get_irrigation_advice,
)


logger = logging.getLogger(__name__)

ToolFunction = Callable[..., Dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolFunction] = {
            "get_crop_recommendation": get_crop_recommendation,
            "get_weather_advisory": get_weather_advisory,
            "get_irrigation_advice": get_irrigation_advice,
            "get_pest_risk": get_pest_risk,
            "get_fertilizer_advice": get_fertilizer_advice,
            "get_pest_diagnosis": get_pest_diagnosis,
            "get_fertilizer_recommendation": lambda snapshot: model_manager.fertilizer.predict(snapshot),
            "get_irrigation_recommendation": lambda snapshot: model_manager.irrigation.predict(snapshot),
        }

    @property
    def gemini_tool(self) -> types.Tool:
        return types.Tool(function_declarations=self.function_declarations)

    @property
    def function_declarations(self) -> List[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
        name="get_crop_recommendation",
        description=(
            "Predict the most suitable crop using the trained machine learning model."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {},
        }
    ),
            types.FunctionDeclaration(
                name="get_weather_advisory",
                description=(
                    "Provide weather-aware advisory for irrigation, spraying, "
                    "fertilizer, and general farm activity planning."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "district": {
                            "type": "STRING",
                            "description": "Farmer's district.",
                        },
                        "crop": {
                            "type": "STRING",
                            "description": "Crop name, if the farmer mentioned one.",
                        },
                        "activity": {
                            "type": "STRING",
                            "description": (
                                "Farm activity such as irrigation, spraying, "
                                "sowing, or fertilizer application."
                            ),
                        },
                    },
                    "required": ["district"],
                },
            ),
                        types.FunctionDeclaration(
                name="get_irrigation_advice",
                description=(
                    "Determine whether irrigation is needed using "
                    "weather, satellite crop health, and crop information."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "district": {
                            "type": "STRING",
                            "description": "Farmer's district.",
                        },
                        "latitude": {
                            "type": "NUMBER",
                            "description": "Farm latitude.",
                        },
                        "longitude": {
                            "type": "NUMBER",
                            "description": "Farm longitude.",
                        },
                        "crop": {
                            "type": "STRING",
                            "description": "Current crop name.",
                        },
                    },
                    "required": [
                        "district",
                        "latitude",
                        "longitude",
                        "crop",
                    ],
                },
            ),
                        types.FunctionDeclaration(
                name="get_fertilizer_recommendation",
                description="Predict the most suitable fertilizer using the trained machine learning model.",
                parameters={
                    "type": "OBJECT",
                    "properties": {},
                },
            ),
                        types.FunctionDeclaration(
                name="get_irrigation_recommendation",
                description="Predict irrigation recommendation using the trained machine learning model.",
                parameters={
                    "type": "OBJECT",
                    "properties": {},
                },
            ),
                        types.FunctionDeclaration(
                name="get_pest_risk",
                description=(
                    "Predict pest risk using weather, satellite crop health, "
                    "crop type and farm location."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "district": {
                            "type": "STRING",
                            "description": "Farmer's district.",
                        },
                        "latitude": {
                            "type": "NUMBER",
                            "description": "Farm latitude.",
                        },
                        "longitude": {
                            "type": "NUMBER",
                            "description": "Farm longitude.",
                        },
                        "crop": {
                            "type": "STRING",
                            "description": "Current crop.",
                        },
                    },
                    "required": [
                        "district",
                        "latitude",
                        "longitude",
                        "crop",
                    ],
                },
            ),
                        types.FunctionDeclaration(
                name="get_fertilizer_advice",
                description=(
                    "Recommend fertilizer using crop, soil type, "
                    "weather and satellite crop health."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "district": {
                            "type": "STRING"
                        },
                        "latitude": {
                            "type": "NUMBER"
                        },
                        "longitude": {
                            "type": "NUMBER"
                        },
                        "crop": {
                            "type": "STRING"
                        },
                        "soil_type": {
                            "type": "STRING"
                        }
                    },
                    "required": [
                        "district",
                        "latitude",
                        "longitude",
                        "crop",
                        "soil_type"
                    ]
                },
            ),
                types.FunctionDeclaration(
                name="get_pest_diagnosis",
                description=(
                    "Detect the crop plant and disease from the uploaded crop image."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {},
                },
            )
        ]
    def execute(self,name: str,arguments: Dict[str, Any],context: Dict[str, Any],) -> Dict[str, Any]:
        if name not in self._tools:
            raise ToolExecutionException(
                message=f"Unknown tool selected by Gemini: {name}",
                details={"tool_name": name},
            )

        logger.info("Executing Tool")
        logger.info("Tool name=%s arguments=%s", name, arguments)

        try:
            if name == "get_crop_recommendation":
                result = self._tools[name](
                    snapshot=context["snapshot"]
                )

            elif name == "get_irrigation_advice":
                result = self._tools[name](
                    snapshot=context["snapshot"]
                )

            elif name == "get_pest_risk":
                result = self._tools[name](
                    snapshot=context["snapshot"]
                )

            elif name == "get_fertilizer_recommendation":
                result = self._tools[name](
                    snapshot=context["snapshot"]
                )
            
            elif name == "get_irrigation_recommendation":
                result = self._tools[name](
                    snapshot=context["snapshot"]
                )
                
            elif name == "get_fertilizer_advice":
                result = self._tools[name](
                    snapshot=context["snapshot"]
                )

            elif name=="get_pest_diagnosis":
                result=self._tools[name](image_path=context["image_path"])
            else:
                result=self._tools[name](**arguments)
        except Exception as exc:
            logger.exception("Tool execution failed tool=%s", name)
            raise ToolExecutionException(
                message=f"Tool execution failed: {name}",
                details={
                    "tool_name": name,
                    "type": exc.__class__.__name__,
                },
            ) from exc

        logger.info("Tool Executed Successfully")
        return result


tool_registry = ToolRegistry()
tool = tool_registry.gemini_tool
