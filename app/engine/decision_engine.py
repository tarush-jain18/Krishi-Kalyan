"""
app/engine/decision_engine.py  [MODIFIED]

Changes vs original:
  - process() now RETURNS a tuple (ai_response: str, context: Dict) instead of
    just str, so callers (telegram.py) can access the built context for expert
    ticket creation WITHOUT re-running context building.

  BACKWARD COMPATIBILITY NOTE:
    If any existing caller only unpacks the first element, e.g.:
        result = decision_engine.process(...)
    ...this is still safe in Python — the call succeeds and result is the tuple.
    The caller must be updated to:
        result, context = decision_engine.process(...)
    OR stay with:
        result = decision_engine.process(...)[0]

  The telegram.py in this module is updated to unpack the tuple correctly.
  The /chat and /chat/image REST endpoints in main.py only use result[0],
  which is unchanged behaviour.

Business logic is completely unchanged.
"""

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.core.exceptions import ValidationException
from app.engine.context_builder import context_builder
from app.engine.farm_snapshot_builder import farm_snapshot_builder
from app.engine.prompt_builder import prompt_builder
from app.services.gemini import gemini_service

logger = logging.getLogger(__name__)


class DecisionEngine:
    def __init__(
        self,
        context_builder_service: Any = context_builder,
        llm_service: Any = gemini_service,
    ) -> None:
        self.context_builder = context_builder_service
        self.llm_service = llm_service

    def process(
        self,
        user_id: str,
        message: str,
        image_path: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Process a farmer query end-to-end.

        Returns
        -------
        Tuple of (ai_response: str, context: Dict[str, Any])

        The context dict is the full farmer context enriched with snapshot,
        image_path, and detected_language. Callers that only need the text
        can index [0]:  response = engine.process(...)[0]
        """
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

        # Return both the AI response and the full context so callers can
        # pass the context to expert_service.create_ticket() without
        # rebuilding it.
        return final_response, context

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
