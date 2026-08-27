import os

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")

def get_prompt_path(name: str, version: str = "v1") -> str:
    """Returns the path to a versioned prompt file."""
    return os.path.join(PROMPTS_DIR, f"{name}_{version}.md")

def load_prompt(name: str, version: str = "v1") -> str:
    """Loads prompt text from file, with a fallback if the file does not exist."""
    path = get_prompt_path(name, version)
    if not os.path.exists(path):
        return f"Default prompt for {name} version {version}."
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
