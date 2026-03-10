from typing import Optional
"""
services/whoop.py — integracja z Whoop API v2
Dokumentacja: https://developer.whoop.com/api
"""
import aiohttp
import json
from datetime import datetime, timedelta, timezone
from config import WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, WHOOP_REDIRECT_URI
from db.database import Database

db = Database()

WHOOP_AUTH_URL  = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE  = "https://api.prod.whoop.com/developer/v1"


class WhoopService:

    def get_auth_url(self, user_id: int) -> str:
        """Generuj URL do autoryzacji OAuth2"""
        import urllib.parse
        params = {
            "client_id":     WHOOP_CLIENT_ID,
            "redirect_uri":  WHOOP_REDIRECT_URI,
            "response_type": "code",
            "scope":         "read:recovery read:cycles read:workout read:sleep read:profile read:body_measurement",
            "state":         str(user_id)
        }
        return f"{WHOOP_AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, user_id: int, code: str) -> bool:
        """Zamień kod autoryzacyjny na token dostępu"""
        async with aiohttp.ClientSession() as session:
            async with session.post(WHOOP_TOKEN_URL, data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  WHOOP_REDIRECT_URI,
                "client_id":     WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET
            }) as resp:
                if resp.status == 200:
                    token_data = await resp.json()
                    await db.save_whoop_token(user_id, token_data)
                    return True
                return False

    async def get_today(self, user_id: int) -> Optional[dict]:
        """Pobierz dane z dzisiejszego dnia"""
        token = await db.get_whoop_token(user_id)
        if not token:
            return None
        
        access_token = await self._ensure_valid_token(user_id, token)
        if not access_token:
            return None
        
        headers = {"Authorization": f"Bearer {access_token}"}
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)
        
        result = {}
        
        async with aiohttp.ClientSession() as session:
            # Recovery (zawiera HRV, RHR, recovery score)
            try:
                async with session.get(
                    f"{WHOOP_API_BASE}/recovery",
                    headers=headers,
                    params={
                        "start": yesterday.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "end":   today.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "limit": 1
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        records = data.get("records", [])
                        if records:
                            r = records[0]
                            score = r.get("score", {})
                            result["recovery"]    = round(score.get("recovery_score", 0))
                            result["hrv"]         = round(score.get("hrv_rmssd_milli", 0))
                            result["rhr"]         = round(score.get("resting_heart_rate", 0))
            except Exception:
                pass
            
            # Cycle (Strain + kalorie)
            try:
                async with session.get(
                    f"{WHOOP_API_BASE}/cycle",
                    headers=headers,
                    params={
                        "start": yesterday.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "end":   today.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "limit": 1
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        records = data.get("records", [])
                        if records:
                            c = records[0]
                            score = c.get("score", {})
                            result["strain"]         = round(score.get("strain", 0), 1)
                            result["calories_burned"] = round(score.get("kilojoule", 0) / 4.184)
                            result["avg_hr"]          = round(score.get("average_heart_rate", 0))
            except Exception:
                pass
            
            # Sleep
            try:
                async with session.get(
                    f"{WHOOP_API_BASE}/activity/sleep",
                    headers=headers,
                    params={
                        "start": yesterday.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "end":   today.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "limit": 1
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        records = data.get("records", [])
                        if records:
                            s = records[0]
                            score = s.get("score", {})
                            total_ms = score.get("total_in_bed_time_milli", 0)
                            result["sleep_hours"] = round(total_ms / 3_600_000, 1)
                            result["sleep_score"] = round(score.get("sleep_performance_percentage", 0))
            except Exception:
                pass
        
        return result if result else None

    async def _ensure_valid_token(self, user_id: int, token: dict) -> Optional[str]:
        """Odśwież token jeśli wygasł"""
        if not token.get("access_token"):
            return None
        
        expires_at = token.get("expires_at", 0)
        now = datetime.now(timezone.utc).timestamp()
        
        if now < expires_at - 60:  # jest ważny
            return token["access_token"]
        
        # Odśwież
        async with aiohttp.ClientSession() as session:
            async with session.post(WHOOP_TOKEN_URL, data={
                "grant_type":    "refresh_token",
                "refresh_token": token.get("refresh_token", ""),
                "client_id":     WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET
            }) as resp:
                if resp.status == 200:
                    new_token = await resp.json()
                    new_token["expires_at"] = now + new_token.get("expires_in", 3600)
                    await db.save_whoop_token(user_id, new_token)
                    return new_token["access_token"]
        
        return None
