from app.services.speech_to_text import speech_to_text_service
import warnings

warnings.filterwarnings("ignore", category=RuntimeWarning)
result = speech_to_text_service.transcribe(
    "sample.m4a"
)

print(result)