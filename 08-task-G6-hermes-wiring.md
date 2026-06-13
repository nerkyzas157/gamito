<!-- Context/Task file: 08-task-G6-hermes-wiring.md -->

# Task: Phase G6 - Hermes Wiring

**Goal**: Connect the Gamito MCP server to Hermes Agent and test end-to-end.

**Required Context**: `00-architecture-and-context.md`, `01-mcp-api-spec.md`

## 11. Hermes Wiring: Config + Skill

### 11.1 `~/.hermes/config.yaml`

```yaml
mcp_servers:
  gamito:
    command: "uv"
    args: ["run", "--project", "/opt/gamito", "gamito-mcp"]
    timeout: 120
```

Hermes connects at startup, lists the 20 tools (13 baseline + 3 plan-lifecycle
+ 4 recipe CRUD), and registers them alongside built-ins. (Pattern confirmed
against Hermes docs; exact keys may drift with Hermes releases — the
integration surface is deliberately this one config block.)

### 11.2 The `gamito` skill (`skills/gamito/SKILL.md`)

Tool *schemas* are auto-discovered; the skill supplies *judgment*. Outline:

```markdown
---
name: gamito
description: Household meal planning — budget-disciplined, allergy-safe plans,
  shopping lists, pantry, and taste learning via the gamito MCP tools.
metadata:
  tags: [cooking, planning, household]
---

## When to use
Meal plans, "what should we eat", shopping lists, pantry updates, meal swaps,
food budgets. NOT for general recipe chat — answer those directly.

## Identity rules (CRITICAL)
- Every person maps to one profile_id. Unknown person → list_profiles, ask.
- New person → run the onboarding interview (below) → save_profile. NEVER
  generate a plan without a profile.
- Allergies and diet are communicated ONLY via save_profile fields. Never
  rely on memory for safety constraints; if the user mentions a new allergy,
  update the profile FIRST, then plan.

## Onboarding interview (collect, then one save_profile call)
language; dietary preference; allergies; disliked ingredients; kitchen tools
(oven, stovetop, air fryer, microwave, slow cooker, grill...); cuisines they
love; cooking skill; meal-prep / leftovers attitude; max minutes per meal.

## Calling conventions
- Compose query_en in ENGLISH always; reply to the user in THEIR language.
- After generate_meal_plan, forward the `text` field, then offer: swap any
  meal, rescale servings, or show the shopping list.
- "Make day 2 dinner cheaper/vegan/faster" → swap_meal with a query_en
  reflecting the request and the slot's constraints.
- "Make a similar plan but better" / "improve last week's plan" →
  regenerate_plan with the source plan_id; rely on stored ratings for
  auto-mode unless the user is explicit ("keep Tuesday, drop the curry").
- "Save this plan as 'Cheap weeknights'" / "favourite this one" →
  label_plan. "What plans have I saved?" → list_plans(favorites_only=true).
- Soft feedback ("less spicy next time") → update_preferences.
  Numeric ratings → rate_meal.
- Pantry photo → extract visible long-shelf-life staples yourself (vision),
  then update_pantry with the labels; relay rejections honestly.
- "Save mama's recipe" / "add this from the cookbook photo" → parse the
  recipe yourself into structured fields (title, ingredients with amounts,
  step-by-step directions, time, cuisine, declared allergens, dietary flags)
  and call add_recipe. ALWAYS ask the user to confirm declared allergens
  and dietary flags before saving — Gamito trusts these as ground truth and
  uses them as hard filters in future plans. Surface unmatched ingredients
  from the response so the user can correct names if desired.
- On error responses, follow the `hint` — it is written for you.

## Automation example
"Every Sunday 18:00: generate_meal_plan for profile <id>, budget 60 EUR,
2 servings, 5 days × 2 meals; send the text to the family group."
```

### 11.3 Live E2E acceptance script (Phase G6)

1. New family member messages the bot → interview → profile created.
2. "Plan 3 days, 2 of us, 50 €" → plan in chat < 10 s end-to-end.
3. "Tuesday dinner without mushrooms" → swap, diff shown.
4. Shelf photo → pantry updated, rejections explained.
5. Rate two meals (9 and 2) → regenerate → plan visibly shifts; "why?" →
   agent cites the taste profile.
6. Sunday automation fires and posts to the group.

---

### Phase G6 — Hermes Wiring (1–2 days) — P0

- [ ] Install Hermes on the target machine; `mcp_servers.gamito` config block
- [ ] Write `skills/gamito/SKILL.md` (§11.2)
- [ ] Run the full §11.3 acceptance script live from Telegram
- [ ] Set up the Sunday automation for real

**Accept**: a family member who has never seen the project gets a plan in chat.
