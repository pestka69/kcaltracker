from typing import Optional
"""
services/vision.py — analiza zdjęć przez Claude API
Obsługuje: posiłki, etykiety, lodówka
"""
import base64
import json
import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


class VisionService:

    async def analyze_photo(self, image_bytes: bytes) -> tuple[str, dict]:
        """
        Główna funkcja: wykrywa typ zdjęcia i analizuje.
        Zwraca: ("meal" | "label" | "fridge", dict z danymi)
        """
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        
        # Krok 1: Klasyfikacja zdjęcia
        photo_type = await self._classify_photo(b64)
        
        # Krok 2: Szczegółowa analiza zależna od typu
        if photo_type == "label":
            data = await self._analyze_label(b64)
        elif photo_type == "fridge":
            data = await self._analyze_fridge(b64)
        else:
            data = await self._analyze_meal(b64)
        
        return photo_type, data

    async def _classify_photo(self, b64: str) -> str:
        """Szybka klasyfikacja: czy to etykieta, lodówka czy posiłek"""
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                    },
                    {
                        "type": "text",
                        "text": (
                            "Classify this image. Reply with ONLY one word:\n"
                            "- 'label' if it shows a nutrition facts label / product label\n"
                            "- 'fridge' if it shows the inside of a refrigerator\n"
                            "- 'meal' for food/dish/meal\n"
                            "Reply only: label, fridge, or meal"
                        )
                    }
                ]
            }]
        )
        result = resp.content[0].text.strip().lower()
        if "label" in result:
            return "label"
        if "fridge" in result:
            return "fridge"
        return "meal"

    async def _analyze_meal(self, b64: str) -> dict:
        """Analiza posiłku — szacowanie składu i kalorii"""
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                    },
                    {
                        "type": "text",
                        "text": """Analyze this meal photo and provide nutritional estimates.
                        
Respond ONLY with valid JSON, no markdown, no explanation:
{
  "name": "nazwa posiłku po polsku",
  "items": [
    {"name": "składnik", "amount_g": 150, "kcal": 200, "protein_g": 10, "carbs_g": 25, "fat_g": 8}
  ],
  "total_kcal": 450,
  "total_protein_g": 35,
  "total_carbs_g": 55,
  "total_fat_g": 18,
  "portion_g": 350,
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": "high"
}

Rules:
- If you can't estimate portion size (e.g. can't tell if it's 100g or 300g), set needs_clarification=true and ask in Polish in clarification_question
- confidence: "high" if clearly visible, "medium" if estimated, "low" if unclear
- All values must be numbers (integers or floats), never null for nutritional values
- Name must be in Polish"""
                    }
                ]
            }]
        )
        return self._parse_json(resp.content[0].text)

    async def _analyze_label(self, b64: str) -> dict:
        """Odczyt etykiety produktu — wartości odżywcze"""
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                    },
                    {
                        "type": "text",
                        "text": """Read the nutrition label from this product.

Respond ONLY with valid JSON:
{
  "success": true,
  "name": "nazwa produktu",
  "store": "Biedronka/Lidl/Aldi/inny lub null",
  "portion_g": 100,
  "kcal": 250,
  "protein_g": 15.5,
  "carbs_g": 30.2,
  "fat_g": 8.1,
  "fiber_g": 2.0,
  "sugar_g": 5.0,
  "salt_g": 1.2,
  "package_g": 300,
  "servings_per_package": 3
}

If label is unreadable, return: {"success": false, "error": "reason"}
Extract ALL visible nutritional info. Values per 100g unless label says otherwise."""
                    }
                ]
            }]
        )
        return self._parse_json(resp.content[0].text)

    async def _analyze_fridge(self, b64: str) -> dict:
        """Analiza zawartości lodówki"""
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
                    },
                    {
                        "type": "text",
                        "text": """Identify all food items visible in this refrigerator.

Respond ONLY with valid JSON:
{
  "items": [
    {
      "name": "nazwa produktu po polsku",
      "estimated_amount": "ok. 300g / 2 sztuki / 1 opakowanie",
      "kcal_per_100g": 150,
      "estimated_total_kcal": 450,
      "category": "białko/węglowodany/warzywa/nabiał/tłuszcze/inne",
      "visible_amount_g": 300
    }
  ],
  "notes": "dodatkowe obserwacje (opcjonalne)"
}

Be specific about quantities. List only clearly visible items."""
                    }
                ]
            }]
        )
        return self._parse_json(resp.content[0].text)

    async def update_with_clarification(self, pending: dict, user_answer: str) -> dict:
        """Zaktualizuj kalkulację po odpowiedzi użytkownika na pytanie o gramaturę"""
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": (
                    f"Original meal analysis: {json.dumps(pending, ensure_ascii=False)}\n\n"
                    f"User answered about portion size: '{user_answer}'\n\n"
                    "Update the nutritional values based on the user's answer. "
                    "Return the same JSON structure with updated values. "
                    "Set needs_clarification=false. "
                    "Respond ONLY with valid JSON."
                )
            }]
        )
        return self._parse_json(resp.content[0].text)

    def _parse_json(self, text: str) -> dict:
        """Bezpieczne parsowanie JSON z odpowiedzi Claude"""
        # Usuń markdown code fences jeśli są
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        text = text.rstrip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Próba wyciągnięcia JSON z tekstu
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
            return {"error": "parse_failed", "raw": text[:300]}
