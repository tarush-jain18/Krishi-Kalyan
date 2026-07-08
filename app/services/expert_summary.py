"""
app/services/expert_summary.py

Generates a concise expert-facing AI summary for a farmer query before
creating an expert ticket.

Design:
  - Calls Gemini directly with a compact, focused prompt.
  - Does NOT go through the DecisionEngine or ToolRegistry — this is a
    single-shot summarisation call with no tool usage.
  - Reuses the existing GeminiService client.
  - Returns a plain string; caller stores it as ai_summary on the ticket.

Error handling:
  - Returns a safe fallback string on any Gemini failure so ticket creation
    is never blocked by a summary failure.
"""

import logging
from typing import Any, Dict, Optional

from google.genai import types

from app.services.gemini import gemini_service

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_INSTRUCTION = """
You are an agricultural expert assistant.
Your task is to write a SHORT expert-facing case summary (5–8 lines maximum).

Structure:
1. What the farmer reports (1 sentence)
2. Possible diagnosis or issue (1–2 sentences)
3. What the AI recommended (1–2 sentences)
4. Confidence level: High / Medium / Low
5. Whether expert confirmation is required (1 sentence)

Be concise. No bullet symbols. Plain paragraph text only.
""".strip()

_FALLBACK_SUMMARY = (
    "Farmer has submitted a query for expert review. "
    "Please review the question and AI response provided in this ticket."
)


def generate_expert_summary(
    *,
    question: str,
    ai_response: str,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Ask Gemini to produce a short expert-facing summary of the farmer query.

    Parameters
    ----------
    question    : The farmer's original question / transcript.
    ai_response : The AI response already given to the farmer.
    context     : Optional farmer context dict (used to extract crop name etc.)

    Returns
    -------
    A plain-text summary string, or a safe fallback on failure.
    """

    crop = ""
    district = ""

    if context:
        farm = context.get("farm", {})
        user = context.get("user", {})
        crop = (
            farm.get("current_crop")
            or context.get("snapshot", {}).get("crop")
            or ""
        )
        district = (
            user.get("district")
            or farm.get("district")
            or ""
        )

    crop_line = f"Crop: {crop}" if crop else ""
    district_line = f"District: {district}" if district else ""
    meta = "\n".join(filter(None, [crop_line, district_line]))

    context_block = ""

    if meta:
        context_block = f"Farmer context:\n{meta}\n"

    prompt = f"""
    {context_block}
    Farmer's question:
    {question}

    AI response given to farmer:
    {ai_response}

    Write the expert case summary now.
    """.strip()

    logger.info("Generating expert AI summary via Gemini")

    try:
        # Use the existing GeminiService client directly.
        # We bypass .chat() because we do NOT want tool usage here —
        # just a single generate_content call with a compact prompt.
        response = gemini_service.client.models.generate_content(
            model=gemini_service.model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=prompt)],
                )
            ],
            config=types.GenerateContentConfig(
                temperature=0.3,
                system_instruction=_SUMMARY_SYSTEM_INSTRUCTION,
            ),
        )

        summary_text = (getattr(response, "text", None) or "").strip()

        if not summary_text:
            logger.warning("Gemini returned empty summary — using fallback")
            return _FALLBACK_SUMMARY

        logger.info("Expert summary generated successfully (%d chars)", len(summary_text))
        return summary_text

    except Exception as exc:
        logger.exception(
            "Expert summary generation failed — using fallback. error=%s",
            exc,
        )
        return _FALLBACK_SUMMARY
