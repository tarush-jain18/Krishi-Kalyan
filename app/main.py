"""
app/main.py  [MODIFIED]

Changes vs original:
  - Imports and registers the new expert router at /expert/*
  - All existing routes, middleware, and startup hooks are UNCHANGED.

The /chat and /chat/image endpoints are updated to unpack the new
(response, context) tuple returned by decision_engine.process() —
backward-compatible: callers still get the response string, context is
discarded at the REST boundary since it is not needed there.
"""

import logging
import os
import shutil
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.expert import router as expert_router        # ← NEW
from app.api.telegram import router as telegram_router
from app.api.voice import router as voice_router
from app.core.exceptions import KrishiKalyanException, ValidationException
from app.database.firestore import firestore_service
from app.engine.context_builder import context_builder
from app.engine.decision_engine import decision_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Krishi Kalyan Backend",
    version="1.0.0",
    description=(
        "Phase-1 backend for farmer advisory using FastAPI, Firestore, "
        "Gemini, and tool execution."
    ),
)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    user_id: Optional[str] = Field(default="demo_user", min_length=1)


# ---------------------------------------------------------------------------
# Startup — register Telegram webhook (UNCHANGED)
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def telegram_startup():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    webhook_url = (
        "https://unadducible-submedially-sana.ngrok-free.dev"
        "/telegram/webhook"
    )

    async with httpx.AsyncClient() as client:
        r1 = await client.get(
            f"https://api.telegram.org/bot{token}/deleteWebhook",
            params={"drop_pending_updates": True},
        )
        print(r1.json())

        r2 = await client.get(
            f"https://api.telegram.org/bot{token}/setWebhook",
            params={"url": webhook_url},
        )
        print(r2.json())


# ---------------------------------------------------------------------------
# Response helpers (UNCHANGED)
# ---------------------------------------------------------------------------

def success_response(data: Any) -> Dict[str, Any]:
    return {"success": True, "data": data}


def failure_response(exception: KrishiKalyanException) -> Dict[str, Any]:
    return {"success": False, "error": exception.to_error_response()}


# ---------------------------------------------------------------------------
# Exception handlers (UNCHANGED)
# ---------------------------------------------------------------------------

@app.exception_handler(KrishiKalyanException)
async def krishi_exception_handler(
    request: Request,
    exc: KrishiKalyanException,
) -> JSONResponse:
    logger.error(
        "Handled application exception path=%s code=%s message=%s",
        request.url.path,
        exc.error_code,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=failure_response(exc),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    validation_error = ValidationException(
        message="Request validation failed",
        details={"errors": exc.errors()},
    )
    logger.warning(
        "Request validation failed path=%s errors=%s",
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(
        status_code=validation_error.status_code,
        content=failure_response(validation_error),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception("Unhandled exception path=%s", request.url.path)
    internal_error = KrishiKalyanException(
        message="Internal server error",
        details={"type": exc.__class__.__name__},
    )
    return JSONResponse(
        status_code=internal_error.status_code,
        content=failure_response(internal_error),
    )


# ---------------------------------------------------------------------------
# Routes (UNCHANGED except /chat & /chat/image unpack tuple)
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> Dict[str, Any]:
    return success_response({"project": "Krishi Kalyan", "status": "Running"})


@app.get("/health")
async def health() -> Dict[str, Any]:
    return success_response({"status": "ok", "service": "krishi-kalyan-backend"})


@app.get("/context/{user_id}")
def get_context(user_id: str) -> Dict[str, Any]:
    return success_response(context_builder.build(user_id=user_id))


@app.get("/firebase-test/{user_id}")
def firebase_test(user_id: str) -> Dict[str, Any]:
    return success_response(firestore_service.get_user(user_id=user_id))


@app.post("/chat")
def chat(request: ChatRequest) -> Dict[str, Any]:
    user_id = request.user_id or "demo_user"
    # decision_engine.process() now returns (response, context) — unpack
    response, _context = decision_engine.process(
        user_id=user_id,
        message=request.message.strip(),
    )
    return success_response({"user_id": user_id, "response": response})


@app.post("/chat/image")
async def chat_with_image(
    file: UploadFile = File(...),
    message: str = Form(""),
    user_id: str = Form("demo_user"),
) -> Dict[str, Any]:
    image_path = os.path.join(UPLOAD_FOLDER, file.filename)
    with open(image_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # decision_engine.process() now returns (response, context) — unpack
    response, _context = decision_engine.process(
        user_id=user_id,
        message=message,
        image_path=image_path,
    )
    return success_response({"user_id": user_id, "response": response})


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(voice_router, tags=["Voice"])
app.include_router(telegram_router)
app.include_router(expert_router)              # ← NEW: /expert/reply, /expert/tickets


# ---------------------------------------------------------------------------
# Weather test (UNCHANGED)
# ---------------------------------------------------------------------------

from app.services.weather.service import weather_service  # noqa: E402


@app.get("/weather-test")
def weather_test():
    return weather_service.get_current_weather("Karimnagar")
