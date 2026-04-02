"""
NutriBot — bot Telegram do śledzenia diety
Używa HTML parse_mode zamiast MarkdownV2 (brak problemów z escapowaniem)
"""
import logging
import asyncio
import base64
import json
import re
from datetime import datetime, date, timezone

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

import os
import urllib.request

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

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

def today_str():
    return date.today().isoformat()

def get_today_meals(user_id):
    return sb_request("GET", "meals", params=f"?user_id=eq.{user_id}&date=eq.{today_str()}&order=created_at.asc")

def get_today_kcal(user_id):
    meals = get_today_meals(user_id)
    return sum(m.get("kcal", 0) for m in meals)

def add_meal(user_id, meal):
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

def analyze_photo_sync(image_bytes):
    b64 = base64.standard_b64encode(image_bytes).decode()
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": """Przeanalizuj to zdjecie jedzenia.

Okresl typ: "label" (etykieta z tabelka wartosci odzywczych), "fridge" (lodowka), "meal" (posilek).

Odpowiedz TYLKO w JSON bez markdown:

Dla meal:
{"type":"meal","name":"nazwa po polsku","kcal":450,"protein_g":35,"carbs_g":55,"fat_g":18,"portion_g":350,"items":[{"name":"skladnik","amount_g":150,"kcal":200}],"needs_clarification":false,"clarification_question":null,"confidence":"high"}

Dla label:
{"type":"label","name":"nazwa produktu","kcal_100g":250,"protein_100g":15,"carbs_100g":30,"fat_100g":8,"portion_g":270,"kcal_total":675,"protein_total":40,"carbs_total":81,"fat_total":22}

Dla fridge:
{"type":"fridge","items":[{"name":"produkt","amount_g":300,"kcal_per_100g":150,"kcal_total":450}]}

