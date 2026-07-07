import os
import joblib
import numpy as np
import pandas as pd

from app.ml.base_predictor import BasePredictor


class CropPredictor(BasePredictor):

    def __init__(self):
        base_dir = os.path.dirname(__file__)
        model_dir = os.path.join(base_dir, "models")

        self.model = joblib.load(
            os.path.join(model_dir, "crop_model.pkl")
        )

        self.scaler = joblib.load(
            os.path.join(model_dir, "scaler.pkl")
        )

        self.label_encoder = joblib.load(
            os.path.join(model_dir, "label_encoder.pkl")
        )

    def predict(self, snapshot):

        N = snapshot["N"]
        P = snapshot["P"]
        K = snapshot["K"]

        temperature = snapshot["temperature"]
        humidity = snapshot["humidity"]
        ph = snapshot["ph"]
        rainfall = snapshot["rainfall"]

        features = pd.DataFrame([
            {
                "N": N,
                "P": P,
                "K": K,
                "temperature": temperature,
                "humidity": humidity,
                "ph": ph,
                "rainfall": rainfall,
            }
        ])

        scaled_features = self.scaler.transform(features)

        prediction = self.model.predict(scaled_features)[0]

        probabilities = self.model.predict_proba(scaled_features)[0]

        crop_name = self.label_encoder.inverse_transform(
            [prediction]
        )[0]

        classes = self.label_encoder.inverse_transform(
            np.arange(len(probabilities))
        )

        top_indices = np.argsort(probabilities)[::-1][:3]

        top_predictions = []

        for idx in top_indices:
            top_predictions.append(
                {
                    "crop": str(classes[idx]),
                    "probability": round(
                        float(probabilities[idx] * 100),
                        2,
                    ),
                }
            )

        return {
            "recommended_crop": str(crop_name),
            "confidence": round(
                float(max(probabilities) * 100),
                2,
            ),
            "top_predictions": top_predictions,
            "input_features": {
                "N": N,
                "P": P,
                "K": K,
                "temperature": temperature,
                "humidity": humidity,
                "ph": ph,
                "rainfall": rainfall,
            },
            "model": {
                "name": self.model.__class__.__name__,
                "version": "1.0",
            },
        }


crop_predictor = CropPredictor()