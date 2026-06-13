---
name: gamito
description: >-
  Household meal planning over the gamito MCP server: budget-disciplined,
  allergy-safe meal plans, shopping lists, pantry tracking, meal swaps, and
  taste learning. Use when the user asks to plan meals, decide "what should we
  eat", build or edit a week of dinners, manage a food budget, update the
  pantry from a shelf photo, save a household recipe, or rate/improve a past
  plan. Do NOT use for general recipe chit-chat or cooking trivia — answer
  those directly.
metadata:
  tags: [cooking, meal-planning, household, budget, mcp]
---

# Gamito Meal Planning

**Audience:** the assistant orchestrating the `gamito` MCP server on behalf of a
household.

The MCP server exposes 20 deterministic tools. Their *schemas* are
auto-discovered at connect time; this skill supplies the *judgment* — when to
call which tool, in what order, and how to recover from errors. Tool reference
lives in `references/tool-index.md`; read it only when you need an exact
signature.

> **Note:** All planning state (profiles, plans, ratings, pantry, recipes)
> lives in the server's SQLite store. Never rely on conversational memory for
> safety constraints. The store is the single source of truth.

## When To Use This Skill

Activate for:

- Meal plans and "what should we eat this week".
- Shopping lists and food-budget questions.
- Pantry updates, including shelf or fridge photos.
- Meal swaps, rescaling servings, and "make day 2 cheaper / vegan / faster".
- Saving household recipes from text or photos.
- Rating meals and improving a previous plan.

Do not activate for general recipe chat, nutrition trivia, or cooking
how-to questions with no plan attached — answer those directly.

## Tool Naming

Always call tools by their fully qualified name, `gamito:<tool>`, e.g.
`gamito:generate_meal_plan`. This prevents resolution errors when other MCP
servers are connected.

## Identity Rules (Critical)

1. Every person maps to exactly one `profile_id`. When the speaker is unknown,
   call `gamito:list_profiles` and ask which profile to use.
2. A new person requires the onboarding interview below, then one
   `gamito:save_profile` call. **Never generate a plan without a profile.**
3. Allergies and dietary restrictions are communicated to the server **only**
   through `gamito:save_profile` fields — they are enforced as hard filters.
   If the user mentions a new allergy or restriction, update the profile
   **first**, then plan or swap.

> **Warning:** Treat allergies as a safety boundary. If you are unsure whether a
> constraint is recorded, call `gamito:get_profile` and confirm before
> producing food suggestions.

## Onboarding Interview

Collect everything below in conversation, then make a single
`gamito:save_profile` call (omit `profile_id` to create):

- Preferred language for replies.
- Dietary preference (e.g. omnivore, vegetarian, vegan).
- Allergies.
- Disliked ingredients.
- Kitchen tools available (oven, stovetop, air fryer, microwave, slow cooker,
  grill, …).
- Cuisines they love.
- Cooking skill level.
- Meal-prep and leftovers attitude.
- Maximum minutes per meal.

On update, supplied arrays **replace** the profile's allergy / tool / cuisine
rows. To preserve existing values, read them with `gamito:get_profile` and pass
them back.

## Calling Conventions

- **Language:** compose every `query_en` argument in **English**, always. Reply
  to the user in **their** profile language.
- **After planning:** forward the returned `text` field verbatim, then offer
  three follow-ups — swap a meal, rescale servings, or show the shopping list.
- **"Make day 2 dinner cheaper / vegan / faster"** → `gamito:swap_meal` with a
  `query_en` reflecting the request and the slot's constraints. `max_price_eur`
  is a total slot cap, not price per serving.
- **"Make a similar plan but better" / "improve last week's plan"** →
  `gamito:regenerate_plan` with the source `plan_id`. Rely on stored ratings for
  auto-mode unless the user is explicit ("keep Tuesday, drop the curry").
- **"Save this as 'Cheap weeknights'" / "favourite this one"** →
  `gamito:label_plan`. **"What plans have I saved?"** →
  `gamito:list_plans` (set `favorites_only=true` when they ask for favourites).
- **Soft feedback** ("less spicy next time") → `gamito:update_preferences`.
  **Numeric ratings** → `gamito:rate_meal`.