Jesli to etykieta odczytaj DOKLADNIE liczby z tabeli wartosci odzywczych. Odpowiedz TYLKO JSON."""}
            ]
        }]
    )
    text = resp.content[0].text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        data = json.loads(text)
        return data.get("type", "meal"), data
    except Exception as e:
        logger.error(f"JSON parse error: {e} | raw: {text[:200]}")
        return "error", {"error": str(e)}

async def cmd_start(update, ctx):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Czesc <b>{name}</b>! Jestem <b>NutriBot</b>\n\n"
        "Wyslij zdjecie posilku, etykiety lub lodowki.\n\n"
        "/dzisiaj — podsumowanie dnia\n"
        "/cel — ustaw cel kaloryczny\n"
        "/historia — ostatnie 7 dni\n"
        "/usun — usun ostatni wpis",
        parse_mode="HTML"
    )

async def cmd_dzisiaj(update, ctx):
    user_id = update.effective_user.id
    meals = get_today_meals(user_id)
    goal = get_goal(user_id)
    total = get_today_kcal(user_id)
    remaining = goal - total
    pct = int(total / goal * 100) if goal else 0
    bar = "X" * min(pct // 10, 10) + "." * max(0, 10 - pct // 10)
    lines = [
        f"Dzis {date.today().strftime('%d.%m.%Y')}\n",
        f"Kalorie: <b>{total} / {goal} kcal</b>",
        f"[{bar}] {pct}%",
        f"Pozostalo: <b>{abs(remaining)} kcal</b>\n",
    ]
    if meals:
        lines.append("<b>Posilki:</b>")
        for m in meals:
            lines.append(f"  {m['name']} — <b>{m['kcal']} kcal</b> ({m.get('time','')})")
    else:
        lines.append("<i>Brak wpisow. Wyslij zdjecie!</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_historia(update, ctx):
    user_id = update.effective_user.id
    history = get_week_meals(user_id)
    goal = get_goal(user_id)
    lines = ["<b>Ostatnie 7 dni:</b>\n"]
    for d in history:
        if d["kcal"] == 0:
            lines.append(f"  {d['day']} {d['date']} — brak danych")
            continue
        pct = int(d["kcal"] / goal * 100) if goal else 0
        status = "OK" if 90 <= pct <= 110 else ("OVER" if pct > 110 else "LOW")
        lines.append(f"[{status}] <b>{d['day']} {d['date']}</b> — {d['kcal']} kcal ({pct}%)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_cel(update, ctx):
    ctx.user_data["awaiting_goal"] = True
    await update.message.reply_text(
        f"Aktualny cel: <b>{get_goal(update.effective_user.id)} kcal</b>\n\nPodaj nowy cel (np. 2000):",
        parse_mode="HTML"
    )

async def cmd_usun(update, ctx):
    user_id = update.effective_user.id
    last = get_last_meal(user_id)
    if not last:
        await update.message.reply_text("Brak wpisow do usuniecia.")
        return
    keyboard = [[
        InlineKeyboardButton("Usun", callback_data=f"del_{last['id']}"),
        InlineKeyboardButton("Anuluj", callback_data="cancel")
    ]]
    await update.message.reply_text(
        f"Usunac: <b>{last['name']}</b> — {last['kcal']} kcal?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
    portion = data.get("portion_g", 100)
    ctx.user_data["pending"] = {"name": name, "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "portion_g": portion, "source": "label"}
    keyboard = [[
        InlineKeyboardButton("Zapisz", callback_data="save"),
        InlineKeyboardButton("Inna ilosc", callback_data="edit"),
        InlineKeyboardButton("Anuluj", callback_data="cancel")
    ]]
    await msg.edit_text(
        f"Etykieta: <b>{name}</b>\nPorcja: {portion}g\nKalorie: <b>{kcal} kcal</b>\nBialko: {protein}g | Wegle: {carbs}g | Tluszcze: {fat}g",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_fridge(update, ctx, msg, data, user_id):
    items = data.get("items", [])
    goal = get_goal(user_id)
    eaten = get_today_kcal(user_id)
    remaining = goal - eaten
    lines = [f"Lodowka — pozostalo do celu: <b>{remaining} kcal</b>\n", "<b>Widze:</b>"]
    for item in items[:8]:
        lines.append(f"  {item.get('name','?')} ~{item.get('amount_g','?')}g — {item.get('kcal_total', '?')} kcal")
    lines.append("\n<b>Propozycja:</b>")
    budget = remaining
    for item in sorted(items, key=lambda x: x.get("kcal_total", 999)):
        kcal = item.get("kcal_total", 0)
        if 0 < kcal <= budget:
            lines.append(f"  {item.get('name')} {item.get('amount_g','?')}g — {kcal} kcal")
            budget -= kcal
    await msg.edit_text("\n".join(lines), parse_mode="HTML")

async def show_meal_confirm(msg, ctx, data, user_id):
    ctx.user_data["pending"] = {
        "name": data.get("name", "Posilek"),
        "kcal": int(data.get("kcal", data.get("total_kcal", 0))),
        "protein": round(float(data.get("protein_g", data.get("total_protein_g", 0))), 1),
        "carbs": round(float(data.get("carbs_g", data.get("total_carbs_g", 0))), 1),
        "fat": round(float(data.get("fat_g", data.get("total_fat_g", 0))), 1),
        "source": "photo"
    }
    meal = ctx.user_data["pending"]
    items = data.get("items", [])
    text = (f"<b>{meal['name']}</b>\n\n"
            f"Kalorie: <b>{meal['kcal']} kcal</b>\n"
            f"Bialko: {meal['protein']}g | Wegle: {meal['carbs']}g | Tluszcze: {meal['fat']}g\n")
    if items:
        text += "\n<b>Skladniki:</b>\n"
        for it in items[:5]:
            text += f"  {it.get('name','')} ~{it.get('amount_g','?')}g — {it.get('kcal','?')} kcal\n"
    keyboard = [[
        InlineKeyboardButton("Zapisz", callback_data="save"),
        InlineKeyboardButton("Popraw ilosc", callback_data="edit"),
        InlineKeyboardButton("Anuluj", callback_data="cancel")
    ]]
    await msg.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

def parse_text_meal_sync(text):
    """Claude parsuje opis tekstowy jedzenia i zwraca makro"""
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


async def handle_text(update, ctx):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Ustawianie celu kalorycznego
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

    # Odpowiedz na pytanie o gramature po zdjeciu
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

    # Naturalne opisy jedzenia — dowolny tekst o posilku
    food_keywords = ["zjadl", "zjadam", "zjadlem", "zjadlam", "wypil", "wypilam", "wypilem",
                     "jad", "pil", "pije", "jem", "g ", "ml ", "sztuk", "kawe", "kawy",
                     "mleko", "cukier", "chleb", "ryż", "kurczak", "jajk", "owsiank"]
    text_lower = text.lower()
    is_food_description = any(kw in text_lower for kw in food_keywords) or (
        any(c.isdigit() for c in text) and len(text) > 5
    )

    if is_food_description:
        msg = await update.message.reply_text("Licze kalorie...")
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, parse_text_meal_sync, text)
            await show_meal_confirm(msg, ctx, data, user_id)
        except Exception as e:
            logger.error(f"Text parse error: {e}", exc_info=True)
            await msg.edit_text(f"Nie udalo mi sie przetworzyc opisu. Sprobuj inaczej lub wyslij zdjecie.")
        return

    # Nierozpoznana wiadomosc
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
                f"Zapisano: <b>{meal['name']}</b> — {meal['kcal']} kcal\n\n"
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

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("dzisiaj",  cmd_dzisiaj))
    app.add_handler(CommandHandler("historia", cmd_historia))
    app.add_handler(CommandHandler("cel",      cmd_cel))
    app.add_handler(CommandHandler("usun",     cmd_usun))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("NutriBot uruchomiony!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


# ── API ─────────────────────────────────────────────────────
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import secrets
import uvicorn
import threading

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "marek")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "nutribot123")

api = FastAPI()
security = HTTPBasic()

def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), DASHBOARD_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), DASHBOARD_PASS.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidlowe haslo",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@api.get("/api/today")
def api_today(user: str = Depends(check_auth)):
    today = date.today().isoformat()
    # pobierz pierwszego usera z Supabase
    users = sb_request("GET", "users", params="?order=user_id.asc&limit=1")
    uid = users[0]["user_id"] if users else None
    goal = users[0]["goal"] if users else DEFAULT_KCAL_GOAL
    if uid:
        meals = sb_request("GET", "meals", params=f"?user_id=eq.{uid}&date=eq.{today}&order=created_at.asc")
    else:
        meals = []
    total = sum(m.get("kcal", 0) for m in meals)
    return {
        "date": today,
        "goal": goal,
        "total_kcal": total,
        "remaining": goal - total,
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
        if uid:
            day_meals = sb_request("GET", "meals", params=f"?user_id=eq.{uid}&date=eq.{d}")
        else:
            day_meals = []
        kcal = sum(m.get("kcal", 0) for m in day_meals)
        dt = datetime.strptime(d, "%Y-%m-%d")
        result.append({
            "date": d,
            "label": dt.strftime("%d.%m"),
            "day": ["Pn","Wt","Sr","Cz","Pt","Sb","Nd"][dt.weekday()],
            "kcal": kcal,
            "meals_count": len(day_meals)
        })
    return result

@api.get("/", response_class=HTMLResponse)
def dashboard(user: str = Depends(check_auth)):
    if os.path.exists("dashboard.html"):
        with open("dashboard.html") as f:
            return f.read()
    return "<h1>Dashboard nie znaleziony</h1>"

def run_api():
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    # Uruchom API w tle, bot w głównym wątku
    t = threading.Thread(target=run_api, daemon=True)
    t.start()
    logger.info(f"API uruchomione na porcie {os.getenv('PORT', 8000)}")
    main()
