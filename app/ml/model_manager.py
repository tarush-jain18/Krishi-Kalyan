from app.ml.crop_predictor import crop_predictor
from app.ml.pest_predictor import pest_predictor
from app.ml.fertilizer_predictor import fertilizer_predictor
from app.ml.irrigation_predictor import irrigation_predictor

class ModelManager:

    def __init__(self):

        self.crop = crop_predictor
        self.pest = pest_predictor
        self.fertilizer = fertilizer_predictor
        self.irrigation = irrigation_predictor


model_manager = ModelManager()