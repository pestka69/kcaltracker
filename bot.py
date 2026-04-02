"""
NutriBot — bot Telegram do śledzenia diety
Webhook mode + FastAPI dashboard
"""
import logging
import asyncio
import base64
import json
import re
import os
import urllib.request
from datetime import datetime, date, timezone

# ── FastAPI (musi być przed botem) ──────────────────────────
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
import secrets
import uvicorn

import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from dotenv import load_dotenv
load_dotenv()

from config import TELEGRAM_TOKEN, ANTHROPIC_API_KEY, CLAUDE_MODEL, DEFAULT_KCAL_GOAL

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── Supabase ─────────────────────────────────────────────────
def sb_request(method, table, data=None, params=""):
    url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("apikey", SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=representation")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        logger.error(f"Supabase {method} {table}: {e.read()}")
        return []

def get_goal(user_id):
    rows = sb_request("GET", "users", params=f"?user_id=eq.{user_id}&select=goal")
    return rows[0]["goal"] if rows else DEFAULT_KCAL_GOAL

def set_goal(user_id, goal):
    existing = sb_request("GET", "users", params=f"?user_id=eq.{user_id}")
    if existing:
        sb_request("PATCH", "users", {"goal": goal}, f"?user_id=eq.{user_id}")
    else:
        sb_request("POST", "users", {"user_id": str(user_id), "goal": goal})

def ensure_user(user_id):
    existing = sb_request("GET", "users", params=f"?user_id=eq.{user_id}")
    if not existing:
        sb_request("POST", "users", {"user_id": str(user_id), "goal": DEFAULT_KCAL_GOAL})

def today_str():
    return date.today().isoformat()

def get_today_meals(user_id):
    return sb_request("GET", "meals", params=f"?user_id=eq.{user_id}&date=eq.{today_str()}&order=created_at.asc")

def get_today_kcal(user_id):
    return sum(m.get("kcal", 0) for m in get_today_meals(user_id))

def add_meal(user_id, meal):
    ensure_user(user_id)
    row = {
        "id": f"{user_id}_{datetime.now().timestamp()}",
        "user_id": str(user_id),
        "date": today_str(),
        "time": datetime.now().strftime("%H:%M"),
        "name": meal.get("name", "Posiłek"),
        "kcal": int(meal.get("kcal", 0)),
        "protein": float(meal.get("protein_g", meal.get("protein", 0))),
        "carbs": float(meal.get("carbs_g", meal.get("carbs", 0))),
        "fat": float(meal.get("fat_g", meal.get("fat", 0))),
        "source": meal.get("source", "photo"),
        "emoji": meal.get("emoji", "🍴"),
    }
    sb_request("POST", "meals", row)
    return row

def get_last_meal(user_id):
    rows = sb_request("GET", "meals", params=f"?user_id=eq.{user_id}&order=created_at.desc&limit=1")
    return rows[0] if rows else None

def delete_meal_by_id(meal_id):
    sb_request("DELETE", "meals", params=f"?id=eq.{meal_id}")

def get_week_meals(user_id):
    from datetime import timedelta
    result = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        rows = sb_request("GET", "meals", params=f"?user_id=eq.{user_id}&date=eq.{d}")
        kcal = sum(m.get("kcal", 0) for m in rows)
        dt = datetime.strptime(d, "%Y-%m-%d")
        result.append({"date": dt.strftime("%d.%m"), "day": ["Pn","Wt","Sr","Cz","Pt","Sb","Nd"][dt.weekday()], "kcal": kcal})
    return result

# ── Claude vision ─────────────────────────────────────────────
def analyze_photo_sync(image_bytes):
    b64 = base64.standard_b64encode(image_bytes).decode()
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
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
                        "Przeanalizuj to zdjecie. Najpierw okresl typ:\n"
                        "1. 'meal' - gotowy posilek lub jedzenie\n"
                        "2. 'label' - etykieta/opakowanie z tabela wartosci odzywczych\n"
                        "3. 'fridge' - zawartosc lodowki/spizarni\n\n"
                        "Odpowiedz TYLKO w JSON:\n"
                        "Dla meal: {\"type\":\"meal\",\"name\":\"nazwa po polsku\",\"kcal\":500,\"protein_g\":30,\"carbs_g\":50,\"fat_g\":15,\"confidence\":\"high\",\"emoji\":\"🍗\"}\n"
                        "Dla label: {\"type\":\"label\",\"name\":\"nazwa produktu\",\"kcal_100g\":250,\"protein_100g\":10,\"carbs_100g\":30,\"fat_100g\":8,\"needs_portion\":true}\n"
                        "Dla fridge: {\"type\":\"fridge\",\"items\":[\"jajka\",\"mleko\"],\"suggestion\":\"omlet z warzywami\",\"suggestion_kcal\":400}\n"
                        "Dla niewyraznego zdjecia: {\"type\":\"error\",\"error\":\"opis problemu\"}"
                    )
                }
            ]
        }]
    )
    raw = re.sub(r"```(?:json)?\s*", "", resp.content[0].text.strip()).rstrip("`").strip()
    data = json.loads(raw)
    photo_type = data.get("type", "meal")
    return photo_type, data

