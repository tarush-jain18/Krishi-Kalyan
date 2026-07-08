from app.ml.crop_predictor import crop_predictor
from app.ml.fertilizer_predictor import fertilizer_predictor
from app.ml.irrigation_predictor import irrigation_predictor


class ModelManager:

    def __init__(self):
        self.crop = crop_predictor
        self.fertilizer = fertilizer_predictor
        self.irrigation = irrigation_predictor
        self._pest = None

    @property
    def pest(self):
        if self._pest is None:
            from app.ml.pest_predictor import pest_predictor
            self._pest = pest_predictor
        return self._pest


model_manager = ModelManager()