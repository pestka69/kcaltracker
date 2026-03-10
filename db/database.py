from typing import List,  Optional
"""
db/database.py — warstwa danych (Supabase / PostgreSQL)
Schemat tabel poniżej w SQL.
"""
import json
from datetime import datetime, date, timezone
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY, DEFAULT_KCAL_GOAL


class Database:
    _client= None

    @property
    def sb(self) -> Client:
        if self._client is None:
            self._client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return self._client

    # ── UŻYTKOWNICY ─────────────────────────────

    async def ensure_user(self, user_id: int, name: str):
        """Utwórz użytkownika jeśli nie istnieje"""
        existing = self.sb.table("users").select("id").eq("telegram_id", user_id).execute()
        if not existing.data:
            self.sb.table("users").insert({
                "telegram_id": user_id,
                "name": name,
                "kcal_goal": DEFAULT_KCAL_GOAL,
                "created_at": datetime.now(timezone.utc).isoformat()
            }).execute()

    async def get_user_goal(self, user_id: int) -> int:
        result = self.sb.table("users").select("kcal_goal").eq("telegram_id", user_id).execute()
        if result.data:
            return result.data[0].get("kcal_goal", DEFAULT_KCAL_GOAL)
        return DEFAULT_KCAL_GOAL

    async def set_user_goal(self, user_id: int, goal: int):
        self.sb.table("users").update({"kcal_goal": goal}).eq("telegram_id", user_id).execute()

    # ── POSIŁKI ──────────────────────────────────

    async def save_meal(self, user_id: int, meal: dict) -> int:
        """Zapisz posiłek do bazy"""
        row = {
            "user_telegram_id": user_id,
            "name":             meal.get("name", "Posiłek"),
            "kcal":             meal.get("total_kcal", meal.get("kcal", 0)),
            "protein_g":        meal.get("total_protein_g", meal.get("protein_g", 0)),
            "carbs_g":          meal.get("total_carbs_g",   meal.get("carbs_g", 0)),
            "fat_g":            meal.get("total_fat_g",     meal.get("fat_g", 0)),
            "portion_g":        meal.get("portion_g", 0),
            "source":           meal.get("source", "photo"),   # photo / label / manual
            "store":            meal.get("store"),
            "items_json":       json.dumps(meal.get("items", []), ensure_ascii=False),
            "eaten_at":         datetime.now(timezone.utc).isoformat(),
            "date":             date.today().isoformat()
        }
        result = self.sb.table("meals").insert(row).execute()
        return result.data[0]["id"] if result.data else 0

    async def get_today_meals(self, user_id: int) -> List[dict]:
        today = date.today().isoformat()
        result = self.sb.table("meals") \
            .select("*") \
            .eq("user_telegram_id", user_id) \
            .eq("date", today) \
            .order("eaten_at") \
            .execute()
        return result.data or []

    async def get_today_kcal(self, user_id: int) -> int:
        meals = await self.get_today_meals(user_id)
        return sum(m.get("kcal", 0) for m in meals)

    async def get_last_meal(self, user_id: int) -> Optional[dict]:
        result = self.sb.table("meals") \
            .select("*") \
            .eq("user_telegram_id", user_id) \
            .order("eaten_at", desc=True) \
            .limit(1) \
            .execute()
        return result.data[0] if result.data else None

    async def delete_meal(self, user_id: int, meal_id: int) -> dict:
        result = self.sb.table("meals") \
            .delete() \
            .eq("id", meal_id) \
            .eq("user_telegram_id", user_id) \
            .execute()
        return result.data[0] if result.data else {}

    async def get_week_history(self, user_id: int) -> List[dict]:
        """Suma kalorii per dzień za ostatnie 7 dni"""
        from datetime import timedelta
        days = []
        for i in range(6, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            result = self.sb.table("meals") \
                .select("kcal") \
                .eq("user_telegram_id", user_id) \
                .eq("date", d) \
                .execute()
            total = sum(m.get("kcal", 0) for m in (result.data or []))
            dt = datetime.strptime(d, "%Y-%m-%d")
            days.append({
                "date": dt.strftime("%d.%m"),
                "day":  ["Pon","Wt","Śr","Czw","Pt","Sob","Ndz"][dt.weekday()],
                "kcal": total
            })
        return days

    # ── WHOOP TOKEN ──────────────────────────────

    async def save_whoop_token(self, user_id: int, token_data: dict):
        from datetime import timezone as tz
        token_data["expires_at"] = (
            datetime.now(tz.utc).timestamp() + token_data.get("expires_in", 3600)
        )
        existing = self.sb.table("whoop_tokens") \
            .select("id").eq("user_telegram_id", user_id).execute()
        
        if existing.data:
            self.sb.table("whoop_tokens") \
                .update({"token_json": json.dumps(token_data)}) \
                .eq("user_telegram_id", user_id) \
                .execute()
        else:
            self.sb.table("whoop_tokens").insert({
                "user_telegram_id": user_id,
                "token_json": json.dumps(token_data)
            }).execute()

    async def get_whoop_token(self, user_id: int) -> Optional[dict]:
        result = self.sb.table("whoop_tokens") \
            .select("token_json").eq("user_telegram_id", user_id).execute()
        if result.data:
            return json.loads(result.data[0]["token_json"])
        return None