def parse_text_meal_sync(text):
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content":
            f"Uzytkownik opisal co zjadl/wypil: \"{text}\"\n\n"
            "Oblicz wartosci odzywcze na podstawie podanych ilosci.\n"
            "Uzyj standardowych wartosci dla tych produktow.\n"
            "Odpowiedz TYLKO JSON bez markdown:\n"
            '{"name":"krotka nazwa po polsku","kcal":150,"protein_g":5,"carbs_g":20,"fat_g":3,"items":[{"name":"skladnik","amount_g":20,"kcal":80}],"confidence":"high"}'
        }]
    )
    raw = re.sub(r"```(?:json)?\s*", "", resp.content[0].text.strip()).rstrip("`").strip()
    return json.loads(raw)

# ── Telegram handlers ─────────────────────────────────────────
async def cmd_start(update, ctx):
    await update.message.reply_text(
        "Czesc! Jestem NutriBot.\n\n"
        "Wyslij mi:\n"
        "📷 <b>Zdjecie posilku</b> — oszacuje kalorie\n"
        "🏷 <b>Zdjecie etykiety</b> — odczytam wartosci\n"
        "❄️ <b>Zdjecie lodowki</b> — zaproponuje posilek\n"
        "✍️ <b>Opis tekstowy</b> — np. 'zjadlem owsianke 60g z bananem'\n\n"
        "/dzisiaj /cel /historia",
        parse_mode="HTML"
    )

