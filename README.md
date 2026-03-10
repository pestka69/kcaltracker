# NutriBot 🥗

Bot Telegram do śledzenia diety z AI. Analizuje zdjęcia posiłków, etykiet i lodówki.

## Funkcje
- 📷 Analiza zdjęć posiłków (Claude Vision)
- 🏷️ Odczyt etykiet produktów
- 🧊 Propozycje posiłków z zawartości lodówki
- 💍 Integracja z Whoop (strain, recovery, kalorie)
- 📊 Dashboard webowy z historią

## Uruchomienie w 5 krokach

### 1. Klonowanie i instalacja
```bash
git clone ... && cd nutribot
pip install -r requirements.txt
cp .env.example .env
# Uzupełnij .env
```

### 2. Baza danych — Supabase
1. Załóż konto na https://supabase.com
2. Nowy projekt → SQL Editor
3. Wklej i uruchom zawartość `db/schema.sql`
4. Skopiuj URL i klucz anon do `.env`

### 3. Telegram Bot
1. Napisz do @BotFather na Telegramie
2. `/newbot` → nadaj nazwę → skopiuj token do `.env`

### 4. Anthropic API
1. Załóż konto: https://console.anthropic.com
2. API Keys → Create Key → skopiuj do `.env`

### 5. Uruchomienie
```bash
python bot.py
```

## Deployment (Railway.app — darmowy)
1. Wgraj kod na GitHub
2. Railway.app → New Project → Deploy from GitHub
3. Dodaj zmienne środowiskowe z `.env`
4. Deploy!

## Whoop Integration
1. Załóż aplikację: https://developer-dashboard.whoop.com
2. Callback URL: `https://twoja-domena/whoop/callback`
3. Uzupełnij `WHOOP_CLIENT_ID` i `WHOOP_CLIENT_SECRET` w `.env`
4. W bocie: `/whoop_connect` → zatwierdź w aplikacji Whoop

## Struktura projektu
```
nutribot/
├── bot.py              ← główny plik bota
├── config.py           ← konfiguracja
├── requirements.txt
├── .env.example
├── db/
│   ├── database.py     ← warstwa Supabase
│   └── schema.sql      ← schemat tabel
├── services/
│   ├── vision.py       ← Claude API vision
│   ├── nutrition.py    ← logika żywieniowa
│   └── whoop.py        ← Whoop API
└── utils/
    └── formatters.py   ← formatowanie wiadomości
```

## Koszt użytkowania
- Claude API: ~$0.01-0.03 za analizę zdjęcia
- Supabase: darmowy do 500MB danych
- Railway: darmowy tier (500h/miesiąc)
- **Łącznie: ~$5-15/miesiąc** przy codziennym użytkowaniu
