import json
from typing import Any, Dict


# ---------------------------------------------------------------------------
# Language code → human-readable name Gemini reliably understands
# ---------------------------------------------------------------------------
_LANGUAGE_NAMES: Dict[str, str] = {
    "hi":    "Hindi (Devanagari script)",
    "te":    "Telugu",
    "kn":    "Kannada",
    "ta":    "Tamil",
    "mr":    "Marathi",
    "gu":    "Gujarati",
    "pa":    "Punjabi",
    "bn":    "Bengali",
    "ml":    "Malayalam",
    "or":    "Odia",
    "en":    "English",
    # Hinglish has no Unicode fingerprint; DecisionEngine marks it "hinglish"
    # when it detects Roman script with Hindi vocabulary patterns.
    "hinglish": "Hinglish (Hindi words written in English/Roman script)",
}


# ---------------------------------------------------------------------------
# SYSTEM_INSTRUCTION
#
# Loaded once into GenerateContentConfig.system_instruction.
# This is the ONLY place that controls persona, tool rules, AND language policy.
# Every rule here is enforced on every single Gemini turn, including tool turns.
#
# LANGUAGE SECTION is the key addition:
#   Rule L1 — Gemini must obey the explicit language tag in the user prompt.
#   Rule L2 — Script must match exactly (Devanagari for Hindi, not transliteration).
#   Rule L3 — Hinglish must be answered in Hinglish (Roman + Hindi vocab).
#   Rule L4 — Applies to every section of the structured tool-result answers too.
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are Krishi Kalyan AI, an expert agricultural advisor for Indian farmers in India.

═══════════════════════════════════════════
LANGUAGE RULES  (highest priority — always obey)
═══════════════════════════════════════════
L1. Every user prompt begins with a line:
        FARMER_LANGUAGE: <language name>
    You MUST reply in that exact language. No exceptions.

L2. Script must match:
    - Hindi → Devanagari script (हिंदी में लिखें)
    - Telugu → Telugu script (తెలుగులో రాయండి)
    - Tamil → Tamil script (தமிழில் எழுதுங்கள்)
    - Kannada → Kannada script
    - Marathi → Devanagari script (Marathi dialect)
    - Hinglish → Roman script with Hindi vocabulary
      (e.g. "Aapke fasal mein fungal infection hai")
    - English → English

L3. If FARMER_LANGUAGE says "Hinglish", do NOT switch to pure Hindi Devanagari.
    Write the way an educated Indian farmer texting a friend would write:
    Roman letters, Hindi/Urdu words, simple grammar.

L4. Apply the language rule to EVERY section of your response —
    section headers, bullet points, advice, unit labels, everything.
    Never mix languages in a single response.

L5. When in doubt about exact language, match the script of the farmer's question
    exactly as it appears in the FARMER QUESTION section below.

═══════════════════════════════════════════
CONTENT RULES
═══════════════════════════════════════════
1. Never identify crop diseases yourself. Always rely on the get_pest_diagnosis tool result.
2. Never hallucinate. Never invent treatment advice not grounded in the tool result or known agronomy.
3. After get_pest_diagnosis returns, use ONLY the tool result. Do not call the tool again.
4. Keep responses practical, simple, and actionable for a farmer.
5. After get_pest_diagnosis returns, always structure your answer with these sections:
   - 🌿 Plant Identified
   - 🦠 Disease Detected
   - 📊 Confidence & Severity
   - ❓ Why This Happens (1-2 sentences, plain language)
   - 🚨 Immediate Action (what to do today)
   - 🌱 Organic Remedies
   - 💊 Chemical Control (if severity is High or Medium)
   - 🛡️ Prevention (for next season)
6. If the farmer asks about fertilizer, always call the get_fertilizer_recommendation tool.
7. Never recommend a fertilizer from your own knowledge.
8. After get_fertilizer_recommendation returns, explain:
   - 🌱 Recommended Fertilizer
   - 📊 Confidence
   - 🏆 Top 3 Recommendations
   - ❓ Why it is suitable
   - 🧪 How to apply
   - ⚠️ Precautions
9. If the farmer asks about irrigation, always call the get_irrigation_recommendation tool.
10. Never recommend irrigation yourself.
11. After get_irrigation_recommendation returns, explain:
   - 💧 Irrigation Recommendation
   - 📊 Confidence
   - 💦 Recommended Water (mm)
   - ⚠️ Urgency
   - 🏆 Top 3 Predictions
   - 🌱 Irrigation Advice
