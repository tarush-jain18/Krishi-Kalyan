from typing import Any, Dict, Optional


class KrishiKalyanException(Exception):
    status_code = 500
    error_code = "INTERNAL_ERROR"
    default_message = "An unexpected error occurred"

    def __init__(
        self,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)

    def to_error_response(self) -> Dict[str, Any]:
        return {
            "code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class GeminiQuotaExceeded(KrishiKalyanException):
    status_code = 429
    error_code = "GEMINI_QUOTA_EXCEEDED"
    default_message = "Gemini quota exceeded. Please try again later."


class GeminiException(KrishiKalyanException):
    status_code = 502
    error_code = "GEMINI_ERROR"
    default_message = "Gemini service failed to generate a response"


class FirestoreException(KrishiKalyanException):
    status_code = 502
    error_code = "FIRESTORE_ERROR"
    default_message = "Firestore operation failed"


class WeatherException(KrishiKalyanException):
    status_code = 502
    error_code = "WEATHER_ERROR"
    default_message = "Weather service failed"


class ToolExecutionException(KrishiKalyanException):
    status_code = 500
    error_code = "TOOL_EXECUTION_ERROR"
    default_message = "Tool execution failed"


class ContextBuilderException(KrishiKalyanException):
    status_code = 500
    error_code = "CONTEXT_BUILDER_ERROR"
    default_message = "Unable to build farmer context"


class ValidationException(KrishiKalyanException):
    status_code = 400
    error_code = "VALIDATION_ERROR"
    default_message = "Invalid request"
    
class TelegramMediaException(KrishiKalyanException):
    def __init__(
        self,
        message="Telegram media error",
        details=None,
    ):
        super().__init__(
            message=message,
            error_code="TELEGRAM_MEDIA_ERROR",
            status_code=500,
            details=details,
        )


class TelegramServiceException(KrishiKalyanException):
    def __init__(
        self,
        message="Telegram service error",
        details=None,
    ):
        super().__init__(
            message=message,
            error_code="TELEGRAM_SERVICE_ERROR",
            status_code=500,
            details=details,
        )
