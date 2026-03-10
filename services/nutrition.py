from typing import Optional
"""
services/nutrition.py — logika żywieniowa, propozycje posiłków
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


class NutritionService:

    async def suggest_meal_from_fridge(
        self,
        fridge_items: list,
        remaining_kcal: int,
        whoop_data: Optional[dict] = None
    ) -> dict:
        """
        Na podstawie zawartości lodówki i pozostałych kalorii
        zaproponuj posiłek.
        """
        whoop_context = ""
        if whoop_data:
            recovery = whoop_data.get("recovery", 0)
            strain = whoop_data.get("strain", 0)
            
            if recovery >= 67:
                whoop_context = f"Użytkownik ma dobry recovery ({recovery}%), może zjeść więcej białka."
            elif recovery < 34:
                whoop_context = f"Słaby recovery ({recovery}%), polecaj lżejsze posiłki z węglowodanami."
            else:
                whoop_context = f"Średni recovery ({recovery}%), zbilansowany posiłek."
            
            if strain > 14:
                whoop_context += f" Wysoki strain ({strain}) — potrzebuje dużo białka i węglo do regeneracji."

        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": (
                    f"Zawartość lodówki: {json.dumps(fridge_items, ensure_ascii=False)}\n"
                    f"Pozostałe kalorie do celu: {remaining_kcal} kcal\n"
                    f"Kontekst Whoop: {whoop_context}\n\n"
                    "Zaproponuj konkretny posiłek z dostępnych produktów. "
                    "Odpowiedz TYLKO JSON:\n"
                    "{\n"
                    '  "meal_name": "nazwa kolacji po polsku",\n'
                    '  "items": [\n'
                    '    {"name": "produkt", "amount_g": 150, "kcal": 200}\n'
                    '  ],\n'
                    '  "total_kcal": 350,\n'
                    '  "protein_g": 30,\n'
                    '  "carbs_g": 20,\n'
                    '  "fat_g": 12,\n'
                    '  "preparation": "krótki opis jak przyrządzić (2-3 zdania)",\n'
                    '  "why": "1 zdanie dlaczego to dobry wybór"\n'
                    "}\n\n"
                    f"Całkowite kcal posiłku nie przekraczaj {remaining_kcal} kcal. "
                    "Staraj się wykorzystać TYLKO produkty z lodówki."
                )
            }]
        )
        
        text = resp.content[0].text.strip()
        import re
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        try:
            return json.loads(text)
        except Exception:
            return {"meal_name": "Propozycja", "total_kcal": remaining_kcal, "items": []}

    def calculate_tdee(
        self,
        weight_kg: float,
        height_cm: float,
        age: int,
        gender: str,
        activity_level: str,
        whoop_strain: float = 0
    ) -> int:
        """
        Oblicz TDEE (Total Daily Energy Expenditure)
        Wzór Mifflin-St Jeor + współczynnik aktywności + korekta Whoop
        """
        if gender == "male":
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        else:
            bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
        
        multipliers = {
            "sedentary":    1.2,
            "light":        1.375,
            "moderate":     1.55,
            "active":       1.725,
            "very_active":  1.9
        }
        tdee = bmr * multipliers.get(activity_level, 1.55)
        
        # Korekta na podstawie strain Whoop
        if whoop_strain > 0:
            strain_bonus = whoop_strain * 40  # ~40 kcal za każdy punkt strain
            tdee += strain_bonus
        
        return int(tdee)

    def get_macro_targets(self, kcal_goal: int, goal_type: str = "maintenance") -> dict:
        """
        Oblicz docelowe makro na podstawie celu kalorycznego
        """
        if goal_type == "cut":
            protein_pct, carbs_pct, fat_pct = 0.35, 0.35, 0.30
        elif goal_type == "bulk":
            protein_pct, carbs_pct, fat_pct = 0.25, 0.50, 0.25
        else:  # maintenance
            protein_pct, carbs_pct, fat_pct = 0.30, 0.40, 0.30
        
        return {
            "protein_g": int((kcal_goal * protein_pct) / 4),
            "carbs_g":   int((kcal_goal * carbs_pct)   / 4),
            "fat_g":     int((kcal_goal * fat_pct)      / 9),
        }

    async def search_product_off(self, query: str) -> list[dict]:
        """
        Szukaj produktu w Open Food Facts (darmowa baza, zawiera polskie produkty)
        """
        import aiohttp
        url = f"https://world.openfoodfacts.org/cgi/search.pl"
        params = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": 5,
            "lc": "pl",
            "cc": "pl"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    products = []
                    for p in data.get("products", [])[:5]:
                        n = p.get("nutriments", {})
                        products.append({
                            "name": p.get("product_name_pl") or p.get("product_name", ""),
                            "brand": p.get("brands", ""),
                            "kcal_100g": n.get("energy-kcal_100g", 0),
                            "protein_100g": n.get("proteins_100g", 0),
                            "carbs_100g": n.get("carbohydrates_100g", 0),
                            "fat_100g": n.get("fat_100g", 0),
                            "barcode": p.get("code", "")
                        })
                    return [p for p in products if p["kcal_100g"]]
        except Exception:
            return []
