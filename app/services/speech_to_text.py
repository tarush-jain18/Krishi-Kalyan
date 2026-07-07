import os

from faster_whisper import WhisperModel


class SpeechToTextService:

    def __init__(self):

        self.model = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8",
        )

    def transcribe(self, audio_path: str):

        segments, info = self.model.transcribe(
            audio_path,
            beam_size=5,
            vad_filter=True,
        )

        transcript = ""

        for segment in segments:
            transcript += segment.text + " "

        return {
            "text": transcript.strip(),
            "language": info.language,
            "language_probability": round(
                info.language_probability,
                2,
            ),
        }


speech_to_text_service = SpeechToTextService()