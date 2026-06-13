"""Structured MCP error contract with LLM-actionable hints."""

from __future__ import annotations

HINTS = {
    "INVALID_INPUT": (
        "Fix the parameter shape/range and retry. For plan_id='latest', "
        "include profile_id."
    ),
    "PROFILE_NOT_FOUND": (
        "Call list_profiles; create one with save_profile after interviewing the user."
    ),
    "PLAN_NOT_FOUND": "Call list_plans or generate_meal_plan, then retry with a valid plan_id.",
    "SLOT_NOT_FOUND": "Valid slot_keys for this plan: {slot_keys}.",
    "INVALID_BUDGET": (
        "Use a positive budget and keep num_days, meals_per_day, and servings "
        "within the documented limits."
    ),
    "BUDGET_TOO_LOW": (
        "Minimum feasible for {servings} servings x {slots} slots is about "
        "{minimum_eur:.2f} EUR; ask the user to raise the budget or reduce days."
    ),
    "NO_CANDIDATES": (
        "Constraints emptying the pool: {constraints}. Suggest relaxing time, "
        "budget, allergy/diet, or tool constraints."
    ),
    "VALIDATION_FAILED": "Validator issues: {issues}",
    "LABEL_TAKEN": (
        "Profile already has plan labelled '{label}' (plan_id={plan_id}). "
        "Pick another label or unset the existing one."
    ),
    "RECIPE_NOT_FOUND": "Call list_custom_recipes; only custom_* recipe_ids are editable.",
    "RECIPE_VALIDATION_FAILED": (
        "Required fields: title, ingredient_names/ingredient_amounts (>=1, same length), "
        "directions (>=1). Provide amounts even if approximate."
    ),
    "RECIPE_IN_USE": (
        "Recipe is referenced by plans {plan_ids}; pass force=true to delete and "
        "orphan those references, or update instead."
    ),
    "EMBEDDING_MODEL_MISMATCH": (
        "Custom recipe was embedded with model {got_model} but index expects "
        "{expected_model}. Run `gamito custom-recipes re-embed`."
    ),
}


class GamitoError(Exception):
    """Exception serialized at the MCP boundary."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        """Return the public MCP error shape."""

        return {"error_code": self.code, "message": self.message, "hint": self.hint}


def err(code: str, message: str, **fmt: object) -> GamitoError:
    """Build a ``GamitoError`` from a spec error code."""

    template = HINTS.get(code, "")
    try:
        hint = template.format(**fmt)
    except (KeyError, ValueError):
        hint = template
    return GamitoError(code, message, hint)
