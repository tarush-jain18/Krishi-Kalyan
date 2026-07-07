import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import errors, types

from app.core.config import settings
from app.core.exceptions import GeminiException, GeminiQuotaExceeded
from app.tools.tool_registry import tool_registry
from app.engine.prompt_builder import SYSTEM_INSTRUCTION, prompt_builder


logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(
        self,
        registry: Any = tool_registry,
        model: str = "gemini-2.5-flash",
        max_retries: int = 3,
        initial_backoff_seconds: int = 1,
    ) -> None:
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = model
        self.registry = registry
        self.max_retries = max_retries
        self.initial_backoff_seconds = initial_backoff_seconds

    def chat(self, prompt: str, context: Dict[str, Any], max_tool_calls: int = 3) -> Tuple[str, Optional[Dict[str, Any]]]:
        if not prompt or not prompt.strip():
            raise GeminiException(
                message="Prompt cannot be empty",
                details={"field": "prompt"},
            )

        logger.info("Sending Prompt To Gemini")

        contents: List[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part(text=prompt)],
            )
        ]

        selected_tool: Optional[Dict[str, Any]] = None

        for _ in range(max_tool_calls + 1):
            logger.info("========== CONTENTS SENT TO GEMINI ==========")
            for c in contents:
                logger.info("role=%s parts=%s", c.role, c.parts)
            logger.info("=============================================")

            response = self._generate_content(contents=contents)
            candidate_content = self._candidate_content(response)
            function_call = self._extract_function_call(candidate_content)

            if function_call is None:
                final_text = self._extract_text(response, candidate_content)

                logger.info("========== FINAL GEMINI RESPONSE ==========")
                logger.info("%s", final_text)
                logger.info("===========================================")

                return final_text, selected_tool

            tool_name = function_call.name
            tool_args = dict(function_call.args or {})

            logger.info("Gemini Selected Tool: name=%s", tool_name)
            logger.info("Tool Arguments: %s", tool_args)

            raw_result = self.registry.execute(
                name=tool_name,
                arguments=tool_args,
                context=context,
            )

            # FIX 2: Ensure the tool result is always a plain JSON-serialisable dict.
            # Part.from_function_response() requires a dict; wrap scalars/lists.
            if isinstance(raw_result, dict):
                tool_response_payload: Dict[str, Any] = raw_result
            else:
                tool_response_payload = {"result": raw_result}

            selected_tool = {
                "name": tool_name,
                "arguments": tool_args,
                "result": tool_response_payload,
            }

            logger.info("========== TOOL RESULT ==========")
            logger.info(json.dumps(tool_response_payload, indent=2, ensure_ascii=False))
            logger.info("=================================")

            # FIX 1: Append the model's function-call turn with role="model".
            contents.append(candidate_content)

            # FIX 1 (root cause): The function response turn MUST use role="user".
            # In the google-genai SDK the conversation alternates strictly
            # user → model → user → model.  The Part.from_function_response()
            # payload signals to the API that this user turn is a tool result,
            # but the outer Content wrapper role must still be "user".
            # Using role="tool" causes Gemini to silently discard the result.
            contents.append(
                types.Content(
                    role="user",                          # ← was "tool" (WRONG)
                    parts=[
                        types.Part.from_function_response(
                            name=tool_name,
                            response=tool_response_payload,
                        )
                    ],
                )
            )

            # After delivering the tool result, append a plain-text user
            # instruction so Gemini is explicitly told to produce the final
            # answer using the result above. Use a diagnosis-specific nudge
            # for get_pest_diagnosis (which echoes back key fields so Gemini
            # does not have to re-parse the JSON), and a generic nudge for all
            # other tools.
            if tool_name == "get_pest_diagnosis":

                nudge_text = prompt_builder.build_post_diagnosis_nudge(
                    tool_response_payload
                )

            elif tool_name == "get_fertilizer_recommendation":

                nudge_text = prompt_builder.build_post_fertilizer_nudge(
                    tool_response_payload
                )

            elif tool_name == "get_irrigation_recommendation":
                nudge_text = prompt_builder.build_post_irrigation_nudge(
                    tool_response_payload
                )
            else:

                nudge_text = (
                    "The tool has returned its result above. "
                    "Now provide the complete final answer to the farmer "
                    "using ONLY that result. Do NOT call any tool again."
                )

            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text=nudge_text)],
                )
            )

        raise GeminiException(
            message="Gemini exceeded the maximum number of tool calls",
            details={"max_tool_calls": max_tool_calls},
        )

    def _generate_content(self, contents: List[types.Content]) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        tools=[self.registry.gemini_tool],
                        temperature=0.4,
                        # System instruction is sent separately so it stays
                        # at the top of Gemini's attention on every turn,
                        # regardless of how many tool-call rounds have elapsed.
                        system_instruction=SYSTEM_INSTRUCTION,
                    ),
                )
            except errors.APIError as exc:
                if self._is_quota_error(exc):
                    self._handle_retryable_error(
                        exc=exc,
                        attempt=attempt,
                        error_message="Gemini quota exceeded",
                    )
                    continue

                if self._should_retry_api_error(exc):
                    self._handle_retryable_error(
                        exc=exc,
                        attempt=attempt,
                        error_message="Retryable Gemini API error",
                    )
                    continue

                logger.exception("Non-retryable Gemini API error")
                raise GeminiException(
                    message="Gemini API request failed",
                    details=self._exception_details(exc),
                ) from exc
            except Exception as exc:
                logger.exception("Unexpected Gemini client error")
                raise GeminiException(
                    message="Unexpected Gemini client error",
                    details=self._exception_details(exc),
                ) from exc

        raise GeminiException(message="Gemini retry loop ended unexpectedly")

    def _handle_retryable_error(
        self,
        exc: errors.APIError,
        attempt: int,
        error_message: str,
    ) -> None:
        if attempt >= self.max_retries:
            if self._is_quota_error(exc):
                logger.error(
                    "Gemini quota exhausted after %s retries",
                    self.max_retries,
                )
                raise GeminiQuotaExceeded(
                    message=(
                        "Gemini quota exceeded. Please try again after some time."
                    ),
                    details=self._exception_details(exc),
                ) from exc

            logger.error(
                "Gemini API failed after %s retries",
                self.max_retries,
            )
            raise GeminiException(
                message="Gemini API failed after retries",
                details=self._exception_details(exc),
            ) from exc

        delay_seconds = self.initial_backoff_seconds * (2 ** attempt)
        logger.warning(
            "%s. Retrying in %s seconds attempt=%s max_retries=%s details=%s",
            error_message,
            delay_seconds,
            attempt + 1,
            self.max_retries,
            self._exception_details(exc),
        )
        time.sleep(delay_seconds)

    @staticmethod
    def _candidate_content(response: Any) -> types.Content:
        if not response.candidates:
            raise GeminiException(message="Gemini returned no candidates")

        candidate = response.candidates[0]
        if candidate.content is None:
            raise GeminiException(message="Gemini candidate did not include content")

        return candidate.content

    @staticmethod
    def _extract_function_call(content: types.Content) -> Any:
        for part in content.parts or []:
            function_call = getattr(part, "function_call", None)
            if function_call:
                return function_call
        return None

    @staticmethod
    def _extract_text(response: Any, content: types.Content) -> str:
        response_text = getattr(response, "text", None)
        if response_text:
            return response_text.strip()

        text_parts = []
        for part in content.parts or []:
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)

        final_text = "\n".join(text_parts).strip()
        if not final_text:
            raise GeminiException(message="Gemini returned an empty final response")
        return final_text

    @staticmethod
    def _is_quota_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "").upper()
        message = str(exc).upper()

        return (
            status_code == 429
            or "RESOURCE_EXHAUSTED" in status
            or "RESOURCE_EXHAUSTED" in message
            or "429" in message
        )

    @staticmethod
    def _should_retry_api_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        message = str(exc).upper()
        return status_code in {500, 502, 503, 504} or "UNAVAILABLE" in message

    @staticmethod
    def _exception_details(exc: Exception) -> Dict[str, Any]:
        return {
            "type": exc.__class__.__name__,
            "status_code": getattr(exc, "status_code", None),
            "code": getattr(exc, "code", None),
            "status": getattr(exc, "status", None),
            "message": str(exc),
        }


gemini_service = GeminiService()
