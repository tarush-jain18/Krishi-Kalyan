import os
import joblib
import numpy as np
import pandas as pd

from app.ml.base_predictor import BasePredictor


class IrrigationPredictor(BasePredictor):

    def __init__(self):

        base_dir = os.path.dirname(__file__)
        model_dir = os.path.join(base_dir, "models")

        self.model = joblib.load(
            os.path.join(model_dir, "irrigation_model.pkl")
        )

    def predict(self, snapshot):

        features = pd.DataFrame([
            {
                "Soil_Type": snapshot["soil_type"],
                "Soil_pH": snapshot["ph"],
                "Temperature_C": snapshot["temperature"],
                "Humidity": snapshot["humidity"],
                "Rainfall_mm": snapshot["rainfall"],
                "Wind_Speed_kmh": snapshot["wind_speed"],
                "Crop_Type": snapshot["crop"],
                "Crop_Growth_Stage": snapshot["growth_stage"],
                "Season": snapshot["season"],
                "Irrigation_Type": snapshot["irrigation_type"],
                "Water_Source": snapshot["water_source"],
                "Field_Area_hectare": snapshot["land_size"],
                "Mulching_Used": (
                    "Yes"
                    if snapshot.get("mulching_used", False)
                    else "No"
                ),
                "Region": snapshot["district"],
            }
        ])

        prediction = self.model.predict(features)[0]

        probabilities = self.model.predict_proba(features)[0]

        classes = self.model.named_steps[
            "classifier"
        ].classes_

        top_indices = np.argsort(probabilities)[::-1][:3]

        top_predictions = []

        for idx in top_indices:
            display_map = {
                    "Low": "No Irrigation",
                    "Medium": "Moderate Irrigation",
                    "High": "Heavy Irrigation",
                }
            top_predictions.append(
                {
                    "recommendation": str(classes[idx]),
                    "probability": round(
                        float(probabilities[idx] * 100),
                        2,
                    ),
                }
            )
        print(prediction)
        print(type(prediction))
        water_map = {
            "No Irrigation": 0,
            "Moderate Irrigation": 12,
            "Heavy Irrigation": 25,
        }


        if prediction == "Low":
            prediction = "No Irrigation"
        elif prediction == "Medium":
            prediction = "Moderate Irrigation"
        elif prediction == "High":
            prediction = "Heavy Irrigation"
        urgency_map = {
            "No Irrigation": "Low",
            "Moderate Irrigation": "Medium",
            "Heavy Irrigation": "High",
        }

        return {
            "irrigation_recommendation": str(prediction),
            "confidence": round(
                float(max(probabilities) * 100),
                2,
            ),
            "recommended_water_mm": water_map.get(
                prediction,
                0,
            ),
            "urgency": urgency_map.get(
                prediction,
                "Low",
            ),
            "top_predictions": top_predictions,
            "model": {
                "name": self.model.named_steps[
                    "classifier"
                ].__class__.__name__,
                "version": "1.0",
            },
        }


irrigation_predictor = IrrigationPredictor()