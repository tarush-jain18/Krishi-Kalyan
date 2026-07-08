import os

import numpy as np
import tensorflow as tf

from app.ml.base_predictor import BasePredictor


class PestPredictor(BasePredictor):

    IMG_SIZE = (224, 224)

    MODEL_NAME = "KrishiVision"
    MODEL_VERSION = "v1.0"

    def __init__(self):

        base_dir = os.path.dirname(__file__)
        self.model_dir = os.path.join(base_dir, "models")

        self.model = None

        self.class_names = [
            "Cashew anthracnose",
            "Cashew gumosis",
            "Cashew healthy",
            "Cashew leaf miner",
            "Cashew red rust",
            "Cassava bacterial blight",
            "Cassava brown spot",
            "Cassava green mite",
            "Cassava healthy",
            "Cassava mosaic",
            "Maize fall armyworm",
            "Maize grasshoper",
            "Maize healthy",
            "Maize leaf beetle",
            "Maize leaf blight",
            "Maize leaf spot",
            "Maize streak virus",
            "Tomato healthy",
            "Tomato leaf blight",
            "Tomato leaf curl",
            "Tomato septoria leaf spot",
            "Tomato verticulium wilt",
        ]
    def load_model(self):

        if self.model is None:

            self.model = tf.keras.models.load_model(
                os.path.join(
                    self.model_dir,
                    "final_pest_model.keras",
                )
            )
    def predict(self, image_path: str):
        self.load_model()
        image = tf.keras.utils.load_img(
            image_path,
            target_size=self.IMG_SIZE,
        )

        image = tf.keras.utils.img_to_array(image)

        image = np.expand_dims(image, axis=0)

        predictions = self.model.predict(
            image,
            verbose=0,
        )[0]

        best_index = np.argmax(predictions)

        predicted_class = self.class_names[best_index]

        confidence = round(
            float(predictions[best_index]) * 100,
            2,
        )

        parts = predicted_class.split()

        plant = parts[0]

        disease = " ".join(parts[1:]).title()

        if disease.lower() == "healthy":

            health_status = "Healthy"

            severity = {
                "level": "None",
                "confidence": 100.0,
            }

        else:

            health_status = "Diseased"

            if confidence >= 98:
                level = "High"

            elif confidence >= 90:
                level = "Moderate"

            else:
                level = "Low"

            severity = {
                "level": level,
                "confidence": confidence,
            }

        return {

            "plant": {

                "name": plant,

                "confidence": confidence,

            },

            "health_status": health_status,

            "diagnosis": {

                "name": disease,

                "confidence": confidence,

            },

            "severity": severity,

            "model": {

                "name": self.MODEL_NAME,

                "version": self.MODEL_VERSION,

            },

        }


pest_predictor = PestPredictor()