async def cmd_dzisiaj(update, ctx):
    user_id = update.effective_user.id
    meals = get_today_meals(user_id)
    goal = get_goal(user_id)
    total = sum(m.get("kcal", 0) for m in meals)
    remaining = goal - total
    pct = min(int(total / goal * 100), 100) if goal else 0
    bar_filled = pct // 10
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    lines = [f"<b>Dzisiaj — {today_str()}</b>\n"]
    for m in meals:
        lines.append(f"• {m.get('name','?')} — <b>{m.get('kcal',0)} kcal</b>")
    lines.append(f"\n{bar} {pct}%")
    lines.append(f"Razem: <b>{total} / {goal} kcal</b>")
    if remaining >= 0:
        lines.append(f"Pozostalo: <b>{remaining} kcal</b>")
    else:
        lines.append(f"Przekroczono o: <b>{abs(remaining)} kcal</b>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_historia(update, ctx):
    user_id = update.effective_user.id
    week = get_week_meals(user_id)
    lines = ["<b>Ostatnie 7 dni:</b>\n"]
    for d in week:
        bar = "█" * min(d["kcal"] // 200, 10)
        lines.append(f"{d['day']} {d['date']}  {bar} <b>{d['kcal']}</b> kcal")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_cel(update, ctx):
    await update.message.reply_text("Podaj swoj dzienny cel kaloryczny (np. 2000):")
    ctx.user_data["awaiting_goal"] = True

async def cmd_usun(update, ctx):
    user_id = update.effective_user.id
    meal = get_last_meal(user_id)
    if not meal:
        await update.message.reply_text("Brak posilkow do usuniecia.")
        return
    keyboard = [[
        InlineKeyboardButton("✅ Tak, usun", callback_data=f"del_{meal['id']}"),
        InlineKeyboardButton("❌ Anuluj", callback_data="cancel")
    ]]
    await update.message.reply_text(
        f"Usunac ostatni wpis?\n<b>{meal.get('name','?')}</b> — {meal.get('kcal',0)} kcal",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def show_meal_confirm(msg, ctx, data, user_id):
    ctx.user_data["pending"] = data
    name = data.get("name", "Posilek")
    kcal = data.get("kcal", 0)
    protein = data.get("protein_g", data.get("protein", 0))
    carbs = data.get("carbs_g", data.get("carbs", 0))
    fat = data.get("fat_g", data.get("fat", 0))
    conf = data.get("confidence", "medium")
    conf_icon = "🟢" if conf == "high" else "🟡" if conf == "medium" else "🔴"
    keyboard = [[
        InlineKeyboardButton("✅ Zapisz", callback_data="save"),
        InlineKeyboardButton("✏️ Edytuj", callback_data="edit"),
        InlineKeyboardButton("❌ Anuluj", callback_data="cancel")
    ]]
    await msg.edit_text(
        f"{conf_icon} <b>{name}</b>\n\n"
        f"🔥 <b>{kcal} kcal</b>\n"
        f"🥩 Białko: {round(float(protein or 0), 1)}g\n"
        f"🍞 Węgle: {round(float(carbs or 0), 1)}g\n"
        f"🧈 Tłuszcz: {round(float(fat or 0), 1)}g",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def handle_photo(update, ctx):
    user_id = update.effective_user.id
    msg = await update.message.reply_text("Analizuje zdjecie...")
    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
        loop = asyncio.get_event_loop()
        photo_type, analysis = await loop.run_in_executor(None, analyze_photo_sync, image_bytes)
        if photo_type == "error":
            await msg.edit_text(f"Blad analizy: {analysis.get('error','?')}\n\nSprobuj ponownie.")
            return
        if photo_type == "label":
            await handle_label(update, ctx, msg, analysis, user_id)
        elif photo_type == "fridge":
            await handle_fridge(update, ctx, msg, analysis, user_id)
        else:
            await handle_meal(update, ctx, msg, analysis, user_id)
    except Exception as e:
        logger.error(f"Photo error: {e}", exc_info=True)
        await msg.edit_text(f"Blad: {str(e)[:100]}")

async def handle_meal(update, ctx, msg, data, user_id):
    if data.get("needs_clarification") and data.get("clarification_question"):
        ctx.user_data["pending"] = data
        ctx.user_data["awaiting_clarification"] = True
        await msg.edit_text(
            f"Widze: <b>{data.get('name','posilek')}</b>\n\n{data['clarification_question']}",
            parse_mode="HTML"
        )
        return
    await show_meal_confirm(msg, ctx, data, user_id)

async def handle_label(update, ctx, msg, data, user_id):
    name = data.get("name", "Produkt")
    kcal = data.get("kcal_total", data.get("kcal_100g", 0))
    protein = data.get("protein_total", data.get("protein_100g", 0))
    carbs = data.get("carbs_total", data.get("carbs_100g", 0))
    fat = data.get("fat_total", data.get("fat_100g", 0))
    if data.get("needs_portion"):
        ctx.user_data["pending"] = data
        ctx.user_data["awaiting_clarification"] = True
        await msg.edit_text(
            f"Etykieta: <b>{name}</b>\n"
            f"Na 100g: {data.get('kcal_100g',0)} kcal\n\n"
            "Ile gramow/ml zjadles?",
            parse_mode="HTML"
        )
        return
    meal_data = {"name": name, "kcal": kcal, "protein_g": protein, "carbs_g": carbs, "fat_g": fat, "source": "label", "confidence": "high"}
    await show_meal_confirm(msg, ctx, meal_data, user_id)

async def handle_fridge(update, ctx, msg, data, user_id):
    items = data.get("items", [])
    suggestion = data.get("suggestion", "brak propozycji")
    kcal = data.get("suggestion_kcal", 0)
    items_str = ", ".join(items[:8]) if items else "brak"
    ctx.user_data["pending"] = {"name": suggestion, "kcal": kcal, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "source": "photo"}
    keyboard = [[
        InlineKeyboardButton("✅ Zapisz propozycje", callback_data="save"),
        InlineKeyboardButton("❌ Anuluj", callback_data="cancel")
    ]]
    await msg.edit_text(
        f"❄️ W lodowce: <b>{items_str}</b>\n\n"
        f"Propozycja: <b>{suggestion}</b> (~{kcal} kcal)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def handle_text(update, ctx):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if ctx.user_data.get("awaiting_goal"):
        ctx.user_data.pop("awaiting_goal")
        try:
            goal = int(text)
            if 500 <= goal <= 8000:
                set_goal(user_id, goal)
                await update.message.reply_text(f"Cel ustawiony: <b>{goal} kcal/dzien</b>", parse_mode="HTML")
            else:
                await update.message.reply_text("Podaj liczbe od 500 do 8000.")
        except ValueError:
            await update.message.reply_text("Podaj sama liczbe, np. 2000")
        return

    if ctx.user_data.get("awaiting_clarification"):
        ctx.user_data.pop("awaiting_clarification")
        pending = ctx.user_data.get("pending", {})
        msg = await update.message.reply_text("Przeliczam...")
        try:
            resp = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=400,
                messages=[{"role": "user", "content":
                    f"Posilek: {json.dumps(pending, ensure_ascii=False)}\n"
                    f"Uzytkownik powiedzial o ilosci: '{text}'\n"
                    f"Zaktualizuj wartosci odzywcze. Odpowiedz TYLKO JSON: name, kcal, protein_g, carbs_g, fat_g, confidence"
                }]
            )
            raw = re.sub(r"```(?:json)?\s*", "", resp.content[0].text.strip()).rstrip("`").strip()
            updated = json.loads(raw)
            await show_meal_confirm(msg, ctx, updated, user_id)
        except Exception as e:
            await msg.edit_text(f"Blad: {e}")
        return

    food_keywords = ["zjadl", "zjadam", "zjadlem", "zjadlam", "wypil", "wypilam", "wypilem",
                     "jad", "pil", "pije", "jem", "g ", "ml ", "sztuk", "kawe", "kawy",
                     "mleko", "cukier", "chleb", "ryż", "kurczak", "jajk", "owsiank"]
    text_lower = text.lower()
    is_food = any(kw in text_lower for kw in food_keywords) or (
        any(c.isdigit() for c in text) and len(text) > 5
    )

    if is_food:
        msg = await update.message.reply_text("Licze kalorie...")
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, parse_text_meal_sync, text)
            await show_meal_confirm(msg, ctx, data, user_id)
        except Exception as e:
            logger.error(f"Text parse error: {e}", exc_info=True)
            await msg.edit_text("Nie udalo mi sie przetworzyc opisu. Sprobuj inaczej lub wyslij zdjecie.")
        return

    await update.message.reply_text(
        "Mozesz:\n"
        "📷 Wyslac <b>zdjecie</b> posilku, etykiety lub lodowki\n"
        "✍️ Opisac co zjadles, np. <i>zjadlem owsianke 60g z bananem i mlekiem 200ml</i>\n\n"
        "/dzisiaj /cel /historia",
        parse_mode="HTML"
    )

async def handle_callback(update, ctx):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    if data == "save":
        meal = ctx.user_data.get("pending")
        if meal:
            add_meal(user_id, meal)
            total = get_today_kcal(user_id)
            goal = get_goal(user_id)
            remaining = goal - total
            await query.edit_message_text(
                f"✅ Zapisano: <b>{meal.get('name','posilek')}</b> ({meal.get('kcal',0)} kcal)\n\n"
                f"Dzis razem: <b>{total} / {goal} kcal</b>\nPozostalo: <b>{abs(remaining)} kcal</b>",
                parse_mode="HTML"
            )
            ctx.user_data.pop("pending", None)
    elif data == "edit":
        await query.edit_message_text("Podaj gramature lub ilosc (np. 250g, 2 sztuki):")
        ctx.user_data["awaiting_clarification"] = True
    elif data.startswith("del_"):
        delete_meal_by_id(data[4:])
        await query.edit_message_text("Usunieto wpis.")
    elif data == "cancel":
        await query.edit_message_text("Anulowano.")
        ctx.user_data.pop("pending", None)

# ── FastAPI app ───────────────────────────────────────────────
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "marek")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "nutribot123")

api = FastAPI()
security = HTTPBasic()

def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), DASHBOARD_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), DASHBOARD_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Nieprawidlowe haslo", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

