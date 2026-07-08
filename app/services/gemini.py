"""
app/services/gemini.py

Change vs previous version
---------------------------
The three post-tool nudge builders now receive `context` as a keyword
argument so they can append a language reminder line.  This prevents Gemini
from drifting back to English after processing a tool result — the most
common place where language compliance breaks down.

Everything else (retry logic, tool dispatch, content building) is unchanged.
"""

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
        self.api_keys = [
            settings.GEMINI_API_KEY_1,
            settings.GEMINI_API_KEY_2,
            settings.GEMINI_API_KEY_3,
            settings.GEMINI_API_KEY_4,
            settings.GEMINI_API_KEY_5,
            settings.GEMINI_API_KEY_6,
            settings.GEMINI_API_KEY_7,
            settings.GEMINI_API_KEY_8,
        ]

        self.api_keys = [k for k in self.api_keys if k]
        self.model  = model
        self.registry = registry
        self.max_retries = max_retries
        self.initial_backoff_seconds = initial_backoff_seconds

    def chat(
        self,
        prompt: str,
        context: Dict[str, Any],
        max_tool_calls: int = 3,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
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

            contents.append(candidate_content)

            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=tool_name,
                            response=tool_response_payload,
                        )
                    ],
                )
            )

            # -----------------------------------------------------------
            # Post-tool nudge — context is passed so the nudge builder
            # can append a language reminder, preventing Gemini from
            # drifting back to English after seeing tool JSON output.
            # -----------------------------------------------------------
            if tool_name == "get_pest_diagnosis":
                nudge_text = prompt_builder.build_post_diagnosis_nudge(
                    tool_response_payload,
                    context=context,          # ← NEW
                )
            elif tool_name == "get_fertilizer_recommendation":
                nudge_text = prompt_builder.build_post_fertilizer_nudge(
                    tool_response_payload,
                    context=context,          # ← NEW
                )
            elif tool_name == "get_irrigation_recommendation":
                nudge_text = prompt_builder.build_post_irrigation_nudge(
                    tool_response_payload,
                    context=context,          # ← NEW
                )
            else:
                lang_code  = context.get("detected_language") or "en"
                from app.engine.prompt_builder import _LANGUAGE_NAMES
                lang_label = _LANGUAGE_NAMES.get(lang_code, lang_code)
                nudge_text = (
                    "The tool has returned its result above. "
                    "Now provide the complete final answer to the farmer "
                    "using ONLY that result. Do NOT call any tool again.\n\n"
                    f"⚠️ IMPORTANT: Write your ENTIRE response in {lang_label}."
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

    # ------------------------------------------------------------------
    # Internal helpers — all unchanged
    # ------------------------------------------------------------------

    def _get_client(self, api_key: str):
        return genai.Client(api_key=api_key)


    def _generate_content(self, contents: List[types.Content]) -> Any:

        last_quota_error = None

        for key_index, api_key in enumerate(self.api_keys):

            logger.info(
                "Using Gemini API Key %d/%d",
                key_index + 1,
                len(self.api_keys),
            )

            client = self._get_client(api_key)

            for attempt in range(self.max_retries + 1):

                try:
                    return client.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            tools=[self.registry.gemini_tool],
                            temperature=0.4,
                            system_instruction=SYSTEM_INSTRUCTION,
                        ),
                    )

                except errors.APIError as exc:

                    if self._is_quota_error(exc):
                        logger.warning(
                            "Gemini API Key %d quota exhausted. Switching to next key...",
                            key_index + 1,
                        )
                        last_quota_error = exc
                        break

                    if self._should_retry_api_error(exc):
                        self._handle_retryable_error(
                            exc=exc,
                            attempt=attempt,
                            error_message="Retryable Gemini API error",
                        )
                        continue

                    raise GeminiException(
                        message="Gemini API request failed",
                        details=self._exception_details(exc),
                    ) from exc

                except Exception as exc:
                    raise GeminiException(
                        message="Unexpected Gemini client error",
                        details=self._exception_details(exc),
                    ) from exc

        raise GeminiQuotaExceeded(
            message="All configured Gemini API keys have exhausted their quota.",
            details=self._exception_details(last_quota_error)
            if last_quota_error
            else {},
        )

    def _handle_retryable_error(
        self,
        exc: errors.APIError,
        attempt: int,
        error_message: str,
    ) -> None:
        if attempt >= self.max_retries:
            if self._is_quota_error(exc):
                logger.error("Gemini quota exhausted after %s retries", self.max_retries)
                raise GeminiQuotaExceeded(
                    message="Gemini quota exceeded. Please try again after some time.",
                    details=self._exception_details(exc),
                ) from exc

            logger.error("Gemini API failed after %s retries", self.max_retries)
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
        status  = str(getattr(exc, "status", "") or "").upper()
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
