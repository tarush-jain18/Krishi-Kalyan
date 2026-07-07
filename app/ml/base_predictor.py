from abc import ABC, abstractmethod
from typing import Dict, Any


class BasePredictor(ABC):

    @abstractmethod
    def predict(self, *args, **kwargs) -> Dict[str, Any]:
        pass