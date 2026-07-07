from typing import Any, Dict


class FarmSnapshotBuilder:

    def build(self, context: Dict[str, Any]) -> Dict[str, Any]:

        farm = context.get("farm", {})
        soil = context.get("soil", {})
        weather = context.get("weather", {})
        crop_health = context.get("crop_health", {})

        snapshot = {

            # Soil
            "N": soil.get("N"),
            "P": soil.get("P"),
            "K": soil.get("K"),
            "ph": soil.get("ph"),

            # Weather
            "temperature": getattr(weather, "temperature", None),
            "humidity": getattr(weather, "humidity", None),
            "rainfall": getattr(weather, "rainfall", 0.0),

            # Farm
            "crop": farm.get("current_crop"),
            "soil_type": farm.get("soil_type"),
            "district": farm.get("district"),
            "latitude": farm.get("latitude"),
            "longitude": farm.get("longitude"),

            # Satellite
            "ndvi": crop_health.get("ndvi"),
            "crop_health": crop_health.get("crop_health"),
        }

        return snapshot


farm_snapshot_builder = FarmSnapshotBuilder()