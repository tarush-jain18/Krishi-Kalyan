import json
from typing import Any, Dict


# ---------------------------------------------------------------------------
# System instruction — loaded once into GenerateContentConfig.system_instruction
# This never changes per request. It defines persona and tool rules only.
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """
You are Krishi Kalyan AI, an expert agricultural advisor for Indian farmers in India.

STRICT RULES:
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
6. If the farmer asks:
   - Which fertilizer should I use?
   - Recommend fertilizer.
   - Best fertilizer for my crop.
   - Fertilizer recommendation.

   Always call the get_fertilizer_recommendation tool.

7. Never recommend a fertilizer from your own knowledge.
   Always rely on the trained fertilizer recommendation model.

8. After get_fertilizer_recommendation returns, explain:
   - 🌱 Recommended Fertilizer
   - 📊 Confidence
   - 🏆 Top 3 Recommendations
   - ❓ Why it is suitable
   - 🧪 How to apply
   - ⚠️ Precautions

9. If the farmer asks:
   - Should I irrigate today?
   - Do I need irrigation?
   - Water recommendation.
   - Irrigation recommendation.
   - How much water should I give?

   Always call the get_irrigation_recommendation tool.

10. Never recommend irrigation yourself.
    Always rely on the trained irrigation recommendation model.

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
    # Main user-turn prompt — contains ONLY factual context + question.
    # System rules and persona live in SYSTEM_INSTRUCTION above.
    # ------------------------------------------------------------------
    def build(
        self,
        context: Dict[str, Any],
        user_message: str,
    ) -> str:

        weather_data = (
            context.get("weather").model_dump(mode="json")
            if context.get("weather")
            else {}
        )

        prompt = f"""
==========================
FARMER PROFILE
==========================
{json.dumps(context.get("user", {}), indent=2)}

==========================
FARM DETAILS
==========================
{json.dumps(context.get("farm", {}), indent=2)}

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
    # Post-tool nudge — injected into contents after the FunctionResponse.
    # This replaces the generic nudge in gemini.py with a diagnosis-aware one.
    # ------------------------------------------------------------------
    def build_post_diagnosis_nudge(self, tool_result: Dict[str, Any]) -> str:
        plant = tool_result.get("plant", {}).get("name", "Unknown plant")
        disease = tool_result.get("diagnosis", {}).get("name", "Unknown disease")
        confidence = tool_result.get("diagnosis", {}).get("confidence", "N/A")
        severity = tool_result.get("severity", {}).get("level", "Unknown")

        return (
            f"The KrishiVision model has diagnosed the uploaded image.\n"
            f"Plant: {plant} | Disease: {disease} | "
            f"Confidence: {confidence}% | Severity: {severity}.\n\n"
            f"Now write the complete farmer-facing answer using ONLY this diagnosis result.\n"
            f"Follow the exact section structure from your instructions.\n"
            f"Do NOT call get_pest_diagnosis again.\n"
            f"Do NOT reference the farm's current_crop if it differs from the diagnosed plant — "
            f"trust the model result."
        )
    def build_post_fertilizer_nudge(self, tool_result: Dict[str, Any]) -> str:

        fertilizer = tool_result.get(
            "recommended_fertilizer",
            "Unknown",
        )

        confidence = tool_result.get(
            "confidence",
            "N/A",
        )

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
            f"Do NOT call get_fertilizer_recommendation again."
        )

    def build_post_irrigation_nudge(
        self,
        tool_result: Dict[str, Any],
    ) -> str:

        recommendation = tool_result.get(
            "irrigation_recommendation",
            "Unknown",
        )

        confidence = tool_result.get(
            "confidence",
            "N/A",
        )

        water = tool_result.get(
            "recommended_water_mm",
            "N/A",
        )

        urgency = tool_result.get(
            "urgency",
            "Unknown",
        )

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

            f"Do NOT call get_irrigation_recommendation again."
        )

prompt_builder = PromptBuilder()
