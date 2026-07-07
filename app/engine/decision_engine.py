import json
import logging
from app.engine.prompt_builder import prompt_builder
import re
from typing import Any, Dict
from typing import Optional
from app.core.exceptions import ValidationException
from app.engine.context_builder import context_builder
from app.engine.farm_snapshot_builder import farm_snapshot_builder
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.services.gemini import gemini_service
from app.engine.farm_snapshot_builder import farm_snapshot_builder

logger = logging.getLogger(__name__)


class DecisionEngine:
    def __init__(
        self,
        context_builder_service: Any = context_builder,
        llm_service: Any = gemini_service,
    ) -> None:
        self.context_builder = context_builder_service
        self.llm_service = llm_service

    def process(self, user_id: str, message: str,image_path: Optional[str] = None,) -> str:
        normalized_user_id = (user_id or "").strip()
        normalized_message = (message or "").strip()

        if not normalized_user_id:
            raise ValidationException(
                message="user_id is required",
                details={"field": "user_id"},
            )
        if not normalized_message:
            raise ValidationException(
                message="message is required",
                details={"field": "message"},
            )

        self._log_start(
            user_id=normalized_user_id,
            message=normalized_message,
        )

        language = self.detect_language(normalized_message)
        logger.info("Language Detection Complete")
        logger.info("Detected Language : %s", language)
        context = self.context_builder.build(user_id=normalized_user_id)
        context["detected_language"] = language
        snapshot = farm_snapshot_builder.build(context)
        context["snapshot"] = snapshot
        context["image_path"] = image_path
        logger.info("Farm Snapshot")
        logger.info(snapshot)

        prompt = prompt_builder.build(
            context=context,
            user_message=normalized_message,
        )

        final_response, selected_tool = self.llm_service.chat(
            prompt=prompt,
            context=context,
        )

        if selected_tool:
            logger.info(
                "Tool execution path completed tool=%s",
                selected_tool["name"],
            )
        else:
            logger.info("Gemini answered without tool execution")

        logger.info("Returning Response")
        logger.info("================================================")
        return final_response

    @staticmethod
    def detect_language(message: str) -> str:
        if re.search(r"[\u0C00-\u0C7F]", message):
            return "te"
        if re.search(r"[\u0900-\u097F]", message):
            return "hi"
        if re.search(r"[\u0C80-\u0CFF]", message):
            return "kn"
        if re.search(r"[\u0B80-\u0BFF]", message):
            return "ta"
        return "en"

    
    @staticmethod
    def _log_start(user_id: str, message: str) -> None:
        logger.info("================================================")
        logger.info("Decision Engine Started")
        logger.info("User ID : %s", user_id)
        logger.info("Message : %s", message)
        logger.info("================================================")


decision_engine = DecisionEngine()
