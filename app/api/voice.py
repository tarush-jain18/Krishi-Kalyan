import os
import tempfile

from fastapi import APIRouter, File, Form, UploadFile

from app.engine.decision_engine import decision_engine
from app.services.speech_to_text import speech_to_text_service

router = APIRouter()


@router.post("/voice")
async def voice_chat(
    file: UploadFile = File(...),
    user_id: str = Form("demo_user"),
):

    suffix = os.path.splitext(file.filename)[1]

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=suffix,
    ) as temp:

        temp.write(await file.read())
        temp_path = temp.name

    try:

        speech = speech_to_text_service.transcribe(temp_path)

        transcript = speech["text"]

        response = decision_engine.process(
            user_id=user_id,
            message=transcript,
        )

        return {
            "success": True,
            "data": {
                "user_id": user_id,
                "transcript": transcript,
                "language": speech["language"],
                "response": response,
            },
        }

    finally:

        if os.path.exists(temp_path):
            os.remove(temp_path)