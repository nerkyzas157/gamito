PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE profiles (
  profile_id    TEXT PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  language      TEXT NOT NULL DEFAULT 'en',
  dietary_pref  TEXT,
  skill_level   TEXT,
  meal_prep_ok  INTEGER NOT NULL DEFAULT 1,
  leftovers_ok  INTEGER NOT NULL DEFAULT 1,
  max_time_min  INTEGER,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE profile_allergies (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  allergen    TEXT NOT NULL,
  UNIQUE (profile_id, allergen)
);

CREATE TABLE profile_tools (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  tool        TEXT NOT NULL,
  UNIQUE (profile_id, tool)
);

CREATE TABLE profile_cuisines (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  cuisine     TEXT NOT NULL,
  UNIQUE (profile_id, cuisine)
);

CREATE TABLE profile_tags (
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  tag         TEXT NOT NULL,
  sentiment   TEXT NOT NULL CHECK (sentiment IN ('positive','negative')),
  weight      INTEGER NOT NULL DEFAULT 1,
  source      TEXT NOT NULL CHECK (source IN ('survey','rating','correction')),
  updated_at  TEXT NOT NULL,
  UNIQUE (profile_id, tag, sentiment)
);
CREATE INDEX idx_tags_profile ON profile_tags(profile_id);

CREATE TABLE meal_plans (
  plan_id          TEXT PRIMARY KEY,
  profile_id       TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  plan_type        TEXT NOT NULL CHECK (plan_type IN ('single','multi_day')),
  num_days         INTEGER NOT NULL,
  meals_per_day    INTEGER NOT NULL,
  total_budget_eur REAL NOT NULL,
  servings         INTEGER NOT NULL,
  max_time_min     INTEGER,
  status           TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('complete','error')),
  total_cost_eur   REAL,
  warnings_json    TEXT,
  label            TEXT,
  is_favorite      INTEGER NOT NULL DEFAULT 0,
  regenerated_from TEXT REFERENCES meal_plans(plan_id) ON DELETE SET NULL,
  seed             INTEGER,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX idx_plans_profile ON meal_plans(profile_id, created_at);
CREATE INDEX idx_plans_favorites ON meal_plans(profile_id, is_favorite) WHERE is_favorite = 1;
CREATE UNIQUE INDEX idx_plans_label ON meal_plans(profile_id, label) WHERE label IS NOT NULL;

CREATE TABLE plan_meals (
  meal_id               TEXT PRIMARY KEY,
  plan_id               TEXT NOT NULL REFERENCES meal_plans(plan_id) ON DELETE CASCADE,
  slot_key              TEXT NOT NULL,
  day_number            INTEGER NOT NULL,
  meal_slot             TEXT NOT NULL CHECK (meal_slot IN ('breakfast','lunch','dinner','snack')),
  recipe_id             TEXT,
  recipe_title          TEXT NOT NULL,
  meal_type             TEXT NOT NULL CHECK (meal_type IN ('new','meal_prep','leftover')),
  source                TEXT NOT NULL DEFAULT 'dataset' CHECK (source IN ('dataset','custom')),
  source_slot_key       TEXT,
  total_time_min        INTEGER,
  difficulty            TEXT,
  cuisines_json         TEXT,
  dietary_json          TEXT,
  nutrition_json        TEXT,
  servings              INTEGER NOT NULL,
  cost_total_eur        REAL,
  cost_per_serving_eur  REAL,
  ingredients_json      TEXT NOT NULL,
  directions_json       TEXT NOT NULL,
  tools_json            TEXT,
  created_at            TEXT NOT NULL,
  UNIQUE (plan_id, slot_key)
);

CREATE TABLE meal_ratings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id  TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  plan_id     TEXT NOT NULL REFERENCES meal_plans(plan_id) ON DELETE CASCADE,
  slot_key    TEXT NOT NULL,
  rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 10),
  created_at  TEXT NOT NULL,
  UNIQUE (profile_id, plan_id, slot_key)
);

CREATE TABLE plan_edits (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id      TEXT NOT NULL REFERENCES meal_plans(plan_id) ON DELETE CASCADE,
  slot_key     TEXT NOT NULL,
  edit_type    TEXT NOT NULL CHECK (edit_type IN ('swap','rescale')),
  payload_json TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE pantry_items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id     TEXT NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE,
  canonical_name TEXT NOT NULL,
  source         TEXT NOT NULL DEFAULT 'agent' CHECK (source IN ('agent','manual')),
  confidence     REAL,
  last_seen_at   TEXT NOT NULL,
  UNIQUE (profile_id, canonical_name)
);
CREATE INDEX idx_pantry_profile ON pantry_items(profile_id);

CREATE TABLE custom_recipes (
  recipe_id             TEXT PRIMARY KEY,
  title                 TEXT NOT NULL,
  cuisines_json         TEXT NOT NULL DEFAULT '[]',
  courses_json          TEXT NOT NULL DEFAULT '[]',
  tastes_json           TEXT NOT NULL DEFAULT '[]',
  total_time_min        INTEGER,
  difficulty            TEXT,
  servings              INTEGER NOT NULL DEFAULT 2,
  ingredients_json      TEXT NOT NULL,
  directions_json       TEXT NOT NULL,
  tools_json            TEXT NOT NULL DEFAULT '[]',
  dietary_json          TEXT NOT NULL DEFAULT '{}',
  allergens_json        TEXT NOT NULL DEFAULT '[]',
  price_per_serving_eur REAL,
  cost_total_eur        REAL,
  nutrition_json        TEXT,
  notes                 TEXT,
  source                TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user','imported')),
  added_by_profile_id   TEXT REFERENCES profiles(profile_id) ON DELETE SET NULL,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);
CREATE INDEX idx_custom_recipes_title ON custom_recipes(title);

CREATE TABLE custom_recipe_embeddings (
  recipe_id    TEXT PRIMARY KEY REFERENCES custom_recipes(recipe_id) ON DELETE CASCADE,
  model        TEXT NOT NULL,
  dims         INTEGER NOT NULL,
  vector       BLOB NOT NULL,
  embed_text   TEXT NOT NULL,
  encoded_at   TEXT NOT NULL
);

CREATE TABLE custom_recipes_meta (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  revision  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO custom_recipes_meta (id, revision) VALUES (1, 0);