""".strip()


class PromptBuilder:

    # ------------------------------------------------------------------
    # Main user-turn prompt
    # ------------------------------------------------------------------
    def build(
        self,
        context: Dict[str, Any],
        user_message: str,
    ) -> str:
        """
        Build the full user-turn prompt.

        The FARMER_LANGUAGE line at the very top is the trigger for Rule L1
        in SYSTEM_INSTRUCTION. Gemini reads it before anything else and locks
        its output language for the entire response.
        """
        weather_data = (
            context.get("weather").model_dump(mode="json")
            if context.get("weather")
            else {}
        )

        # Resolve the language label Gemini will receive.
        # DecisionEngine stores the detected code in context["detected_language"].
        lang_code: str = context.get("detected_language") or "en"
        lang_label: str = _LANGUAGE_NAMES.get(lang_code, lang_code)

        prompt = f"""
FARMER_LANGUAGE: {lang_label}

==========================
FARMER PROFILE
==========================
{json.dumps(context.get("user", {}), indent=2,default=str)}

==========================
FARM DETAILS
==========================
{json.dumps(context.get("farm", {}), indent=2,default=str)}

==========================
WEATHER (Live)
==========================
{json.dumps(weather_data, indent=2)}

==========================
SATELLITE CROP HEALTH
==========================
{json.dumps(context.get("crop_health", {}), indent=2)}

==========================
VILLAGE CONTEXT
==========================
{json.dumps(context.get("village", {}), indent=2)}

==========================
IMAGE UPLOADED
==========================
{"Yes — farmer has uploaded a crop image. Use get_pest_diagnosis immediately." if context.get("image_path") else "No image uploaded."}

==========================
FARMER QUESTION
==========================
{user_message}
""".strip()

        return prompt

    # ------------------------------------------------------------------
    # Post-tool nudges — language instruction is re-stated so Gemini
    # does not drift back to English after processing tool results.
    # ------------------------------------------------------------------

    def _language_reminder(self, context: Dict[str, Any]) -> str:
        """One-line reminder appended to every post-tool nudge."""
        lang_code: str = context.get("detected_language") or "en"
        lang_label: str = _LANGUAGE_NAMES.get(lang_code, lang_code)
        return f"⚠️ IMPORTANT: Write your ENTIRE response in {lang_label}. Do NOT switch to English."

    def build_post_diagnosis_nudge(
        self,
        tool_result: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> str:
        plant      = tool_result.get("plant", {}).get("name", "Unknown plant")
        disease    = tool_result.get("diagnosis", {}).get("name", "Unknown disease")
        confidence = tool_result.get("diagnosis", {}).get("confidence", "N/A")
        severity   = tool_result.get("severity", {}).get("level", "Unknown")

        lang_reminder = self._language_reminder(context or {})

        return (
            f"The KrishiVision model has diagnosed the uploaded image.\n"
            f"Plant: {plant} | Disease: {disease} | "
            f"Confidence: {confidence}% | Severity: {severity}.\n\n"
            f"Now write the complete farmer-facing answer using ONLY this diagnosis result.\n"
            f"Follow the exact section structure from your instructions.\n"
            f"Do NOT call get_pest_diagnosis again.\n"
            f"Do NOT reference the farm's current_crop if it differs from the diagnosed plant — "
            f"trust the model result.\n\n"
            f"{lang_reminder}"
        )

    def build_post_fertilizer_nudge(
        self,
        tool_result: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> str:
        fertilizer = tool_result.get("recommended_fertilizer", "Unknown")
        confidence = tool_result.get("confidence", "N/A")
        lang_reminder = self._language_reminder(context or {})

        return (
            f"The fertilizer recommendation model has completed prediction.\n"
            f"Recommended Fertilizer: {fertilizer}\n"
            f"Confidence: {confidence}%\n\n"
            f"Now explain the recommendation using ONLY the tool result.\n"
            f"Include:\n"
            f"- Recommended fertilizer\n"
            f"- Confidence\n"
            f"- Top 3 recommendations\n"
            f"- Why it is suitable\n"
            f"- Application advice\n"
            f"- Precautions\n\n"
            f"Do NOT call get_fertilizer_recommendation again.\n\n"
            f"{lang_reminder}"
        )

    def build_post_irrigation_nudge(
        self,
        tool_result: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> str:
        recommendation = tool_result.get("irrigation_recommendation", "Unknown")
        confidence     = tool_result.get("confidence", "N/A")
        water          = tool_result.get("recommended_water_mm", "N/A")
        urgency        = tool_result.get("urgency", "Unknown")
        lang_reminder  = self._language_reminder(context or {})

        return (
            f"The irrigation recommendation model has completed prediction.\n"
            f"Recommendation: {recommendation}\n"
            f"Confidence: {confidence}%\n"
            f"Recommended Water: {water} mm\n"
            f"Urgency: {urgency}\n\n"
            f"Now explain the recommendation using ONLY the tool result.\n"
            f"Include:\n"
            f"- Irrigation recommendation\n"
            f"- Confidence\n"
            f"- Recommended water\n"
            f"- Urgency\n"
            f"- Top 3 predictions\n"
            f"- Irrigation advice\n\n"
            f"Do NOT call get_irrigation_recommendation again.\n\n"
            f"{lang_reminder}"
        )


prompt_builder = PromptBuilder()