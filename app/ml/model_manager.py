from app.ml.crop_predictor import crop_predictor
from app.ml.pest_predictor import pest_predictor


class ModelManager:

    def __init__(self):

        self.crop = crop_predictor

        self.pest = pest_predictor


model_manager = ModelManager()