bot_app = None

@api.on_event("startup")
async def startup():
    global bot_app
    bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_app.add_handler(CommandHandler("start",    cmd_start))
    bot_app.add_handler(CommandHandler("dzisiaj",  cmd_dzisiaj))
    bot_app.add_handler(CommandHandler("historia", cmd_historia))
    bot_app.add_handler(CommandHandler("cel",      cmd_cel))
    bot_app.add_handler(CommandHandler("usun",     cmd_usun))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    bot_app.add_handler(CallbackQueryHandler(handle_callback))
    await bot_app.initialize()
    await bot_app.start()
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    webhook_url = f"https://{domain}/webhook" if domain else os.getenv("WEBHOOK_URL", "")
    if webhook_url:
        await bot_app.bot.set_webhook(webhook_url, drop_pending_updates=True)
        logger.info(f"Webhook ustawiony: {webhook_url}")
    else:
        logger.warning("Brak RAILWAY_PUBLIC_DOMAIN — webhook nie ustawiony")

@api.on_event("shutdown")
async def shutdown():
    global bot_app
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()

@api.post("/webhook")
async def webhook(request: Request):
    global bot_app
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@api.get("/api/today")
def api_today(user: str = Depends(check_auth)):
    today = date.today().isoformat()
    users = sb_request("GET", "users", params="?order=user_id.asc&limit=1")
    uid = users[0]["user_id"] if users else None
    goal = users[0]["goal"] if users else DEFAULT_KCAL_GOAL
    meals = sb_request("GET", "meals", params=f"?user_id=eq.{uid}&date=eq.{today}&order=created_at.asc") if uid else []
    total = sum(m.get("kcal", 0) for m in meals)
    return {
        "date": today, "goal": goal, "total_kcal": total, "remaining": goal - total,
        "total_protein": round(sum(m.get("protein", 0) for m in meals), 1),
        "total_carbs":   round(sum(m.get("carbs", 0)   for m in meals), 1),
        "total_fat":     round(sum(m.get("fat", 0)     for m in meals), 1),
        "meals": meals
    }

@api.get("/api/history")
def api_history(user: str = Depends(check_auth)):
    from datetime import timedelta
    users = sb_request("GET", "users", params="?order=user_id.asc&limit=1")
    uid = users[0]["user_id"] if users else None
    result = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        day_meals = sb_request("GET", "meals", params=f"?user_id=eq.{uid}&date=eq.{d}") if uid else []
        kcal = sum(m.get("kcal", 0) for m in day_meals)
        dt = datetime.strptime(d, "%Y-%m-%d")
        result.append({
            "date": d, "label": dt.strftime("%d.%m"),
            "day": ["Pn","Wt","Sr","Cz","Pt","Sb","Nd"][dt.weekday()],
            "kcal": kcal, "meals_count": len(day_meals)
        })
    return result

@api.get("/", response_class=HTMLResponse)
def dashboard(user: str = Depends(check_auth)):
    if os.path.exists("dashboard.html"):
        with open("dashboard.html") as f:
            return f.read()
    return "<h1>Dashboard nie znaleziony</h1>"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="info")
