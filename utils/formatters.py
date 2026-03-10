from typing import Optional
"""
utils/formatters.py — formatowanie wiadomości Telegram (MarkdownV2)
"""
from datetime import date


def esc(text) -> str:
    """Escape znaków specjalnych MarkdownV2"""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def progress_bar(current: int, goal: int, length: int = 10) -> str:
    """Pasek postępu: ████░░░░░░"""
    if goal <= 0:
        return "░" * length
    pct = min(current / goal, 1.2)
    filled = min(int(pct * length), length)
    bar = "█" * filled + "░" * (length - filled)
    return bar


def format_daily_summary(meals: list, goal: int, whoop: Optional[dict]) -> str:
    """Pełne podsumowanie dnia"""
    total_kcal    = sum(m.get("kcal", 0) for m in meals)
    total_protein = sum(m.get("protein_g", 0) for m in meals)
    total_carbs   = sum(m.get("carbs_g", 0) for m in meals)
    total_fat     = sum(m.get("fat_g", 0) for m in meals)
    
    remaining = goal - total_kcal
    pct = int((total_kcal / goal * 100)) if goal else 0
    bar = progress_bar(total_kcal, goal)
    
    today_str = date.today().strftime("%d\\.%m\\.%Y")
    status_emoji = "✅" if 90 <= pct <= 110 else ("🔴" if pct > 110 else "🔵")
    
    lines = [
        f"📊 *Podsumowanie — {today_str}*\n",
        f"🔥 Kalorie: *{esc(total_kcal)} / {esc(goal)} kcal*",
        f"`{bar}` {pct}%",
        f"{status_emoji} Pozostało: *{esc(abs(remaining))} kcal*",
        "",
        f"🥩 Białko:       *{esc(round(total_protein,1))}g*",
        f"🍞 Węglowodany: *{esc(round(total_carbs,1))}g*",
        f"🧈 Tłuszcze:    *{esc(round(total_fat,1))}g*",
    ]
    
    if whoop:
        lines += [
            "",
            f"💍 *Whoop:*",
            f"   ⚡ Strain: *{esc(whoop.get('strain','—'))}*",
            f"   💚 Recovery: *{esc(whoop.get('recovery','—'))}%*",
            f"   🔥 Spalono: *{esc(whoop.get('calories_burned','—'))} kcal*",
        ]
        
        burned = whoop.get("calories_burned", 0)
        if burned and burned > 0:
            net = burned - total_kcal
            net_str = f"\\+{esc(abs(net))}" if net > 0 else f"\\-{esc(abs(net))}"
            lines.append(f"   📈 Bilans: *{net_str} kcal*")
    
    if meals:
        lines += ["", f"🍽️ *Posiłki dziś \\({len(meals)}\\):*"]
        for m in meals:
            time_str = ""
            if m.get("eaten_at"):
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(m["eaten_at"].replace("Z", "+00:00"))
                    time_str = f" _{dt.strftime('%H:%M')}_"
                except Exception:
                    pass
            source_icon = {"label": "🏷️", "photo": "📷"}.get(m.get("source", "photo"), "📷")
            lines.append(
                f"   {source_icon} {esc(m['name'])} — *{esc(m['kcal'])} kcal*{time_str}"
            )
    else:
        lines += ["", "_Brak wpisów\\._ Wyślij zdjęcie posiłku\\!"]
    
    return "\n".join(lines)


def format_meal_entry(meal: dict) -> str:
    """Potwierdzenie wpisu posiłku"""
    name     = meal.get("name", "Posiłek")
    kcal     = meal.get("total_kcal", meal.get("kcal", 0))
    protein  = meal.get("total_protein_g", meal.get("protein_g", 0))
    carbs    = meal.get("total_carbs_g",   meal.get("carbs_g", 0))
    fat      = meal.get("total_fat_g",     meal.get("fat_g", 0))
    items    = meal.get("items", [])
    conf     = meal.get("confidence", "medium")
    
    conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "🟡")
    
    lines = [
        f"🍽️ *{esc(name)}*",
        f"{conf_icon} Pewność: {esc(conf)}\n",
        f"🔥 *{esc(kcal)} kcal*",
        f"🥩 Białko: *{esc(round(protein,1))}g*",
        f"🍞 Węgle:  *{esc(round(carbs,1))}g*",
        f"🧈 Tłuszcz: *{esc(round(fat,1))}g*",
    ]
    
    if items:
        lines += ["", "📋 *Składniki:*"]
        for item in items[:6]:  # max 6 składników
            lines.append(
                f"   • {esc(item.get('name',''))} "
                f"~{esc(item.get('amount_g','?'))}g "
                f"— {esc(item.get('kcal','?'))} kcal"
            )
    
    lines += ["", "_Czy zapisać ten posiłek?_"]
    return "\n".join(lines)


def format_fridge_suggestion(
    items: list,
    suggestion: dict,
    remaining: int,
    eaten: int,
    goal: int
) -> str:
    """Propozycja posiłku z lodówki"""
    bar = progress_bar(eaten, goal)
    pct = int(eaten / goal * 100) if goal else 0
    
    lines = [
        f"🧊 *Analiza lodówki*\n",
        f"🔥 Dziś: *{esc(eaten)} / {esc(goal)} kcal* \\({pct}%\\)",
        f"`{bar}`",
        f"💡 Pozostało: *{esc(remaining)} kcal*\n",
        f"🛒 *Widzę w lodówce:*",
    ]
    
    for item in items[:8]:
        lines.append(
            f"   • {esc(item.get('name','?'))} "
            f"\\({esc(item.get('estimated_amount','?'))}\\)"
        )
    
    if suggestion and suggestion.get("meal_name"):
        s_kcal    = suggestion.get("total_kcal", "?")
        s_protein = suggestion.get("protein_g", "?")
        s_name    = suggestion.get("meal_name", "Propozycja")
        
        lines += [
            "",
            f"✨ *Proponuję na kolację:*",
            f"*{esc(s_name)}*",
            f"🔥 {esc(s_kcal)} kcal  \\|  🥩 {esc(s_protein)}g białka\n",
            "_Składniki:_"
        ]
        
        for item in suggestion.get("items", []):
            lines.append(
                f"   → {esc(item.get('name',''))} — "
                f"*{esc(item.get('amount_g','?'))}g* "
                f"\\({esc(item.get('kcal','?'))} kcal\\)"
            )
        
        if suggestion.get("preparation"):
            lines += ["", f"👨‍🍳 _{esc(suggestion['preparation'])}_"]
        
        if suggestion.get("why"):
            lines += [f"💡 _{esc(suggestion['why'])}_"]
    
    return "\n".join(lines)
