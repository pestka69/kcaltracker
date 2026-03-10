-- ============================================================
-- NutriBot — schemat bazy danych (Supabase / PostgreSQL)
-- Uruchom w Supabase SQL Editor: https://supabase.com
-- ============================================================

-- Tabela użytkowników
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    name            TEXT,
    kcal_goal       INTEGER DEFAULT 2000,
    protein_goal_g  INTEGER DEFAULT 140,
    carbs_goal_g    INTEGER DEFAULT 250,
    fat_goal_g      INTEGER DEFAULT 70,
    goal_type       TEXT DEFAULT 'maintenance',  -- maintenance / cut / bulk
    weight_kg       FLOAT,
    height_cm       FLOAT,
    age             INTEGER,
    gender          TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Tabela posiłków
CREATE TABLE IF NOT EXISTS meals (
    id                  BIGSERIAL PRIMARY KEY,
    user_telegram_id    BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    kcal                INTEGER NOT NULL DEFAULT 0,
    protein_g           FLOAT DEFAULT 0,
    carbs_g             FLOAT DEFAULT 0,
    fat_g               FLOAT DEFAULT 0,
    fiber_g             FLOAT DEFAULT 0,
    portion_g           FLOAT DEFAULT 0,
    source              TEXT DEFAULT 'photo',   -- 'photo' | 'label' | 'manual'
    store               TEXT,                   -- 'Biedronka' | 'Lidl' | ...
    items_json          TEXT DEFAULT '[]',       -- JSON lista składników
    eaten_at            TIMESTAMPTZ DEFAULT NOW(),
    date                DATE DEFAULT CURRENT_DATE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Tokeny Whoop OAuth
CREATE TABLE IF NOT EXISTS whoop_tokens (
    id                  BIGSERIAL PRIMARY KEY,
    user_telegram_id    BIGINT UNIQUE NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    token_json          TEXT NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Cache danych Whoop (opcjonalne — żeby nie odpytywać API za często)
CREATE TABLE IF NOT EXISTS whoop_daily_cache (
    id                  BIGSERIAL PRIMARY KEY,
    user_telegram_id    BIGINT NOT NULL,
    date                DATE NOT NULL,
    data_json           TEXT NOT NULL,
    cached_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_telegram_id, date)
);

-- ── INDEKSY ────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_meals_user_date
    ON meals(user_telegram_id, date);

CREATE INDEX IF NOT EXISTS idx_meals_user_eaten_at
    ON meals(user_telegram_id, eaten_at DESC);

-- ── ROW LEVEL SECURITY (opcjonalne, zalecane) ──────────────
ALTER TABLE meals ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- ── WIDOK: dzienne podsumowanie ────────────────────────────
CREATE OR REPLACE VIEW daily_summary AS
SELECT
    user_telegram_id,
    date,
    COUNT(*)            AS meal_count,
    SUM(kcal)           AS total_kcal,
    SUM(protein_g)      AS total_protein_g,
    SUM(carbs_g)        AS total_carbs_g,
    SUM(fat_g)          AS total_fat_g
FROM meals
GROUP BY user_telegram_id, date
ORDER BY date DESC;
