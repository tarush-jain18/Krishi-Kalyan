import os
import joblib
import numpy as np
import pandas as pd

from app.ml.base_predictor import BasePredictor


class FertilizerPredictor(BasePredictor):

    def __init__(self):

        base_dir = os.path.dirname(__file__)
        model_dir = os.path.join(base_dir, "models")

        self.model = joblib.load(
            os.path.join(model_dir, "fertilizer_model.pkl")
        )

    def predict(self, snapshot):

        features = pd.DataFrame([
            {
                "Soil_pH": snapshot["ph"],
                "Nitrogen_Level": snapshot["N"],
                "Phosphorus_Level": snapshot["P"],
                "Potassium_Level": snapshot["K"],
                "Temperature": snapshot["temperature"],
                "Humidity": snapshot["humidity"],
                "Rainfall": snapshot["rainfall"],
                "Soil_Type": snapshot["soil_type"],
                "Crop_Type": snapshot["crop"],
                "Season": snapshot["season"],
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

            top_predictions.append(
                {
                    "fertilizer": str(classes[idx]),
                    "probability": round(
                        float(probabilities[idx] * 100),
                        2,
                    ),
                }
            )

        return {
            "recommended_fertilizer": str(prediction),
            "confidence": round(
                float(max(probabilities) * 100),
                2,
            ),
            "top_predictions": top_predictions,
            "input_features": features.iloc[0].to_dict(),
            "model": {
                "name": self.model.named_steps["classifier"].__class__.__name__,
                "version": "1.0",
            },
        }


fertilizer_predictor = FertilizerPredictor()