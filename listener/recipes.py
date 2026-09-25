"""Custom analysis recipe loader.

Loads built-in recipes from listener/recipes/*.yaml and
user-defined recipes from ~/.listener/recipes/*.yaml.

Recipe = system prompt + user prompt template that tells Claude
how to analyze a meeting transcript.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, asdict

BUILTIN_DIR = Path(__file__).parent / "recipes"
CUSTOM_DIR = Path.home() / ".listener" / "recipes"


@dataclass
class Recipe:
    id: str
    name: str
    category: str
    system_prompt: str
    description: str = ""
    user_prompt_template: str = "Analyze this meeting transcript:\n\n{transcript}"
    model: str = "claude-sonnet-4-5"
    is_builtin: bool = False

    def to_dict(self) -> dict:
        """Serialize for API responses (exclude system_prompt for brevity)."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "model": self.model,
            "is_builtin": self.is_builtin,
        }


def load_recipes() -> list[Recipe]:
    """Load all recipes from built-in and custom directories."""
    recipes = []
    for directory, builtin in [(BUILTIN_DIR, True), (CUSTOM_DIR, False)]:
        if not directory.exists():
            continue
        for f in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                for entry in data.get("recipes", []):
                    entry["is_builtin"] = builtin
                    recipes.append(Recipe(**entry))
            except Exception:
                continue  # Skip malformed files
    return recipes


def get_recipe(recipe_id: str) -> Recipe | None:
    """Look up a recipe by its ID. Returns None if not found."""
    for r in load_recipes():
        if r.id == recipe_id:
            return r
    return None