- **Pantry photo** → extract visible long-shelf-life staples yourself (vision),
  then call `gamito:update_pantry` with the labels. Relay rejections honestly,
  using the `reason` on each rejected item.
- **"Save mama's recipe" / "add this from the cookbook photo"** → parse the
  recipe yourself into structured fields (title; ingredients with amounts;
  step-by-step directions; time; cuisine; declared allergens; dietary flags),
  then call `gamito:add_recipe`. **Always confirm declared allergens and dietary
  flags with the user before saving** — the server trusts these as ground truth
  and applies them as hard filters in future plans. Surface any unmatched
  ingredients from the response so the user can correct names.
- **On error responses:** follow the `hint` field. It is written for you.

## Core Workflows

### Plan A Week Of Meals

1. Resolve the `profile_id` (interview + `gamito:save_profile` if new).
2. Confirm budget, servings, number of days (1–14), and meals per day (1–3).
3. Call `gamito:generate_meal_plan`.
4. Forward the `text`; offer swap / rescale / shopping list.

```text
gamito:generate_meal_plan(
  profile_id="<id>", budget_eur=50, servings=2,
  num_days=3, meals_per_day=2
)
```

### Edit An Existing Plan

| User intent | Tool | Key arguments |
|---|---|---|
| Replace one meal | `gamito:swap_meal` | `plan_id`, `slot_key`, `query_en` |
| Change a meal's serving count | `gamito:rescale_meal` | `plan_id`, `slot_key`, `servings` |
| Rebuild a better plan | `gamito:regenerate_plan` | `plan_id` (+ optional overrides) |

`slot_key` is always `day_N:<slot>` (e.g. `day_2:dinner`). On a
`SLOT_NOT_FOUND` error, the hint lists the valid slot keys for that plan.

### Capture Feedback And Learn

- Numeric rating (1–10) → `gamito:rate_meal`. Ratings ≥ 8 are preserved and
  recipes from slots rated ≤ 4 are avoided when you later call
  `gamito:regenerate_plan` in auto-mode.
- Conversational preference → `gamito:update_preferences` with `liked_tags` /
  `disliked_tags`. Example: "this time without chillies" → `disliked_tags:
  ["spicy"]`.

### Pantry From A Photo

1. Identify long-shelf-life staples in the image yourself.
2. Call `gamito:update_pantry(profile_id, add_items=[...labels...])`.
3. Report `added` plainly; explain each `rejected` item using its `reason`
   (`perishable`, `unrecognised`).

### Save A Household Recipe

1. Parse the source (text or photo) into structured fields yourself.
2. Confirm allergens and dietary flags with the user.
3. Call `gamito:add_recipe`.
4. Surface unmatched ingredients from the response for correction.

## Error Handling

Every failure returns `{error_code, message, hint}`. The `hint` is written for
you — act on it before asking the user. Common cases:

| `error_code` | What to do |
|---|---|
| `INVALID_INPUT` | Fix the parameter shape/range and retry. For `plan_id="latest"`, include `profile_id`. |
| `PROFILE_NOT_FOUND` | Call `gamito:list_profiles`; create one with `gamito:save_profile` after interviewing. |
| `PLAN_NOT_FOUND` / `SLOT_NOT_FOUND` | Use a valid `plan_id`/`slot_key`; the hint lists valid slot keys. |
| `BUDGET_TOO_LOW` | Relay the stated minimum; ask the user to raise budget or reduce days. |
| `NO_CANDIDATES` | Constraints emptied the pool; propose the relaxation the hint suggests. |
| `LABEL_TAKEN` | The label is in use; pick another or unset the existing plan's label. |
| `RECIPE_VALIDATION_FAILED` | Provide title, paired ingredient names/amounts (≥1), and directions (≥1). |
| `RECIPE_IN_USE` | Recipe is referenced by plans; pass `force=true` to delete, or update instead. |

For the full error table and tool signatures, see
`references/tool-index.md`.

## Reference

- `references/tool-index.md` — all 20 tools by namespace, with parameters,
  return shapes, and the complete error contract. Read just-in-time when you
  need an exact signature.

## Automation Example

A recurring household plan can be scheduled in natural language:

```text
Every Sunday 18:00: gamito:generate_meal_plan for profile <id>,
budget 60 EUR, 2 servings, 5 days x 2 meals; send the text to the
family group.
```
