"""
config.py — konfiguracja z .env
Skopiuj .env.example → .env i uzupełnij wartości
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# ── Anthropic (Claude API) ────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-opus-4-5"   # najlepszy do vision

# ── Supabase ──────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── Whoop API ─────────────────────────────────
WHOOP_CLIENT_ID     = os.getenv("WHOOP_CLIENT_ID", "")
WHOOP_CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET", "")
WHOOP_REDIRECT_URI  = os.getenv("WHOOP_REDIRECT_URI", "https://twoja-domena.com/whoop/callback")

# ── Open Food Facts ───────────────────────────
OFF_API_URL = "https://world.openfoodfacts.org/api/v2/product"

# ── Ustawienia domyślne ───────────────────────
DEFAULT_KCAL_GOAL     = 2000
DEFAULT_PROTEIN_GOAL  = 140   # g
DEFAULT_CARBS_GOAL    = 250   # g
DEFAULT_FAT_GOAL      = 70    # g
